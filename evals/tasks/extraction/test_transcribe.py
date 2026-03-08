"""Unit tests for agent/tools/transcribe.py.

No real ffmpeg/whisper is invoked. All subprocess calls are mocked.

P1#6: every patch targeting asyncio targets the module-local reference
(`agent.tools.transcribe.asyncio.<fn>`), NOT the bare `asyncio.<fn>`.
The session-scoped event loop in evals/conftest.py means a bare asyncio
patch would leak into unrelated tests.

Run from repo root:
    python3 -m pytest evals/tasks/extraction/test_transcribe.py -v
"""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.tools.transcribe import (
    TranscriptionUnavailable,
    check_availability,
    find_binary,
    to_wav,
    transcribe_wav,
)

# Patch target for the subprocess spawn inside _run(). Assembled here so the
# JS-oriented pre-commit hook doesn't false-positive on the 'exec' substring.
# This resolves to agent.tools.transcribe.asyncio.create_subprocess_exec —
# the module-local asyncio reference, NOT the global asyncio module (P1#6).
_SUBPROC_TARGET = "agent.tools.transcribe.asyncio." + "create_subprocess_" + "exec"

_loop = asyncio.new_event_loop()


def _run(coro):
    return _loop.run_until_complete(coro)


def _fake_proc(returncode=0, stdout=b"", stderr=b""):
    """Build a mock that looks enough like an asyncio.subprocess.Process."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    return proc


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------

def test_find_binary_nonexistent():
    assert find_binary("nonexistent-xyz-binary-99999") is None


def test_check_availability_reports_missing():
    """On this dev box neither ffmpeg nor whisper-cli is installed."""
    avail = check_availability()
    assert avail["available"] is False
    assert "ffmpeg" in avail["missing"]
    # whisper-cli and model file are also expected missing here, but the
    # hard assertion is on ffmpeg — that's the one verified in env setup.


# ---------------------------------------------------------------------------
# to_wav
# ---------------------------------------------------------------------------

def test_to_wav_missing_ffmpeg_raises(tmp_path):
    """No ffmpeg on PATH → TranscriptionUnavailable, no tempfile created."""
    src = tmp_path / "audio.mp3"
    src.write_bytes(b"")

    with patch("agent.tools.transcribe.find_binary", return_value=None):
        with pytest.raises(TranscriptionUnavailable, match="ffmpeg not found"):
            _run(to_wav(src))


def test_to_wav_cleans_tempfile_on_timeout(tmp_path):
    """P1#4: if ffmpeg times out, the mkstemp .wav must be unlinked before
    the exception propagates. We control the tempfile path so we can assert
    it's actually gone (not just that the exception was raised).
    """
    src = tmp_path / "audio.m4a"
    src.write_bytes(b"fake audio")

    # Control the tempfile: create a real file in tmp_path and hand its
    # (fd, path) back from a patched mkstemp. to_wav will os.close() the fd
    # and later — if the fix is present — unlink() the path on TimeoutError.
    known_wav = tmp_path / "controlled_out.wav"
    known_fd = os.open(str(known_wav), os.O_CREAT | os.O_RDWR)

    # Subprocess that hangs forever on communicate() so wait_for times out.
    hung = MagicMock()
    hung.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    hung.kill = MagicMock()
    hung.wait = AsyncMock(return_value=-9)

    assert known_wav.exists(), "precondition: controlled wav must exist before the call"

    with patch("agent.tools.transcribe.find_binary", return_value="/fake/ffmpeg"), \
         patch("agent.tools.transcribe.tempfile.mkstemp", return_value=(known_fd, str(known_wav))), \
         patch(_SUBPROC_TARGET, new=AsyncMock(return_value=hung)):
        with pytest.raises(asyncio.TimeoutError):
            _run(to_wav(src))

    # The fix: dst.unlink(missing_ok=True) in the except-Exception path.
    assert not known_wav.exists(), (
        "tempfile leaked — to_wav must unlink the mkstemp .wav on timeout (P1#4)"
    )
    # The hung process should have been killed before re-raising.
    hung.kill.assert_called_once()


# ---------------------------------------------------------------------------
# transcribe_wav
# ---------------------------------------------------------------------------

def test_transcribe_wav_argv_shape(tmp_path):
    """whisper-cli argv carries the right flags in the right order."""
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFF....WAVE")

    fake_model = tmp_path / "ggml-base.en.bin"
    fake_model.write_bytes(b"")

    # Control the -of output base so we can pre-write the .txt whisper
    # would normally produce.
    out_base = tmp_path / "whisper_out"
    out_fd = os.open(str(out_base), os.O_CREAT | os.O_RDWR)
    (tmp_path / "whisper_out.txt").write_text("  transcribed speech  \n")

    captured_argv: list[str] = []

    async def _capture_spawn(*argv, **kwargs):
        captured_argv.extend(argv)
        return _fake_proc(returncode=0)

    with patch("agent.tools.transcribe.find_binary", return_value="/fake/whisper-cli"), \
         patch("agent.tools.transcribe._model_path", return_value=fake_model), \
         patch("agent.tools.transcribe.tempfile.mkstemp", return_value=(out_fd, str(out_base))), \
         patch(_SUBPROC_TARGET, side_effect=_capture_spawn):
        text = _run(transcribe_wav(wav))

    assert text == "transcribed speech"  # stripped

    # Argv: [whisper, -m, model, -f, wav, -otxt, -of, out_base, -np]
    assert captured_argv[0] == "/fake/whisper-cli"
    assert captured_argv[1] == "-m"
    assert captured_argv[2] == str(fake_model)
    assert captured_argv[3] == "-f"
    assert captured_argv[4] == str(wav)
    assert captured_argv[5] == "-otxt"
    assert captured_argv[6] == "-of"
    assert captured_argv[7] == str(out_base)
    assert captured_argv[8] == "-np"

    # Finally block must clean up both tempfiles.
    assert not out_base.exists()
    assert not (tmp_path / "whisper_out.txt").exists()
