"""FastAPI HTTP server wrapping the metadata capture agent service."""

import json
import uuid
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .db.database import close_db, init_db
from .service import chat, confirm_draft, get_all_drafts, get_session_messages, get_sessions

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


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    """Stream a chat response using Server-Sent Events.

    Accepts {"message": str, "session_id": str | null}.
    Returns an SSE stream with:
      data: {"session_id": "..."}
      data: {"content": "..."}   (repeated)
      data: [DONE]
    """
    session_id = req.session_id or str(uuid.uuid4())

    async def event_stream():
        async for chunk in chat(session_id, req.message):
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


@app.get("/metadata")
async def list_metadata(status: str | None = None) -> list[dict[str, Any]]:
    """List all draft metadata entries, optionally filtered by status."""
    return await get_all_drafts(status)


@app.post("/metadata/{session_id}/confirm")
async def confirm_metadata_endpoint(session_id: str):
    """Confirm (finalize) the draft metadata for a session."""
    result = await confirm_draft(session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No draft found for this session")
    return result


@app.get("/sessions")
async def list_sessions() -> list[dict[str, Any]]:
    """List all chat sessions with message counts."""
    return await get_sessions()


@app.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str) -> list[dict[str, Any]]:
    """Get conversation history for a session."""
    return await get_session_messages(session_id)


@app.delete("/sessions/{session_id}")
async def delete_session_endpoint(session_id: str):
    """Delete a session and all its data (conversations + draft metadata)."""
    from .tools.metadata_store import delete_session

    deleted = await delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="No data found for this session")
    return {"status": "deleted", "session_id": session_id}


class UpdateFieldRequest(BaseModel):
    field: str
    value: dict[str, Any]


@app.put("/metadata/{session_id}/fields")
async def update_field(session_id: str, req: UpdateFieldRequest):
    """Update a single metadata field for a session's draft."""
    from .tools.metadata_store import update_draft_metadata

    result = await update_draft_metadata(session_id, req.field, req.value)
    if result is None:
        raise HTTPException(status_code=404, detail="No draft found for this session")
    return result


@app.get("/metadata/{session_id}/validation")
async def get_validation(session_id: str):
    """Get validation results for a session's draft metadata."""
    from .service import get_draft_metadata
    draft = await get_draft_metadata(session_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="No draft found for this session")
    return draft.get("validation_results_json") or {"status": "pending", "errors": [], "warnings": [], "missing_required": [], "valid_fields": [], "completeness_score": 0}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}
