from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from .config import settings
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
                    "name": "search_partselect_content",
                    "description": "Search indexed PartSelect website content for context snippets.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                        },
                        "required": ["query"],
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
            source_url=row[3],
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
