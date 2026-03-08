"""Shared state for cross-module communication within a single request."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar

# Queue for passing events (validation results, artifact notifications) from
# tool handlers back to the streaming loop in service.py. Each event is a
# tagged dict: {"kind": "validation" | "artifact", ...}. Set per-request.
stream_events: ContextVar[asyncio.Queue | None] = ContextVar(
    "stream_events", default=None
)
