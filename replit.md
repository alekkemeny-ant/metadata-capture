# AIND Metadata Capture System

## Overview
A real-time metadata capture and validation platform for AIND (Allen Institute for Neural Dynamics). Scientists interact via a web chat interface; a Claude agent extracts, validates, and enriches metadata against AIND schemas and external registries.

## Architecture
- **Frontend**: Next.js 14 + TypeScript + Tailwind CSS (port 5000)
- **Backend**: FastAPI Python API wrapping Claude Agent SDK (port 8001, localhost only)
- **Database**: PostgreSQL (asyncpg) or SQLite (aiosqlite), auto-selected by environment
- **MCP**: aind-metadata-mcp server (21 tools for AIND DB access)

## Project Structure
```
workspace/
├── agent/               # Python backend (FastAPI + Claude Agent SDK)
│   ├── server.py        # FastAPI endpoints
│   ├── run.py           # Uvicorn entry point (port 8001)
│   ├── service.py       # Core agent logic
│   ├── validation.py    # Schema validation
│   ├── db/              # Database layer (PG + SQLite backends)
│   ├── prompts/         # System prompts
│   └── tools/           # MCP tools (metadata_store, capture, registry)
├── frontend/            # Next.js frontend (port 5000)
│   ├── app/
│   │   ├── page.tsx     # Chat interface
│   │   ├── dashboard/   # Dashboard view
│   │   ├── components/  # React components
│   │   └── lib/api.ts   # API client
│   └── next.config.mjs  # Rewrites proxy API to backend
├── aind-metadata-mcp/   # MCP server package (installed as editable)
└── evals/               # Evaluation suite
```

## Running
- Single workflow runs both backend and frontend
- Backend: `python -m agent.run` (localhost:8001)
- Frontend: `npm run dev` from frontend/ (0.0.0.0:5000)
- Next.js rewrites proxy all API calls from frontend to backend

## Dependencies
- Python: `pip install -r agent/requirements.txt && pip install -e ./aind-data-mcp`
- Node.js: `cd frontend && npm install`
- Claude Code CLI: `npm install -g @anthropic-ai/claude-code` (required by claude-agent-sdk)

## Key Configuration
- Next.js configured with `allowedDevOrigins: ['*']` for Replit proxy
- API calls proxied via Next.js rewrites (no direct backend access from browser)
- API_BASE in frontend defaults to empty string (uses same origin + rewrites)
- ANTHROPIC_API_KEY must be set as a Replit secret
- DATABASE_URL: if set, uses PostgreSQL (asyncpg); if unset, falls back to SQLite (aiosqlite) at `agent/metadata.db`
- METADATA_DB_DIR: optional override for SQLite database directory (defaults to agent/ package dir)

## Recent Changes
- 2026-03-21: Chunked file upload — bypasses Replit reverse-proxy 413 limit
  - Files > 8 MB are automatically split into 5 MB chunks by the frontend and reassembled server-side
  - Three new backend endpoints: `POST /upload/init`, `POST /upload/chunk`, `POST /upload/finalize`
  - Small files (≤ 8 MB) still use the original single-XHR path for progress reporting
  - `CHUNKS_DIR = UPLOADS_DIR / "chunks"` stores temp chunk files during assembly; cleaned up after finalize
  - Next.js rewrites and `.replitignore` updated accordingly

- 2026-03-21: Fixed deployment bundle size — added `.replitignore`
  - `uploads/` (12 GB local videos) excluded from the deployment bundle
  - `.pythonlibs/`, `frontend/node_modules/` remain in bundle (needed at runtime in Replit autoscale)
  - `.cache/`, `.local/`, `.config/`, `__pycache__/`, `*.pyc`, `*.db` also excluded


- 2026-03-20: PyAV keyframe extraction — replaces per-frame ffmpeg subprocesses
  - `av` (PyAV) added to requirements.txt; opens the video container ONCE and seeks N times
  - `_extract_frames_sync` in `transcribe.py`: synchronous, runs in thread-pool executor; moov atom parsed once, H.264 decoder initialized once, one frame decoded per seek, PIL resize+PNG encode per frame
  - `extract_keyframes_gen` calls `_extract_frames_sync` via `run_in_executor` then yields frames — peak RSS is one frame in memory at a time (same as before but without N×subprocess overhead and N×moov-parse)
  - Removed per-frame ffmpeg subprocess spawning, temp file writes/reads, and the entire `tmpdir` tmpfile loop
  - `thread_count=1, thread_type=FRAME` on the codec context — same single-thread constraint as the old `-threads 1` ffmpeg flag

