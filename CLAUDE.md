# Agentic Metadata Capture System — Implementation Plan

## Overview
Build a real-time metadata capture and validation platform for AIND using the **Claude Agent SDK** (Python). Scientists interact via a web app; a Claude agent extracts, validates, and enriches metadata against AIND schemas and external registries. The existing `aind-metadata-mcp` server plugs in directly via MCP integration.

## MVP Scope
- **Text-based metadata capture** via conversational chat interface
- **NLP extraction** of structured metadata from free-text scientist input
- **Granular metadata records** — each metadata type (subject, procedures, etc.) stored as its own record
- **Shared vs asset-specific** — subjects, instruments, procedures, rigs reusable across experiments
- **Cross-session linking** — shared records can be linked from multiple chat sessions
- **Schema validation** against AIND's live metadata database (read-only via MCP)
- **External registry validation** (Addgene, NCBI GenBank, MGI)
- **Context-aware prompting** — agent only asks about metadata relevant to current conversation
- **Dashboard** with session view + library view for reviewing and confirming metadata
- **Local SQLite** for metadata storage (future: AIND MongoDB write access)

Multi-modal (audio, image, video) deferred to post-MVP.

---

## Architecture

```
┌─────────────────────────────────────────────┐
│           Next.js Frontend (React)          │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐ │
│  │Chat View │  │Dashboard │  │Validation │ │
│  │(capture) │  │(review)  │  │(status)   │ │
│  └──────────┘  └──────────┘  └───────────┘ │
└──────────────────┬──────────────────────────┘
                   │ REST / SSE
┌──────────────────▼──────────────────────────┐
│       Thin API Layer (FastAPI)               │
│       Wraps Claude Agent SDK query()         │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│         Claude Agent SDK (Python)            │
│                                              │
│  ┌─────────────────────────────────────┐     │
│  │ Main Capture Agent                  │     │
│  │ - System prompt with AIND schema    │     │
│  │ - Extracts metadata from text       │     │
│  │ - Proactively prompts for gaps      │     │
│  │ - Orchestrates validation           │     │
│  └─────────────────────────────────────┘     │
│                                              │
│  Built-in tools: Read, Write, Bash, Grep,    │
│  Glob, WebSearch, WebFetch                   │
│                                              │
│  MCP: aind-metadata-mcp (21 tools)           │
│  ┌──────────────────────────────────────┐    │
│  │ get_records, aggregation_retrieval,  │    │
│  │ count_records, get_subject_example,  │    │
│  │ get_procedures_example, etc.         │    │
│  └──────────────────────────────────────┘    │
└──────────────────┬──────────────────────────┘
        ┌──────────┼──────────┐
        ▼          ▼          ▼
   ┌─────────┐ ┌────────┐ ┌────────────┐
   │SQLite   │ │AIND    │ │External    │
   │(drafts) │ │MongoDB │ │Registries  │
   │local    │ │(read)  │ │(web APIs)  │
   └─────────┘ └────────┘ └────────────┘
```

---

## Project Structure

```
metadata-capture/
├── agent/                          # Claude Agent SDK service
│   ├── server.py                   # FastAPI: /chat (SSE), /records CRUD, /sessions, /health, /models
│   ├── service.py                  # Core agent: streaming query(), token-level StreamEvent handling
│   ├── validation.py               # Per-record-type validation: required fields, enums, formats
│   ├── schema_info.py              # Schema introspection from aind-data-schema Pydantic models
│   ├── shared.py                   # Shared async state (validation event queue between tool & stream)
│   ├── prompts/
│   │   └── system_prompt.py        # Context-aware AIND schema + granular record instructions
│   ├── tools/
│   │   ├── metadata_store.py       # SQLite CRUD: records, links, sessions, conversations
│   │   ├── capture_mcp.py          # MCP tools: capture_metadata, find_records, link_records
│   │   └── registry_lookup.py      # Addgene, NCBI, MGI API wrappers
│   ├── db/
│   │   ├── database.py             # Async SQLite (aiosqlite, WAL mode)
│   │   └── models.py               # DDL: metadata_records + record_links + conversations
│   └── requirements.txt
│
├── frontend/                       # Next.js 14 + TypeScript + Tailwind CSS
│   ├── app/
│   │   ├── page.tsx                # Three-pane layout: sessions sidebar | chat | metadata sidebar
│   │   ├── dashboard/page.tsx      # Dashboard with session view + library view toggle
│   │   ├── components/
│   │   │   ├── Header.tsx          # Shared nav bar + live Agent Online/Offline health indicator
│   │   │   ├── ChatPanel.tsx       # Token-streaming chat, model selector, tool progress indicators
│   │   │   ├── SessionsSidebar.tsx # Chat history list with first-message titles
│   │   │   └── MetadataSidebar.tsx # Records grouped by type (shared/asset) with status badges
│   │   └── lib/api.ts              # API client: chat (SSE + AbortSignal), metadata CRUD, sessions
│   └── package.json
│
├── evals/                          # Comprehensive eval suite (see evals/README.md)
│
├── aind-metadata-mcp/              # MCP server (21 tools, moved into project)
```

