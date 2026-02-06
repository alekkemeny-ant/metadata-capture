"""Shared state for cross-module communication within a single request."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar

# Queue for passing validation results from capture_metadata tool back to
# the streaming loop in service.py. Set per-request; None when not streaming.
validation_events: ContextVar[asyncio.Queue | None] = ContextVar(
    "validation_events", default=None
)
