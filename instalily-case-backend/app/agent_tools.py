from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import List
from urllib.parse import parse_qs, urlparse

from .config import settings
from .mcp_browser import MCPBrowserRunner
from .models import CompatibilityResult, RetrievedDoc
from .retrieval import SampleRepository

TOKEN_RE = re.compile(r"[a-zA-Z0-9]{3,}")


@dataclass
class AgentToolbox:
    repo: SampleRepository

    @staticmethod
    def tool_schemas() -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "check_part_compatibility",
                    "description": "Check if a PartSelect part number is compatible with an appliance model.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "model_number": {"type": "string"},
                            "partselect_number": {"type": "string"},
                        },
                        "required": ["model_number", "partselect_number"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "crawl_partselect_live",
                    "description": "Live crawl PartSelect pages for fresh context via MCP browser automation. Prefer this for source-backed answers and model/part lookups.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "model_number": {"type": "string"},
                            "query": {"type": "string"},
                            "max_pages": {"type": "integer", "minimum": 1, "maximum": 5},
                        },
                        "required": ["url"],
                    },
                },
            },
        ]

    def check_part_compatibility(
        self, model_number: str, partselect_number: str
    ) -> CompatibilityResult:
        if settings.data_mode == "supabase":
            live = self._check_compatibility_supabase(model_number, partselect_number)
            if live:
                return live
        return self.repo.check_compatibility(model_number, partselect_number)

    def search_partselect_content(self, query: str, limit: int = 6) -> List[RetrievedDoc]:
        if settings.data_mode == "supabase":
            docs = self._search_supabase(query, limit)
            if docs:
                return docs
        return self.repo.retrieve(query, k=limit)

    def crawl_partselect_live(
        self, url: str, model_number: str = "", query: str = "", max_pages: int = 2
    ) -> List[RetrievedDoc]:
        if not settings.mcp_browser_enabled:
            raise RuntimeError("MCP browser is required. Set MCP_BROWSER_ENABLED=true.")
        resolved_query = self._extract_query_hint(url, model_number, query)
        runner = MCPBrowserRunner(
            command=settings.mcp_browser_command,
            args=settings.mcp_browser_args,
        )
        docs = asyncio.run(
            runner.run_live_lookup(url=url, query=resolved_query, max_pages=max(1, min(max_pages, 5)))
        )
        return [
            RetrievedDoc(
                url=str(d.get("url") or url),
                title=str(d.get("title") or "Live MCP source"),
                text=str(d.get("text") or "")[:4000],
                score=float(d.get("score") or 0.5),
            )
            for d in docs
        ]

    def _check_compatibility_supabase(
        self, model_number: str, partselect_number: str
    ) -> CompatibilityResult | None:
        if not settings.supabase_db_url:
            return None
        try:
            import psycopg  # type: ignore
        except Exception:
            return None

        sql = """
            select
              m.model_number,
              p.partselect_number,
              mp.compatibility_confidence,
              coalesce(mp.source_url, m.source_url, p.source_url) as source_url
            from model_parts mp
            join models m on m.id = mp.model_id
            join parts p on p.id = mp.part_id
            where m.model_number_norm = upper(regexp_replace(%s, '[^A-Za-z0-9]', '', 'g'))
              and p.partselect_number_norm = upper(regexp_replace(%s, '[^A-Za-z0-9]', '', 'g'))
            limit 1
        """
        with psycopg.connect(settings.supabase_db_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (model_number, partselect_number))
                row = cur.fetchone()

        if not row:
            return CompatibilityResult(
                model_number=model_number.upper(),
                partselect_number=partselect_number.upper(),
                compatible=False,
                confidence=0.45,
                source_url=None,
            )

        return CompatibilityResult(
            model_number=row[0],
            partselect_number=row[1],
            compatible=True,
            confidence=float(row[2] or 0.9),
            source_url=self._sanitize_source_url(row[3], row[0]),
        )

    def _search_supabase(self, query: str, limit: int) -> List[RetrievedDoc]:
        if not settings.supabase_db_url:
            return []
        try:
            import psycopg  # type: ignore
        except Exception:
            return []

        tokens = [t for t in TOKEN_RE.findall(query.lower())[:6]]
        if not tokens:
            return []

        conditions = " or ".join(["cleaned_markdown ilike %s"] * len(tokens))
        params = [f"%{t}%" for t in tokens] + [max(1, min(limit, 10))]
        sql = f"""
            select
              url,
              coalesce(title, '') as title,
              left(coalesce(cleaned_markdown, ''), 4000) as snippet
            from crawled_pages
            where status = 'parsed'
              and ({conditions})
            order by parsed_at desc nulls last
            limit %s
        """

        with psycopg.connect(settings.supabase_db_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        docs: List[RetrievedDoc] = []
        for idx, row in enumerate(rows):
            docs.append(
                RetrievedDoc(
                    url=row[0],
                    title=row[1] or "source",
                    text=row[2] or "",
                    score=max(0.1, 1.0 - idx * 0.05),
                )
            )
        return docs

    def _extract_query_hint(self, url: str, model_number: str = "", query: str = "") -> str:
        direct = (query or "").strip()
        if direct:
            return direct
        model = re.sub(r"[^A-Za-z0-9]", "", (model_number or "").upper())
        if model:
            return model
        try:
            parsed = urlparse(url or "")
            qs = parse_qs(parsed.query)
            for key in ("SearchTerm", "searchTerm", "q", "query"):
                values = qs.get(key) or []
                if values:
                    raw = str(values[0]).strip()
                    if raw:
                        return raw
        except Exception:
            return ""
        return ""

    @staticmethod
    def _sanitize_source_url(source_url: str | None, model_number: str = "") -> str | None:
        raw = (source_url or "").strip()
        model = re.sub(r"[^A-Za-z0-9]", "", (model_number or "").upper())
        fallback = f"https://www.partselect.com/Models/{model}/" if model else "https://www.partselect.com/"
        if not raw:
            return fallback
        try:
            parsed = urlparse(raw)
        except Exception:
            return fallback
        host = (parsed.hostname or "").lower()
        if host.endswith("partselect.com"):
            return raw
        return fallback
