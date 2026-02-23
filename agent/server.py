"""FastAPI HTTP server wrapping the metadata capture agent service."""

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .db.database import close_db, init_db
from .service import AVAILABLE_MODELS, DEFAULT_MODEL, chat, get_session_messages, get_sessions

UPLOADS_DIR = Path(__file__).resolve().parent.parent / "uploads"
ALLOWED_CONTENT_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf",
}
MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 MB

# Load environment variables from .env file
load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise the database on startup, close on shutdown."""
    await init_db()
    yield
    await close_db()


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


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------


@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    """Stream a chat response using Server-Sent Events."""
    session_id = req.session_id or str(uuid.uuid4())
    attachments = [a.model_dump() for a in req.attachments] if req.attachments else None

    async def event_stream():
        async for chunk in chat(session_id, req.message, model=req.model, attachments=attachments):
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Records endpoints
# ---------------------------------------------------------------------------


@app.get("/records")
async def list_records_endpoint(
    type: str | None = None,
    category: str | None = None,
    session_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List metadata records with optional filters."""
    from .tools.metadata_store import list_records

    return await list_records(
        record_type=type,
        category=category,
        session_id=session_id,
        status=status,
    )


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


@app.put("/records/{record_id}")
async def update_record_endpoint(record_id: str, req: UpdateRecordDataRequest) -> dict[str, Any]:
    """Update a record's data."""
    from .tools.metadata_store import get_record, update_record
    from .validation import validate_record

    existing = await get_record(record_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Record not found")

    result = await update_record(record_id, data=req.data)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to update record")

    # Re-validate
    from .tools.metadata_store import update_record_validation
    validation = validate_record(existing["record_type"], result.get("data_json") or {})
    await update_record_validation(record_id, validation.to_dict())

    updated = await get_record(record_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Record not found after update")
    return updated


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


@app.post("/upload")
async def upload_file(file: UploadFile, session_id: str | None = None):
    """Upload a file (image or PDF) for use in chat messages."""
    from .tools.metadata_store import save_upload

    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Allowed: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}",
        )

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 20 MB.")

    file_id = str(uuid.uuid4())
    ext = Path(file.filename or "file").suffix or ".bin"
    dest = UPLOADS_DIR / f"{file_id}{ext}"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(contents)

    return await save_upload(
        upload_id=file_id,
        original_filename=file.filename or "unknown",
        content_type=file.content_type or "application/octet-stream",
        file_path=str(dest),
        size_bytes=len(contents),
        session_id=session_id,
    )


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
    return {"status": "ok"}
