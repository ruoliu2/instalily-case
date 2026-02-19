# Instalily Case Backend

## 1) Chat API (existing MVP)
- `GET /health`
- `POST /chat`
- `GET /tools`
- `POST /tools/check-compatibility`
- `POST /tools/search-site`
- `POST /tools/crawl-live`

Shortcut start (with `uv`):
```bash
cd instalily-case-backend
uv sync
OPENAI_BASE_URL=http://localhost:11434/v1 OPENAI_API_KEY=ollama OPENAI_MODEL=gpt-oss:20b uv run start
```

Shortcut stop (same port, default `8000`):
```bash
cd instalily-case-backend
uv run stop
```

Use a custom port:
```bash
APP_PORT=8001 uv run start
APP_PORT=8001 uv run stop
```

Run:
```bash
cd instalily-case-backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

## 2) Supabase Ingestion Service (new)
Parallel site ingestion for core data:
- `models`
- `parts`
- `model_parts`
- `model_symptoms`
- `model_media`
- `model_qa`

The service:
- crawls from seed URLs,
- discovers additional in-scope links,
- retries with multiple crawl strategies,
- runs in parallel workers,
- upserts into Supabase Postgres.

### Env vars
```bash
export SUPABASE_DB_URL='postgresql://...'
export DATA_MODE='mock'          # mock | supabase
export CRAWL_CONCURRENCY=12
export MAX_RUNTIME_HOURS=48
export MAX_PAGES=250000
export REQUEUE_SEEDS_ON_START=false
export SAVE_MARKDOWN=false
```

### Data source switch (mock vs Supabase)
Use this constant/environment variable:

```bash
DATA_MODE=mock
```

or

```bash
DATA_MODE=supabase
```

Behavior:
- `mock`: tool calls read from local `sample/` data.
- `supabase`: tool calls read from Supabase/Postgres tables (`models`, `parts`, `model_parts`, `crawled_pages`), with mock fallback if Supabase has no match.

### Run ingestion
```bash
cd instalily-case-backend
source .venv/bin/activate
python -m ingestion.cli
```

### Notes
- Schema is auto-applied from `sql/001_ingestion_core.sql`.
- Runtime defaults to 48h for broad prefetch.
- Increase `CRAWL_CONCURRENCY` carefully to avoid more anti-bot pressure.
- Duplicate page fetch prevention is DB-backed: ingestion preloads already-processed URLs from `crawled_pages` (statuses: `parsed`, `skipped`) and avoids re-queueing them.
- `crawled_pages` dedupe keys:
  - `url_canonical`: normalized URL without query/fragment (unique)
  - `url_hash`: hash of canonical URL (unique)
  - ingestion writes with `ON CONFLICT (url_canonical)` to collapse tracking-parameter variants.
- Resume/checkpoint behavior:
  - frontier queue is persisted in `crawl_frontier` (`queued`, `processing`, `done`, `failed`)
  - startup reconciliation moves stale `processing`/`failed` back to `queued`
  - workers lease URLs with DB locking (`FOR UPDATE SKIP LOCKED`) so interrupted runs can resume safely
  - optional seed refresh: set `REQUEUE_SEEDS_ON_START=true` to revisit hub seeds.

## 3) Local gpt-oss wiring
```bash
export OPENAI_BASE_URL=http://localhost:11434/v1
export OPENAI_API_KEY=dummy
export OPENAI_MODEL=gpt-oss
export MCP_BROWSER_ENABLED=true
export MCP_BROWSER_COMMAND=npx
export MCP_BROWSER_ARGS="-y @playwright/mcp@latest --headless"
```

## 4) Agent tool calls (MCP-style function schemas)
The backend now exposes a tool registry for agent usage:
- `check_part_compatibility(model_number, partselect_number)`
- `search_partselect_content(query, limit=6)`
- `crawl_partselect_live(url, model_number="", query="", max_pages=2)`

Tool schemas are available from:
- `GET /tools`

Manual test examples:
```bash
curl -X POST http://localhost:8001/tools/check-compatibility \
  -H "content-type: application/json" \
  -d '{"model_number":"WDT780SAEM1","partselect_number":"PS11750093"}'
```

```bash
curl -X POST http://localhost:8001/tools/search-site \
  -H "content-type: application/json" \
  -d '{"query":"dishwasher not draining", "limit": 5}'
```

```bash
curl -X POST http://localhost:8001/tools/crawl-live \
  -H "content-type: application/json" \
  -d '{"url":"https://www.partselect.com/", "model_number":"WDT780SAEM1", "query":"WDT780SAEM1", "max_pages": 2}'
```

Live crawl note:
- MCP browser is required (`MCP_BROWSER_ENABLED=true`).
- The tool auto-discovers search inputs using MCP snapshot output, types query/model text, and submits the form dynamically.
