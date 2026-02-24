# AIND Metadata Capture System

## Overview
A real-time metadata capture and validation platform for AIND (Allen Institute for Neural Dynamics). Scientists interact via a web chat interface; a Claude agent extracts, validates, and enriches metadata against AIND schemas and external registries.

## Architecture
- **Frontend**: Next.js 14 + TypeScript + Tailwind CSS (port 5000)
- **Backend**: FastAPI Python API wrapping Claude Agent SDK (port 8001, localhost only)
- **Database**: Local SQLite via aiosqlite (WAL mode)
- **MCP**: aind-metadata-mcp server (21 tools for AIND DB access)

## Project Structure
```
workspace/
├── agent/               # Python backend (FastAPI + Claude Agent SDK)
│   ├── server.py        # FastAPI endpoints
│   ├── run.py           # Uvicorn entry point (port 8001)
│   ├── service.py       # Core agent logic
│   ├── validation.py    # Schema validation
│   ├── db/              # SQLite database layer
│   ├── prompts/         # System prompts
│   └── tools/           # MCP tools (metadata_store, capture, registry)
├── frontend/            # Next.js frontend (port 5000)
│   ├── app/
│   │   ├── page.tsx     # Chat interface
│   │   ├── dashboard/   # Dashboard view
│   │   ├── components/  # React components
│   │   └── lib/api.ts   # API client
│   └── next.config.mjs  # Rewrites proxy API to backend
├── aind-metadata-mcp/   # MCP server package
└── evals/               # Evaluation suite
```

## Running
- Single workflow runs both backend and frontend
- Backend: `python -m agent.run` (localhost:8001)
- Frontend: `npm run dev` from frontend/ (0.0.0.0:5000)
- Next.js rewrites proxy all API calls from frontend to backend

## Key Configuration
- Next.js configured with `allowedDevOrigins: ['*']` for Replit proxy
- API calls proxied via Next.js rewrites (no direct backend access from browser)
- NEXT_PUBLIC_API_URL env var set to empty string (uses same origin + rewrites)

## Recent Changes
- 2026-02-24: Configured for Replit environment
  - Set Next.js to port 5000 with all hosts allowed
  - Set backend to localhost:8001
  - Added Next.js rewrites to proxy API calls to backend
  - Set up deployment config (autoscale)
