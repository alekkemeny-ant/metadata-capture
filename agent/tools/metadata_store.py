"""Tools for persisting and retrieving metadata records."""

import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from ..db.database import get_db
from ..db.models import CATEGORY_MAP, VALID_RECORD_TYPES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json(value: str | None) -> Any:
    """Parse a JSON string, returning None on failure."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Parse JSON columns in a row dict."""
    d = dict(row)
    if d.get("data_json"):
        d["data_json"] = _parse_json(d["data_json"])
    if d.get("validation_json"):
        d["validation_json"] = _parse_json(d["validation_json"])
    return d


def _serialize(value: Any) -> str:
    """Serialize a value to JSON, guarding against double-serialization."""
    if isinstance(value, str):
        try:
            json.loads(value)
            return value  # already valid JSON
        except (json.JSONDecodeError, ValueError):
            return json.dumps(value)
    return json.dumps(value)


def _auto_name(record_type: str, data: dict[str, Any]) -> str | None:
    """Auto-generate a display name from record data."""
    if record_type == "subject":
        sid = data.get("subject_id")
        species = data.get("species", {})
        species_name = species.get("name", "") if isinstance(species, dict) else ""
        if sid:
            return f"{species_name} {sid}".strip() if species_name else str(sid)
    elif record_type == "instrument":
        return data.get("instrument_id") or data.get("name")
    elif record_type == "rig":
        return data.get("rig_id") or data.get("name")
    elif record_type == "procedures":
        ptype = data.get("procedure_type")
        return str(ptype) if ptype else None
    elif record_type == "data_description":
        pname = data.get("project_name")
        return str(pname) if pname else None
    elif record_type == "session":
        start = data.get("session_start_time")
        return f"Session {start}" if start else None
    return None


# ---------------------------------------------------------------------------
# Record CRUD
# ---------------------------------------------------------------------------

