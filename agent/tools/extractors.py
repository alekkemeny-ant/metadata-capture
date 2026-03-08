"""File content extraction registry.

Dispatch uploaded files to type-specific extractors. Heavy dependencies
(openpyxl, python-docx, transcribe) are lazy-imported inside each
extractor's function body to keep module cold-start fast (staff review
P1#5).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

# spreadsheet.py is stdlib-only at module level (openpyxl is lazy inside
# _parse_xlsx), so this import is cheap.
from .spreadsheet import SPREADSHEET_CONTENT_TYPES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEXT_TRUNCATE_CHARS = 50_000
SHEET_ROW_LIMIT = 100

# MIME types the Claude API handles natively — callers should skip
# extraction for these and send the raw file bytes instead.
NATIVE_TYPES = frozenset({
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "application/pdf",
})


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ExtractedContent:
    text: str
    images: list[tuple[bytes, str]] = field(default_factory=list)  # (png_bytes, caption)
    meta: dict = field(default_factory=dict)
    error: str | None = None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

Extractor = Callable[[Path, str], Awaitable[ExtractedContent]]

EXTRACTORS: dict[str, Extractor] = {}       # MIME type -> extractor
EXT_EXTRACTORS: dict[str, Extractor] = {}   # .ext -> extractor (fallback)


def register(*mimes: str, exts: tuple[str, ...] = ()):
    """Decorator: register an extractor for the given MIME types and/or extensions.

    Extensions are the fallback path for application/octet-stream and other
    generic content types.
    """
    def _wrap(fn: Extractor) -> Extractor:
        for m in mimes:
            EXTRACTORS[m] = fn
        for e in exts:
            EXT_EXTRACTORS[e.lower()] = fn
        return fn
    return _wrap


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

async def extract(path: Path, content_type: str) -> ExtractedContent:
    """Route a file to its extractor.

    MIME type takes precedence; file extension is the fallback (handles
    application/octet-stream and other generic upload types). Unknown types
    return an error result. Extractor exceptions are caught, logged, and
    folded into the result's error field — this function never raises.
    """
    fn = EXTRACTORS.get(content_type)
    if fn is None:
        fn = EXT_EXTRACTORS.get(path.suffix.lower())
    if fn is None:
        return ExtractedContent(
            text="",
            error=f"Unsupported type: {content_type} ({path.suffix or 'no extension'})",
        )
    try:
        return await fn(path, content_type)
    except Exception as e:
        logger.exception("Extractor %s failed for %s", fn.__name__, path)
        return ExtractedContent(
            text="",
            error=f"{fn.__name__} failed: {type(e).__name__}: {e}",
        )


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------

@register(
    "text/plain", "text/markdown", "application/json", "application/x-yaml",
    exts=(".txt", ".md", ".json", ".yaml", ".yml", ".py", ".log"),
)
async def extract_text(path: Path, content_type: str) -> ExtractedContent:
    """Read plain text, truncating at TEXT_TRUNCATE_CHARS."""
    raw = path.read_text(errors="replace")
    original = len(raw)
    truncated = original > TEXT_TRUNCATE_CHARS
    if truncated:
        remaining = original - TEXT_TRUNCATE_CHARS
        text = raw[:TEXT_TRUNCATE_CHARS] + f"\n[... {remaining} more chars truncated]"
    else:
        text = raw
    return ExtractedContent(
        text=text,
        meta={"chars": len(text), "truncated": truncated, "original_chars": original},
    )


@register(*SPREADSHEET_CONTENT_TYPES, exts=(".csv", ".xlsx", ".xls"))
async def extract_spreadsheet(path: Path, content_type: str) -> ExtractedContent:
    """Parse CSV/XLSX via spreadsheet.parse_spreadsheet and render as markdown."""
    from .spreadsheet import parse_spreadsheet  # lazy: triggers openpyxl load for xlsx

    parsed = parse_spreadsheet(path, content_type)
    columns: list[str] = parsed.get("columns", [])
    rows: list[list[str]] = parsed.get("rows", [])
    total_rows: int = parsed.get("total_rows", len(rows))
    sheet_name = parsed.get("sheet_name")

    lines: list[str] = []
    if columns:
        lines.append("| " + " | ".join(str(c) for c in columns) + " |")
        lines.append("| " + " | ".join("---" for _ in columns) + " |")
    shown = rows[:SHEET_ROW_LIMIT]
    for r in shown:
        safe = [str(c).replace("|", "\\|").replace("\n", " ") for c in r]
        lines.append("| " + " | ".join(safe) + " |")

    if total_rows > len(shown):
        remaining = total_rows - len(shown)
        lines.append(
            f"\n[... {remaining} more rows — ask to query specific ranges]"
        )

    return ExtractedContent(
        text="\n".join(lines),
        meta={"total_rows": total_rows, "columns": columns, "sheet_name": sheet_name},
    )


@register(
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    exts=(".docx",),
)
async def extract_docx(path: Path, content_type: str) -> ExtractedContent:
    """Pull paragraph text from a .docx via python-docx."""
    from docx import Document  # lazy: python-docx is a heavy import

    doc = Document(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    text = "\n\n".join(paragraphs)
    original = len(text)
    if original > TEXT_TRUNCATE_CHARS:
        remaining = original - TEXT_TRUNCATE_CHARS
        text = text[:TEXT_TRUNCATE_CHARS] + f"\n[... {remaining} more chars truncated]"
    return ExtractedContent(text=text, meta={"paragraphs": len(paragraphs)})


@register(
    "audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/ogg",
    exts=(".mp3", ".wav", ".m4a", ".ogg"),
)
async def extract_audio(path: Path, content_type: str) -> ExtractedContent:
    """Transcribe audio via whisper.cpp."""
    from . import transcribe as T  # lazy: avoid subprocess machinery at import

    try:
        text = await T.transcribe(path)
    except T.TranscriptionUnavailable as e:
        return ExtractedContent(text="", error=str(e))
    except asyncio.TimeoutError:
        return ExtractedContent(
            text="",
            error=f"Transcription timed out after {T.TRANSCRIBE_TIMEOUT_SEC}s",
        )
    return ExtractedContent(text=text, meta={"transcribed": True})


@register(
    "video/mp4", "video/quicktime", "video/webm", "video/x-matroska",
    exts=(".mp4", ".mov", ".webm", ".mkv"),
)
async def extract_video(path: Path, content_type: str) -> ExtractedContent:
    """Transcribe audio track + pull keyframes. Partial success is OK."""
    from . import transcribe as T  # lazy

    async def _transcribe() -> str:
        return await T.transcribe(path)

    async def _frames() -> list[tuple[bytes, str]]:
        return await T.extract_keyframes(path, count=3)

    transcript_r, frames_r = await asyncio.gather(
        _transcribe(), _frames(), return_exceptions=True
    )

    text = ""
    images: list[tuple[bytes, str]] = []
    errors: list[str] = []

    # gather(return_exceptions=True) surfaces BaseException subclasses as values.
    # Re-raise BaseException-but-not-Exception (KeyboardInterrupt, SystemExit,
    # GeneratorExit) — those must propagate, not be stringified into .error.
    for r in (transcript_r, frames_r):
        if isinstance(r, BaseException) and not isinstance(r, Exception):
            raise r

    if isinstance(transcript_r, Exception):
        if isinstance(transcript_r, asyncio.TimeoutError):
            errors.append(f"Transcription timed out after {T.TRANSCRIBE_TIMEOUT_SEC}s")
        else:
            errors.append(f"Transcription failed: {transcript_r}")
    else:
        text = transcript_r

    if isinstance(frames_r, Exception):
        errors.append(f"Keyframe extraction failed: {frames_r}")
    else:
        images = frames_r

    transcribed = not isinstance(transcript_r, Exception)
    n_frames = len(images)

    # Both failed → error-only result.
    if not transcribed and n_frames == 0:
        return ExtractedContent(
            text="",
            error="; ".join(errors) if errors else "Video extraction produced no content",
            meta={"transcribed": False, "keyframes": 0},
        )

    # Partial success: whichever half failed goes in .error.
    return ExtractedContent(
        text=text,
        images=images,
        meta={"transcribed": transcribed, "keyframes": n_frames},
        error="; ".join(errors) if errors else None,
    )
