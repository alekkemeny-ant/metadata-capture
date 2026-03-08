"""Persistent ClaudeSDKClient pool — eliminates the ~4s subprocess spawn per chat.

The SDK's ClaudeSDKClient keeps a single `claude` CLI subprocess (and its
MCP stdio subprocesses) alive across queries. This is what we want: a
warm client so requests 2+ skip the 4s spawn entirely.

The catch (SDK client.py:55-62): the client's internal anyio.TaskGroup is
bound to the async context where `connect()` was called. You cannot create
a client in one request handler and reuse it in another — different task
contexts. So we run each client inside a dedicated asyncio task (its own
context) and exchange messages with it via asyncio.Queues.

Flow:
  - warmup(): starts a background task that owns a ClaudeSDKClient,
    connects it, then waits on an input queue
  - chat handler puts (prompt, model, out_queue, sentinel_ctx) on the
    input queue
  - worker task runs client.query(), reads responses, puts them on
    out_queue, puts a DONE sentinel when ResultMessage arrives
  - chat handler drains out_queue, translates to SSE events

The stream_events contextvar (used by capture_metadata to push validation
results) is a problem: the tool handler runs inside the worker task's
context, not the HTTP handler's. We bridge this by having the worker set
its own queue and forward drained events into out_queue with a marker.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import ResultMessage

from .shared import stream_events

logger = logging.getLogger(__name__)

# Sentinels for the output queue — class-as-sentinel pattern so they're
# unambiguously not SDK message objects.
class _Done: ...
class _Error:
    def __init__(self, exc: BaseException): self.exc = exc
_DONE = _Done()


class _Work:
    """A single chat request routed to a pooled client."""
    __slots__ = ("prompt", "model", "out_q")

    def __init__(self, prompt: str | list[dict[str, Any]], model: str | None,
                 out_q: asyncio.Queue):
        self.prompt = prompt
        self.model = model
        self.out_q = out_q


class SDKClientPool:
    """Owns one warm ClaudeSDKClient in a background task.

    Size=1 is fine for our single-worker uvicorn — FastAPI serialises
    requests on the event loop anyway, and ClaudeSDKClient cannot
    interleave queries on the same stdin stream. If we ever go
    multi-worker, this becomes one pool per worker (process-local).
    """

    def __init__(self, options_factory):
        """options_factory(model) -> ClaudeAgentOptions (or cached)."""
        self._options_factory = options_factory
        self._in_q: asyncio.Queue[_Work | None] = asyncio.Queue(maxsize=1)
        self._worker: asyncio.Task | None = None
        self._ready = asyncio.Event()
        # Wall time of the connect() — lets the caller log warm-vs-cold.
        self._connect_ms: float = 0.0

    async def warmup(self):
        """Start the worker task and wait for it to connect.

        Call this from FastAPI lifespan startup so the first chat
        request doesn't pay the 4s spawn cost.
        """
        if self._worker is not None:
            return
        self._worker = asyncio.create_task(self._run(), name="sdk-client-pool")
        # Block until connect() completes (or the task crashes).
        ready_wait = asyncio.create_task(self._ready.wait())
        worker_wait = asyncio.ensure_future(self._worker)
        _done, pending = await asyncio.wait(
            {ready_wait, worker_wait}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            if t is ready_wait:  # only cancel the ready waiter, not the worker
                t.cancel()
        if self._worker.done():
            exc = self._worker.exception()
            if exc is not None:
                raise exc  # connect failed
        logger.info("SDK client pool warm: connect took %.0fms", self._connect_ms)

    async def shutdown(self):
        if self._worker is None:
            return
        await self._in_q.put(None)  # signals worker to disconnect
        try:
            await asyncio.wait_for(self._worker, timeout=5)
        except asyncio.TimeoutError:
            self._worker.cancel()
        self._worker = None

    @property
    def is_warm(self) -> bool:
        return self._ready.is_set() and self._worker is not None and not self._worker.done()

    async def submit(self, prompt, model: str | None) -> AsyncIterator[Any]:
        """Run a query on the warm client and yield raw SDK messages + tool events.

        Yields:
          - SDK message objects (StreamEvent, AssistantMessage, ResultMessage)
          - dicts with key 'tool_event' for validation/artifact results
            pushed by capture_metadata via the stream_events contextvar
        """
        if not self.is_warm:
            raise RuntimeError("SDK client pool not warm — call warmup() first or fall back to query()")

        out_q: asyncio.Queue = asyncio.Queue()
        await self._in_q.put(_Work(prompt, model, out_q))

        while True:
            item = await out_q.get()
            if item is _DONE:
                return
            if isinstance(item, _Error):
                raise item.exc
            yield item

    async def _run(self):
        # Build options once — the worker's client is single-model at
        # connect time but set_model() swaps it per request.
        opts = self._options_factory(None)
        client = ClaudeSDKClient(options=opts)

        t0 = time.perf_counter()
        await client.connect()
        self._connect_ms = (time.perf_counter() - t0) * 1000
        self._ready.set()

        try:
            while True:
                work = await self._in_q.get()
                if work is None:
                    break
                await self._handle(client, work)
        finally:
            try:
                await client.disconnect()
            except Exception:
                logger.exception("Error during client disconnect")

    async def _handle(self, client: ClaudeSDKClient, work: _Work):
        """Run one query and stream results to work.out_q.

        If the client's subprocess died (BrokenPipeError on query() or
        no messages received), we propagate the error — the caller
        should fall back to the one-shot query() path. A fancier pool
        would reconnect here, but reconnect races with the caller
        already having given up, so simpler is better.
        """
        # Bridge the stream_events contextvar: tool handlers (which run
        # in THIS task's context via the SDK-MCP bridge) push to our
        # queue; we forward them as dicts so the caller can tell them
        # apart from SDK messages.
        tool_q: asyncio.Queue = asyncio.Queue()
        token = stream_events.set(tool_q)

        try:
            if work.model:
                await client.set_model(work.model)

            # ClaudeSDKClient.query() takes str or AsyncIterable[dict].
            # Our prompt is str or list[dict] (multimodal content blocks).
            # The list case needs wrapping in an async iterator.
            if isinstance(work.prompt, list):
                async def _one():
                    yield {"type": "user", "message": {"role": "user", "content": work.prompt}}
                await client.query(_one())
            else:
                await client.query(work.prompt)

            async for msg in client.receive_response():
                # Drain tool events between SDK messages — same pattern
                # as service.chat()'s queue drain.
                while not tool_q.empty():
                    evt = tool_q.get_nowait()
                    await work.out_q.put({"tool_event": evt})
                await work.out_q.put(msg)
                if isinstance(msg, ResultMessage):
                    break

            # Final drain
            while not tool_q.empty():
                await work.out_q.put({"tool_event": tool_q.get_nowait()})
            await work.out_q.put(_DONE)

        except Exception as exc:
            logger.exception("Pool query failed")
            await work.out_q.put(_Error(exc))
            # Subprocess may be dead. Clear ready flag so callers know
            # to fall back; a future warmup() call will reconnect.
            self._ready.clear()
        finally:
            stream_events.reset(token)


# Module-level singleton — one pool per worker process. warmup() is
# called from server.py lifespan; USE_SDK_POOL=0 disables it for
# debugging or when something goes sideways.
_pool: SDKClientPool | None = None


def get_pool() -> SDKClientPool | None:
    return _pool


def init_pool(options_factory) -> SDKClientPool:
    global _pool
    _pool = SDKClientPool(options_factory)
    return _pool
