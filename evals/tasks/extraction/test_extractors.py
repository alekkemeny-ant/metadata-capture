"""Unit tests for agent/tools/extractors.py.

Covers the extraction registry dispatch, each extractor's happy path,
truncation limits, and the audio/video paths via mocked subprocess calls.
No real ffmpeg/whisper is invoked.

Run from repo root:
    python3 -m pytest evals/tasks/extraction/test_extractors.py -v
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent.tools.extractors import (
    ExtractedContent,
    extract,
    EXTRACTORS,
    EXT_EXTRACTORS,
    NATIVE_TYPES,
    TEXT_TRUNCATE_CHARS,
    SHEET_ROW_LIMIT,
)

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"

# A single event loop for this module's tests. Matches the pattern in
# evals/tasks/end_to_end/test_new_features.py — sync test functions driving
# coroutines through run_until_complete keeps things independent of the
# session-scoped conftest loop.
_loop = asyncio.new_event_loop()


def _run(coro):
    return _loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# Dataclass + registry sanity
# ---------------------------------------------------------------------------

def test_extracted_content_defaults():
    """Default fields on ExtractedContent: empty images/meta, no error."""
    ec = ExtractedContent(text="hello")
    assert ec.text == "hello"
    assert ec.images == []
    assert ec.meta == {}
    assert ec.error is None


def test_native_types_are_image_and_pdf():
    """NATIVE_TYPES should only contain formats Claude ingests directly."""
    assert len(NATIVE_TYPES) > 0
    for t in NATIVE_TYPES:
        assert t.startswith("image/") or t == "application/pdf", (
            f"{t!r} is in NATIVE_TYPES but is neither image/* nor PDF"
        )
    # Native types must never also be extractor-handled — that would
    # mean we extract something Claude can already read natively.
    assert NATIVE_TYPES.isdisjoint(EXTRACTORS.keys())


def test_extract_unknown_type_returns_error(tmp_path):
    """Unknown MIME + unknown extension → error result, no raise."""
    f = tmp_path / "mystery.xyz"
    f.write_text("data")
    result = _run(extract(f, "application/x-nonexistent"))
    assert result.error is not None
    assert "Unsupported type" in result.error
    assert result.text == ""
    assert result.images == []


# ---------------------------------------------------------------------------
# Text extractor
# ---------------------------------------------------------------------------

def test_extract_text_plain():
    """text/plain reads the file and surfaces expected content."""
    result = _run(extract(FIXTURES / "sample.txt", "text/plain"))
    assert result.error is None
    assert "12345" in result.text
    assert "viral injection" in result.text
    assert result.meta["truncated"] is False
    assert result.meta["chars"] == len(result.text)


def test_extract_text_markdown_via_extension_fallback():
    """Generic content-type falls back to .md extension handler."""
    # application/octet-stream is NOT in EXTRACTORS — dispatch must fall
    # through to the .md entry in EXT_EXTRACTORS.
    assert "application/octet-stream" not in EXTRACTORS
    assert ".md" in EXT_EXTRACTORS
    result = _run(extract(FIXTURES / "sample.md", "application/octet-stream"))
    assert result.error is None
    assert "67890" in result.text
    assert "Protocol" in result.text


def test_extract_text_truncation(tmp_path):
    """Files larger than TEXT_TRUNCATE_CHARS are capped with a marker."""
    big = tmp_path / "big.txt"
    big.write_text("A" * 60_000)
    result = _run(extract(big, "text/plain"))
    assert result.error is None
    # Truncated body + marker should be well under the original 60k
    assert len(result.text) < 51_000
    assert "truncated" in result.text
    assert result.meta["truncated"] is True
    assert result.meta["original_chars"] == 60_000
    # The marker reports exactly how much was cut.
    remaining = 60_000 - TEXT_TRUNCATE_CHARS
    assert str(remaining) in result.text


# ---------------------------------------------------------------------------
# Spreadsheet extractor
# ---------------------------------------------------------------------------

def test_extract_spreadsheet_csv():
    """CSV renders as markdown table; meta carries row count."""
    result = _run(extract(FIXTURES / "sample.csv", "text/csv"))
    assert result.error is None
    # Header row
    assert "subject_id" in result.text
    assert "procedure" in result.text
    # Data rows
    assert "12345" in result.text
    assert "injection" in result.text
    assert "67890" in result.text
    assert "surgery" in result.text
    # Meta: 2 data rows (header excluded)
    assert result.meta["total_rows"] == 2
    assert result.meta["columns"] == ["subject_id", "procedure", "date"]


def test_extract_spreadsheet_truncates_rows(tmp_path):
    """Rows beyond SHEET_ROW_LIMIT are omitted with a 'more rows' marker."""
    csv = tmp_path / "wide.csv"
    lines = ["id,value"]
    for i in range(200):
        lines.append(f"{i},row_value_{i}")
    csv.write_text("\n".join(lines) + "\n")

    result = _run(extract(csv, "text/csv"))
    assert result.error is None
    assert result.meta["total_rows"] == 200
    # First rows present
    assert "row_value_0" in result.text
    assert f"row_value_{SHEET_ROW_LIMIT - 1}" in result.text
    # Row past the limit is gone
    assert "row_value_150" not in result.text
    # Truncation marker reports the remainder
    assert "more rows" in result.text
    assert str(200 - SHEET_ROW_LIMIT) in result.text


# ---------------------------------------------------------------------------
# DOCX extractor
# ---------------------------------------------------------------------------

def test_extract_docx():
    """python-docx pulls paragraph text and counts paragraphs."""
    result = _run(extract(
        FIXTURES / "sample.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ))
    assert result.error is None
    assert "99999" in result.text
    assert "confocal" in result.text
    assert "ABC-123" in result.text
    assert result.meta["paragraphs"] == 2


# ---------------------------------------------------------------------------
# Audio extractor (mocked)
# ---------------------------------------------------------------------------

def test_extract_audio_mocked(tmp_path):
    """Transcribe result flows through to ExtractedContent.text."""
    audio = tmp_path / "fake.mp3"
    audio.write_bytes(b"not real mp3 data")

    fake_transcript = "The mouse was given water ad libitum."
    # extract_audio does `from . import transcribe as T; await T.transcribe(...)`
    # — T is the agent.tools.transcribe module object, so patching the
    # function attribute on that module intercepts the call.
    with patch("agent.tools.transcribe.transcribe", new=AsyncMock(return_value=fake_transcript)):
        result = _run(extract(audio, "audio/mpeg"))

    assert result.error is None
    assert result.text == fake_transcript
    assert result.meta == {"transcribed": True}


def test_extract_audio_unavailable(tmp_path):
    """TranscriptionUnavailable is caught and folded into .error."""
    from agent.tools.transcribe import TranscriptionUnavailable

    audio = tmp_path / "fake.wav"
    audio.write_bytes(b"")

    msg = "ffmpeg not found on PATH"
    with patch(
        "agent.tools.transcribe.transcribe",
        new=AsyncMock(side_effect=TranscriptionUnavailable(msg)),
    ):
        result = _run(extract(audio, "audio/wav"))

    assert result.text == ""
    assert result.error == msg
    assert result.images == []


# ---------------------------------------------------------------------------
# Video extractor (mocked)
# ---------------------------------------------------------------------------

def test_extract_video_mocked(tmp_path):
    """Both transcript and keyframes succeed → text + images populated."""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"")

    fake_transcript = "This clip shows the rig in operation."
    fake_frames = [
        (b"\x89PNG frame 0", "Frame at 0.0s"),
        (b"\x89PNG frame 1", "Frame at 5.0s"),
        (b"\x89PNG frame 2", "Frame at 10.0s"),
    ]
    with patch("agent.tools.transcribe.transcribe", new=AsyncMock(return_value=fake_transcript)), \
         patch("agent.tools.transcribe.extract_keyframes", new=AsyncMock(return_value=fake_frames)):
        result = _run(extract(video, "video/mp4"))

    assert result.error is None
    assert result.text == fake_transcript
    assert result.images == fake_frames
    assert result.meta["transcribed"] is True
    assert result.meta["keyframes"] == 3


def test_extract_video_partial_success(tmp_path):
    """Transcription fails but keyframes succeed → images populated, error set, text empty."""
    video = tmp_path / "fake.mov"
    video.write_bytes(b"")

    fake_frames = [(b"\x89PNG only frame", "Frame at 2.5s")]
    with patch(
        "agent.tools.transcribe.transcribe",
        new=AsyncMock(side_effect=RuntimeError("whisper crashed")),
    ), patch(
        "agent.tools.transcribe.extract_keyframes",
        new=AsyncMock(return_value=fake_frames),
    ):
        result = _run(extract(video, "video/quicktime"))

    # Partial success: keyframes made it through.
    assert result.images == fake_frames
    assert result.text == ""
    assert result.error is not None
    assert "Transcription failed" in result.error
    assert "whisper crashed" in result.error
    assert result.meta["transcribed"] is False
    assert result.meta["keyframes"] == 1


def test_extract_video_reraises_keyboard_interrupt(tmp_path):
    """cb5a470: KeyboardInterrupt must propagate through extract(), not be
    swallowed by the blanket except-Exception or stringified into .error.

    gather(return_exceptions=True) may either return it as a value (older
    Pythons) or propagate it directly (3.11+) — either way extract_video's
    BaseException re-raise loop and extract()'s except-Exception guarantee
    it reaches the caller.
    """
    video = tmp_path / "fake.webm"
    video.write_bytes(b"")

    # Yield once before raising so the sibling _frames() task gets a loop
    # tick to run to completion. Without this, KeyboardInterrupt tears out
    # of the loop before _frames is ever stepped, leaving an unawaited
    # coroutine that warns at GC time.
    async def _interrupt_after_yield(*args, **kwargs):
        await asyncio.sleep(0)
        raise KeyboardInterrupt

    # Use a throwaway loop — not the module-level _loop. KeyboardInterrupt
    # tearing through run_until_complete can leave callbacks queued; if we
    # later re-enter the same loop, those callbacks re-deliver the interrupt
    # into pytest. A fresh loop that we close immediately contains the
    # blast radius.
    local = asyncio.new_event_loop()
    local.set_exception_handler(lambda loop, context: None)
    try:
        with patch(
            "agent.tools.transcribe.transcribe",
            new=_interrupt_after_yield,
        ), patch(
            "agent.tools.transcribe.extract_keyframes",
            new=AsyncMock(return_value=[]),
        ):
            with pytest.raises(KeyboardInterrupt):
                local.run_until_complete(extract(video, "video/webm"))
    finally:
        for t in asyncio.all_tasks(local):
            if t.done() and not t.cancelled():
                t.exception()  # mark retrieved → no "exception never retrieved" at GC
        local.close()