- 2026-03-20: Video keyframe storage refactor — fixes OOM crash on large videos
  - New `upload_keyframes` table: one row per frame (BYTEA), replacing the single huge `extracted_images_json` blob
  - `extract_keyframes_gen` async generator in `transcribe.py`: yields one PNG at a time, writes to disk, reads back, yields, deletes — only one frame in Python memory at any point
  - `_extract_and_store` in `server.py` now has a dedicated video path: consumes the generator and calls `save_keyframe()` per frame before moving to the next
  - Extraction status poll (`/uploads/{id}/extraction`) uses `COUNT(*)` on `upload_keyframes` for image count — no image bytes loaded just to count frames
  - `get_upload_extraction` loads from `upload_keyframes` first; falls back to old `extracted_images_json` for backward compat with uploads created before this change
  - `extract_video` in `extractors.py` is now a stub (kept so MIME types stay in the allowed set; actual extraction happens in `server.py`)
  - Fixed `size_bytes` column: `INTEGER` → `BIGINT` in PostgreSQL DDL (max was 2.1GB, videos can be larger)
  - `UPLOADS_DIR` moved from shared env var to production-only; dev now defaults to `workspace/uploads/` (253GB) instead of `/tmp/uploads` (2GB Replit quota)

- 2026-03-04: Dual database backend (PostgreSQL + SQLite)
  - `agent/db/database.py`: `Database` ABC with `PostgresDatabase` and `SQLiteDatabase` implementations
  - Auto-selects backend: `DATABASE_URL` set → PostgreSQL (asyncpg pool), unset → SQLite (aiosqlite)
  - All queries use `?` placeholders; PG backend auto-converts to `$1, $2, ...`
  - `agent/db/models.py`: separate `PG_TABLES` and `SQLITE_TABLES` DDL lists, shared `CREATE_INDEXES`
  - Tables: metadata_records, record_links, conversations, uploads
  - Added /artifacts rewrite to Next.js config
- 2026-03-09: Production MCP debugging & fixes
  - MCP subprocess env now inherits full parent env (was only PYTHONPATH, stripping PATH/credentials)
  - Added PYTHONUNBUFFERED=1 to deployment run command for unbuffered log output
  - Added print-based startup diagnostics (lifespan steps, MCP dir check, registration)
  - Re-added count_records and aggregation_retrieval to allowed MCP tools (16 tools total)
  - Updated system prompt to explicitly list aggregation tools and clarify AIND MCP vs local capture tools
  - SDK client pool enabled (USE_SDK_POOL=1) in both dev and prod; pool pre-warms a CLI subprocess at startup (~2.5s) so chat requests skip the ~4s spawn overhead
  - Added chat path logging (pool vs query) for production diagnostics
  - Pool can be disabled with USE_SDK_POOL=0 env var if MCP idle disconnects recur
  - Fixed real-time streaming on Replit: Replit's reverse proxy buffers all HTTP responses (including SSE with X-Accel-Buffering: no), so switched chat to WebSocket transport which delivers frames immediately
  - Added WebSocket endpoint `/ws/chat` to FastAPI backend (agent/server.py) — accepts JSON message, streams events as individual WS frames
  - Created custom Node.js server (frontend/server.mjs) that proxies `/ws/chat` WebSocket upgrades to backend and passes all other requests to Next.js
  - Frontend `sendChatMessage` in api.ts auto-detects Replit (hostname includes `.replit.dev`, `.repl.co`, or `.replit.app`) and uses WebSocket; otherwise falls back to SSE via `/chat` rewrite
  - SSE `/chat` endpoint still exists on backend and via Next.js rewrite for non-Replit environments
  - `npm run dev` / `npm run start` use standard Next.js (SSE); `npm run dev:replit` / `npm run start:replit` use custom server (WebSocket)
  - Workflow and deployment commands use `dev:replit` / `start:replit` scripts
  - Fixed NEXT_PUBLIC_API_URL default from 'http://localhost:8001' to '' (empty = use same-origin rewrites), preventing mixed-content HTTPS→HTTP failures in Replit iframe
- 2026-03-10: Fixed "final message missing" bug for multi-turn tool-using queries
  - Root cause: `max_turns=5` was too low for complex tool-using queries (e.g. VR foraging needed 11 turns); SDK hit the limit and returned empty ResultMessage with no text. Increased to `max_turns=15`
  - Secondary fix: `_translate_to_sse()` now extracts text from `ResultMessage.result` when `full_response` is empty, and appends unstreamed trailing text. Handles `is_error=True` ResultMessages by surfacing error details to the user
  - Hardened reconciliation: only appends suffix when `result.startswith(streamed_text)`
  - Added comprehensive ResultMessage logging: `is_error`, `subtype`, `result_len`
  - Reduced watchfiles reload noise: added `reload_excludes` for frontend/, .local/, node_modules/, etc. in `agent/run.py`
  - Fixed Next.js `allowedDevOrigins` from `['*']` to explicit Replit host patterns
  - Cleaned up diagnostic logging: removed noisy per-AssistantMessage warnings, removed PII from result logging