---

## Implementation Phases

### Phase 1: Agent Core Setup ✅
**Files:** `agent/service.py`, `agent/server.py`, `agent/prompts/system_prompt.py`

- Claude Agent SDK with `query()` for streaming responses
- FastAPI wrapper: POST /chat (SSE), GET /metadata, PUT /metadata/{id}/fields, POST /metadata/{id}/confirm, GET /sessions/{id}/messages, GET /health
- System prompt with AIND schema context
- Model: `claude-opus-4-6`

### Phase 2: Local Storage + Custom Tools ✅
**Files:** `agent/db/`, `agent/tools/metadata_store.py`

- SQLite with async aiosqlite (WAL mode)
- `metadata_records` table: one row per typed record (subject, procedures, session, etc.)
- `record_links` table: explicit many-to-many links between records
- `conversations` table for multi-turn history
- Category system: shared (subject, procedures, instrument, rig) vs asset (data_description, session, etc.)
- CRUD: create/get/update/list/confirm/delete records + link/unlink/find

### Phase 3: Validation Engine ✅ (partial)
**Files:** `agent/validation.py`, `agent/schema_info.py`, `agent/shared.py`, `agent/tools/registry_lookup.py`, `agent/tools/capture_mcp.py`

Done:
- Per-record-type validation: required fields, enum checks, format rules, completeness scoring
- External registry lookups: Addgene (catalog + search), NCBI E-utilities, MGI quick search
- Auto-validation after every metadata capture/update via the `capture_metadata` tool
- Validation results stored per-record in `validation_json`
- Schema-derived enums via `aind-data-schema` Pydantic model introspection (modalities, species, sex)
- Unknown-field warnings for fields not in the canonical schema
- Inline validation display in chat tool dropdowns (errors/warnings shown with colored badges)
- Validation results streamed back to frontend via `tool_result` SSE events using async queue

Not yet done:
- Automatic registry validation in extraction pipeline (functions exist but not auto-triggered)
- Validation feedback loop into agent conversation for proactive prompting

### Phase 3.5: Tool-Based Extraction ✅ → Granular Records ✅
**Files:** `agent/tools/capture_mcp.py`, `agent/service.py`, `agent/prompts/system_prompt.py`

Three MCP tools for metadata capture:
- `capture_metadata`: Save/update a single typed record (one record_type per call)
- `find_records`: Search existing records to avoid duplicates (supports type, query, category filters)
- `link_records`: Create explicit links between records (e.g., session ↔ subject)
- System prompt instructs agent to be context-aware: only ask about metadata the user is discussing
- Agent reuses shared records across sessions via find_records before creating duplicates

### Phase 4: Frontend — Chat Interface ✅
**Files:** `frontend/app/page.tsx`, `frontend/app/components/`

- Token-by-token SSE streaming via SDK `include_partial_messages` + `StreamEvent` deltas
- Stop button aborts the stream mid-response via `AbortController`
- Auto-expanding textarea (no height cap); send button is an up-arrow icon inside the input
- Sessions sidebar lists all chats by first-message preview; click to switch, "New Chat" to start fresh
- Conversation history persists across page reloads (loaded from `GET /sessions/{id}/messages`)
- Side panel with extracted metadata fields, expandable JSON sections
- Metadata cards in the sidebar are clickable — navigate to the dashboard entry
- Auto-refresh, status badges, mobile-responsive

### Phase 5: Frontend — Dashboard ✅
**Files:** `frontend/app/dashboard/page.tsx`

