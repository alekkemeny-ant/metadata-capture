"""Tests for service.py prompt-assembly wiring.

_build_multimodal_content turns cached extraction results into the content
blocks Claude actually sees. If this is broken, uploads succeed but the agent
gets nothing.

_format_conversation_context adds history markers so follow-up turns know
what was previously attached (staff review P1#3).
"""
import asyncio
import base64
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from agent.service import _build_multimodal_content, _format_conversation_context

# Sync tests drive coroutines via run_until_complete — same pattern as
# test_extractors.py. Keeps us independent of the session-scoped conftest loop.
_loop = asyncio.new_event_loop()


def _run(coro):
    return _loop.run_until_complete(coro)


# Minimal valid PNG: signature + IHDR + IDAT + IEND. 1x1 transparent.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a"
    "0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000000000200015e9e47f8"
    "0000000049454e44ae426082"
)


def _ext(status="done", text="", images=None, error=None, meta=None):
    """Shape matches agent.tools.metadata_store.get_upload_extraction."""
    return {
        "status": status,
        "text": text,
        "images": images or [],
        "meta": meta or {},
        "error": error,
    }


def _build(prompt, atts, extraction_mock=None):
    """Run _build_multimodal_content with get_upload_extraction patched."""
    if extraction_mock is None:
        return _run(_build_multimodal_content(prompt, atts))
    with patch("agent.service.get_upload_extraction", new=extraction_mock):
        return _run(_build_multimodal_content(prompt, atts))


# ---------------------------------------------------------------------------
# Native types (images, PDF) — unchanged direct-to-Claude path
# ---------------------------------------------------------------------------


