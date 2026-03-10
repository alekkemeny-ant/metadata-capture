"""FastAPI HTTP server wrapping the metadata capture agent service."""

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# The SDK runs `claude -v` in a fresh subprocess before every query() to
# detect version mismatches. Profiled at ~50–300ms per call, but more
# importantly it's one extra subprocess spawn + Python interpreter boot in
# the hot path. We check at startup instead (version drift mid-process is
# a non-concern for a single-worker service).
os.environ.setdefault("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .db.database import close_db, init_db
from .sdk_client_pool import init_pool
from .service import AVAILABLE_MODELS, DEFAULT_MODEL, _get_options, chat, get_session_messages, get_sessions
from .tools.extractors import EXTRACTORS, EXT_EXTRACTORS, NATIVE_TYPES
from .tools.spreadsheet import SPREADSHEET_CONTENT_TYPES, parse_spreadsheet

logger = logging.getLogger(__name__)

UPLOADS_DIR = Path(__file__).resolve().parent.parent / "uploads"

# Extensions corresponding to NATIVE_TYPES — used as the extension fallback
# for native files that arrive with a generic content-type like
# application/octet-stream.
_NATIVE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"})

# Derive allowed types from the extractor registry so server.py never drifts
# from extractors.py. NATIVE_TYPES are the formats Claude sees directly
# (images, PDF); everything in EXTRACTORS/EXT_EXTRACTORS goes through the
# upload-time background extraction pipeline. Native extensions are included
# in the extension fallback so .png/.pdf etc. pass validation even when the
# browser reports a generic MIME type.
ALLOWED_CONTENT_TYPES = NATIVE_TYPES | set(EXTRACTORS.keys())
ALLOWED_EXTENSIONS = set(EXT_EXTRACTORS.keys()) | _NATIVE_EXTS
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB — video needs headroom

# Audio/video: fail fast at upload if ffmpeg/whisper are unavailable rather
# than accepting the file and erroring 120s later at chat time.
_AV_EXTS = frozenset({".mp3", ".wav", ".m4a", ".ogg", ".mp4", ".mov", ".webm", ".mkv"})

# Load environment variables from .env file
load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise DB + warm the SDK client pool on startup.

    The pool pre-connects a ClaudeSDKClient during startup so the ~4s
    subprocess spawn happens once, not per request. If the connect fails
    (no API key, missing MCP deps, etc.), we log and continue — chat()
    falls back to one-shot query() which has the same failure surface.
    """
    print("[lifespan] Initializing database...", flush=True)
    try:
        await init_db()
        print("[lifespan] Database initialized OK", flush=True)
    except Exception:
        logger.exception("Database initialization failed — continuing without DB")

    if os.environ.get("USE_SDK_POOL", "0") == "1":
        pool = init_pool(_get_options)
        print("[lifespan] Warming SDK client pool...", flush=True)
        try:
            await asyncio.wait_for(pool.warmup(), timeout=30)
            print("[lifespan] SDK client pool warm", flush=True)
        except asyncio.TimeoutError:
            logger.warning(
                "SDK client pool warmup timed out after 30s — chat() will fall back to "
                "per-request query() (~4s slower)."
            )
            print("[lifespan] SDK client pool warmup timed out", flush=True)
        except Exception:
            logger.exception(
                "SDK client pool warmup failed — chat() will fall back to "
                "per-request query() (~4s slower). Set USE_SDK_POOL=0 to silence."
            )

    yield
    await close_db()
    # Pool shutdown: disconnect() is best-effort — the subprocess dies
    # with the worker anyway on SIGTERM.
    from .sdk_client_pool import get_pool
    p = get_pool()
    if p is not None:
        try:
            await p.shutdown()
        except Exception:
            pass


app = FastAPI(title="AIND Metadata Capture Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class AttachmentRef(BaseModel):
    file_id: str
    filename: str
    content_type: str


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    model: str | None = None
    attachments: list[AttachmentRef] | None = None


class LinkRequest(BaseModel):
    source_id: str
    target_id: str


class UpdateRecordDataRequest(BaseModel):
    data: dict[str, Any]


class PatchFieldRequest(BaseModel):
    field: str
    value: str


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------


@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    """Stream a chat response using Server-Sent Events."""
    session_id = req.session_id or str(uuid.uuid4())
    attachments = [a.model_dump() for a in req.attachments] if req.attachments else None

    async def event_stream():
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def _produce():
            try:
                async for chunk in chat(session_id, req.message, model=req.model, attachments=attachments):
                    await queue.put(f"data: {json.dumps(chunk)}\n\n")
                await queue.put("data: [DONE]\n\n")
            except Exception as exc:
                await queue.put(f"data: {json.dumps({'error': str(exc)})}\n\n")
            finally:
                await queue.put(None)

        producer = asyncio.create_task(_produce())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                yield item
        finally:
            producer.cancel()
            try:
                await producer
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.websocket("/ws/chat")
async def chat_ws(ws: WebSocket):
    """WebSocket chat endpoint — delivers events without proxy buffering."""
    await ws.accept()
    try:
        raw = await ws.receive_text()
        req_data = json.loads(raw)
        message = req_data.get("message", "")
        session_id = req_data.get("session_id") or str(uuid.uuid4())
        model = req_data.get("model")
        attachments = req_data.get("attachments")

        async for chunk in chat(session_id, message, model=model, attachments=attachments):
            await ws.send_text(json.dumps(chunk))
        await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await ws.send_text(json.dumps({"error": str(exc)}))
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Records endpoints
# ---------------------------------------------------------------------------


@app.get("/records")
async def list_records_endpoint(
    type: str | None = None,
    category: str | None = None,
    session_id: str | None = None,
    status: str | None = None,
    ids: str | None = None,
) -> list[dict[str, Any]]:
    """List metadata records with optional filters.

    `ids` is a comma-separated list of record IDs — used by the spreadsheet
    overlay to batch-fetch live data for an artifact snapshot.
    """
    from .tools.metadata_store import list_records

    id_list = [i for i in ids.split(",") if i] if ids else None
    return await list_records(
        record_type=type,
        category=category,
        session_id=session_id,
        status=status,
        ids=id_list,
    )


@app.get("/schema/enums")
async def get_schema_enums() -> dict[str, list[str]]:
    """Controlled vocabularies for dropdown-editable fields.

    Imports from validation.py (which has inline fallbacks) rather than
    schema_info so this works without aind-data-schema installed.
    Modality is omitted — stored as list[{abbreviation}], doesn't fit a
    single-cell dropdown.
    """
    from .validation import VALID_SEX, VALID_SPECIES

    return {"species": sorted(VALID_SPECIES), "sex": sorted(VALID_SEX)}


@app.get("/records/{record_id}")
async def get_record_endpoint(record_id: str) -> dict[str, Any]:
    """Get a single record with its linked records."""
    from .tools.metadata_store import get_linked_records, get_record

    record = await get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")

    links = await get_linked_records(record_id)
    record["links"] = links
    return record


async def _apply_record_update(
    record_id: str, record_type: str, data_patch: dict[str, Any]
) -> dict[str, Any]:
    """Shared tail for PUT and PATCH: merge → validate → refetch."""
    from .tools.metadata_store import get_record, update_record, update_record_validation
    from .validation import validate_record

    result = await update_record(record_id, data=data_patch)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to update record")

    validation = validate_record(record_type, result.get("data_json") or {})
    await update_record_validation(record_id, validation.to_dict())

    updated = await get_record(record_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="Record not found after update")
    return updated


@app.put("/records/{record_id}")
async def update_record_endpoint(record_id: str, req: UpdateRecordDataRequest) -> dict[str, Any]:
    """Update a record's data."""
    from .tools.metadata_store import get_record

    existing = await get_record(record_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Record not found")

    return await _apply_record_update(record_id, existing["record_type"], req.data)


def _build_field_patch(field: str, value: str) -> dict[str, Any]:
    """Map a flat (field, value) pair to the correct data_json shape.

    `species` is stored nested — a naive flat write would clobber registry
    info via update_record's shallow merge. species_name_to_dict reconstructs
    the complete dict so shallow merge replaces old-complete with new-complete.
    """
    from .schema_info import species_name_to_dict

    if field == "species":
        return {"species": species_name_to_dict(value)}
    return {field: value}


@app.patch("/records/{record_id}/field")
async def patch_record_field(record_id: str, req: PatchFieldRequest) -> dict[str, Any]:
    """Update a single field on a record, with server-side shape mapping.

    Unlike PUT /records/{id}, this:
    - Rejects unknown fields with 400 (PUT warn-and-stores them)
    - Knows about nested field shapes so the frontend can stay dumb
    - Takes only {field, value} — server-side merge, no read-before-write race
    """
    from .schema_info import KNOWN_FIELDS
    from .tools.metadata_store import get_record

    existing = await get_record(record_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Record not found")

    record_type = existing["record_type"]
    known = KNOWN_FIELDS.get(record_type, frozenset())
    if known and req.field not in known:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown field '{req.field}' for record type '{record_type}'",
        )

    return await _apply_record_update(
        record_id, record_type, _build_field_patch(req.field, req.value)
    )


@app.post("/records/{record_id}/confirm")
async def confirm_record_endpoint(record_id: str):
    """Confirm a record."""
    from .tools.metadata_store import confirm_record

    result = await confirm_record(record_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return result


@app.get("/records/{record_id}/links")
async def get_record_links_endpoint(record_id: str) -> list[dict[str, Any]]:
    """Get all records linked to a given record."""
    from .tools.metadata_store import get_linked_records, get_record

    record = await get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return await get_linked_records(record_id)


@app.post("/records/link")
async def link_records_endpoint(req: LinkRequest) -> dict[str, Any]:
    """Link two records together."""
    from .tools.metadata_store import get_record, link_records

    source = await get_record(req.source_id)
    target = await get_record(req.target_id)
    if source is None or target is None:
        raise HTTPException(status_code=404, detail="One or both records not found")

    return await link_records(req.source_id, req.target_id)


@app.delete("/records/{record_id}")
async def delete_record_endpoint(record_id: str):
    """Delete a record."""
    from .tools.metadata_store import delete_record

    deleted = await delete_record(record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"status": "deleted", "record_id": record_id}


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------


@app.get("/sessions")
async def list_sessions() -> list[dict[str, Any]]:
    """List all chat sessions with message counts."""
    return await get_sessions()


@app.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str) -> list[dict[str, Any]]:
    """Get conversation history for a session."""
    return await get_session_messages(session_id)


@app.get("/sessions/{session_id}/records")
async def get_session_records_endpoint(session_id: str) -> list[dict[str, Any]]:
    """Get all records created in a session."""
    from .tools.metadata_store import get_session_records

    return await get_session_records(session_id)


@app.delete("/sessions/{session_id}")
async def delete_session_endpoint(session_id: str):
    """Delete a session and all its data."""
    from .tools.metadata_store import delete_session

    deleted = await delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="No data found for this session")
    return {"status": "deleted", "session_id": session_id}


# ---------------------------------------------------------------------------
# Upload endpoints
# ---------------------------------------------------------------------------

# Folder uploads can enqueue dozens of files at once — each non-native file
# spawns a background extraction task. Cap concurrency so a 100-file folder
# of CSVs doesn't fire 100 simultaneous pandas parses and starve the event
# loop / blow out memory. The single /upload endpoint stays unchanged; this
# is purely backpressure on the background work.
_EXTRACTION_SEMAPHORE = asyncio.Semaphore(3)


async def _extract_and_store(upload_id: str, path: Path, content_type: str) -> None:
    """Background: run extraction and persist to the uploads row.

    Swallows all exceptions — a background failure must never crash the
    server. On any error the upload row is marked status='error' so the
    chat path can surface a readable message to the user.
    """
    from .tools.extractors import extract
    from .tools.metadata_store import set_upload_extraction

    async with _EXTRACTION_SEMAPHORE:
        try:
            result = await extract(path, content_type)
            await set_upload_extraction(
                upload_id,
                text=result.text,
                images=result.images,
                meta=result.meta,
                error=result.error,
            )
        except Exception as exc:
            logger.exception("Background extraction failed for %s", upload_id)
            try:
                await set_upload_extraction(
                    upload_id, text="", images=[], meta={}, error=str(exc),
                )
            except Exception:
                # DB write itself failed — log and give up. The row stays
                # 'pending'; the chat path will tell the user to wait, which
                # is the least-bad outcome here.
                logger.exception("Failed to persist extraction error for %s", upload_id)


@app.post("/upload")
async def upload_file(file: UploadFile, session_id: str | None = None):
    """Upload a file for use in chat messages.

    Native types (images, PDF) are stored as-is. Everything else is handed to
    the extraction pipeline as a background task so the single-worker uvicorn
    process isn't blocked for the duration of a transcription.
    """
    from .tools.metadata_store import save_upload

    content_type = file.content_type or ""
    ext = Path(file.filename or "").suffix.lower()

    # MIME type takes precedence; extension is the fallback for browsers that
    # report application/octet-stream or text/plain for known formats.
    if content_type not in ALLOWED_CONTENT_TYPES and ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {content_type or '(none)'} / {ext or '(no ext)'}",
        )

    # Audio/video: refuse the upload if ffmpeg/whisper aren't installed.
    # Better a 503 now than a silent "still processing" forever.
    if content_type.startswith(("audio/", "video/")) or ext in _AV_EXTS:
        from .tools.transcribe import check_availability
        avail = check_availability()
        if not avail["available"]:
            raise HTTPException(
                status_code=503,
                detail=f"Transcription unavailable: missing {', '.join(avail['missing'])}",
            )

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_UPLOAD_SIZE // (1024 * 1024)} MB.",
        )

    file_id = str(uuid.uuid4())
    dest_ext = ext or ".bin"
    dest = UPLOADS_DIR / f"{file_id}{dest_ext}"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(contents)

    # Native types (images, PDFs) go directly to Claude at chat time — no
    # extraction pipeline. Mark them 'done' at insert so the frontend's
    # polling loop sees them as ready immediately.
    is_native = content_type in NATIVE_TYPES or ext in _NATIVE_EXTS

    result = await save_upload(
        upload_id=file_id,
        original_filename=file.filename or "unknown",
        content_type=content_type,
        file_path=str(dest),
        size_bytes=len(contents),
        session_id=session_id,
        initial_status="done" if is_native else "pending",
    )

    # Schedule extraction for non-native types. The task runs after this
    # response is returned; the DB row starts as 'pending' and flips to
    # 'done'/'error' when the task finishes.
    if not is_native:
        asyncio.create_task(_extract_and_store(file_id, dest, content_type))

    return result