- **Session view**: table grouped by chat session, expandable rows showing per-session records
- **Library view**: records grouped by type (Shared: subjects, procedures, instruments, rigs; Asset: sessions, etc.)
- Toggle between views with a segmented control
- **Inline editing**: click any value to edit; saves on Enter or blur
- **Delete fields**: trash icon on hover
- **Add fields**: `+ Add field` row at the bottom of every record
- **Schema placeholders**: known fields shown as "click to add" rows
- Confirm individual records, filter by status, search
- Shared `Header` with live Agent Online / Offline indicator (polls `/health` every 5 s)

### Phase 6: Streaming & UX Polish ✅
**Files:** `agent/service.py`, `frontend/app/components/ChatPanel.tsx`, `frontend/tailwind.config.ts`

- Enabled `include_partial_messages` on the SDK; yield `content_block_delta` text tokens directly
- Replaced generic blue palette with warm Anthropic-inspired light theme (sand neutrals, terracotta `#D97757` accent)
- Streaming cursor uses a blinking filled circle in the accent color
- Tool dropdowns show inline validation results: red X for errors, amber triangle for warnings
- `capture_metadata` tool results auto-expand when validation issues are found
- Validation streamed to frontend via `tool_result` SSE events (async queue piped from MCP tool handler)

---

## Key Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Agent framework | Claude Agent SDK (Python) | Built-in tools, MCP integration, session management |
| MCP integration | Existing `aind-metadata-mcp` | 21 tools already built for AIND DB access |
| Frontend | Next.js 14 + TypeScript + Tailwind | App Router, SSE streaming, mobile-responsive |
| API layer | FastAPI wrapping SDK `query()` | Async streaming, Python ecosystem match |
| Local DB | SQLite via aiosqlite | Zero-config MVP, WAL mode for concurrency |
| Model | claude-opus-4-6 | Most capable for complex metadata extraction |
| Streaming | SDK `include_partial_messages` + `StreamEvent` | Token-by-token deltas without buffering full messages |
| Stop streaming | `AbortController` + `AbortSignal` on fetch | Cleanly closes SSE connection; partial response stays visible |
| Metadata granularity | One record per type (shared vs asset) | Context-aware capture; no irrelevant follow-ups |
| Record linking | Explicit `record_links` table | Cross-session reuse of subjects, instruments, rigs |
| Inline editing | Per-record PUT endpoint | Auto-saves on blur, no full-page reload |
| Session titles | First user message from DB | Matches Claude desktop UX; no extra LLM call needed |
| Schema validation | `aind-data-schema` Pydantic introspection | Canonical enums + unknown-field checks, no hardcoded drift |
| Validation display | `tool_result` SSE + `contextvars` queue | Tool handler pushes results; stream drains them inline |
| Auth | None for MVP | Add Allen SSO later |

---

## Running the Project

```bash
# Backend (from metadata-capture/ directory)
pip install -r agent/requirements.txt
python3 -m uvicorn agent.server:app --port 8001 --reload  # auto-reloads on file save

# Frontend (from metadata-capture/frontend/ directory)
npm install
npm run dev                                                # auto-reloads on file save

# Run evals (from metadata-capture/ directory)
python3 -m pytest evals/ -x -q                                    # deterministic only
python3 -m pytest evals/tasks/conversation/ -v -m llm              # LLM-graded (needs ANTHROPIC_API_KEY)
python3 -m pytest evals/tasks/validation/ -v -m network            # registry lookups (needs network)
```

---

## Known Issues & Fixes Applied

- **Relative imports**: Must run backend from `metadata-capture/` directory, not from `agent/`
- **Tool-based extraction**: Regex extraction has been replaced with Claude tool calls. The old regex bugs (project name, session end time, protocol ID) are no longer applicable.
- **Python version**: The backend uses `X | Y` union type syntax, which requires Python 3.10+. Any Python ≥ 3.10 works — no conda needed.

---

## Future Work
- Auto-trigger registry lookups (Addgene, NCBI, MGI) when relevant fields are extracted
- Feed validation results back into agent conversation for proactive prompting
- Multi-modal input (audio, image, video, documents)
- MCP write access to AIND MongoDB
- Cloud deployment (Cloud Run)
- Allen SSO authentication
- Performance optimization (concurrent users, token efficiency)