def test_native_image_goes_direct_no_db_lookup():
    """Images base64 straight to an image block — no extraction lookup."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(_TINY_PNG)
        png = Path(f.name)
    try:
        att = [{"file_path": str(png), "content_type": "image/png", "filename": "x.png"}]
        with patch("agent.service.get_upload_extraction") as mock_get:
            blocks = _run(_build_multimodal_content("describe this", att))
        mock_get.assert_not_called()
        assert len(blocks) == 2
        assert blocks[0]["type"] == "image"
        assert blocks[0]["source"]["media_type"] == "image/png"
        assert base64.standard_b64decode(blocks[0]["source"]["data"]) == _TINY_PNG
        assert blocks[1] == {"type": "text", "text": "describe this"}
    finally:
        png.unlink()


def test_native_pdf_becomes_document_block():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4\n%fake")
        pdf = Path(f.name)
    try:
        att = [{"file_path": str(pdf), "content_type": "application/pdf", "filename": "d.pdf"}]
        blocks = _build("summarize", att)
        assert blocks[0]["type"] == "document"
        assert blocks[0]["source"]["media_type"] == "application/pdf"
    finally:
        pdf.unlink()


def test_native_image_missing_file_skipped_not_crashed():
    att = [{"file_path": "/nope.png", "content_type": "image/png", "filename": "gone.png"}]
    blocks = _build("hello", att)
    assert blocks == [{"type": "text", "text": "hello"}]


# ---------------------------------------------------------------------------
# Cached extraction path — the core wiring
# ---------------------------------------------------------------------------


def test_pending_extraction_injects_wait_marker():
    """User sent message before background task finished → agent is told."""
    att = [{"file_id": "u1", "content_type": "text/plain", "filename": "notes.txt", "file_path": ""}]
    blocks = _build("extract metadata", att, AsyncMock(return_value=_ext(status="pending")))

    assert len(blocks) == 2
    marker = blocks[0]["text"]
    assert "notes.txt" in marker
    assert "still being processed" in marker
    assert "wait" in marker.lower()
    assert blocks[1]["text"] == "extract metadata"


def test_done_extraction_text_reaches_agent():
    """The payoff: extracted text is in the content blocks with a filename header."""
    att = [{"file_id": "u2", "content_type": "text/plain", "filename": "protocol.txt", "file_path": ""}]
    mock = AsyncMock(return_value=_ext(text="Subject 12345 underwent viral injection at 3pm."))
    blocks = _build("what subject?", att, mock)

    assert len(blocks) == 2
    content = blocks[0]["text"]
    assert content.startswith("[Attachment: protocol.txt]")
    assert "Subject 12345" in content
    assert "viral injection" in content


def test_done_with_images_emits_image_blocks_before_text():
    """Video: keyframes → image blocks, then transcript → text, in that order."""
    att = [{"file_id": "u3", "content_type": "video/mp4", "filename": "lab.mp4", "file_path": ""}]
    fa, fb = b"\x89PNG\r\n\x1a\nframeA", b"\x89PNG\r\n\x1a\nframeB"
    mock = AsyncMock(return_value=_ext(
        text="now injecting subject 555",
        images=[(fa, "Frame at 0.0s"), (fb, "Frame at 5.0s")],
    ))
    blocks = _build("describe", att, mock)

    # img, caption, img, caption, text, prompt
    assert len(blocks) == 6
    assert blocks[0]["type"] == "image"
    assert base64.standard_b64decode(blocks[0]["source"]["data"]) == fa
    assert blocks[1] == {"type": "text", "text": "[Frame at 0.0s]"}
    assert blocks[2]["type"] == "image"
    assert base64.standard_b64decode(blocks[2]["source"]["data"]) == fb
    assert blocks[3] == {"type": "text", "text": "[Frame at 5.0s]"}
    assert "555" in blocks[4]["text"]
    assert blocks[5]["text"] == "describe"


def test_error_extraction_injects_failure_marker():
    att = [{"file_id": "u4", "content_type": "audio/mpeg", "filename": "memo.mp3", "file_path": ""}]
    mock = AsyncMock(return_value=_ext(status="error", error="whisper-cli not found on PATH"))
    blocks = _build("transcribe", att, mock)

    assert "memo.mp3" in blocks[0]["text"]
    assert "extraction failed" in blocks[0]["text"]
    assert "whisper-cli" in blocks[0]["text"]


def test_partial_extraction_surfaces_error_alongside_content():
    """Video keyframes OK, transcript timed out → both in the prompt."""
    att = [{"file_id": "u5", "content_type": "video/mp4", "filename": "long.mp4", "file_path": ""}]
    mock = AsyncMock(return_value=_ext(
        text="partial transcript before timeout",
        error="Transcription timed out after 120s",
    ))
    blocks = _build("what happened?", att, mock)

    content = blocks[0]["text"]
    assert "partial transcript" in content
    assert "partial extraction" in content
    assert "timed out" in content


def test_done_but_empty_still_tells_agent():
    """Edge: extraction succeeded but produced nothing. Don't silently drop."""
    att = [{"file_id": "u6", "content_type": "text/plain", "filename": "empty.txt", "file_path": ""}]
    mock = AsyncMock(return_value=_ext(text="", images=[]))
    blocks = _build("anything?", att, mock)

    assert "empty.txt" in blocks[0]["text"]
    assert "produced no content" in blocks[0]["text"]


def test_non_native_without_file_id_skipped():
    """Malformed attachment — log + skip, don't crash."""
    att = [{"content_type": "text/plain", "filename": "oops.txt", "file_path": ""}]
    blocks = _build("hello", att)
    assert blocks == [{"type": "text", "text": "hello"}]


def test_extraction_row_missing_skipped():
    """Upload deleted between upload and chat — skip, don't crash."""
    att = [{"file_id": "gone", "content_type": "text/plain", "filename": "x.txt", "file_path": ""}]
    blocks = _build("hello", att, AsyncMock(return_value=None))
    assert blocks == [{"type": "text", "text": "hello"}]


def test_prompt_always_last():
    att = [{"file_id": "a", "content_type": "text/csv", "filename": "a.csv", "file_path": ""}]
    mock = AsyncMock(return_value=_ext(text="col1,col2"))
    blocks = _build("the final prompt", att, mock)
    assert blocks[-1] == {"type": "text", "text": "the final prompt"}


