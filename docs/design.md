# Instalily x PartSelect Chat Agent Design (Compact)

## 1) Scope
- Domain: PartSelect **dishwasher + refrigerator** support only.
- Model for demo: local `gpt-oss`.
- Data source: prefetch + targeted live crawl via `mcp-crawl4ai`.
- Goal: low latency, grounded answers, minimal context pollution.

## 2) Crawl Strategy Tradeoffs

### Option A: Full Pre-Crawl
- Pros: best latency, stable retrieval quality.
- Cons: stale inventory/price risk, heavy upfront indexing.

### Option B: Live Crawl Only
- Pros: freshest content, strong long-tail coverage.
- Cons: slower responses, fragile runtime crawling, noisy payloads.

### Option C: Hybrid (Selected)
- Pre-crawl high-value pages (models, parts, top Q&A, symptoms, install docs).
- Live crawl only when index is missing data, confidence is low, or freshness is required.
- Cache and re-index successful live fallback results.

Decision: **Hybrid** gives best latency/freshness balance for this use case.

## 3) Non-Verbose Parsing Design
- Primary extraction path: `extract_structured_data` with page-type schemas.
- Fallback path: `crawl_url` + local cleaner.
- Store only compact entities, not full page dumps.

Keep:
- model metadata,
- part cards (`PS...`, title, price, availability, URL),
- Q&A pairs,
- symptoms,
- videos/instructions links,
- repair guide topic blocks.

Drop:
- nav/footer/legal/marketing blocks,
- unrelated links,
- binary payloads (`screenshot`, `pdf`) from retrieval index.

## 4) Low-Latency Retrieval Path (Supabase)
1. Normalize query (model/part tokens).
2. L1/L2 cache lookup.
3. Exact structured lookup first (`model_parts`, `parts`, `models`).
4. Vector fallback only when exact path is insufficient.
5. Build compact context (top 6-10 chunks max) for `gpt-oss`.
6. Return grounded answer with citations and cache response.

Latency principles:
- prefer exact-match over semantic search,
- reduce DB round trips via Supabase RPC,
- keep payload small,
- prewarm top model/part queries.

## 5) Runtime Components
- `ingestion-service`: crawl, parse, normalize, index.
- `retrieval-api`: exact + hybrid retrieval orchestration.
- `chat-agent`: tool-using main agent (lookup, retrieve, optional crawl fallback).
- `frontend chat`: answer + source cards.

## 6) Database Choice and Tradeoff
Selected: **Supabase Postgres + pgvector**
- Pros: one system for relational + vector, fast product iteration, SQL RPC, cron.
- Cons: requires indexing/tuning; dedicated vector DB may outperform at very large scale.

Schema and indexes:
- `docs/db-schema.md`
- `docs/capacity-estimate.md`

## 7) Risks
- Anti-bot blocking on some pages.
- Layout changes can break selectors.
- Freshness-sensitive fields need tighter recrawl policy.

## 8) Sample Crawl Evidence
Sample outputs used to shape schema are stored in:
- `sample/manifest.json`
- `sample/raw/`
- `sample/parsed/`
