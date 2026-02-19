from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

from .models import CompatibilityResult, RetrievedDoc

MODEL_RE = re.compile(r"\b([A-Z]{2,}\d[A-Z0-9]{4,})\b")
PS_RE = re.compile(r"\b(PS\d{6,})\b", re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-zA-Z0-9]{3,}")


@dataclass
class PageRecord:
    url: str
    title: str
    raw_path: Optional[Path]
    parsed: dict


class SampleRepository:
    def __init__(self, sample_dir: Path):
        self.sample_dir = sample_dir
        self.pages: List[PageRecord] = []
        self.model_to_parts: Dict[str, Set[str]] = {}
        self._load()

    def _load(self) -> None:
        manifest_path = self.sample_dir / "manifest.json"
        if not manifest_path.exists():
            return

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for page in manifest.get("pages", []):
            url = page.get("url", "")
            slug = self._slug(url)
            raw_path = self.sample_dir / "raw" / f"{slug}.md"
            if not raw_path.exists():
                raw_path = None

            record = PageRecord(
                url=url,
                title=page.get("title", ""),
                raw_path=raw_path,
                parsed=page,
            )
            self.pages.append(record)

            model = page.get("model_number_from_url")
            parts = {p.upper() for p in page.get("partselect_numbers", [])}
            if model and parts:
                self.model_to_parts.setdefault(model.upper(), set()).update(parts)

    @staticmethod
    def _slug(url: str) -> str:
        path = re.sub(r"^https?://", "", url)
        path = path.split("/", 1)[1] if "/" in path else path
        path = path.strip("/") or "home"
        path = path.replace("/", "__")
        return re.sub(r"[^a-zA-Z0-9_\-]", "_", path)[:120]

    def extract_entities(self, text: str) -> tuple[Optional[str], Optional[str]]:
        model = None
        ps = None
        for candidate in MODEL_RE.findall(text.upper()):
            if not candidate.startswith("PS"):
                model = candidate
                break
        p = PS_RE.search(text.upper())
        if p:
            ps = p.group(1).upper()
        return model, ps

    def check_compatibility(self, model: str, ps: str) -> CompatibilityResult:
        normalized_model = model.upper()
        normalized_ps = ps.upper()
        known = normalized_ps in self.model_to_parts.get(normalized_model, set())

        source = None
        for page in self.pages:
            model_from_page = (page.parsed.get("model_number_from_url") or "").upper()
            if model_from_page == normalized_model:
                source = page.url
                break

        return CompatibilityResult(
            model_number=normalized_model,
            partselect_number=normalized_ps,
            compatible=known,
            confidence=0.98 if known else 0.55,
            source_url=source,
        )

    def retrieve(self, query: str, k: int = 6) -> List[RetrievedDoc]:
        q_tokens = set(t.lower() for t in TOKEN_RE.findall(query))
        docs: List[RetrievedDoc] = []

        for page in self.pages:
            text = ""
            if page.raw_path and page.raw_path.exists():
                text = page.raw_path.read_text(encoding="utf-8")
            if not text:
                continue

            tokens = set(t.lower() for t in TOKEN_RE.findall(text[:10000]))
            overlap = len(q_tokens.intersection(tokens))
            score = overlap / (len(q_tokens) + 1)
            if score <= 0:
                continue

            docs.append(
                RetrievedDoc(
                    url=page.url,
                    title=page.title,
                    text=text[:4000],
                    score=score,
                )
            )

        docs.sort(key=lambda d: d.score, reverse=True)
        return docs[:k]
