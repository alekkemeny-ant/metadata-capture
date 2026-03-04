"""Tools for persisting and retrieving metadata records in PostgreSQL."""

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
    """Convert an asyncpg Record to a plain dict, parsing JSON columns."""
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

    pool = await get_db()
    record_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    category = CATEGORY_MAP[record_type]
    display_name = name or _auto_name(record_type, data)

    await pool.execute(
        """INSERT INTO metadata_records
           (id, session_id, record_type, category, name, data_json, status, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, 'draft', $7, $8)""",
        record_id, session_id, record_type, category, display_name, _serialize(data), now, now,
    )
    record = await get_record(record_id)
    assert record is not None, f"Record {record_id} not found after insert"
    return record


async def get_record(record_id: str) -> dict[str, Any] | None:
    """Get a single record by ID."""
    pool = await get_db()
    row = await pool.fetchrow("SELECT * FROM metadata_records WHERE id = $1", record_id)
    return _row_to_dict(row) if row else None


async def update_record(
    record_id: str,
    data: dict[str, Any] | None = None,
    name: str | None = None,
) -> dict[str, Any] | None:
    """Update a record's data and/or name. Merges data with existing."""
    pool = await get_db()
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
        await pool.execute(
            "UPDATE metadata_records SET data_json = $1, updated_at = $2 WHERE id = $3",
            _serialize(merged_data), now, record_id,
        )

    if name is not None:
        await pool.execute(
            "UPDATE metadata_records SET name = $1, updated_at = $2 WHERE id = $3",
            name, now, record_id,
        )
    elif data is not None:
        auto = _auto_name(existing["record_type"], merged_data)
        if auto:
            await pool.execute(
                "UPDATE metadata_records SET name = $1, updated_at = $2 WHERE id = $3",
                auto, now, record_id,
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
    pool = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    await pool.execute(
        "UPDATE metadata_records SET validation_json = $1, updated_at = $2 WHERE id = $3",
        _serialize(validation), now, record_id,
    )


async def confirm_record(record_id: str) -> dict[str, Any] | None:
    """Mark a record as confirmed."""
    pool = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    row = await pool.fetchrow("SELECT id FROM metadata_records WHERE id = $1", record_id)
    if row is None:
        return None
    await pool.execute(
        "UPDATE metadata_records SET status = 'confirmed', updated_at = $1 WHERE id = $2",
        now, record_id,
    )
    return await get_record(record_id)


async def delete_record(record_id: str) -> bool:
    """Delete a record and its links."""
    pool = await get_db()
    result = await pool.execute("DELETE FROM metadata_records WHERE id = $1", record_id)
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
) -> list[dict[str, Any]]:
    """List records with optional filters."""
    pool = await get_db()
    clauses: list[str] = []
    params: list[Any] = []
    idx = 1

    if record_type:
        clauses.append(f"record_type = ${idx}")
        params.append(record_type)
        idx += 1
    if category:
        clauses.append(f"category = ${idx}")
        params.append(category)
        idx += 1
    if session_id:
        clauses.append(f"session_id = ${idx}")
        params.append(session_id)
        idx += 1
    if status:
        clauses.append(f"status = ${idx}")
        params.append(status)
        idx += 1

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = await pool.fetch(
        f"SELECT * FROM metadata_records{where} ORDER BY created_at DESC",
        *params,
    )
    return [_row_to_dict(r) for r in rows]


async def find_records(
    record_type: str | None = None,
    query: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Search records by type, category, and/or text query against name and data."""
    pool = await get_db()
    clauses: list[str] = []
    params: list[Any] = []
    idx = 1

    if record_type:
        clauses.append(f"record_type = ${idx}")
        params.append(record_type)
        idx += 1
    if category:
        clauses.append(f"category = ${idx}")
        params.append(category)
        idx += 1
    if query:
        clauses.append(f"(name LIKE ${idx} OR data_json LIKE ${idx + 1})")
        params.extend([f"%{query}%", f"%{query}%"])
        idx += 2

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = await pool.fetch(
        f"SELECT * FROM metadata_records{where} ORDER BY updated_at DESC LIMIT 50",
        *params,
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
    pool = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    try:
        await pool.execute(
            "INSERT INTO record_links (source_id, target_id, created_at) VALUES ($1, $2, $3)",
            source_id, target_id, now,
        )
    except Exception:
        pass
    return {"source_id": source_id, "target_id": target_id}


async def get_linked_records(record_id: str) -> list[dict[str, Any]]:
    """Get all records linked to a given record (in either direction)."""
    pool = await get_db()
    rows = await pool.fetch(
        """SELECT m.* FROM metadata_records m
           INNER JOIN record_links l ON (m.id = l.target_id AND l.source_id = $1)
                                     OR (m.id = l.source_id AND l.target_id = $2)""",
        record_id, record_id,
    )
    return [_row_to_dict(r) for r in rows]


async def unlink_records(source_id: str, target_id: str) -> bool:
    """Remove a link between two records."""
    pool = await get_db()
    result = await pool.execute(
        "DELETE FROM record_links WHERE (source_id = $1 AND target_id = $2) OR (source_id = $3 AND target_id = $4)",
        source_id, target_id, target_id, source_id,
    )
    count = int(result.split()[-1]) if result else 0
    return count > 0


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

async def delete_session(session_id: str) -> bool:
    """Delete all data for a session (conversations and records)."""
    pool = await get_db()
    r1 = await pool.execute("DELETE FROM conversations WHERE session_id = $1", session_id)
    r2 = await pool.execute("DELETE FROM metadata_records WHERE session_id = $1", session_id)
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
    pool = await get_db()
    attachments_json = json.dumps(attachments) if attachments else None
    await pool.execute(
        "INSERT INTO conversations (session_id, role, content, attachments_json) VALUES ($1, $2, $3, $4)",
        session_id, role, content, attachments_json,
    )


async def get_conversation_history(session_id: str) -> list[dict[str, Any]]:
    """Retrieve full conversation history for a session."""
    pool = await get_db()
    rows = await pool.fetch(
        "SELECT role, content, attachments_json, created_at FROM conversations WHERE session_id = $1 ORDER BY created_at ASC",
        session_id,
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
) -> dict[str, Any]:
    """Persist an upload record."""
    pool = await get_db()
    await pool.execute(
        """INSERT INTO uploads (id, original_filename, content_type, file_path, size_bytes, session_id)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        upload_id, original_filename, content_type, file_path, size_bytes, session_id,
    )
    return {
        "id": upload_id,
        "filename": original_filename,
        "content_type": content_type,
        "size": size_bytes,
    }


async def get_upload(upload_id: str) -> dict[str, Any] | None:
    """Fetch an upload record by ID."""
    pool = await get_db()
    row = await pool.fetchrow("SELECT * FROM uploads WHERE id = $1", upload_id)
    return dict(row) if row else None
