"""Core agent service that wraps the Claude Code SDK for metadata capture."""

import asyncio
import base64
import json
import logging
import os
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    TextBlock,
)

from .prompts.system_prompt import SYSTEM_PROMPT
from .sdk_client_pool import get_pool
from .shared import stream_events
from .tools.capture_mcp import capture_server
from .tools.metadata_store import (
    get_conversation_history,
    get_session_records,
    get_upload,
    get_upload_extraction,
    save_conversation_turn,
)

logger = logging.getLogger(__name__)

# Set CHAT_PROFILE=1 to log per-stage latency (context gather, query() iter,
# first text delta). Kept off by default — time.perf_counter() is cheap but
# the extra log lines clutter output.
_PROFILE = os.environ.get("CHAT_PROFILE") == "1"

# Path to the AIND MCP server for schema context
MCP_SERVER_DIR = Path(__file__).resolve().parent.parent / "aind-metadata-mcp"


DEFAULT_MODEL = "claude-sonnet-4-6"

AVAILABLE_MODELS = [
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]


def _build_options(model: str | None = None) -> ClaudeAgentOptions:
    """Build the ClaudeAgentOptions for the metadata agent."""
    # MCP tool names are prefixed with "mcp__<server-name>__".
    # Trimmed from 20 → 13: dropped count_records, aggregation_retrieval,
    # get_summary, flatten_records, and the two identify_nwb_contents_*
    # (which pull in hdmf_zarr + boto3 — ~660ms of MCP startup). These
    # are browse/NWB-inspection tools; the capture workflow never calls
    # them. The get_*_example tools stay — system_prompt.py points the
    # agent at them for schema reference. Saves ~1000 tokens of tool
    # schemas per API turn.
    aind_mcp_tools = [
        "mcp__aind-metadata-mcp__get_records",
        "mcp__aind-metadata-mcp__get_project_names",
        "mcp__aind-metadata-mcp__get_modality_types",
        "mcp__aind-metadata-mcp__get_subject_example",
        "mcp__aind-metadata-mcp__get_procedures_example",
        "mcp__aind-metadata-mcp__get_data_description_example",
        "mcp__aind-metadata-mcp__get_session_example",
        "mcp__aind-metadata-mcp__get_instrument_example",
        "mcp__aind-metadata-mcp__get_acquisition_example",
        "mcp__aind-metadata-mcp__get_processing_example",
        "mcp__aind-metadata-mcp__get_quality_control_example",
        "mcp__aind-metadata-mcp__get_rig_example",
        "mcp__aind-metadata-mcp__get_top_level_nodes",
    ]

    # Capture tools (capture_metadata, find_records, link_records, render_artifact)
    capture_tools = [
        "mcp__capture__capture_metadata",
        "mcp__capture__find_records",
        "mcp__capture__link_records",
        "mcp__capture__render_artifact",
    ]

    # Combine all MCP servers
    mcp_servers: dict[str, Any] = {
        "capture": capture_server,
    }

    # Add AIND MCP server if available. SKIP_AIND_MCP=1 disables it for
    # perf testing — the stdio subprocess spawn is the dominant TTFT cost.
    #
    # Previously this called the `aind-metadata-mcp` CLI entrypoint. That
    # broke silently when the editable install's .pth file pointed at a
    # moved directory — the CLI would ModuleNotFoundError on startup and
    # the claude CLI would retry with backoff, adding 25–30s of latency
    # before giving up. Running the module directly with PYTHONPATH set
    # to our vendored source (a) can't break that way, (b) picks up our
    # lazy-import fix (~600ms off cold start), (c) makes the failure mode
    # obvious: if AIND_MCP_PYTHON doesn't have the deps, the backend
    # crashes at startup instead of every chat being mysteriously slow.
    #
    # AIND_MCP_PYTHON: path to a Python that has aind-data-access-api +
    # fastmcp installed. Defaults to the system conda py311 env that was
    # already hosting the editable install.
    mcp_src = MCP_SERVER_DIR / "src"
    print(f"[MCP] MCP_SERVER_DIR={MCP_SERVER_DIR}, src exists={mcp_src.is_dir()}, SKIP={os.environ.get('SKIP_AIND_MCP')}", flush=True)
    if mcp_src.is_dir() and os.environ.get("SKIP_AIND_MCP") != "1":
        mcp_python = os.environ.get(
            "AIND_MCP_PYTHON",
            sys.executable,
        )
        existing_pypath = os.environ.get("PYTHONPATH", "")
        new_pypath = f"{mcp_src}:{existing_pypath}" if existing_pypath else str(mcp_src)
        mcp_env = {**os.environ, "PYTHONPATH": new_pypath}
        print(f"[MCP] Registering AIND MCP server via {mcp_python} (src={mcp_src})", flush=True)
        logger.info("Registering AIND MCP server via %s (src=%s)", mcp_python, mcp_src)
        mcp_servers["aind-metadata-mcp"] = {
            "type": "stdio",
            "command": mcp_python,
            "args": ["-m", "aind_metadata_mcp.data_access_server"],
            "env": mcp_env,
        }

    # Built-in tools (Bash/Read/Glob/Grep/WebFetch/WebSearch) dropped — the
    # capture workflow is purely MCP-driven (capture_metadata + AIND schema
    # lookups). Each built-in adds a few hundred tokens of tool schema to
    # every API turn for zero value. Read stays so the agent can inspect
    # uploaded files on disk if base64 isn't enough (rare edge case).
    opts = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=["Read"] + capture_tools + aind_mcp_tools,
        max_turns=5,
        model=model if model in AVAILABLE_MODELS else DEFAULT_MODEL,
        mcp_servers=mcp_servers,
        include_partial_messages=True,
    )

    return opts


