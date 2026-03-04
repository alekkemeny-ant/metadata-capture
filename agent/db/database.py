"""Async PostgreSQL database setup for the metadata capture system."""

import os
import logging

import asyncpg

from .models import ALL_TABLES, CREATE_INDEXES

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Return the shared connection pool, creating it if needed."""
    global _pool
    if _pool is None:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL environment variable is not set")
        _pool = await asyncpg.create_pool(database_url, min_size=2, max_size=10)
        logger.info("PostgreSQL connection pool created")
    return _pool


async def get_db() -> asyncpg.Pool:
    """Return the shared connection pool (alias for get_pool)."""
    return await get_pool()


async def init_db() -> None:
    """Initialize the database tables and indexes."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        for ddl in ALL_TABLES:
            await conn.execute(ddl)
        for idx_ddl in CREATE_INDEXES:
            await conn.execute(idx_ddl)
    logger.info("Database tables initialized")


async def close_db() -> None:
    """Close the database connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL connection pool closed")