def test_multiple_attachments_preserve_order():
    atts = [
        {"file_id": "first", "content_type": "text/plain", "filename": "a.txt", "file_path": ""},
        {"file_id": "second", "content_type": "text/plain", "filename": "b.txt", "file_path": ""},
    ]

    async def fake_get(fid):
        return _ext(text=f"content from {fid}")

    blocks = _build("compare", atts, AsyncMock(side_effect=fake_get))
    assert len(blocks) == 3
    assert "content from first" in blocks[0]["text"]
    assert "content from second" in blocks[1]["text"]
    assert blocks[2]["text"] == "compare"


# ---------------------------------------------------------------------------
# _format_conversation_context: history markers (P1#3)
# ---------------------------------------------------------------------------


def test_history_marker_image():
    h = [{"role": "user", "content": "photo",
          "attachments_json": [{"content_type": "image/png", "filename": "scope.png"}]}]
    out = _format_conversation_context(h, "what did I show?")
    assert "[Attached image: scope.png]" in out


def test_history_marker_pdf():
    h = [{"role": "user", "content": "doc",
          "attachments_json": [{"content_type": "application/pdf", "filename": "p.pdf"}]}]
    out = _format_conversation_context(h, "follow up")
    assert "[Attached PDF: p.pdf]" in out


def test_history_marker_generic_covers_audio():
    """P1#3: without the else-branch, audio in history was invisible to follow-ups."""
    h = [{"role": "user", "content": "memo",
          "attachments_json": [{"content_type": "audio/mpeg", "filename": "memo.mp3"}]}]
    out = _format_conversation_context(h, "what did I say?")
    assert "[Attached audio/mpeg: memo.mp3]" in out


def test_history_marker_generic_covers_spreadsheet():
    h = [{"role": "user", "content": "data",
          "attachments_json": [{"content_type": "text/csv", "filename": "subjects.csv"}]}]
    out = _format_conversation_context(h, "rows?")
    assert "[Attached text/csv: subjects.csv]" in out


def test_history_marker_handles_missing_content_type():
    """Malformed history — fall back to 'file', don't crash."""
    h = [{"role": "user", "content": "x",
          "attachments_json": [{"filename": "mystery.bin"}]}]
    out = _format_conversation_context(h, "?")
    assert "[Attached file: mystery.bin]" in out


def test_history_no_attachments_no_markers():
    h = [{"role": "user", "content": "just text"}]
    out = _format_conversation_context(h, "follow up")
    assert "[Attached" not in out
    assert "USER: just text" in out
    assert "USER: follow up" in out


# ---------------------------------------------------------------------------
# DB round-trip: set_upload_extraction ↔ get_upload_extraction
# ---------------------------------------------------------------------------


def test_extraction_db_round_trip(tmp_path, monkeypatch):
    """What set_upload_extraction writes, get_upload_extraction reads back."""
    monkeypatch.setattr("agent.db.database.DB_PATH", str(tmp_path / "rt.db"))
    monkeypatch.setattr("agent.db.database._db_connection", None)

    from agent.db.database import init_db, close_db
    from agent.tools.metadata_store import (
        save_upload, set_upload_extraction, get_upload_extraction,
    )

    _run(init_db())
    try:
        upload = _run(save_upload(
            upload_id="rt", original_filename="lab.mp4", content_type="video/mp4",
            file_path="/tmp/fake", size_bytes=100, session_id=None,
        ))
        assert upload["id"] == "rt"

        frame = b"\x89PNG\r\n\x1a\nroundtrip"
        _run(set_upload_extraction(
            "rt", text="transcript", images=[(frame, "Frame at 2.5s")],
            meta={"duration_sec": 10.0, "keyframes": 1}, error=None,
        ))

        got = _run(get_upload_extraction("rt"))
        assert got["status"] == "done"
        assert got["text"] == "transcript"
        assert got["error"] is None
        assert got["meta"] == {"duration_sec": 10.0, "keyframes": 1}
        assert got["images"] == [(frame, "Frame at 2.5s")]  # bytes survive b64 encode/decode
    finally:
        _run(close_db())


