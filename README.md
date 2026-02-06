# AIND Metadata Capture

A real-time metadata capture and validation platform for the Allen Institute for Neural Dynamics (AIND). Scientists describe their experiments in natural language; a Claude-powered agent extracts structured metadata, validates it against AIND schemas and external registries, and saves it as granular, linkable records.

## Features

- **Conversational capture** — Chat interface where scientists describe experiments in plain language
- **Context-aware extraction** — Agent only asks about metadata relevant to what the user is describing (no irrelevant follow-ups about modality when you're describing a surgery)
- **Granular records** — Each metadata type (subject, procedures, instrument, session, etc.) is stored as its own record, not lumped into one monolithic entry
- **Shared vs asset-specific** — Subjects, instruments, procedures, and rigs are reusable across experiments; sessions, acquisitions, and data descriptions are tied to specific data assets
- **Cross-session linking** — Shared records created in one chat can be found and linked from another chat
- **Token-by-token streaming** — Real-time SSE streaming with stop button, tool progress indicators with elapsed timers, and model selector (Opus/Sonnet/Haiku)
- **Schema-backed validation** — Enum sets (modalities, species, sex) derived from `aind-data-schema` Pydantic models at import time, so they never drift from the canonical schema. Unknown fields trigger warnings.
- **Inline validation display** — Validation errors and warnings appear directly in the chat tool dropdowns with colored badges (red for errors, amber for warnings), auto-expanded when issues are found
- **Live database validation** — Validates project names, subject IDs, and modalities against AIND's live MongoDB via MCP
- **Registry validation** — Cross-references Addgene, NCBI GenBank, and MGI databases
- **Session persistence** — Conversations survive page reloads; a sidebar lets you switch between chats
- **Dashboard with two views** — Session view groups records by chat session; Library view groups by record type (shared vs asset). Inline editing, field deletion, and schema-guided placeholders
- **Live health indicator** — Header badge polls the backend and shows Agent Online / Offline in real time

## Architecture

The system has three components:

1. **Agent backend** (`agent/`) — Python service using the Claude Agent SDK, wrapped in FastAPI. Streams token-by-token via `include_partial_messages` + `StreamEvent`. Three MCP tools for metadata capture: `capture_metadata` (one record type per call), `find_records` (search existing shared records), and `link_records` (associate related records). Stores records, links, and conversation history in SQLite.

2. **Web frontend** (`frontend/`) — Next.js 14 app with TypeScript and Tailwind CSS. Three-pane layout: sessions sidebar, token-streaming chat panel with model selector, and metadata sidebar grouped by record type. Dashboard page with session/library view toggle and inline editing.

3. **AIND MCP server** (`aind-metadata-mcp/`) — MCP server with 20 tools for read-only access to AIND's live metadata MongoDB (hosted at `api.allenneuraldynamics.org`). Connected to the agent via stdio transport.

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+
- An Anthropic API key (set as `ANTHROPIC_API_KEY` environment variable or in `metadata-capture/.env`)

### MCP Server

From the `metadata-capture/` directory, install the MCP server:

```bash
cd aind-metadata-mcp
pip install -e .
cd ..
```

This installs the `aind-metadata-mcp` package and its dependencies (`aind-data-access-api`, `fastmcp`, etc.). The agent automatically discovers and launches it via the `mcp_config.json` file.

### Backend

From the `metadata-capture/` directory, create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r agent/requirements.txt
python3 -m uvicorn agent.server:app --port 8001 --reload  # auto-reloads on save
```

The API will be available at `http://localhost:8001`. Key endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | POST | Send a message (with optional `model`), receive token-level SSE stream |
| `/records` | GET | List all metadata records (filter by `type`, `category`, `session_id`, `status`) |
| `/records/{id}` | GET | Get a single record with its linked records |
| `/records/{id}` | PUT | Update a record's data |
| `/records/{id}/confirm` | POST | Confirm a record |
| `/records/link` | POST | Link two records together |
| `/sessions` | GET | List all chat sessions with message counts |
| `/sessions/{id}/messages` | GET | Full conversation history for a session |
| `/sessions/{id}/records` | GET | All records created in a session |
| `/models` | GET | List available models and the default |
| `/health` | GET | Health check (polled by the frontend every 5 s) |

### Frontend

From the `metadata-capture/` directory:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. The frontend connects to the backend at `localhost:8001` by default (configurable via `NEXT_PUBLIC_API_URL`).

## Database Schema

The SQLite database uses two main tables:

- **`metadata_records`** — One row per typed record. Each record has a `record_type` (subject, procedures, instrument, rig, data_description, acquisition, session, processing, quality_control) and a `category` (shared or asset). Shared records are reusable across experiments; asset records are tied to specific data assets.

- **`record_links`** — Explicit many-to-many links between records (e.g., a session linked to a subject). Links are bidirectional.

- **`conversations`** — Multi-turn chat history.

## Example Interaction

**Scientist:** "I performed a stereotactic injection on mouse 123456 today, targeting the thalamus with GFP adenovirus."

**Agent creates two records:**
- **Subject** (shared): `{subject_id: "123456", species: {name: "Mus musculus"}}`
- **Procedures** (shared): `{procedure_type: "Injection", injection_type: "Stereotactic", target_brain_region: "Thalamus", injection_materials: "GFP Adenovirus", ...}`

The agent only asks follow-up questions about the procedure — not about modality, project name, or session timing, since those aren't relevant to what the user described.

## Evals

See [`evals/README.md`](evals/README.md) for the comprehensive evaluation suite covering extraction accuracy, conversational quality, registry validation, and end-to-end pipeline tests.

```bash
# Run all deterministic tests (no API key or network needed)
python3 -m pytest evals/ -x -q -m "not llm and not network"

# Run LLM-graded conversation tests (requires ANTHROPIC_API_KEY)
python3 -m pytest evals/tasks/conversation/ -v -m llm

# Run registry validation tests (requires network)
python3 -m pytest evals/tasks/validation/ -v -m network
```

## Project Status

- [x] Agent core (Claude Agent SDK + FastAPI + model selector)
- [x] Granular metadata records (per-type records with shared/asset categories)
- [x] Record linking (explicit many-to-many links, cross-session reuse)
- [x] Validation (schema-derived enums via `aind-data-schema`, unknown-field warnings, inline chat display, Addgene/NCBI/MGI registries, live AIND MongoDB via MCP)
- [x] Context-aware tool-based extraction (3 MCP tools: capture, find, link)
- [x] Chat interface (token streaming, tool progress indicators with elapsed timers, model selector)
- [x] Dashboard (session view + library view, inline editing, schema placeholders)
- [x] Streaming & UX polish (terracotta theme, live health indicator, shimmer animations)
- [x] Eval suite (53 deterministic tests + LLM-graded + network tests)
- [ ] Auto-trigger registry lookups when relevant fields are extracted
- [ ] Validation feedback loop into agent conversation for proactive prompting
- [x] Deeper schema validation via `aind-data-schema` Pydantic models
- [ ] Multi-modal input (audio recordings, images of lab notebooks, documents)
- [ ] MCP write access to AIND MongoDB
- [ ] Cloud deployment
- [ ] Authentication (Allen SSO)
