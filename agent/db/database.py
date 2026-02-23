"""Async SQLite database setup for the metadata capture system."""

import os
from pathlib import Path

import aiosqlite

from .models import ALL_TABLES

DB_DIR = Path(os.environ.get("METADATA_DB_DIR", Path(__file__).resolve().parent.parent))
DB_PATH = DB_DIR / "metadata.db"

_db_connection: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    """Return the shared database connection, creating it if needed."""
    global _db_connection
    if _db_connection is None:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        _db_connection = await aiosqlite.connect(str(DB_PATH))
        _db_connection.row_factory = aiosqlite.Row
        await _db_connection.execute("PRAGMA journal_mode=WAL")
        await _db_connection.execute("PRAGMA foreign_keys=ON")
    return _db_connection


async def init_db() -> None:
    """Initialize the database tables."""
    db = await get_db()
    for ddl in ALL_TABLES:
        await db.executescript(ddl)
    # Migrate: add attachments_json to conversations if missing
    cursor = await db.execute("PRAGMA table_info(conversations)")
    cols = {row[1] for row in await cursor.fetchall()}
    if "attachments_json" not in cols:
        await db.execute("ALTER TABLE conversations ADD COLUMN attachments_json TEXT")
    await db.commit()


async def close_db() -> None:
    """Close the database connection."""
    global _db_connection
    if _db_connection is not None:
        await _db_connection.close()
        _db_connection = None