def test_extraction_db_round_trip_error_status(tmp_path, monkeypatch):
    """Writing with error → status becomes 'error'."""
    monkeypatch.setattr("agent.db.database.DB_PATH", str(tmp_path / "rt_err.db"))
    monkeypatch.setattr("agent.db.database._db_connection", None)

    from agent.db.database import init_db, close_db
    from agent.tools.metadata_store import (
        save_upload, set_upload_extraction, get_upload_extraction,
    )

    _run(init_db())
    try:
        _run(save_upload(
            upload_id="rt-err", original_filename="x.mp3", content_type="audio/mpeg",
            file_path="/tmp/x", size_bytes=10, session_id=None,
        ))
        _run(set_upload_extraction(
            "rt-err", text="", images=[], meta={}, error="ffmpeg not found",
        ))
        got = _run(get_upload_extraction("rt-err"))
        assert got["status"] == "error"
        assert got["error"] == "ffmpeg not found"
    finally:
        _run(close_db())


# ---------------------------------------------------------------------------
# Options caching (TTFT fix) — _get_options should build once per model
# ---------------------------------------------------------------------------


def test_get_options_caches_per_model():
    """_get_options builds once per model key and returns the same object."""
    from agent.service import _get_options, _OPTIONS_CACHE, DEFAULT_MODEL

    _OPTIONS_CACHE.clear()  # isolate from other tests / prior runs
    try:
        a = _get_options(None)
        b = _get_options(None)
        assert a is b, "repeated calls with same model must return cached instance"
        assert DEFAULT_MODEL in _OPTIONS_CACHE

        # Unknown model falls back to DEFAULT_MODEL — same cache slot
        c = _get_options("claude-nonexistent-99")
        assert c is a, "unknown models fall back to default cache entry"
    finally:
        _OPTIONS_CACHE.clear()


def test_get_options_separate_entries_per_known_model():
    from agent.service import _get_options, _OPTIONS_CACHE, AVAILABLE_MODELS

    _OPTIONS_CACHE.clear()
    try:
        # Two distinct known models → two distinct cached instances
        m0, m1 = AVAILABLE_MODELS[0], AVAILABLE_MODELS[1]
        o0 = _get_options(m0)
        o1 = _get_options(m1)
        assert o0 is not o1
        assert len(_OPTIONS_CACHE) == 2
    finally:
        _OPTIONS_CACHE.clear()


# ---------------------------------------------------------------------------
# Extraction semaphore (folder upload backpressure)
# ---------------------------------------------------------------------------


def test_extraction_semaphore_limits_concurrency():
    """_extract_and_store must honor the module-level semaphore cap.

    We patch the extractor with a slow coroutine and schedule 6 tasks at
    once. At most 3 should be inside extract() at any moment.
    """
    from agent import server as srv

    max_concurrent = 0
    current = 0
    gate = asyncio.Event()

    async def fake_extract(path, content_type):
        nonlocal current, max_concurrent
        current += 1
        max_concurrent = max(max_concurrent, current)
        # Hold until the test releases us — guarantees all tasks are
        # queued before any completes, so the semaphore is the only
        # thing limiting observed concurrency.
        await gate.wait()
        current -= 1
        # Minimal shape for set_upload_extraction kwargs access
        class R:  # noqa: N801
            text = ""
            images: list = []
            meta: dict = {}
            error = None
        return R()

    # Throw away DB writes and use our fake extractor
    with patch("agent.tools.extractors.extract", new=fake_extract), \
         patch("agent.tools.metadata_store.set_upload_extraction", new=AsyncMock()):

        async def drive():
            tasks = [
                asyncio.create_task(srv._extract_and_store(f"id{i}", Path("/tmp/x"), "text/csv"))
                for i in range(6)
            ]
            # Let the first batch enter the semaphore
            await asyncio.sleep(0.01)
            assert max_concurrent <= 3, f"semaphore leaked: saw {max_concurrent} concurrent extractions"
            # Release everything and drain
            gate.set()
            await asyncio.gather(*tasks)

        _run(drive())

    assert max_concurrent == 3, f"expected exactly 3 concurrent (limit), got {max_concurrent}"
