from __future__ import annotations

import re
from typing import List

from openai import OpenAI

from .agent_tools import AgentToolbox
from .config import settings
from .models import ChatResponse, Citation, ToolTrace
from .retrieval import SampleRepository


class MainAgent:
    def __init__(self, repo: SampleRepository, tools: AgentToolbox):
        self.repo = repo
        self.tools = tools
        self.client = None
        if settings.use_llm:
            self.client = OpenAI(
                base_url=settings.openai_base_url, api_key=settings.openai_api_key
            )

    @staticmethod
    def detect_intent(message: str) -> str:
        m = message.lower()
        if "fit" in m or "compatible" in m or "ps" in m and "model" in m:
            return "compatibility"
        if "install" in m or "replace" in m:
            return "installation"
        if "not" in m or "broken" in m or "symptom" in m or "drain" in m:
            return "troubleshoot"
        return "general_parts_help"

    def _build_llm_answer(
        self, message: str, intent: str, context_blocks: List[str]
    ) -> str:
        if not self.client:
            return ""

        context_str = (
            "\n\n---\n\n".join(context_blocks[:6])
            if context_blocks
            else "No specific context available for this query."
        )
        prompt = (
            "You are a PartSelect appliance parts assistant for dishwasher and refrigerator only. "
            "Be helpful and conversational. If you don't have specific context, still try to help the user. "
            f"Intent: {intent}\n"
            f"User: {message}\n\n"
            "Context:\n" + context_str
        )
        resp = self.client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful appliance parts assistant. Be friendly and concise.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
        )
        return (resp.choices[0].message.content or "").strip()

    @staticmethod
    def _summarize_doc(text: str, max_len: int = 180) -> str:
        cleaned = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
        cleaned = re.sub(r"[#>*_`]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return ""

        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        candidate = next((s for s in sentences if len(s) > 30), cleaned)
        if len(candidate) <= max_len:
            return candidate

        clipped = candidate[:max_len].rsplit(" ", 1)[0].strip()
        if not clipped.endswith((".", "!", "?")):
            clipped += "."
        return clipped

    def _build_structured_model_answer(self, model: str) -> str | None:
        model_upper = model.upper()
        for page in self.repo.pages:
            page_model = (page.parsed.get("model_number_from_url") or "").upper()
            if page_model != model_upper:
                continue

            part_numbers = page.parsed.get("partselect_numbers", [])[:8]
            prices = page.parsed.get("prices", [])[:6]
            headings = [
                h for h in page.parsed.get("headings", []) if "overview" not in h.lower()
            ][:3]

            lines = [f"I found model `{model_upper}` in the indexed catalog."]
            if part_numbers:
                lines.append(
                    "Compatible part numbers in the index include: "
                    + ", ".join(f"`{p}`" for p in part_numbers)
                    + "."
                )
            if prices:
                lines.append("Observed price points on indexed pages: " + ", ".join(prices) + ".")
            if headings:
                lines.append("Helpful sections available: " + "; ".join(headings) + ".")
            lines.append("Tell me your symptom or part number and I can narrow this down.")
            return "\n\n".join(lines)
        return None

    def _get_model_page_url(self, model: str) -> str | None:
        model_upper = model.upper()
        for page in self.repo.pages:
            page_model = (page.parsed.get("model_number_from_url") or "").upper()
            if page_model == model_upper:
                return page.url
        return None

    def _ensure_citations(
        self,
        message: str,
        citations: List[Citation],
        model: str | None = None,
    ) -> List[Citation]:
        deduped: List[Citation] = []
        seen: set[str] = set()
        for c in citations:
            if not c.url or c.url in seen:
                continue
            seen.add(c.url)
            deduped.append(c)

        if model:
            model_url = self._get_model_page_url(model)
            if model_url and model_url not in seen:
                deduped.insert(0, Citation(url=model_url, title="Model page"))
                seen.add(model_url)

        if deduped:
            return deduped

        fallback_docs = self.tools.search_partselect_content(message, limit=1)
        if fallback_docs:
            first = fallback_docs[0]
            return [Citation(url=first.url, title=first.title or first.url)]

        return [Citation(url="https://www.partselect.com/", title="PartSelect")]

    def _build_fallback_answer(self, message: str, intent: str, docs: List) -> str:
        model, _ = self.repo.extract_entities(message)
        if model:
            model_answer = self._build_structured_model_answer(model)
            if model_answer:
                return model_answer

        if not docs:
            return (
                "I could not find enough indexed context yet. Share a model number (for example WDT780SAEM1) "
                "or part number (for example PS11750093), and I can run a focused lookup."
            )

        preface = {
            "installation": "I found installation-related information in the index:",
            "troubleshoot": "I found troubleshooting information in the index:",
            "general_parts_help": "I found relevant part information in the index:",
        }.get(intent, "I found relevant information in the index:")

        points = []
        for idx, doc in enumerate(docs[:3], start=1):
            summary = self._summarize_doc(doc.text)
            if summary:
                points.append(f"{idx}. {summary}")

        if not points:
            return preface
        return preface + "\n\n" + "\n".join(points)

    def run(self, message: str) -> ChatResponse:
        traces: List[ToolTrace] = []
        intent = self.detect_intent(message)
        traces.append(ToolTrace(step="intent", detail=intent))
        traces.append(ToolTrace(step="data_mode", detail=settings.data_mode))

        model, ps = self.repo.extract_entities(message)
        if model:
            traces.append(ToolTrace(step="extract_model", detail=model))
        if ps:
            traces.append(ToolTrace(step="extract_part", detail=ps))

        citations: List[Citation] = []
        confidence = 0.7

        if intent == "compatibility" and model and ps:
            result = self.tools.check_part_compatibility(model, ps)
            traces.append(
                ToolTrace(
                    step="check_compatibility",
                    detail=f"compatible={result.compatible} confidence={result.confidence}",
                )
            )
            confidence = result.confidence
            if result.source_url:
                citations.append(Citation(url=result.source_url, title="Model page"))

            if result.compatible:
                answer = (
                    f"Yes, `{result.partselect_number}` appears compatible with model `{result.model_number}` "
                    f"based on indexed PartSelect model data."
                )
            else:
                answer = (
                    f"I could not confirm `{result.partselect_number}` as compatible with `{result.model_number}` "
                    "from the current index. I can run a targeted live crawl fallback if you want."
                )
            return ChatResponse(
                answer=answer,
                intent=intent,
                confidence=confidence,
                citations=self._ensure_citations(message, citations, model),
                traces=traces,
            )

        docs = self.tools.search_partselect_content(message, limit=6)
        traces.append(ToolTrace(step="search_partselect_content", detail=f"count={len(docs)}"))

        if not self.client:
            fallback_citations: List[Citation] = []
            for d in docs[:3]:
                fallback_citations.append(Citation(url=d.url, title=d.title or d.url))
            model_url = self._get_model_page_url(model) if model else None
            if model_url and all(c.url != model_url for c in fallback_citations):
                fallback_citations.insert(0, Citation(url=model_url, title="Model page"))

            answer = self._build_fallback_answer(message, intent, docs)
            return ChatResponse(
                answer=answer,
                intent=intent,
                confidence=0.72 if docs else 0.58,
                citations=self._ensure_citations(message, fallback_citations, model),
                traces=traces,
            )

        for d in docs[:3]:
            citations.append(Citation(url=d.url, title=d.title or d.url))

        context_blocks = [f"URL: {d.url}\n{d.text}" for d in docs] if docs else []
        answer = self._build_llm_answer(message, intent, context_blocks)
        confidence = 0.75 if docs else 0.6

        return ChatResponse(
            answer=answer,
            intent=intent,
            confidence=confidence,
            citations=self._ensure_citations(message, citations, model),
            traces=traces,
        )
