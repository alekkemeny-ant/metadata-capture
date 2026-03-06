"""Integration tests: POST /upload → background extraction → GET /uploads/{id}/extraction.

Exercises the FastAPI app through httpx ASGITransport (no real network).
The /upload handler schedules extraction via asyncio.create_task — these
tests poll the extraction endpoint, yielding to the event loop between
polls so the background task gets scheduled and run.

Run from repo root:
    python3 -m pytest evals/tasks/extraction/test_upload_extraction.py -v
"""

import asyncio
import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

# 1×1 transparent PNG — smallest valid PNG. Used to test the native-type
# skip path in /upload.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
)

# One persistent loop — same pattern as test_new_features.py. Every coro
# awaits on this loop, so the asyncio.create_task() in /upload schedules on
# the same loop and gets a chance to run each time we re-enter via _run().
_loop = asyncio.new_event_loop()


def _run(coro):
    return _loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def setup_db(tmp_path):
    """Fresh SQLite in tmp_path; reset module-level DB globals."""

    async def _setup():
        os.environ["METADATA_DB_DIR"] = str(tmp_path)
        import agent.db.database as db_mod
        db_mod._db_connection = None
        db_mod.DB_DIR = tmp_path
        db_mod.DB_PATH = tmp_path / "metadata.db"
        from agent.db.database import init_db
        await init_db()

    _run(_setup())
    yield

    async def _teardown():
        from agent.db.database import close_db
        await close_db()

    _run(_teardown())


@pytest.fixture()
def client(setup_db, tmp_path, monkeypatch):
    """AsyncClient + redirect UPLOADS_DIR into tmp_path so tests don't
    litter the real uploads/ directory."""
    import agent.server as server_mod
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    monkeypatch.setattr(server_mod, "UPLOADS_DIR", uploads_dir)

    transport = ASGITransport(app=server_mod.app)
    c = AsyncClient(transport=transport, base_url="http://testserver")
    yield c
    _run(c.aclose())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _poll_extraction(client, file_id, timeout_sec=2.0):
    """Poll GET /uploads/{id}/extraction until status leaves 'pending'.

    Each iteration does `await asyncio.sleep(0)` first — that yields the
    loop so the background extraction task (scheduled by create_task in
    /upload) gets a turn to execute before we check status.
    """
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while True:
        await asyncio.sleep(0)  # yield to background task
        resp = await client.get(f"/uploads/{file_id}/extraction")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] != "pending":
            return body
        if asyncio.get_event_loop().time() > deadline:
            pytest.fail(
                f"extraction still pending after {timeout_sec}s: {body}"
            )
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_upload_text_file_schedules_extraction(client):
    """POST .txt → background task extracts → GET reports status=done with content."""
    content = b"Subject 555 was perfused with PFA.\nSurgeon: Dr. Lee."

    resp = _run(client.post(
        "/upload",
        files={"file": ("notes.txt", content, "text/plain")},
    ))
    assert resp.status_code == 200
    upload = resp.json()
    file_id = upload["id"]
    assert upload["filename"] == "notes.txt"
    assert upload["content_type"] == "text/plain"

    # Poll until the background task finishes. Text extraction is just
    # a file read — should complete in a handful of loop ticks.
    extraction = _run(_poll_extraction(client, file_id))

    assert extraction["status"] == "done"
    assert extraction["error"] is None
    assert "Subject 555" in extraction["text_preview"]
    assert "Dr. Lee" in extraction["text_preview"]
    assert extraction["image_count"] == 0
    # extract_text meta carries the truncation flag
    assert extraction["meta"]["truncated"] is False


def test_upload_native_image_skips_extraction(client):
    """Native types (image/png) bypass the background task entirely —
    extraction_status stays at the DB default ('pending').
    """
    resp = _run(client.post(
        "/upload",
        files={"file": ("pixel.png", _TINY_PNG, "image/png")},
    ))
    assert resp.status_code == 200
    file_id = resp.json()["id"]

    # Give the loop a few turns — if a background task WAS wrongly scheduled,
    # this would let it run and flip status to done/error.
    async def _settle():
        for _ in range(5):
            await asyncio.sleep(0)
    _run(_settle())

    resp = _run(client.get(f"/uploads/{file_id}/extraction"))
    assert resp.status_code == 200
    body = resp.json()
    # Native types skip extraction: status is still the DB DEFAULT 'pending'.
    # No task ever ran, so there's no text, no meta, no error.
    assert body["status"] == "pending"
    assert body["text_preview"] == ""
    assert body["error"] is None
    assert body["image_count"] == 0


def test_health_reports_transcription_status(client):
    """GET /health includes a transcription field derived from check_availability()."""
    resp = _run(client.get("/health"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "transcription" in body
    t = body["transcription"]
    assert t == "available" or t.startswith("unavailable:"), (
        f"unexpected transcription value: {t!r}"
    )
    # On this box ffmpeg is missing — verify the missing-binaries string
    # is actually populated, not just the prefix.
    if t.startswith("unavailable:"):
        assert "ffmpeg" in t
