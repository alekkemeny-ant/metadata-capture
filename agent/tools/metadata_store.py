"""Tools for persisting and retrieving metadata records in SQLite."""

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


def _row_to_dict(row) -> dict[str, Any]:
    """Convert an aiosqlite Row to a plain dict, parsing JSON columns."""
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
    await db.commit()
    record = await get_record(record_id)
    assert record is not None, f"Record {record_id} not found after insert"
    return record


async def get_record(record_id: str) -> dict[str, Any] | None:
    """Get a single record by ID."""
    db = await get_db()
    cursor = await db.execute("SELECT * FROM metadata_records WHERE id = ?", (record_id,))
    row = await cursor.fetchone()
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
        # Auto-update name from merged data
        auto = _auto_name(existing["record_type"], merged_data)
        if auto:
            await db.execute(
                "UPDATE metadata_records SET name = ?, updated_at = ? WHERE id = ?",
                (auto, now, record_id),
            )

    await db.commit()
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
    await db.commit()


async def confirm_record(record_id: str) -> dict[str, Any] | None:
    """Mark a record as confirmed."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute("SELECT id FROM metadata_records WHERE id = ?", (record_id,))
    if await cursor.fetchone() is None:
        return None
    await db.execute(
        "UPDATE metadata_records SET status = 'confirmed', updated_at = ? WHERE id = ?",
        (now, record_id),
    )
    await db.commit()
    return await get_record(record_id)


async def delete_record(record_id: str) -> bool:
    """Delete a record and its links."""
    db = await get_db()
    cursor = await db.execute("DELETE FROM metadata_records WHERE id = ?", (record_id,))
    await db.commit()
    return (cursor.rowcount or 0) > 0


# ---------------------------------------------------------------------------
# Record queries
# ---------------------------------------------------------------------------

async def list_records(
    record_type: str | None = None,
    category: str | None = None,
    session_id: str | None = None,
    status: str | None = None,
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

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    cursor = await db.execute(
        f"SELECT * FROM metadata_records{where} ORDER BY created_at DESC",
        params,
    )
    rows = await cursor.fetchall()
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
    cursor = await db.execute(
        f"SELECT * FROM metadata_records{where} ORDER BY updated_at DESC LIMIT 50",
        params,
    )
    rows = await cursor.fetchall()
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
        await db.commit()
    except Exception:
        # UNIQUE constraint — link already exists
        pass
    return {"source_id": source_id, "target_id": target_id}


async def get_linked_records(record_id: str) -> list[dict[str, Any]]:
    """Get all records linked to a given record (in either direction)."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT m.* FROM metadata_records m
           INNER JOIN record_links l ON (m.id = l.target_id AND l.source_id = ?)
                                     OR (m.id = l.source_id AND l.target_id = ?)""",
        (record_id, record_id),
    )
    rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def unlink_records(source_id: str, target_id: str) -> bool:
    """Remove a link between two records."""
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM record_links WHERE (source_id = ? AND target_id = ?) OR (source_id = ? AND target_id = ?)",
        (source_id, target_id, target_id, source_id),
    )
    await db.commit()
    return (cursor.rowcount or 0) > 0


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

async def delete_session(session_id: str) -> bool:
    """Delete all data for a session (conversations and records)."""
    db = await get_db()
    c1 = await db.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
    c2 = await db.execute("DELETE FROM metadata_records WHERE session_id = ?", (session_id,))
    await db.commit()
    return (c1.rowcount or 0) + (c2.rowcount or 0) > 0


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
    await db.commit()


async def get_conversation_history(session_id: str) -> list[dict[str, Any]]:
    """Retrieve full conversation history for a session."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT role, content, attachments_json, created_at FROM conversations WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,),
    )
    rows = await cursor.fetchall()
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
) -> dict[str, Any]:
    """Persist an upload record."""
    db = await get_db()
    await db.execute(
        """INSERT INTO uploads (id, original_filename, content_type, file_path, size_bytes, session_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (upload_id, original_filename, content_type, file_path, size_bytes, session_id),
    )
    await db.commit()
    return {
        "id": upload_id,
        "filename": original_filename,
        "content_type": content_type,
        "size": size_bytes,
    }


async def get_upload(upload_id: str) -> dict[str, Any] | None:
    """Fetch an upload record by ID."""
    db = await get_db()
    cursor = await db.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None
