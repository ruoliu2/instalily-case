from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig

from .config import IngestionConfig
from .parser import canonicalize_url, is_core_url, parse_page
from .store import Store


CRAWL_CONFIGS = [
    ("baseline", CrawlerRunConfig(word_count_threshold=1, exclude_external_links=True, exclude_social_media_links=True, remove_overlay_elements=True, verbose=False)),
    ("wait_body", CrawlerRunConfig(wait_for="body", word_count_threshold=1, exclude_external_links=True, exclude_social_media_links=True, remove_overlay_elements=True, verbose=False)),
    ("selector_article", CrawlerRunConfig(css_selector="article, main, body", wait_for="body", word_count_threshold=1, exclude_external_links=True, exclude_social_media_links=True, remove_overlay_elements=True, verbose=False)),
]


@dataclass
class CrawlStats:
    queued: int = 0
    fetched: int = 0
    parsed: int = 0
    failed: int = 0
    discovered: int = 0


@dataclass
class IngestionService:
    cfg: IngestionConfig
    store: Store
    run_id: str
    stats: CrawlStats = field(default_factory=CrawlStats)

    def enqueue(self, url: str, force_requeue: bool = False) -> None:
        canonical = canonicalize_url(url)
        if not canonical:
            return
        if not is_core_url(canonical):
            return
        self.store.upsert_frontier_queued(
            canonical,
            self.run_id,
            source_url=url,
            force_requeue=force_requeue,
        )
        self.stats.queued += 1

    async def crawl_best(self, crawler: AsyncWebCrawler, url: str):
        best = None
        for strategy, cfg in CRAWL_CONFIGS:
            try:
                result = await crawler.arun(url=url, config=cfg)
                markdown = result.markdown or ""
                candidate = {
                    "strategy": strategy,
                    "success": bool(result.success),
                    "markdown": markdown,
                    "metadata": result.metadata or {},
                    "title": (result.metadata or {}).get("title", ""),
                    "word_count": len(markdown.split()),
                    "error": result.error_message if not result.success else None,
                }
                if best is None or candidate["word_count"] > best["word_count"]:
                    best = candidate
            except Exception as exc:
                candidate = {
                    "strategy": strategy,
                    "success": False,
                    "markdown": "",
                    "metadata": {},
                    "title": "",
                    "word_count": 0,
                    "error": str(exc),
                }
                if best is None:
                    best = candidate

        return best

    async def worker(self, crawler: AsyncWebCrawler, end_time: float) -> None:
        idle_ticks = 0
        while time.monotonic() < end_time and self.stats.fetched < self.cfg.max_pages:
            url = self.store.claim_next_frontier_url(self.run_id)
            if not url:
                idle_ticks += 1
                pending = self.store.count_frontier_pending()
                if pending == 0 or idle_ticks >= 5:
                    return
                await asyncio.sleep(1.0)
                continue
            idle_ticks = 0
            best = await self.crawl_best(crawler, url)
            self.stats.fetched += 1

            if not best or not best.get("success"):
                self.stats.failed += 1
                self.store.upsert_crawled_page(
                    run_id=self.run_id,
                    url=url,
                    page_kind="other",
                    status="failed",
                    markdown="",
                    title=(best or {}).get("title", ""),
                    metadata=(best or {}).get("metadata", {}),
                    error=(best or {}).get("error", "crawl_failed"),
                )
                self.store.mark_frontier_failed(url, self.run_id, (best or {}).get("error", "crawl_failed"))
                continue

            markdown = best["markdown"]
            title = best["title"]
            parsed = parse_page(url, markdown, title)

            self.store.upsert_crawled_page(
                run_id=self.run_id,
                url=url,
                page_kind=parsed.page_kind,
                status="parsed",
                markdown=markdown if self.cfg.save_markdown else "",
                title=title,
                metadata={"strategy": best["strategy"], "word_count": best["word_count"]},
            )
            self.store.persist_parsed_page(parsed, source_url=url)
            self.store.mark_frontier_done(url, self.run_id)
            self.stats.parsed += 1

            for durl in parsed.discovered_urls:
                if is_core_url(durl):
                    self.enqueue(durl)
                    self.stats.discovered += 1

    async def run(self) -> CrawlStats:
        end_time = time.monotonic() + (self.cfg.max_runtime_hours * 3600)
        self.store.reconcile_frontier_for_resume()

        for s in self.cfg.seed_urls:
            self.enqueue(s, force_requeue=self.cfg.requeue_seeds_on_start)

        async with AsyncWebCrawler(verbose=False) as crawler:
            workers = [
                asyncio.create_task(self.worker(crawler, end_time))
                for _ in range(self.cfg.crawl_concurrency)
            ]
            await asyncio.gather(*workers)

        return self.stats


def run_ingestion(cfg: IngestionConfig) -> CrawlStats:
    store = Store.from_dsn(cfg.supabase_db_url)
    try:
        store.apply_schema(Path(__file__).resolve().parents[1] / "sql" / "001_ingestion_core.sql")
        run_id = store.begin_run(mode="prefetch", notes=f"parallel={cfg.crawl_concurrency} runtime_hours={cfg.max_runtime_hours}")
        service = IngestionService(cfg=cfg, store=store, run_id=run_id)

        try:
            stats = asyncio.run(service.run())
            store.end_run(run_id, "done")
            return stats
        except Exception:
            store.end_run(run_id, "failed")
            raise
    finally:
        store.close()
