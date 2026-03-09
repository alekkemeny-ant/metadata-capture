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
- Python: `pip install -r agent/requirements.txt && pip install -e ./aind-metadata-mcp`
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
  - Added /debug/mcp diagnostic endpoint (tests MCP import, API connectivity, pool status, tool list)
  - Re-added count_records and aggregation_retrieval to allowed MCP tools (16 tools total)
  - Updated system prompt to explicitly list aggregation tools and clarify AIND MCP vs local capture tools
  - Added /debug rewrite to Next.js config
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
  - Added `ws` package to frontend dependencies
  - Fixed NEXT_PUBLIC_API_URL default from 'http://localhost:8001' to '' (empty = use same-origin rewrites), preventing mixed-content HTTPS→HTTP failures in Replit iframe
- 2026-02-27: Added offline chat protection
  - Health check state lifted to page.tsx, passed as prop to Header and ChatPanel
  - Chat input disabled with "Agent is starting up..." overlay when agent offline
  - Deployment set to autoscale (cold start ~60s)
- 2026-02-24: Configured for Replit environment
  - Set Next.js to port 5000 with all hosts allowed
  - Set backend to localhost:8001
  - Added Next.js rewrites to proxy API calls to backend
  - Fixed API_BASE in Header.tsx and api.ts to use empty string (proxy) instead of localhost
  - Installed claude-code CLI globally for claude-agent-sdk
  - Installed aind-metadata-mcp as editable local package
