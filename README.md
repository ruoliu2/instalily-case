# Instalily Take-Home Monorepo

This repository contains the full Instalily case project:
- `instalily-case-backend`: FastAPI + agent/tool orchestration
- `instalily-case-front`: Next.js chat UI
- `docs`: product/design/architecture notes
- `mini-swe-agent`: reference agent pattern used for guidance

## Prerequisites

- Python `3.11+`
- [`uv`](https://docs.astral.sh/uv/)
- Node.js `18+`
- [`bun`](https://bun.sh/)
- `npx` (for Playwright MCP browser automation)

## Quick Start (Recommended)

From the repo root:

```bash
make dev
```

This starts:
- backend on `http://localhost:8000`
- frontend on `http://localhost:3000`

Stop both with `Ctrl+C`.

### Custom ports

```bash
APP_PORT=8001 FRONTEND_PORT=3001 make dev
```

## First-Time Setup

### 1) Backend setup

```bash
cd instalily-case-backend
uv sync
```

The backend loads env vars from `instalily-case-backend/.env`.
For local/mock development, make sure you have at least:

```bash
DATA_MODE=mock
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
OPENAI_MODEL=gpt-oss:20b
MCP_BROWSER_ENABLED=true
MCP_BROWSER_COMMAND=npx
MCP_BROWSER_ARGS="-y @playwright/mcp@latest --headless"
```

Notes:
- `DATA_MODE=mock` avoids requiring Supabase for runtime tool calls.
- Set `MCP_BROWSER_ENABLED=true` for live PartSelect browser automation (`crawl_partselect_live`).

### 2) Frontend setup

```bash
cd instalily-case-front
bun install
```

Frontend API base URL (optional):

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000
```

If unset, the frontend defaults to `http://localhost:8000`.

## Run Services Manually

### Backend

```bash
cd instalily-case-backend
uv run start
```

Health check:

```bash
curl -i http://127.0.0.1:8000/health
```

### Frontend

```bash
cd instalily-case-front
bun run dev
```

## Backend Tests

```bash
cd instalily-case-backend
./.venv/bin/python -m unittest -q tests.test_agent_loop
```

## Useful Paths

- Backend app: `instalily-case-backend/app`
- Backend tests: `instalily-case-backend/tests`
- Frontend app: `instalily-case-front/src`
- Design docs: `docs`
