# Multimodal Upload Expansion — Design

**Date:** 2026-03-06
**Branch:** `multimodal-feature`
**Author:** alekkemeny

## Goal

Enable scientists to upload and extract metadata from: plain text, markdown, JSON/YAML, spreadsheets (CSV/XLSX/XLS), Word documents (DOCX), audio (MP3/WAV/M4A/OGG), and video (MP4/MOV/WebM/MKV) — in addition to the existing images + PDFs.

## Current state

| Layer | Supports |
|---|---|
| `server.py ALLOWED_CONTENT_TYPES` | `image/{png,jpeg,gif,webp}`, `application/pdf` |
| `service.py _build_multimodal_content()` | `image/*` → Claude `image` block; `application/pdf` → `document` block; everything else silently dropped |
| `ChatPanel.tsx <input accept=>` | same 5 MIME types |

Claude API natively supports **only** images and PDFs as content blocks. No native audio/video. `text/plain` can go in a `document` block but docx/xlsx/csv cannot. Everything non-native requires server-side extraction to text.

## Architecture

Single extraction pipeline in a new `agent/tools/extractors.py`. A registry maps `content_type → extractor_fn`. Each extractor returns a common shape; the result is injected into `_build_multimodal_content()` as text + optional image blocks.

```
upload → uploads/ dir → [extractor registry] → ExtractedContent → content blocks → Claude
                              │
                   ┌──────────┼──────────┬──────────┬──────────┐
                 text     spreadsheet   docx     audio     video
                (read)   (openpyxl)  (py-docx) (ffmpeg→  (ffmpeg→
                                               whisper)  whisper + frames)
```

**Extraction timing:** at chat-time (when the attachment is referenced in a message), not upload-time. Upload stays fast; extraction cost is paid only when the file is actually used.

### ExtractedContent

```python
@dataclass
class ExtractedContent:
    text: str                        # primary extracted text (transcript, table, paragraphs)
    images: list[tuple[bytes, str]]  # derived images: (png_bytes, caption) — e.g. video keyframes
    meta: dict                       # duration, row_count, sheet_name, etc. for UI display
    error: str | None = None         # if extraction partially failed
```

## Extractor registry

| Types | Extractor | Output | Deps |
|---|---|---|---|
| `text/plain`, `text/markdown`, `application/json`, `.txt`, `.md`, `.json`, `.yaml`, `.py` | `extract_text` | raw text, truncated at 50k chars with `[... N more chars truncated]` marker | none |
| `text/csv`, `application/vnd.ms-excel`, `...spreadsheetml.sheet`, `.csv`, `.xlsx`, `.xls` | `extract_spreadsheet` — **cherry-picked from `7787e21:agent/tools/spreadsheet.py`** | markdown table (first 100 rows) + `meta.total_rows`, `meta.columns` | `openpyxl` |
| `application/vnd...wordprocessingml.document`, `.docx` | `extract_docx` | paragraphs joined with `\n\n`. Embedded images skipped (YAGNI). | `python-docx` |
| `audio/mpeg`, `audio/wav`, `audio/mp4`, `audio/ogg`, `.mp3`, `.wav`, `.m4a`, `.ogg` | `extract_audio` | transcript + `meta.duration_sec` | `ffmpeg` (PATH), `whisper-cli` (PATH) |
| `video/mp4`, `video/quicktime`, `video/webm`, `.mp4`, `.mov`, `.webm`, `.mkv` | `extract_video` | transcript + 3 keyframes (t=0%, 50%, 100%) as `images` + `meta.duration_sec` | `ffmpeg` (PATH), `whisper-cli` (PATH) |

Registry dispatch tries MIME type first, falls back to file extension (browsers often send `application/octet-stream` for uncommon types).

## whisper.cpp integration

ffmpeg is a transcoder, not a recognizer. It normalizes arbitrary audio into the 16kHz mono PCM WAV that whisper.cpp's log-mel spectrogram frontend requires. Pipeline:

```
{audio|video} ─ffmpeg→ 16kHz mono WAV ─whisper-cli→ transcript.txt
```