async def create_record(
    session_id: str,
    record_type: str,
    data: dict[str, Any],
    name: str | None = None,
) -> dict[str, Any]:
    """Create a new metadata record.

    Returns the created record as a dict.
    """
    if record_type not in VALID_RECORD_TYPES:
        raise ValueError(f"Invalid record_type: {record_type}")

    db = await get_db()
    record_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    category = CATEGORY_MAP[record_type]
    display_name = name or _auto_name(record_type, data)

    await db.execute(
        """INSERT INTO metadata_records
           (id, session_id, record_type, category, name, data_json, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
        (record_id, session_id, record_type, category, display_name, _serialize(data), now, now),
    )
    record = await get_record(record_id)
    assert record is not None, f"Record {record_id} not found after insert"
    return record


async def get_record(record_id: str) -> dict[str, Any] | None:
    """Get a single record by ID."""
    db = await get_db()
    row = await db.fetchrow("SELECT * FROM metadata_records WHERE id = ?", (record_id,))
    return _row_to_dict(row) if row else None


async def update_record(
    record_id: str,
    data: dict[str, Any] | None = None,
    name: str | None = None,
) -> dict[str, Any] | None:
    """Update a record's data and/or name. Merges data with existing."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    existing = await get_record(record_id)
    if existing is None:
        return None

    merged_data = existing.get("data_json") or {}

    if data is not None:
        if isinstance(merged_data, dict) and isinstance(data, dict):
            merged_data = {**merged_data, **data}
        else:
            merged_data = data
        await db.execute(
            "UPDATE metadata_records SET data_json = ?, updated_at = ? WHERE id = ?",
            (_serialize(merged_data), now, record_id),
        )

    if name is not None:
        await db.execute(
            "UPDATE metadata_records SET name = ?, updated_at = ? WHERE id = ?",
            (name, now, record_id),
        )
    elif data is not None:
        auto = _auto_name(existing["record_type"], merged_data)
        if auto:
            await db.execute(
                "UPDATE metadata_records SET name = ?, updated_at = ? WHERE id = ?",
                (auto, now, record_id),
            )

    return await get_record(record_id)


async def update_record_field(record_id: str, field: str, value: Any) -> dict[str, Any] | None:
    """Update a single field within a record's data_json."""
    existing = await get_record(record_id)
    if existing is None:
        return None
    data = existing.get("data_json") or {}
    if not isinstance(data, dict):
        data = {}
    data[field] = value
    return await update_record(record_id, data=data)


async def update_record_validation(record_id: str, validation: dict[str, Any]) -> None:
    """Update a record's validation results."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE metadata_records SET validation_json = ?, updated_at = ? WHERE id = ?",
        (_serialize(validation), now, record_id),
    )


async def confirm_record(record_id: str) -> dict[str, Any] | None:
    """Mark a record as confirmed."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    row = await db.fetchrow("SELECT id FROM metadata_records WHERE id = ?", (record_id,))
    if row is None:
        return None
    await db.execute(
        "UPDATE metadata_records SET status = 'confirmed', updated_at = ? WHERE id = ?",
        (now, record_id),
    )
    return await get_record(record_id)


async def delete_record(record_id: str) -> bool:
    """Delete a record and its links."""
    db = await get_db()
    result = await db.execute("DELETE FROM metadata_records WHERE id = ?", (record_id,))
    count = int(result.split()[-1]) if result else 0
    return count > 0


# ---------------------------------------------------------------------------
# Record queries
# ---------------------------------------------------------------------------

async def list_records(
    record_type: str | None = None,
    category: str | None = None,
    session_id: str | None = None,
    status: str | None = None,
    ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """List records with optional filters."""
    db = await get_db()
    clauses: list[str] = []
    params: list[Any] = []

    if record_type:
        clauses.append("record_type = ?")
        params.append(record_type)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if ids:
        placeholders = ",".join("?" * len(ids))
        clauses.append(f"id IN ({placeholders})")
        params.extend(ids)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = await db.fetch(
        f"SELECT * FROM metadata_records{where} ORDER BY created_at DESC",
        params,
    )
    return [_row_to_dict(r) for r in rows]


async def find_records(
    record_type: str | None = None,
    query: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Search records by type, category, and/or text query against name and data."""
    db = await get_db()
    clauses: list[str] = []
    params: list[Any] = []

    if record_type:
        clauses.append("record_type = ?")
        params.append(record_type)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if query:
        clauses.append("(name LIKE ? OR data_json LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = await db.fetch(
        f"SELECT * FROM metadata_records{where} ORDER BY updated_at DESC LIMIT 50",
        params,
    )
    return [_row_to_dict(r) for r in rows]


async def get_session_records(session_id: str) -> list[dict[str, Any]]:
    """Get all records created in a session."""
    return await list_records(session_id=session_id)


# ---------------------------------------------------------------------------
# Record linking
# ---------------------------------------------------------------------------

async def link_records(source_id: str, target_id: str) -> dict[str, Any]:
    """Create a link between two records. Returns link info."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    try:
        await db.execute(
            "INSERT INTO record_links (source_id, target_id, created_at) VALUES (?, ?, ?)",
            (source_id, target_id, now),
        )
    except Exception:
        pass
    return {"source_id": source_id, "target_id": target_id}


async def get_linked_records(record_id: str) -> list[dict[str, Any]]:
    """Get all records linked to a given record (in either direction)."""
    db = await get_db()
    rows = await db.fetch(
        """SELECT m.* FROM metadata_records m
           INNER JOIN record_links l ON (m.id = l.target_id AND l.source_id = ?)
                                     OR (m.id = l.source_id AND l.target_id = ?)""",
        (record_id, record_id),
    )
    return [_row_to_dict(r) for r in rows]


async def unlink_records(source_id: str, target_id: str) -> bool:
    """Remove a link between two records."""
    db = await get_db()
    result = await db.execute(
        "DELETE FROM record_links WHERE (source_id = ? AND target_id = ?) OR (source_id = ? AND target_id = ?)",
        (source_id, target_id, target_id, source_id),
    )
    count = int(result.split()[-1]) if result else 0
    return count > 0


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

async def delete_session(session_id: str) -> bool:
    """Delete all data for a session (conversations and records)."""
    db = await get_db()
    r1 = await db.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
    r2 = await db.execute("DELETE FROM metadata_records WHERE session_id = ?", (session_id,))
    c1 = int(r1.split()[-1]) if r1 else 0
    c2 = int(r2.split()[-1]) if r2 else 0
    return c1 + c2 > 0


# ---------------------------------------------------------------------------
# Conversation history helpers
# ---------------------------------------------------------------------------

async def save_conversation_turn(
    session_id: str,
    role: str,
    content: str,
    attachments: list[dict] | None = None,
) -> None:
    """Persist a single conversation turn, optionally with attachment metadata."""
    db = await get_db()
    attachments_json = json.dumps(attachments) if attachments else None
    await db.execute(
        "INSERT INTO conversations (session_id, role, content, attachments_json) VALUES (?, ?, ?, ?)",
        (session_id, role, content, attachments_json),
    )


async def get_conversation_history(session_id: str) -> list[dict[str, Any]]:
    """Retrieve full conversation history for a session."""
    db = await get_db()
    rows = await db.fetch(
        "SELECT role, content, attachments_json, created_at FROM conversations WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,),
    )
    result = []
    for r in rows:
        d = dict(r)
        if d.get("attachments_json"):
            d["attachments_json"] = _parse_json(d["attachments_json"])
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Upload management
# ---------------------------------------------------------------------------

async def save_upload(
    upload_id: str,
    original_filename: str,
    content_type: str,
    file_path: str,
    size_bytes: int,
    session_id: str | None = None,
    initial_status: str = "pending",
) -> dict[str, Any]:
    """Persist an upload record.

    initial_status: 'pending' for types that need background extraction,
    'done' for native types (images/PDFs) that are ready immediately.
    """
    db = await get_db()
    await db.execute(
        """INSERT INTO uploads (id, original_filename, content_type, file_path, size_bytes, session_id, extraction_status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (upload_id, original_filename, content_type, file_path, size_bytes, session_id, initial_status),
    )
    return {
        "id": upload_id,
        "filename": original_filename,
        "content_type": content_type,
        "size": size_bytes,
    }


async def get_upload(upload_id: str) -> dict[str, Any] | None:
    """Fetch an upload record by ID."""
    db = await get_db()
    row = await db.fetchrow("SELECT * FROM uploads WHERE id = ?", (upload_id,))
    return dict(row) if row else None


async def set_upload_extraction(
    upload_id: str,
    text: str,
    images: list[tuple[bytes, str]],
    meta: dict,
    error: str | None,
) -> None:
    """Persist extraction results for an upload.

    Image bytes are base64-encoded for JSON storage:
    [{"data": <b64 str>, "caption": <str>}, ...].
    """
    db = await get_db()
    images_json = json.dumps([
        {"data": base64.b64encode(b).decode("ascii"), "caption": cap}
        for b, cap in images
    ])
    meta_json = json.dumps(meta)
    status = "error" if error else "done"
    # Database.execute() auto-commits on the SQLite backend and asyncpg
    # doesn't need explicit commit outside transactions — no .commit() here.
    await db.execute(
        """UPDATE uploads
           SET extracted_text = ?,
               extracted_images_json = ?,
               extracted_meta_json = ?,
               extraction_status = ?,
               extraction_error = ?
           WHERE id = ?""",
        (text, images_json, meta_json, status, error, upload_id),
    )


async def append_upload_transcript(
    upload_id: str, text: str, error: str | None = None,
) -> None:
    """Merge a transcript into an existing extraction row without touching
    the images/status columns.

    Used by the slow video-transcript background task — keyframes already
    landed and flipped status to 'done', so the user may have already sent
    their chat. This just fills in the text for follow-up turns.
    """
    db = await get_db()
    row = await db.fetchrow(
        "SELECT extracted_meta_json, extraction_error FROM uploads WHERE id = ?",
        (upload_id,),
    )
    if row is None:
        return

    meta = _parse_json(row["extracted_meta_json"]) or {}
    if not isinstance(meta, dict):
        meta = {}
    meta["transcript_pending"] = False
    if error:
        meta["transcript_error"] = error

    # Preserve any prior error (e.g. partial keyframe failure); append ours.
    prior_err = row["extraction_error"]
    merged_err = "; ".join(e for e in (prior_err, error) if e) or None

    await db.execute(
        """UPDATE uploads
           SET extracted_text = ?,
               extracted_meta_json = ?,
               extraction_error = ?
           WHERE id = ?""",
        (text, json.dumps(meta), merged_err, upload_id),
    )


async def get_upload_extraction(upload_id: str) -> dict[str, Any] | None:
    """Fetch extraction results for an upload.

    Returns {"status", "text", "images", "meta", "error"} with images
    decoded back to [(bytes, caption), ...], or None if the upload
    doesn't exist.
    """
    db = await get_db()
    row = await db.fetchrow(
        """SELECT extraction_status, extracted_text, extracted_images_json,
                  extracted_meta_json, extraction_error
           FROM uploads WHERE id = ?""",
        (upload_id,),
    )
    if row is None:
        return None

    images: list[tuple[bytes, str]] = []
    decode_error: str | None = None
    raw_images = row["extracted_images_json"]
    if raw_images:
        try:
            for item in json.loads(raw_images):
                images.append(
                    (base64.b64decode(item["data"]), item["caption"])
                )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Malformed extracted_images_json for upload %s", upload_id)
            # Surface corruption to caller — don't silently pretend no images existed.
            decode_error = f"stored image data unreadable: {exc}"
            images = []

    meta: dict = {}
    raw_meta = row["extracted_meta_json"]
    if raw_meta:
        parsed = _parse_json(raw_meta)
        if isinstance(parsed, dict):
            meta = parsed

    stored_error = row["extraction_error"]
    return {
        "status": row["extraction_status"],
        "text": row["extracted_text"],
        "images": images,
        "meta": meta,
        "error": decode_error or stored_error,
    }


# ---------------------------------------------------------------------------
# Artifact management
# ---------------------------------------------------------------------------

async def create_artifact(
    session_id: str,
    artifact_type: str,
    title: str,
    content: Any,
    language: str | None = None,
) -> dict[str, Any]:
    """Persist an agent-generated artifact. Returns the created artifact."""
    db = await get_db()
    artifact_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO artifacts (id, session_id, artifact_type, title, content_json, language)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (artifact_id, session_id, artifact_type, title, _serialize(content), language),
    )
    created = await get_artifact(artifact_id)
    assert created is not None
    return created


async def get_artifact(artifact_id: str) -> dict[str, Any] | None:
    """Fetch a single artifact by ID, with content parsed from JSON."""
    db = await get_db()
    row = await db.fetchrow("SELECT * FROM artifacts WHERE id = ?", (artifact_id,))
    if row is None:
        return None
    d = dict(row)
    d["content"] = _parse_json(d.pop("content_json", None))
    return d


async def list_artifacts(session_id: str) -> list[dict[str, Any]]:
    """List all artifacts for a session, newest first."""
    db = await get_db()
    rows = await db.fetch(
        "SELECT * FROM artifacts WHERE session_id = ? ORDER BY created_at DESC",
        (session_id,),
    )
    result = []
    for row in rows:
        d = dict(row)
        d["content"] = _parse_json(d.pop("content_json", None))
        result.append(d)
    return result
