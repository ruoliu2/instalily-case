# Supabase DB Schema (Sample-Driven, Low Latency)

This schema is based on observed entities in `sample/manifest.json`:
- model overview pages,
- part cards (`PS...`),
- model Q&A,
- common symptoms,
- videos and installation links,
- repair guide pages.

## 1) Extensions
```sql
create extension if not exists vector;
create extension if not exists pg_trgm;
```

## 2) Crawl + Snapshot Layer
```sql
create table if not exists crawl_runs (
  id uuid primary key,
  mode text not null check (mode in ('prefetch','incremental','fallback')),
  status text not null check (status in ('queued','running','done','failed')),
  started_at timestamptz,
  finished_at timestamptz,
  notes text
);

create table if not exists crawled_pages (
  id bigserial primary key,
  run_id uuid references crawl_runs(id),
  url text not null unique,
  page_kind text not null check (
    page_kind in ('model','part','repair','qa','symptom','install','video','other')
  ),
  status text not null check (status in ('queued','fetched','parsed','failed','skipped')),
  content_hash text,
  title text,
  cleaned_markdown text,
  metadata_json jsonb not null default '{}'::jsonb,
  fetched_at timestamptz,
  parsed_at timestamptz,
  last_error text
);
```

## 3) Core Serving Tables
```sql
create table if not exists models (
  id bigserial primary key,
  model_number text not null unique,
  model_number_norm text generated always as (upper(regexp_replace(model_number, '[^A-Za-z0-9]', '', 'g'))) stored,
  brand text,
  appliance_type text not null check (appliance_type in ('dishwasher','refrigerator')),
  source_url text not null,
  updated_at timestamptz not null default now()
);

create table if not exists parts (
  id bigserial primary key,
  partselect_number text not null unique, -- e.g. PS11750093
  partselect_number_norm text generated always as (upper(regexp_replace(partselect_number, '[^A-Za-z0-9]', '', 'g'))) stored,
  manufacturer_part_number text,
  name text,
  price_value numeric(10,2),
  currency text default 'USD',
  availability text,
  source_url text not null,
  updated_at timestamptz not null default now()
);

create table if not exists model_parts (
  model_id bigint not null references models(id) on delete cascade,
  part_id bigint not null references parts(id) on delete cascade,
  compatibility_confidence numeric(4,3) not null default 1.000,
  source_url text not null,
  updated_at timestamptz not null default now(),
  primary key (model_id, part_id)
);

create table if not exists model_qa (
  id bigserial primary key,
  model_id bigint references models(id) on delete set null,
  part_id bigint references parts(id) on delete set null,
  question text not null,
  answer text not null,
  source_url text not null,
  updated_at timestamptz not null default now()
);

create table if not exists model_symptoms (
  id bigserial primary key,
  model_id bigint references models(id) on delete cascade,
  symptom text not null,
  source_url text not null,
  updated_at timestamptz not null default now()
);

create table if not exists model_media (
  id bigserial primary key,
  model_id bigint references models(id) on delete cascade,
  media_type text not null check (media_type in ('video','instruction')),
  title text not null,
  media_url text not null,
  source_url text not null,
  updated_at timestamptz not null default now()
);

create table if not exists repair_guides (
  id bigserial primary key,
  appliance_type text not null,
  topic text not null,
  section_title text,
  body text,
  source_url text not null,
  updated_at timestamptz not null default now()
);

create table if not exists docs (
  id bigserial primary key,
  doc_type text not null check (doc_type in ('qa','symptom','repair','install','video','model_summary')),
  model_id bigint references models(id) on delete set null,
  part_id bigint references parts(id) on delete set null,
  title text,
  body text not null,
  source_url text not null,
  updated_at timestamptz not null default now()
);

create table if not exists doc_chunks (
  id bigserial primary key,
  doc_id bigint not null references docs(id) on delete cascade,
  chunk_text text not null,
  embedding vector(1536) not null,
  metadata_json jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
```

## 4) Latency-Critical Indexes
```sql
-- crawl layer
create index if not exists idx_crawled_pages_kind_status on crawled_pages(page_kind, status);
create index if not exists idx_crawled_pages_fetched_at on crawled_pages(fetched_at desc);

-- exact lookup fast path
create index if not exists idx_models_model_norm on models(model_number_norm);
create index if not exists idx_parts_ps_norm on parts(partselect_number_norm);
create index if not exists idx_model_parts_model_id on model_parts(model_id);
create index if not exists idx_model_parts_part_id on model_parts(part_id);

-- retrieval filtering
create index if not exists idx_model_qa_model_id on model_qa(model_id);
create index if not exists idx_model_symptoms_model_id on model_symptoms(model_id);
create index if not exists idx_model_media_model_type on model_media(model_id, media_type);
create index if not exists idx_docs_type_updated on docs(doc_type, updated_at desc);
create index if not exists idx_docs_model_id on docs(model_id);
create index if not exists idx_docs_part_id on docs(part_id);

-- vector ANN
create index if not exists idx_doc_chunks_embedding_hnsw
  on doc_chunks using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);

-- optional fuzzy fallback
create index if not exists idx_models_model_trgm on models using gin (model_number gin_trgm_ops);
create index if not exists idx_parts_ps_trgm on parts using gin (partselect_number gin_trgm_ops);
```

## 5) Materialized View for Compatibility
```sql
create materialized view if not exists mv_model_part_lookup as
select
  m.id as model_id,
  m.model_number,
  m.model_number_norm,
  p.id as part_id,
  p.partselect_number,
  p.partselect_number_norm,
  p.name as part_name,
  p.price_value,
  p.currency,
  p.availability,
  mp.compatibility_confidence,
  greatest(m.updated_at, p.updated_at, mp.updated_at) as updated_at
from model_parts mp
join models m on m.id = mp.model_id
join parts p on p.id = mp.part_id;

create unique index if not exists idx_mv_model_part_unique on mv_model_part_lookup(model_id, part_id);
create index if not exists idx_mv_model_part_model_norm on mv_model_part_lookup(model_number_norm);
create index if not exists idx_mv_model_part_ps_norm on mv_model_part_lookup(partselect_number_norm);
```

## 6) RPC Fast Path
```sql
create or replace function fn_check_compatibility(in_model text, in_ps text)
returns table (
  model_number text,
  partselect_number text,
  part_name text,
  compatible boolean,
  compatibility_confidence numeric,
  price_value numeric,
  currency text,
  availability text
)
language sql
stable
as $$
  with norm as (
    select
      upper(regexp_replace(in_model, '[^A-Za-z0-9]', '', 'g')) as model_norm,
      upper(regexp_replace(in_ps,    '[^A-Za-z0-9]', '', 'g')) as ps_norm
  )
  select
    mv.model_number,
    mv.partselect_number,
    mv.part_name,
    true,
    mv.compatibility_confidence,
    mv.price_value,
    mv.currency,
    mv.availability
  from mv_model_part_lookup mv
  join norm n
    on mv.model_number_norm = n.model_norm
   and mv.partselect_number_norm = n.ps_norm
  limit 1;
$$;
```

## 7) Notes
- Keep retrieval payload compact (no full raw page in prompts).
- Use cache first, then exact lookup, then vector fallback.
- Refresh `mv_model_part_lookup` after ingestion batches.

Linked design:
- `docs/design.md`
