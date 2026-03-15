"""whisper.cpp subprocess wrapper for audio/video transcription.

All process spawning uses asyncio.create_subprocess_exec with list-form
arguments (argv-style). No shell is ever invoked. Paths come from the
uploads/ directory via DB lookup (server.py), never raw user input.
"""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
MODEL_FILENAME = "ggml-base.en.bin"
# Legacy constant kept for short-clip paths; long-form uses _scaled_timeout.
TRANSCRIBE_TIMEOUT_SEC = 120
# whisper.cpp base.en runs ~4-8× realtime on CPU. 0.35× duration gives
# headroom above the worst case; floor keeps short clips from getting
# unreasonably tight budgets.
_WHISPER_TIMEOUT_FACTOR = 0.35
_WHISPER_TIMEOUT_FLOOR_SEC = 120
# One frame per ~3min of footage, bounded. 20 frames × ~500KB ≈ 10MB of
# image blocks — comfortably inside a single API request.
_KEYFRAME_MIN = 3
_KEYFRAME_MAX = 20
_KEYFRAME_SEC_PER_FRAME = 180


class TranscriptionUnavailable(RuntimeError):
    """Raised when a required binary or model file is missing."""
    pass


def find_binary(name: str) -> str | None:
    """Locate an executable on PATH. Returns the full path or None."""
    return shutil.which(name)


def _model_path() -> Path | None:
    """Return the whisper model file path if it exists on disk."""
    p = MODELS_DIR / MODEL_FILENAME
    return p if p.exists() else None


def check_availability() -> dict:
    """Report which transcription dependencies are available.

    Returns {"available": bool, "missing": [str]}.
    Checks: ffmpeg, whisper-cli, and the model file.
    """
    missing: list[str] = []
    if find_binary("ffmpeg") is None:
        missing.append("ffmpeg")
    if find_binary("whisper-cli") is None:
        missing.append("whisper-cli")
    if _model_path() is None:
        missing.append(f"model file ({MODELS_DIR / MODEL_FILENAME})")
    return {"available": len(missing) == 0, "missing": missing}