- **Binary:** expect `whisper-cli` on `$PATH` (ggerganov/whisper.cpp `main` executable, symlinked). Replit: add `pkgs.whisper-cpp` + `pkgs.ffmpeg` to `replit.nix`. Local dev: `brew install whisper-cpp ffmpeg` or build from source.
- **Model:** `ggml-base.en.bin` (~140MB). Downloaded on first use to `agent/models/` (`.gitignore`'d). FastAPI `lifespan()` logs a warning on startup if missing but doesn't fail — audio uploads will 503 instead.
- **Invocation:** `asyncio.create_subprocess_exec` so the SSE stream doesn't block.
  - ffmpeg: `ffmpeg -i {input} -ar 16000 -ac 1 -c:a pcm_s16le -f wav {tmp.wav}`
  - whisper: `whisper-cli -m {model} -f {tmp.wav} -otxt -of {tmp_basename}` → read `{tmp_basename}.txt`
- **Timeout:** 120s hard cap on transcription. Longer → return partial with error marker.

## Video keyframes

```
ffmpeg -i {input} -vf "select='eq(n,0)+eq(n,{mid})+eq(n,{last})'" -vsync 0 {tmp}_%d.png
```

Frame indices computed from `ffprobe`-reported duration × fps. Keyframes go into `ExtractedContent.images` with captions `"Frame at 0s"`, `"Frame at {mid}s"`, `"Frame at {end}s"`. `_build_multimodal_content()` emits them as Claude `image` blocks preceding the transcript text.

## Server changes (`server.py`)

- Expand `ALLOWED_CONTENT_TYPES` — **single definition**, avoid the double-def bug present in `7787e21`.
- Extension fallback: if MIME is `application/octet-stream` or missing, re-derive from filename suffix.
- Bump `MAX_UPLOAD_SIZE` to 100MB (video). Per-type caps enforced in extractors (e.g. spreadsheet still capped at 20MB).
- `/health` extended: `{"status": "ok", "transcription": "available" | "unavailable: whisper-cli not found"}`.

## Service changes (`service.py`)

`_build_multimodal_content()` gets one new branch:

```python
elif content_type not in NATIVE_TYPES:  # not image/*, not pdf
    extracted = await extract(file_path, content_type)
    if extracted.error:
        content_blocks.append({"type": "text", "text": f"[Attachment {filename}: {extracted.error}]"})
    else:
        for (img_bytes, caption) in extracted.images:
            content_blocks.append({"type": "image", "source": {...base64...}})
            content_blocks.append({"type": "text", "text": caption})
        content_blocks.append({"type": "text", "text": f"[Attachment {filename}]\n{extracted.text}"})
```

## Frontend changes (`ChatPanel.tsx`, `api.ts`)

- Expand `<input accept=...>` to the full MIME + extension list.
- Preview strip: show type-specific icon instead of thumbnail for non-images (🎵 audio, 🎬 video, 📊 sheet, 📄 doc, 📝 text).
- During SSE wait, if attachment is audio/video and no tokens have streamed yet for >3s, show "transcribing…" in the assistant message placeholder.
- `SpreadsheetViewer.tsx` + `ArtifactModal.tsx` cherry-picked from `7787e21` unchanged.

## System prompt (`system_prompt.py`)

New guidance block:

> When given a video, you receive keyframes (visual snapshots) + a transcript of the spoken audio. Correlate what's said with what's visible. When given audio, you receive only the transcript. Extract subject IDs, instrument IDs, procedure steps, timestamps, and any spoken metadata. For spreadsheets, the first 100 rows are shown — ask if you need the full data.

## Error handling

| Failure | Behavior |
|---|---|
| Extractor returns `None` / exception | `[Attachment {name} could not be processed: {reason}]` injected as text — agent knows something was attached |
| Transcription timeout (>120s) | `[Transcription timed out — file may be too long. Partial transcript: ...]` |
| `whisper-cli` or `ffmpeg` not on PATH | `/upload` returns 503 for audio/video types. Other types unaffected. |
| Spreadsheet >10k rows | Extract first 100, inject `[... {N} more rows — ask to query specific ranges]` |
| Text file >50k chars | Truncate, mark `[... truncated]` |

## Testing

- **Unit:** one test per extractor with tiny fixtures in `tests/fixtures/`: `sample.txt` (100 bytes), `sample.csv` (3 rows), `sample.xlsx` (3 rows), `sample.docx` (2 paragraphs), `sample.wav` (2s tone — no speech, asserts pipeline wiring), `sample.mp4` (3s, 1 frame). Audio/video tests mock `subprocess` in CI.
- **Integration:** 1 new e2e eval in `evals/tasks/agent/` — upload a 10s WAV saying *"subject 12345, procedure viral injection, 3pm"*, assert `capture_metadata` is called with `subject_id` containing `12345`.
- **Markers:** `@pytest.mark.binary` for tests requiring real `ffmpeg`/`whisper-cli`. Skip if not on PATH.

## Dependencies

| Package | For | Size |
|---|---|---|
| `openpyxl` | XLSX parse | ~500KB |
| `python-docx` | DOCX parse | ~200KB |
| `ffmpeg` (system, PATH) | audio transcode, video demux, keyframes | ~70MB |
| `whisper-cli` (system, PATH) | STT inference | ~1MB binary |
| `ggml-base.en.bin` | whisper model weights | ~140MB (downloaded on first use) |

No Python ML stack (no torch, no transformers).

## Prerequisites

Rebase `multimodal-feature` onto `7787e21` (`feat/spreadsheet-artifacts`) before implementation to inherit `agent/tools/spreadsheet.py`, `frontend/app/components/SpreadsheetViewer.tsx`, and `frontend/app/components/ArtifactModal.tsx`. That branch is already merged upstream at AllenNeuralDynamics; it just hasn't landed in this fork's `main` yet.

## Out of scope

- DOCX embedded images (convert-to-PDF path) — defer until someone asks
- Audio/video streaming playback in the UI — files are downloadable via `/uploads/{id}`, that's enough
- Per-user model selection for whisper (base.en only)
- Transcription language detection (English only via `base.en`)