# Options are immutable once built — cache per-model to avoid re-running
# shutil.which() + file stat on the MCP config for every chat request. The
# MCP subprocess spawn itself still happens inside query() (SDK limitation),
# but this shaves the setup overhead and positions us for future session reuse.
_OPTIONS_CACHE: dict[str, ClaudeAgentOptions] = {}


def _get_options(model: str | None) -> ClaudeAgentOptions:
    key = model if model in AVAILABLE_MODELS else DEFAULT_MODEL
    if key not in _OPTIONS_CACHE:
        _OPTIONS_CACHE[key] = _build_options(model=key)
    return _OPTIONS_CACHE[key]


def _format_conversation_context(history: list[dict[str, Any]], user_message: str) -> str:
    """Format conversation history + new message into a prompt string."""
    parts: list[str] = []

    if history:
        parts.append("Previous conversation:")
        for turn in history[-10:]:  # Keep last 10 turns for context
            role = turn["role"].upper()
            content = turn["content"]
            # Add markers for attachments in history so agent knows what was shared
            attachments = turn.get("attachments_json")
            if attachments and isinstance(attachments, list):
                for att in attachments:
                    ct = att.get("content_type", "")
                    fname = att.get("filename", "file")
                    if ct.startswith("image/"):
                        content += f"\n[Attached image: {fname}]"
                    elif ct == "application/pdf":
                        content += f"\n[Attached PDF: {fname}]"
                    else:
                        # Generic fallback covers spreadsheets, text, docx,
                        # audio, video — anything the extraction pipeline
                        # handles. Include the MIME type so the agent can
                        # reason about what kind of file it was.
                        content += f"\n[Attached {ct or 'file'}: {fname}]"
            parts.append(f"{role}: {content}")
        parts.append("")

    parts.append(f"USER: {user_message}")
    return "\n".join(parts)


def _format_records_context(records: list[dict[str, Any]]) -> str:
    """Format existing records as context for the agent prompt."""
    if not records:
        return ""

    parts = ["\nExisting metadata records for this session:"]
    for r in records:
        data = r.get("data_json", {})
        name = r.get("name", "unnamed")
        parts.append(f"- [{r['record_type']}] id={r['id']} name=\"{name}\" data={json.dumps(data, default=str)}")

    return "\n".join(parts)


async def _create_message_stream(prompt: str | list[dict[str, Any]]):
    """Create an async generator for streaming input to the SDK."""
    yield {
        "type": "user",
        "message": {
            "role": "user",
            "content": prompt,
        },
    }