@app.get("/uploads/{file_id}/extraction")
async def get_upload_extraction_endpoint(file_id: str):
    """Report extraction status and a preview of the extracted content.

    Does not return image bytes — only counts — to keep the response small.
    The frontend polls this to know when an upload is ready to reference
    in chat.
    """
    from .tools.metadata_store import get_upload_extraction

    result = await get_upload_extraction(file_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Upload not found")

    return {
        "status": result["status"],
        "text_preview": (result["text"] or "")[:500],
        "meta": result["meta"],
        "error": result["error"],
        "image_count": len(result["images"]),
    }


@app.get("/uploads/{file_id}")
async def get_uploaded_file(file_id: str):
    """Serve an uploaded file by ID."""
    from .tools.metadata_store import get_upload

    upload = await get_upload(file_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")

    file_path = Path(upload["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(
        path=str(file_path),
        media_type=upload["content_type"],
        filename=upload["original_filename"],
    )


@app.get("/uploads/{file_id}/table")
async def get_upload_as_table(file_id: str) -> dict[str, Any]:
    """Parse a spreadsheet upload (CSV/XLSX) into columns + rows."""
    from .tools.metadata_store import get_upload

    upload = await get_upload(file_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")

    content_type = upload["content_type"]
    if content_type not in SPREADSHEET_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Upload is not a spreadsheet")

    file_path = Path(upload["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    try:
        parsed = parse_spreadsheet(file_path, content_type)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse spreadsheet: {exc}")

    return {
        **parsed,
        "filename": upload["original_filename"],
    }


# ---------------------------------------------------------------------------
# Artifact endpoints
# ---------------------------------------------------------------------------


@app.get("/artifacts/{artifact_id}")
async def get_artifact_endpoint(artifact_id: str) -> dict[str, Any]:
    """Fetch a single artifact by ID."""
    from .tools.metadata_store import get_artifact

    artifact = await get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


@app.get("/sessions/{session_id}/artifacts")
async def list_session_artifacts(session_id: str) -> list[dict[str, Any]]:
    """List all artifacts for a session."""
    from .tools.metadata_store import list_artifacts

    return await list_artifacts(session_id)


# ---------------------------------------------------------------------------
# Models + Health
# ---------------------------------------------------------------------------


@app.get("/models")
async def list_models():
    """List available models and the current default."""
    return {"models": AVAILABLE_MODELS, "default": DEFAULT_MODEL}


@app.get("/health")
async def health():
    """Health check endpoint."""
    from .tools.transcribe import check_availability

    avail = check_availability()
    return {
        "status": "ok",
        "transcription": "available" if avail["available"]
                         else f"unavailable: {', '.join(avail['missing'])}",
    }


@app.get("/debug/mcp")
async def debug_mcp():
    """Diagnostic: check MCP server import and AIND API connectivity."""
    import subprocess
    import sys

    result: dict[str, Any] = {}

    try:
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'aind-metadata-mcp/src'); "
             "from aind_metadata_mcp.data_access_server import setup_mongodb_client; "
             "c = setup_mongodb_client(); "
             "print(c._count_records(filter_query={}))"],
            capture_output=True, text=True, timeout=15,
            cwd="/home/runner/workspace",
        )
        result["mcp_import"] = "ok" if proc.returncode == 0 else "failed"
        result["stdout"] = proc.stdout.strip()
        if proc.returncode != 0:
            result["error_hint"] = proc.stderr.strip()[-200:] if proc.stderr else ""
    except Exception as e:
        result["mcp_import"] = f"error: {e}"

    from .sdk_client_pool import get_pool
    pool = get_pool()
    result["pool_warm"] = pool.is_warm if pool else False
    result["pool_connect_ms"] = pool._connect_ms if pool else None

    opts = _get_options(None)
    result["mcp_servers"] = list(opts.mcp_servers.keys()) if opts.mcp_servers else []
    result["allowed_tools_count"] = len([t for t in (opts.allowed_tools or []) if "aind" in t])

    return result
