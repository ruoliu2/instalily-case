from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg

from .parser import ParsedModel, ParsedPage, ParsedPart
from .parser import canonicalize_url


@dataclass
class Store:
    conn: psycopg.Connection

    @classmethod
    def from_dsn(cls, dsn: str) -> "Store":
        conn = psycopg.connect(dsn, autocommit=True)
        return cls(conn=conn)

    def close(self) -> None:
        self.conn.close()

    def apply_schema(self, sql_path: Path) -> None:
        sql = sql_path.read_text(encoding="utf-8")
        with self.conn.cursor() as cur:
            cur.execute(sql)

    def begin_run(self, mode: str = "prefetch", notes: str = "") -> str:
        run_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into crawl_runs(id, mode, status, started_at, notes)
                values (%s, %s, 'running', %s, %s)
                """,
                (run_id, mode, datetime.now(timezone.utc), notes),
            )
        return run_id

    def end_run(self, run_id: str, status: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                update crawl_runs
                set status=%s, finished_at=%s
                where id=%s
                """,
                (status, datetime.now(timezone.utc), run_id),
            )

    def load_processed_urls(self, statuses: tuple[str, ...] = ("parsed", "skipped")) -> set[str]:
        if not statuses:
            return set()
        placeholders = ", ".join(["%s"] * len(statuses))
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                select url_canonical
                from crawled_pages
                where status in ({placeholders}) and url_canonical is not null
                """,
                tuple(statuses),
            )
            return {str(row[0]) for row in cur.fetchall()}

    def upsert_frontier_queued(
        self,
        url_canonical: str,
        run_id: str,
        source_url: str = "",
        force_requeue: bool = False,
    ) -> None:
        with self.conn.cursor() as cur:
            if force_requeue:
                cur.execute(
                    """
                    insert into crawl_frontier(
                      url_canonical, status, attempts, source_url, discovered_at, updated_at, last_run_id, last_error
                    )
                    values (%s, 'queued', 0, %s, now(), now(), %s, null)
                    on conflict (url_canonical) do update set
                      status='queued',
                      source_url=excluded.source_url,
                      updated_at=now(),
                      last_run_id=excluded.last_run_id,
                      last_error=null
                    """,
                    (url_canonical, source_url, run_id),
                )
            else:
                cur.execute(
                    """
                    insert into crawl_frontier(
                      url_canonical, status, attempts, source_url, discovered_at, updated_at, last_run_id, last_error
                    )
                    values (%s, 'queued', 0, %s, now(), now(), %s, null)
                    on conflict (url_canonical) do update set
                      status = case
                        when crawl_frontier.status in ('done', 'processing') then crawl_frontier.status
                        else 'queued'
                      end,
                      source_url = excluded.source_url,
                      updated_at = now(),
                      last_run_id = excluded.last_run_id,
                      last_error = case
                        when crawl_frontier.status = 'done' then crawl_frontier.last_error
                        else null
                      end
                    """,
                    (url_canonical, source_url, run_id),
                )

    def reconcile_frontier_for_resume(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                update crawl_frontier
                set status='queued', updated_at=now(), last_error=null
                where status in ('processing', 'failed')
                """
            )
            return int(cur.rowcount or 0)

    def claim_next_frontier_url(self, run_id: str) -> Optional[str]:
        with self.conn.transaction():
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    select url_canonical
                    from crawl_frontier
                    where status='queued'
                    order by updated_at asc
                    limit 1
                    for update skip locked
                    """
                )
                row = cur.fetchone()
                if not row:
                    return None
                url_canonical = str(row[0])
                cur.execute(
                    """
                    update crawl_frontier
                    set status='processing', attempts=attempts + 1, updated_at=now(), last_run_id=%s
                    where url_canonical=%s
                    """,
                    (run_id, url_canonical),
                )
                return url_canonical

    def count_frontier_pending(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select count(*)
                from crawl_frontier
                where status in ('queued', 'processing')
                """
            )
            return int(cur.fetchone()[0])

    def mark_frontier_processing(self, url_canonical: str, run_id: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                update crawl_frontier
                set status='processing', attempts=attempts + 1, updated_at=now(), last_run_id=%s
                where url_canonical=%s and status <> 'done'
                """,
                (run_id, url_canonical),
            )

    def mark_frontier_done(self, url_canonical: str, run_id: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                update crawl_frontier
                set status='done', updated_at=now(), last_run_id=%s, last_error=null
                where url_canonical=%s
                """,
                (run_id, url_canonical),
            )

    def mark_frontier_failed(self, url_canonical: str, run_id: str, error: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                update crawl_frontier
                set status='failed', updated_at=now(), last_run_id=%s, last_error=%s
                where url_canonical=%s and status <> 'done'
                """,
                (run_id, error[:1000], url_canonical),
            )

    def upsert_crawled_page(
        self,
        run_id: str,
        url: str,
        page_kind: str,
        status: str,
        markdown: str,
        title: str,
        metadata: dict,
        error: Optional[str] = None,
    ) -> None:
        url_canonical = canonicalize_url(url)
        url_hash = hashlib.md5(url_canonical.encode("utf-8", errors="ignore")).hexdigest() if url_canonical else None
        content_hash = hashlib.md5(markdown.encode("utf-8", errors="ignore")).hexdigest() if markdown else None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into crawled_pages(
                  run_id, url, url_canonical, url_hash, page_kind, status, content_hash, title, cleaned_markdown, metadata_json, fetched_at, parsed_at, last_error
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (url_canonical) do update set
                  run_id=excluded.run_id,
                  page_kind=excluded.page_kind,
                  status=excluded.status,
                  url=excluded.url,
                  url_hash=excluded.url_hash,
                  content_hash=excluded.content_hash,
                  title=excluded.title,
                  cleaned_markdown=excluded.cleaned_markdown,
                  metadata_json=excluded.metadata_json,
                  fetched_at=excluded.fetched_at,
                  parsed_at=excluded.parsed_at,
                  last_error=excluded.last_error
                """,
                (
                    run_id,
                    url,
                    url_canonical,
                    url_hash,
                    page_kind,
                    status,
                    content_hash,
                    title,
                    markdown,
                    json.dumps(metadata or {}),
                    datetime.now(timezone.utc),
                    datetime.now(timezone.utc),
                    error,
                ),
            )

    def upsert_model(self, model: ParsedModel, source_url: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into models(model_number, brand, appliance_type, source_url, updated_at)
                values (%s,%s,%s,%s,now())
                on conflict (model_number) do update set
                  brand=excluded.brand,
                  appliance_type=excluded.appliance_type,
                  source_url=excluded.source_url,
                  updated_at=now()
                returning id
                """,
                (model.model_number, model.brand, model.appliance_type, source_url),
            )
            return int(cur.fetchone()[0])

    def upsert_part(self, part: ParsedPart, source_url: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into parts(partselect_number, manufacturer_part_number, name, price_value, source_url, updated_at)
                values (%s,%s,%s,%s,%s,now())
                on conflict (partselect_number) do update set
                  manufacturer_part_number=coalesce(excluded.manufacturer_part_number, parts.manufacturer_part_number),
                  name=coalesce(excluded.name, parts.name),
                  price_value=coalesce(excluded.price_value, parts.price_value),
                  source_url=excluded.source_url,
                  updated_at=now()
                returning id
                """,
                (
                    part.partselect_number,
                    part.manufacturer_part_number,
                    part.name,
                    part.price_value,
                    source_url,
                ),
            )
            return int(cur.fetchone()[0])

    def upsert_model_part(self, model_id: int, part_id: int, source_url: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into model_parts(model_id, part_id, compatibility_confidence, source_url, updated_at)
                values (%s,%s,1.0,%s,now())
                on conflict (model_id, part_id) do update set
                  source_url=excluded.source_url,
                  updated_at=now()
                """,
                (model_id, part_id, source_url),
            )

    def insert_model_symptom(self, model_id: int, symptom: str, source_url: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into model_symptoms(model_id, symptom, source_url, updated_at)
                values (%s,%s,%s,now())
                on conflict (model_id, symptom) do update set updated_at=now(), source_url=excluded.source_url
                """,
                (model_id, symptom, source_url),
            )

    def insert_model_media(self, model_id: int, media_type: str, title: str, media_url: str, source_url: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into model_media(model_id, media_type, title, media_url, source_url, updated_at)
                values (%s,%s,%s,%s,%s,now())
                on conflict (model_id, media_type, media_url) do update set
                  title=excluded.title,
                  source_url=excluded.source_url,
                  updated_at=now()
                """,
                (model_id, media_type, title, media_url, source_url),
            )

    def insert_model_qa(self, model_id: int, question: str, answer: str, source_url: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into model_qa(model_id, question, answer, source_url, updated_at)
                values (%s,%s,%s,%s,now())
                on conflict (model_id, question, answer) do update set updated_at=now(), source_url=excluded.source_url
                """,
                (model_id, question, answer, source_url),
            )

    def persist_parsed_page(self, page: ParsedPage, source_url: str) -> None:
        if page.model:
            model_id = self.upsert_model(page.model, source_url)
            for part in page.model.parts:
                part_id = self.upsert_part(part, part.part_url or source_url)
                self.upsert_model_part(model_id, part_id, source_url)
            for s in page.model.symptoms:
                self.insert_model_symptom(model_id, s, source_url)
            for media_type, title, media_url in page.model.media:
                self.insert_model_media(model_id, media_type, title, media_url, source_url)
            for q, a in page.model.qa:
                self.insert_model_qa(model_id, q, a, source_url)

        if page.part:
            self.upsert_part(page.part, source_url)
