"""Async database setup for the metadata capture system.

Automatically selects PostgreSQL (via asyncpg) when DATABASE_URL is set,
otherwise falls back to SQLite (via aiosqlite) for local development.
"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _sqlite_to_pg(sql: str) -> str:
    """Convert ``?`` placeholders to ``$1, $2, ...`` for PostgreSQL."""
    counter = 0

    def _replacer(match: re.Match) -> str:
        nonlocal counter
        counter += 1
        return f"${counter}"

    return re.sub(r"\?", _replacer, sql)


class Database(ABC):
    """Unified async database interface."""

    @abstractmethod
    async def execute(self, sql: str, params: tuple | list = ()) -> str:
        """Execute a statement. Returns a status string (e.g. 'DELETE 1')."""

    @abstractmethod
    async def fetch(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        """Execute a query and return all rows as dicts."""

    @abstractmethod
    async def fetchrow(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        """Execute a query and return a single row as a dict, or None."""

    @abstractmethod
    async def close(self) -> None:
        """Close the database connection / pool."""

    @abstractmethod
    async def init_tables(self) -> None:
        """Create tables and indexes, run backend-specific migrations."""


class PostgresDatabase(Database):
    """PostgreSQL backend using asyncpg."""

    def __init__(self) -> None:
        self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg
            database_url = os.environ["DATABASE_URL"]
            self._pool = await asyncpg.create_pool(database_url, min_size=2, max_size=10)
            logger.info("PostgreSQL connection pool created")
        return self._pool

    async def execute(self, sql: str, params: tuple | list = ()) -> str:
        pool = await self._get_pool()
        result = await pool.execute(_sqlite_to_pg(sql), *params)
        return result or ""

    async def fetch(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(_sqlite_to_pg(sql), *params)
        return [dict(r) for r in rows]

    async def fetchrow(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(_sqlite_to_pg(sql), *params)
        return dict(row) if row else None

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("PostgreSQL connection pool closed")

    async def init_tables(self) -> None:
        from .models import PG_TABLES, CREATE_INDEXES, UPLOADS_EXTRACTION_COLUMNS
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            for ddl in PG_TABLES:
                await conn.execute(ddl)
            for idx in CREATE_INDEXES:
                await conn.execute(idx)
            for col_name, col_type in UPLOADS_EXTRACTION_COLUMNS:
                await conn.execute(
                    f"ALTER TABLE uploads ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                )


class SQLiteDatabase(Database):
    """SQLite backend using aiosqlite."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn = None

    async def _get_conn(self):
        if self._conn is None:
            import aiosqlite
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA foreign_keys=ON")
            logger.info("SQLite connection opened: %s", self._db_path)
        return self._conn

    async def execute(self, sql: str, params: tuple | list = ()) -> str:
        conn = await self._get_conn()
        cursor = await conn.execute(sql, tuple(params))
        await conn.commit()
        return f"OK {cursor.rowcount}"

    async def fetch(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        conn = await self._get_conn()
        cursor = await conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def fetchrow(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        conn = await self._get_conn()
        cursor = await conn.execute(sql, tuple(params))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.info("SQLite connection closed")

    async def init_tables(self) -> None:
        from .models import SQLITE_TABLES, CREATE_INDEXES, UPLOADS_EXTRACTION_COLUMNS
        import aiosqlite
        conn = await self._get_conn()
        for ddl in SQLITE_TABLES:
            await conn.executescript(ddl)
        for idx in CREATE_INDEXES:
            await conn.execute(idx)
        # Migrate: add attachments_json to conversations if missing
        cols_cursor = await conn.execute("PRAGMA table_info(conversations)")
        cols = {row[1] for row in await cols_cursor.fetchall()}
        if "attachments_json" not in cols:
            await conn.execute("ALTER TABLE conversations ADD COLUMN attachments_json TEXT")
        # Migrate: add extraction columns to uploads if missing. Tolerate the
        # duplicate-column race when multiple workers call init_db concurrently
        # (check-then-alter is not atomic across processes with SQLite).
        cursor = await conn.execute("PRAGMA table_info(uploads)")
        upload_cols = {row[1] for row in await cursor.fetchall()}
        for col_name, col_def in UPLOADS_EXTRACTION_COLUMNS:
            if col_name not in upload_cols:
                try:
                    await conn.execute(f"ALTER TABLE uploads ADD COLUMN {col_name} {col_def}")
                except aiosqlite.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
        await conn.commit()


_db: Database | None = None


def _create_backend() -> Database:
    """Select the right backend based on environment."""
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        logger.info("Using PostgreSQL backend (DATABASE_URL is set)")
        return PostgresDatabase()
    else:
        db_dir = Path(os.environ.get("METADATA_DB_DIR", Path(__file__).resolve().parent.parent))
        db_path = db_dir / "metadata.db"
        logger.info("Using SQLite backend: %s", db_path)
        return SQLiteDatabase(db_path)


async def get_db() -> Database:
    """Return the shared database instance, creating it if needed."""
    global _db
    if _db is None:
        _db = _create_backend()
    return _db


async def init_db() -> None:
    """Initialize the database tables and indexes."""
    db = await get_db()
    await db.init_tables()
    logger.info("Database tables initialized")


async def close_db() -> None:
    """Close the database connection."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None
