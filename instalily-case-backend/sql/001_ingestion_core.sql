create extension if not exists pg_trgm;

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
  url_canonical text not null unique,
  url_hash text not null unique,
  page_kind text not null check (page_kind in ('model','part','repair','qa','symptom','install','video','other')),
  status text not null check (status in ('queued','fetched','parsed','failed','skipped')),
  content_hash text,
  title text,
  cleaned_markdown text,
  metadata_json jsonb not null default '{}'::jsonb,
  fetched_at timestamptz,
  parsed_at timestamptz,
  last_error text
);

create table if not exists crawl_frontier (
  url_canonical text primary key,
  status text not null check (status in ('queued','processing','done','failed')),
  attempts int not null default 0,
  source_url text,
  discovered_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  last_run_id uuid references crawl_runs(id),
  last_error text
);

create table if not exists models (
  id bigserial primary key,
  model_number text not null unique,
  model_number_norm text generated always as (upper(regexp_replace(model_number, '[^A-Za-z0-9]', '', 'g'))) stored,
  brand text,
  appliance_type text not null default 'unknown',
  source_url text not null,
  updated_at timestamptz not null default now()
);

create table if not exists parts (
  id bigserial primary key,
  partselect_number text not null unique,
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

create table if not exists model_symptoms (
  id bigserial primary key,
  model_id bigint not null references models(id) on delete cascade,
  symptom text not null,
  source_url text not null,
  updated_at timestamptz not null default now(),
  unique (model_id, symptom)
);

create table if not exists model_media (
  id bigserial primary key,
  model_id bigint not null references models(id) on delete cascade,
  media_type text not null check (media_type in ('video','instruction')),
  title text not null,
  media_url text not null,
  source_url text not null,
  updated_at timestamptz not null default now(),
  unique (model_id, media_type, media_url)
);

create table if not exists model_qa (
  id bigserial primary key,
  model_id bigint not null references models(id) on delete cascade,
  question text not null,
  answer text not null,
  source_url text not null,
  updated_at timestamptz not null default now(),
  unique (model_id, question, answer)
);

create index if not exists idx_crawled_pages_kind_status on crawled_pages(page_kind, status);
create index if not exists idx_crawled_pages_status_canonical on crawled_pages(status, url_canonical);
create index if not exists idx_crawl_frontier_status_updated on crawl_frontier(status, updated_at);
create index if not exists idx_models_model_norm on models(model_number_norm);
create index if not exists idx_parts_ps_norm on parts(partselect_number_norm);
create index if not exists idx_model_parts_model_id on model_parts(model_id);
create index if not exists idx_model_parts_part_id on model_parts(part_id);