- 2026-02-27: Added offline chat protection
  - Health check state lifted to page.tsx, passed as prop to Header and ChatPanel
  - Chat input disabled with "Agent is starting up..." overlay when agent offline
  - Deployment set to autoscale (cold start ~60s)
- 2026-03-18: Upload durability & schema cleanup
  - Uploads table now stores raw file bytes (`file_data` BYTEA/BLOB) in the database
  - File-serve endpoints fall back to DB bytes when disk file is missing (fixes autoscale 404s)
  - `_prepare_attachments` in service.py also falls back to DB for native types (images/PDFs)
  - `UPLOADS_DIR` is now an env var (default: `workspace/uploads/`, Replit: `/tmp/uploads`)
  - Removed all migration/drift-detection logic from `database.py` — `init_tables` just runs DDL
  - Removed `UPLOADS_EXTRACTION_COLUMNS` from models.py — no duplicated column lists
- 2026-03-24: Pool auto-reconnect — fixes AIND tools dropping mid-thread
  - `_run()` now wraps connect+query loop in an outer reconnect loop
  - Noisy failure: `_handle()` raises → `_ready` cleared → inner loop breaks → reconnect after 5s
  - Silent failure: MCP subprocess dies without exception (Claude marks tools gone, pool stays "warm") → 30-minute idle timeout on `Queue.get()` triggers proactive reconnect to refresh MCP subprocess
  - Single cleanup `finally` block handles all exit routes (break, exception, CancelledError) — no double-reset of `stream_events` token
  - `connect_failed` flag distinguishes "failed to connect" (60s retry) from "normal reconnect" (5s)
  - Shutdown via `None` sentinel still works; `cancel()` interrupts any sleep cleanly
  - MCP-unavailability detection: `chat()` checks response text for "aind-data-mcp" / "MCP server" + "reconnect" / "not available"; if found, clears `_ready` to force immediate pool reconnect so the next query works
  - MCP watchdog: background task pings `api.allenneuraldynamics.org/v2` every 2 min; if API is healthy and pool connection is older than 5 min, forces proactive reconnect to refresh MCP subprocess
  - Pool `_run()` polls every 30s (down from 5 min idle timeout) so it picks up the watchdog's `_needs_reconnect` flag promptly
  - `start()` launches both the worker task and the watchdog task

- 2026-03-24: MCP cold-start improvements
  - Removed `nwb_tools` import from `data_access_server.py` — boto3 + hdmf_zarr were adding 20-40s of startup latency; those NWB tools are not in the allowed list anyway
  - SDK client pool warmup now non-blocking: `lifespan` calls `pool.start()` (fire-and-forget) and yields immediately so health checks pass. The pool warms in the background (~90-200s in production)
  - `chat()` falls through to per-request `query()` immediately if pool not warm — no blocking wait that would freeze the first request
  - Added `start()` and `await_warm(timeout)` methods to `SDKClientPool` for finer warmup control
  - MongoDB connection in MCP tools is lazy (per tool call via `setup_mongodb_client()`) — NOT at MCP subprocess startup

- 2026-03-17: Production MCP fixes
  - Build command now installs Python deps (`pip install -r agent/requirements.txt && pip install -e ./aind-metadata-mcp`) before frontend build — ensures aind-metadata-mcp is available in production container
  - SDK pool warmup timeout increased from 30s → 60s (MCP startup takes ~33s in production due to MongoDB connection latency)
  - 529 Overloaded errors now show user-friendly message instead of raw SDK error string
  - Fallback model list in `api.ts` updated to match backend (claude-sonnet-4-6)
  - Restored `NEXT_PUBLIC_API_URL` default to `http://localhost:8001` for local dev SSE streaming (Replit overrides to `""` via `[userenv.shared]`)
- 2026-02-24: Configured for Replit environment
  - Set Next.js to port 5000 with all hosts allowed
  - Set backend to localhost:8001
  - Added Next.js rewrites to proxy API calls to backend
  - Fixed API_BASE in Header.tsx and api.ts to use empty string (proxy) instead of localhost
  - Installed claude-code CLI globally for claude-agent-sdk
  - Installed aind-metadata-mcp as editable local package