async def _build_multimodal_content(
    text_prompt: str, attachments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build Claude API content blocks from text + file attachments.

    Native types (images, PDF) are read from disk and base64-encoded inline.
    Everything else is looked up in the uploads.extraction_* columns — the
    heavy work (spreadsheet parsing, docx, transcription, keyframes) already
    ran as a background task at upload time.
    """
    content_blocks: list[dict[str, Any]] = []

    for att in attachments:
        file_path = Path(att.get("file_path", ""))
        content_type = att.get("content_type", "")
        filename = att.get("filename", file_path.name or "file")
        file_id = att.get("file_id")

        # --- Native types: send raw bytes to Claude ------------------------
        if content_type.startswith("image/") or content_type == "application/pdf":
            if not file_path.exists():
                logger.warning("Attachment file not found: %s", file_path)
                continue
            raw = file_path.read_bytes()
            b64_data = base64.standard_b64encode(raw).decode("ascii")
            if content_type.startswith("image/"):
                content_blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": content_type, "data": b64_data},
                })
            else:
                content_blocks.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": b64_data},
                })
            continue

        # --- Non-native: read cached extraction from DB --------------------
        if not file_id:
            logger.warning("Non-native attachment %s has no file_id; skipping", filename)
            continue

        extraction = await get_upload_extraction(file_id)
        if extraction is None:
            # Upload row doesn't exist — shouldn't happen if get_upload()
            # succeeded upstream, but guard anyway.
            logger.warning("No extraction row for upload %s", file_id)
            continue

        status = extraction["status"]

        if status == "pending":
            content_blocks.append({
                "type": "text",
                "text": (
                    f"[Attachment {filename} is still being processed — "
                    f"extraction not yet complete. Ask the user to wait a "
                    f"moment and resend their message.]"
                ),
            })
            continue

        if status == "error":
            content_blocks.append({
                "type": "text",
                "text": f"[Attachment {filename}: extraction failed — {extraction['error']}]",
            })
            continue

        # status == "done": inject extracted images then text
        for img_bytes, caption in extraction["images"]:
            content_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(img_bytes).decode("ascii"),
                },
            })
            content_blocks.append({"type": "text", "text": f"[{caption}]"})

        text = extraction["text"] or ""
        if text:
            header = f"[Attachment: {filename}]\n"
            # Partial-success case (e.g. video keyframes OK but transcription
            # timed out) — surface the error alongside the content we do have.
            if extraction["error"]:
                header += f"[Note: partial extraction — {extraction['error']}]\n"
            content_blocks.append({"type": "text", "text": header + text})
        elif not extraction["images"]:
            # Done but empty — rare, but tell the agent rather than silently
            # dropping the attachment.
            content_blocks.append({
                "type": "text",
                "text": f"[Attachment {filename}: extraction completed but produced no content]",
            })

    # Text prompt always comes last
    content_blocks.append({"type": "text", "text": text_prompt})
    return content_blocks


# ---------------------------------------------------------------------------
# SDK message → SSE translation
# ---------------------------------------------------------------------------
#
# Both the warm-pool path and the fallback query() path emit the same
# SDK message types. The only difference is where tool-handler events
# (validation/artifact) come from:
#   - query() path: we set stream_events locally; tool handlers run in
#     our task via the SDK-MCP bridge and push to our queue. We drain
#     between SDK messages.
#   - pool path: the worker task owns stream_events; it wraps events
#     in {"tool_event": ...} dicts and interleaves them in its output.
#
# _translate_to_sse() handles both by checking for the dict wrapper
# first, otherwise treating the item as an SDK message.


async def _query_with_tool_events(prompt_content, options) -> AsyncIterator[Any]:
    """Run query() and interleave tool-handler events into the stream.

    Yields the same mix _translate_to_sse() expects from the pool:
    SDK messages + {"tool_event": {...}} dicts.
    """
    queue: asyncio.Queue = asyncio.Queue()
    token = stream_events.set(queue)
    try:
        async for message in query(prompt=_create_message_stream(prompt_content), options=options):
            # Drain tool events between SDK messages (validation results
            # arrive after the tool_use AssistantMessage, before the
            # next text response).
            while not queue.empty():
                yield {"tool_event": queue.get_nowait()}
            yield message
        # Final drain after ResultMessage
        while not queue.empty():
            yield {"tool_event": queue.get_nowait()}
    finally:
        stream_events.reset(token)


async def _translate_to_sse(
    raw_iter: AsyncIterator[Any],
    full_response: list[str],
    _t,  # profiler timestamp fn or None
) -> AsyncIterator[dict[str, Any]]:
    """Translate SDK messages + tool events into SSE event dicts.

    `full_response` is mutated in place so the caller can persist the
    complete assistant text after the stream ends. `_t` is the
    CHAT_PROFILE timestamp closure or None.
    """
    # Tracking state for matching tool_use IDs to their results and
    # backfilling text that didn't arrive via deltas.
    last_capture_tool_use_id: str | None = None
    last_render_tool_use_id: str | None = None
    streamed_len_before_msg = 0
    first_delta_logged = False
    _first_iter = True

    async for item in raw_iter:
        if _t and _first_iter:
            print(f"[profile] +{_t():.0f}ms: first message from iterator (type={type(item).__name__})", flush=True)
            _first_iter = False

        # Tool-handler event forwarded by pool worker (or drained
        # inline by _query_with_tool_events). Route to the matching
        # tool_use_id tracked from earlier content_block_start.
        if isinstance(item, dict) and "tool_event" in item:
            evt = item["tool_event"]
            kind = evt.get("kind")
            if kind == "validation" and last_capture_tool_use_id:
                yield {"tool_result": {
                    "tool_use_id": last_capture_tool_use_id,
                    "validation": evt.get("data"),
                }}
                last_capture_tool_use_id = None
            elif kind == "artifact":
                yield {"artifact": {
                    **evt.get("artifact", {}),
                    "tool_use_id": last_render_tool_use_id,
                }}
                last_render_tool_use_id = None
            continue

        # SDK message objects
        message = item
        if isinstance(message, StreamEvent):
            event = message.event
            event_type = event.get("type")

            if event_type == "content_block_start":
                block = event.get("content_block", {})
                block_type = block.get("type")
                if block_type == "thinking":
                    yield {"thinking_start": True}
                elif block_type == "tool_use":
                    tool_name = block.get("name", "")
                    tool_id = block.get("id", "")
                    if "capture_metadata" in tool_name:
                        last_capture_tool_use_id = tool_id
                    elif "render_artifact" in tool_name:
                        last_render_tool_use_id = tool_id
                    yield {"tool_use_start": {"name": tool_name, "id": tool_id}}

            elif event_type == "content_block_delta":
                delta = event.get("delta", {})
                delta_type = delta.get("type")
                if delta_type == "text_delta":
                    text = delta["text"]
                    if _t and not first_delta_logged:
                        print(f"[profile] +{_t():.0f}ms: FIRST TEXT DELTA ({len(text)} chars) — this is TTFT", flush=True)
                        first_delta_logged = True
                    full_response.append(text)
                    yield {"content": text}
                elif delta_type == "thinking_delta":
                    yield {"thinking": delta.get("thinking", "")}
                elif delta_type == "input_json_delta":
                    yield {"tool_use_input": delta.get("partial_json", "")}

            elif event_type == "content_block_stop":
                yield {"block_stop": True}

        elif isinstance(message, AssistantMessage):
            # Backfill text that didn't arrive via deltas (e.g.
            # post-tool turns where streaming skipped a block).
            msg_text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
            streamed_since = "".join(full_response[streamed_len_before_msg:])
            if msg_text and msg_text != streamed_since:
                unstreamed = msg_text[len(streamed_since):] if msg_text.startswith(streamed_since) else msg_text
                if unstreamed:
                    logger.info("Yielding %d chars of unstreamed text", len(unstreamed))
                    full_response.append(unstreamed)
                    yield {"content": unstreamed}
            streamed_len_before_msg = len(full_response)

        elif isinstance(message, ResultMessage):
            if _t:
                out_chars = sum(len(s) for s in full_response)
                otps = (out_chars / 4) / (message.duration_ms / 1000) if message.duration_ms else 0
                print(f"[profile] +{_t():.0f}ms: ResultMessage (turns={message.num_turns} sdk_duration={message.duration_ms}ms out={out_chars} chars ~{out_chars // 4} tok, OTPS~{otps:.1f} tok/s)", flush=True)
            logger.info("Query complete: %d turns, %s ms", message.num_turns, message.duration_ms)


async def chat(
    session_id: str,
    user_message: str,
    model: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Process a chat message and stream the agent's response.

    Yields dicts with keys:
    - {"content": str} for text chunks
    - {"session_id": str} sent once at the start
    - {"done": True} when complete
    """
    t0 = time.perf_counter() if _PROFILE else 0.0
    _t = lambda: (time.perf_counter() - t0) * 1000  # ms since chat() start  # noqa: E731

    # Save the user's message (with attachment metadata if present)
    attachment_meta = None
    if attachments:
        attachment_meta = [
            {"file_id": a["file_id"], "filename": a["filename"], "content_type": a["content_type"]}
            for a in attachments
        ]
    await save_conversation_turn(session_id, "user", user_message, attachments=attachment_meta)

    # Send session_id first so the frontend can set up its UI state while
    # we're still gathering context below.
    yield {"session_id": session_id}
    if _PROFILE:
        print(f"[profile] +{_t():.0f}ms: session_id yielded", flush=True)

    # History + records + attachment lookups are independent reads — run them
    # concurrently. TTFT is dominated by the MCP subprocess spawn inside
    # query(), but this shaves the serialized round-trips that came before it.
    # The attachment lookups are themselves a gather, so we end up with one
    # await that fans out to 2 + N coroutines.
    async def _resolve_uploads() -> list[dict[str, Any] | None]:
        if not attachments:
            return []
        return await asyncio.gather(*(get_upload(a["file_id"]) for a in attachments))

    history, records, uploads = await asyncio.gather(
        get_conversation_history(session_id),
        get_session_records(session_id),
        _resolve_uploads(),
    )
    if _PROFILE:
        print(f"[profile] +{_t():.0f}ms: context gathered (history={len(history)} records={len(records)} uploads={len(uploads)})", flush=True)

    # Build conversation context
    prior_history = history[:-1] if history else []
    prompt = _format_conversation_context(prior_history, user_message)
    if records:
        prompt += _format_records_context(records)

    # Add session_id context for the capture tools — the agent doesn't know the
    # session_id otherwise, and the tool handlers hard-require it.
    prompt += (
        f"\n\nIMPORTANT: When calling capture_metadata, find_records, link_records, "
        f"or render_artifact, always use session_id=\"{session_id}\""
    )

    # Build multimodal content if attachments are present. Upload rows were
    # already fetched concurrently above; zip them back onto the attachment
    # descriptors and skip any that didn't resolve.
    if attachments and uploads:
        resolved_attachments = [
            {
                "file_id": att["file_id"],
                "file_path": up["file_path"],
                "content_type": att["content_type"],
                "filename": att["filename"],
            }
            for att, up in zip(attachments, uploads)
            if up is not None
        ]
        if resolved_attachments:
            prompt_content = await _build_multimodal_content(prompt, resolved_attachments)
        else:
            prompt_content = prompt
    else:
        prompt_content = prompt

    if _PROFILE:
        prompt_len = len(prompt_content) if isinstance(prompt_content, str) else sum(
            len(b.get("text", "")) if isinstance(b, dict) else 0 for b in prompt_content
        )
        print(f"[profile] +{_t():.0f}ms: prompt built ({prompt_len} chars, ~{prompt_len // 4} tokens)", flush=True)

    full_response: list[str] = []

    # Warm-pool fast path: skips the ~4s subprocess spawn on requests
    # 2+. Falls back to query() if the pool is down or disabled.
    pool = get_pool()
    use_pool = pool is not None and pool.is_warm and os.environ.get("USE_SDK_POOL", "1") == "1"

    if _PROFILE:
        path = "pool" if use_pool else "query()"
        print(f"[profile] +{_t():.0f}ms: entering {path}", flush=True)

    if use_pool:
        # Pool path: tool events arrive interleaved as {"tool_event": ...}
        # dicts — the worker task owns the stream_events queue, not us.
        raw_iter = pool.submit(prompt_content, model)
    else:
        # Fallback: spawn a fresh subprocess per request (~4s). We own
        # the stream_events queue here — tool handlers run in our
        # context via the SDK-MCP bridge.
        options = _get_options(model)
        raw_iter = _query_with_tool_events(prompt_content, options)

    try:
        async for sse_evt in _translate_to_sse(raw_iter, full_response, _t if _PROFILE else None):
            yield sse_evt
    except Exception as exc:
        logger.exception("Agent query failed for session %s: %s", session_id, exc)
        error_msg = "I encountered an error processing your request. Please try again."
        full_response.append(error_msg)
        yield {"content": error_msg}

    # Save the assistant's complete response
    assistant_text = "".join(full_response)
    if assistant_text.strip():
        await save_conversation_turn(session_id, "assistant", assistant_text)

    yield {"done": True}


async def get_session_messages(session_id: str) -> list[dict[str, Any]]:
    """Get conversation history for a session."""
    return await get_conversation_history(session_id)


async def get_sessions() -> list[dict[str, Any]]:
    """Get all sessions with their message counts."""
    from .db.database import get_db

    db = await get_db()
    rows = await db.fetch(
        """
        SELECT
            session_id,
            MIN(created_at) as created_at,
            MAX(created_at) as last_active,
            COUNT(*) as message_count,
            (
                SELECT content FROM conversations c2
                WHERE c2.session_id = conversations.session_id
                  AND c2.role = 'user'
                ORDER BY c2.created_at ASC
                LIMIT 1
            ) as first_message
        FROM conversations
        GROUP BY session_id
        ORDER BY MAX(created_at) DESC
        """
    )
    return [dict(r) for r in rows]
