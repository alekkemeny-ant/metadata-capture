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
from urllib.request import urlopen
from urllib.error import URLError

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

    HEALTH_CHECK_INTERVAL_S = 120   # check every 2 min
    MAX_POOL_AGE_S = 300            # force reconnect after 5 min
    AIND_API_URL = "https://api.allenneuraldynamics.org/v2"

    def __init__(self, options_factory):
        """options_factory(model) -> ClaudeAgentOptions (or cached)."""
        self._options_factory = options_factory
        self._in_q: asyncio.Queue[_Work | None] = asyncio.Queue(maxsize=1)
        self._worker: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._needs_reconnect = False
        self._connect_monotonic: float = 0.0
        self._connect_ms: float = 0.0

    def start(self) -> None:
        """Start the pool worker and MCP watchdog in the background.

        Returns immediately — the worker task runs concurrently and sets
        _ready when connect() finishes. Use await_warm() to wait for it.
        """
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="sdk-client-pool")
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog(), name="mcp-watchdog")

    async def await_warm(self, timeout: float) -> bool:
        """Wait up to `timeout` seconds for the pool to become warm.

        Returns True if the pool is warm, False if the timeout elapsed.
        Safe to call even before start() — returns False immediately.
        """
        if self.is_warm:
            return True
        if self._worker is None:
            return False
        try:
            await asyncio.wait_for(asyncio.shield(self._ready.wait()), timeout=timeout)
            return self.is_warm
        except asyncio.TimeoutError:
            return False

    async def warmup(self):
        """Start the worker task and wait for it to connect.

        Legacy blocking API kept for compatibility. Prefer start() +
        await_warm() so callers can control the timeout independently.
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
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            self._watchdog_task = None
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

    @staticmethod
    def _ping_aind_api(url: str, timeout: float = 10.0) -> bool:
        """Synchronous HTTP check — run in executor to avoid blocking."""
        try:
            with urlopen(url, timeout=timeout) as resp:
                return resp.status == 200
        except (URLError, OSError, TimeoutError):
            return False

    async def _watchdog(self):
        """Background task: periodically health-check the AIND API and
        force a pool reconnect when the connection is stale.

        Every HEALTH_CHECK_INTERVAL_S:
        1. Ping api.allenneuraldynamics.org — if unreachable, log a
           warning (reconnecting won't help if the API is down).
        2. If reachable AND the pool connection is older than
           MAX_POOL_AGE_S, set _needs_reconnect so _run() picks it up
           on the next 30s poll cycle.
        """
        loop = asyncio.get_running_loop()
        while True:
            try:
                await asyncio.sleep(self.HEALTH_CHECK_INTERVAL_S)

                healthy = await loop.run_in_executor(
                    None, self._ping_aind_api, self.AIND_API_URL
                )

                if not healthy:
                    logger.warning("MCP watchdog: AIND API unreachable (%s)", self.AIND_API_URL)
                    continue

                if not self.is_warm:
                    logger.info("MCP watchdog: pool not warm, skipping age check")
                    continue

                age = time.monotonic() - self._connect_monotonic
                if age > self.MAX_POOL_AGE_S:
                    logger.info(
                        "MCP watchdog: pool age %.0fs > %ds and AIND API healthy — requesting reconnect",
                        age, self.MAX_POOL_AGE_S,
                    )
                    self._needs_reconnect = True
                else:
                    logger.debug("MCP watchdog: pool age %.0fs OK, AIND API healthy", age)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("MCP watchdog: unexpected error")

    async def _run(self):
        """Worker task — owns one ClaudeSDKClient and auto-reconnects on failure.

        Three reconnect triggers:
        1. Noisy failure: _handle() raises → _ready cleared → reconnect.
        2. Idle timeout: no work for POLL_TIMEOUT_S → reconnect.
        3. Watchdog: _needs_reconnect flag set by background health
           check → reconnect on next poll cycle.

        On each reconnect cycle, stream_events contextvar is re-set on
        the fresh Queue so tool callbacks in the new client context land
        in the right queue.
        """
        RECONNECT_DELAY_S = 5         # pause between reconnect cycles after failure
        CONNECT_RETRY_DELAY_S = 60    # pause before retrying a failed connect()
        POLL_TIMEOUT_S = 30.0         # wake every 30s to check watchdog flag

        while True:
            opts = self._options_factory(None)
            client = ClaudeSDKClient(options=opts)

            # Set stream_events BEFORE connect(). connect() spawns the SDK's
            # stdio reader task which inherits contextvar at spawn time.
            # Reset and re-set on each cycle so the fresh client gets its
            # own queue; stale references from old cycles are gone.
            self._tool_q = asyncio.Queue()
            token = stream_events.set(self._tool_q)

            reconnect_reason: str = "idle"
            connect_failed = False
            try:
                t0 = time.perf_counter()
                await client.connect()
                self._connect_ms = (time.perf_counter() - t0) * 1000
                self._connect_monotonic = time.monotonic()
                self._needs_reconnect = False
                self._ready.set()
                logger.info("SDK client pool ready (connect=%.0fms)", self._connect_ms)

                while True:
                    # Poll with short timeout so we can check the watchdog
                    # reconnect flag regularly (every 30s).
                    try:
                        work = await asyncio.wait_for(
                            self._in_q.get(), timeout=POLL_TIMEOUT_S
                        )
                    except asyncio.TimeoutError:
                        if self._needs_reconnect:
                            self._needs_reconnect = False
                            reconnect_reason = "watchdog"
                            break
                        continue  # keep polling

                    if work is None:
                        return  # shutdown signal

                    await self._handle(client, work)
                    if not self._ready.is_set():
                        reconnect_reason = "handle-failure"
                        break

                    if self._needs_reconnect:
                        self._needs_reconnect = False
                        reconnect_reason = "watchdog"
                        break

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Pool connect() failed")
                connect_failed = True
            finally:
                # Single cleanup path for all exit routes (normal break,
                # exception, CancelledError). ready and token are always
                # cleared here — no double-reset in the except block.
                self._ready.clear()
                stream_events.reset(token)
                try:
                    await client.disconnect()
                except Exception:
                    logger.exception("Error disconnecting pool client")

            delay = CONNECT_RETRY_DELAY_S if connect_failed else RECONNECT_DELAY_S
            logger.info("Pool reconnecting (%s) in %ds...", reconnect_reason, delay)
            await asyncio.sleep(delay)

    async def _handle(self, client: ClaudeSDKClient, work: _Work):
        """Run one query and stream results to work.out_q.

        If the client's subprocess died (BrokenPipeError on query() or
        no messages received), we propagate the error — the caller
        should fall back to the one-shot query() path. A fancier pool
        would reconnect here, but reconnect races with the caller
        already having given up, so simpler is better.
        """
        # Flush any stale events from the last request — shouldn't happen
        # since _handle drains fully before returning, but defensive.
        tool_q = self._tool_q
        while not tool_q.empty():
            tool_q.get_nowait()

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
