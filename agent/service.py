"""Core agent service that wraps the Claude Code SDK for metadata capture."""

import asyncio
import base64
import json
import logging
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

# Path to the AIND MCP server for schema context
MCP_SERVER_DIR = Path(__file__).resolve().parent.parent / "aind-metadata-mcp"


DEFAULT_MODEL = "claude-opus-4-6"

AVAILABLE_MODELS = [
    "claude-opus-4-6",
    "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5-20251001",
]


def _build_options(model: str | None = None) -> ClaudeAgentOptions:
    """Build the ClaudeAgentOptions for the metadata agent."""
    # MCP tool names are prefixed with "mcp__<server-name>__"
    aind_mcp_tools = [
        "mcp__aind-metadata-mcp__get_records",
        "mcp__aind-metadata-mcp__count_records",
        "mcp__aind-metadata-mcp__aggregation_retrieval",
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
        "mcp__aind-metadata-mcp__get_additional_schema_help",
        "mcp__aind-metadata-mcp__get_summary",
        "mcp__aind-metadata-mcp__flatten_records",
        "mcp__aind-metadata-mcp__identify_nwb_contents_in_code_ocean",
        "mcp__aind-metadata-mcp__identify_nwb_contents_with_s3_link",
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

    # Add AIND MCP server if available
    mcp_config_path = MCP_SERVER_DIR / "mcp_config.json"
    if mcp_config_path.exists():
        import shutil

        mcp_command = shutil.which("aind-metadata-mcp") or "aind-metadata-mcp"
        logger.info("Registering AIND MCP server: %s", mcp_command)
        mcp_servers["aind-metadata-mcp"] = {
            "type": "stdio",
            "command": mcp_command,
        }

    opts = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=["Bash", "Read", "Glob", "Grep", "WebFetch", "WebSearch"] + capture_tools + aind_mcp_tools,
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

    # Build conversation context
    prior_history = history[:-1] if history else []
    prompt = _format_conversation_context(prior_history, user_message)
    if records:
        prompt += _format_records_context(records)

    # Add session_id context for the capture tools
    prompt += f"\n\nIMPORTANT: When calling capture_metadata, always use session_id=\"{session_id}\""

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

    # Run the agent with streaming input (required for custom MCP tools)
    options = _get_options(model)
    full_response: list[str] = []

    # Track how much text was streamed via deltas so we can detect
    # AssistantMessages whose text wasn't streamed (e.g. post-tool turns).
    streamed_len_before_msg = 0

    # Set up an events queue so tool handlers (capture_metadata,
    # render_artifact) can push results back to us for streaming.
    queue: asyncio.Queue = asyncio.Queue()
    token = stream_events.set(queue)
    last_capture_tool_use_id: str | None = None
    last_render_tool_use_id: str | None = None

    try:
        async for message in query(prompt=_create_message_stream(prompt_content), options=options):
            # Drain any pending events pushed by tool handlers. These arrive
            # after the tool executes (between the tool_use AssistantMessage
            # and the next text response).
            while not queue.empty():
                evt = queue.get_nowait()
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
                        full_response.append(text)
                        yield {"content": text}
                    elif delta_type == "thinking_delta":
                        yield {"thinking": delta.get("thinking", "")}
                    elif delta_type == "input_json_delta":
                        yield {"tool_use_input": delta.get("partial_json", "")}

                elif event_type == "content_block_stop":
                    yield {"block_stop": True}

            elif isinstance(message, AssistantMessage):
                # Check if this message has text that wasn't already streamed.
                msg_text_parts: list[str] = []
                for block in message.content:
                    if isinstance(block, TextBlock):
                        msg_text_parts.append(block.text)

                msg_text = "".join(msg_text_parts)
                streamed_since = "".join(full_response[streamed_len_before_msg:])

                if msg_text and msg_text != streamed_since:
                    unstreamed = msg_text
                    if streamed_since and msg_text.startswith(streamed_since):
                        unstreamed = msg_text[len(streamed_since):]
                    if unstreamed:
                        logger.info(
                            "Yielding %d chars of unstreamed text from AssistantMessage",
                            len(unstreamed),
                        )
                        full_response.append(unstreamed)
                        yield {"content": unstreamed}

                streamed_len_before_msg = len(full_response)

            elif isinstance(message, ResultMessage):
                logger.info(
                    "Query complete: %d turns, %s ms",
                    message.num_turns,
                    message.duration_ms,
                )
    except Exception as exc:
        logger.exception("Agent query failed for session %s: %s", session_id, exc)
        error_msg = "I encountered an error processing your request. Please try again."
        full_response.append(error_msg)
        yield {"content": error_msg}
    finally:
        stream_events.reset(token)

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
    cursor = await db.execute(
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
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]