async def _run(argv: list[str], timeout: float) -> tuple[int, bytes, bytes]:
    """Spawn argv, wait with timeout.

    On asyncio.TimeoutError: kill the process, await its exit, then re-raise.
    Returns (returncode, stdout, stderr).
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, stdout, stderr


def _scaled_timeout(duration_sec: float, factor: float, floor: float) -> float:
    return max(floor, duration_sec * factor)


async def to_wav(src: Path, duration_hint: float = 0.0) -> Path:
    """Convert any audio/video source to 16kHz mono PCM WAV via ffmpeg.

    Returns path to a tempfile. Caller is responsible for deleting it on
    success. On any exception (including TimeoutError), the tempfile is
    unlinked before the exception is re-raised (staff review P1#4).

    duration_hint scales the timeout for long-form sources — ffmpeg has to
    decode+re-encode the full audio stream, so a 2hr video needs minutes,
    not the 60s that was fine for short clips.
    """
    ffmpeg = find_binary("ffmpeg")
    if ffmpeg is None:
        raise TranscriptionUnavailable("ffmpeg not found on PATH")

    fd, tmp_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    dst = Path(tmp_path)

    # ffmpeg audio transcode runs well above realtime; 0.05× duration is
    # generous. Floor at 60s for short clips.
    timeout = _scaled_timeout(duration_hint, factor=0.05, floor=60)

    argv = [
        ffmpeg, "-y",
        "-i", str(src),
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        "-f", "wav",
        str(dst),
    ]
    try:
        rc, _, stderr = await _run(argv, timeout=timeout)
    except Exception:
        dst.unlink(missing_ok=True)
        raise
    if rc != 0:
        dst.unlink(missing_ok=True)
        raise RuntimeError(
            f"ffmpeg failed ({rc}): {stderr.decode(errors='replace')[:500]}"
        )
    return dst


async def transcribe_wav(wav: Path, duration_hint: float = 0.0) -> str:
    """Run whisper-cli against a 16kHz WAV file and return the transcript.

    whisper-cli writes {out_base}.txt; we read, strip, unlink, and return.
    Timeout scales with duration_hint — base.en runs ~4-8× realtime on CPU.
    """
    whisper = find_binary("whisper-cli")
    if whisper is None:
        raise TranscriptionUnavailable("whisper-cli not found on PATH")
    model = _model_path()
    if model is None:
        raise TranscriptionUnavailable(
            f"whisper model not found at {MODELS_DIR / MODEL_FILENAME}"
        )

    fd, out_base_path = tempfile.mkstemp(suffix="")
    os.close(fd)
    out_base = Path(out_base_path)
    out_txt = out_base.with_suffix(".txt")

    timeout = _scaled_timeout(
        duration_hint, _WHISPER_TIMEOUT_FACTOR, _WHISPER_TIMEOUT_FLOOR_SEC,
    )

    argv = [
        whisper,
        "-m", str(model),
        "-f", str(wav),
        "-otxt",
        "-of", str(out_base),
        "-np",
    ]
    try:
        rc, _, stderr = await _run(argv, timeout=timeout)
        if rc != 0:
            raise RuntimeError(
                f"whisper-cli failed ({rc}): {stderr.decode(errors='replace')[:500]}"
            )
        text = out_txt.read_text(encoding="utf-8", errors="replace").strip()
        return text
    finally:
        out_base.unlink(missing_ok=True)
        out_txt.unlink(missing_ok=True)


async def transcribe(src: Path) -> str:
    """Convert source to WAV, transcribe it, clean up the WAV tempfile."""
    duration = await _probe_duration(src)
    wav = await to_wav(src, duration_hint=duration)
    try:
        return await transcribe_wav(wav, duration_hint=duration)
    finally:
        wav.unlink(missing_ok=True)


async def _probe_duration(src: Path) -> float:
    """Get media duration in seconds via ffprobe. Returns 0.0 on any failure."""
    ffprobe = find_binary("ffprobe")
    if ffprobe is None:
        return 0.0
    argv = [
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(src),
    ]
    try:
        rc, stdout, _ = await _run(argv, timeout=30)
        if rc != 0:
            return 0.0
        return float(stdout.decode(errors="replace").strip())
    except Exception:
        return 0.0


async def extract_keyframes(src: Path, count: int | None = None) -> list[tuple[bytes, str]]:
    """Pull evenly-spaced PNG frames from a video.

    count=None auto-scales with duration (~1 frame per 3min, bounded 3..20).
    Timestamps are clamped to [0, duration - 0.1]. Uses -ss before -i for
    fast seeking — per-frame cost is constant regardless of file size.
    Frames are scaled to 1024px wide (aspect preserved).

    Returns [(png_bytes, "Frame at {ts}s"), ...].
    """
    ffmpeg = find_binary("ffmpeg")
    if ffmpeg is None:
        raise TranscriptionUnavailable("ffmpeg not found on PATH")

    duration = await _probe_duration(src)

    if count is None:
        if duration > 0:
            count = max(_KEYFRAME_MIN, min(_KEYFRAME_MAX, int(duration // _KEYFRAME_SEC_PER_FRAME)))
        else:
            count = _KEYFRAME_MIN

    if duration <= 0:
        # No usable duration — just grab the first frame.
        timestamps = [0.0]
    else:
        upper = max(0.0, duration - 0.1)
        if count <= 1:
            timestamps = [upper / 2]
        else:
            step = upper / (count - 1)
            timestamps = [min(upper, max(0.0, i * step)) for i in range(count)]

    frames: list[tuple[bytes, str]] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for i, ts in enumerate(timestamps):
            out_png = Path(tmpdir) / f"frame_{i}.png"
            argv = [
                ffmpeg, "-y",
                "-ss", f"{ts:.2f}",
                "-i", str(src),
                "-frames:v", "1",
                "-vf", "scale=1024:-1",
                str(out_png),
            ]
            try:
                rc, _, _ = await _run(argv, timeout=30)
            except Exception:
                continue
            if rc != 0 or not out_png.exists():
                continue
            # scale=1024:-1 should keep frames under ~500KB, but skip pathological
            # outliers so a single frame can't balloon the extraction payload.
            if out_png.stat().st_size > 5_000_000:
                continue
            frames.append((out_png.read_bytes(), f"Frame at {ts:.1f}s"))

    return frames
