# Agent Tools

This project exposes agent-callable tools through backend endpoints and function schemas.

## Data switch constant

Use `DATA_MODE` in `instalily-case-backend`:

- `DATA_MODE=mock`: tools return data from local `sample/` files.
- `DATA_MODE=supabase`: tools query Supabase/Postgres first, then fall back to mock if no data is found.

Required for live Supabase mode:

- `SUPABASE_DB_URL=postgresql://...`

## Tool schemas

`GET /tools` returns MCP-style function tool schemas:

- `check_part_compatibility(model_number, partselect_number)`
- `search_partselect_content(query, limit)`

## Tool endpoints

- `POST /tools/check-compatibility`
- `POST /tools/search-site`

Example:

```bash
curl -X POST http://localhost:8001/tools/search-site \
  -H "content-type: application/json" \
  -d '{"query":"ice maker not working", "limit": 5}'
```

## Website search behavior

- In `mock` mode, site search uses the local indexed sample pages.
- In `supabase` mode, site search runs SQL against `crawled_pages.cleaned_markdown` and returns top matching snippets.
