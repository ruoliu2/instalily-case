from __future__ import annotations

import json
import re
from typing import Any, Dict, Generator, List

from openai import OpenAI

from .agent_tools import AgentToolbox
from .config import settings
from .models import ChatResponse, Citation, ToolTrace
from .prompt_templates import INSTANCE_TEMPLATE, SYSTEM_TEMPLATE
from .retrieval import SampleRepository


class MainAgent:
    def __init__(self, repo: SampleRepository, tools: AgentToolbox):
        self.repo = repo
        self.tools = tools
        self.step_limit = 6
        self.client = OpenAI(
            base_url=settings.openai_base_url, api_key=settings.openai_api_key
        )

    @staticmethod
    def _ensure_citations(citations: List[Citation]) -> List[Citation]:
        deduped: List[Citation] = []
        seen: set[str] = set()
        for c in citations:
            if not c.url or c.url in seen:
                continue
            seen.add(c.url)
            deduped.append(c)
        return deduped

    @staticmethod
    def _extract_model_number(message: str) -> str:
        explicit = re.search(
            r"\bmodel(?:\s+number)?\s*[:#-]?\s*([A-Za-z0-9-]{5,})\b",
            message,
            flags=re.I,
        )
        if explicit:
            token = explicit.group(1)
            if re.search(r"[A-Za-z]", token) and re.search(r"\d", token):
                return token.upper()
        candidates = re.findall(r"\b[A-Za-z0-9-]{6,}\b", message)
        for token in candidates:
            up = token.upper()
            if up.startswith("PS"):
                continue
            if re.search(r"[A-Za-z]", up) and re.search(r"\d", up):
                return up
        return ""

    @staticmethod
    def _extract_partselect_number(message: str) -> str:
        m = re.search(r"\b(PS\d{5,})\b", message, flags=re.I)
        return m.group(1).upper() if m else ""

    @staticmethod
    def _extract_url(message: str) -> str:
        m = re.search(r"(https?://[^\s]+)", message)
        return m.group(1) if m else ""

    @staticmethod
    def _wants_live_lookup(message: str) -> bool:
        m = message.lower()
        return any(
            k in m
            for k in [
                "live",
                "latest",
                "current",
                "right now",
                "real-time",
                "real time",
                "open ",
                "check page",
            ]
        ) or "http://" in m or "https://" in m

    @staticmethod
    def _strip_markdown_noise(text: str) -> str:
        s = (text or "").strip()
        if not s:
            return ""
        # image markdown first
        s = re.sub(r"!\[([^\]]*)\]\(([^)]*)\)", r"\1", s)
        # links [label](url) -> label
        s = re.sub(r"\[([^\]]+)\]\(([^)]*)\)", r"\1", s)
        # bare markdown tokens / heading markers
        s = re.sub(r"[`*_>#]+", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    @staticmethod
    def _citation_snippet(text: str, limit: int = 220) -> str:
        if not text:
            return ""
        compact_lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        if not compact_lines:
            return ""
        for line in compact_lines:
            # Prefer readable plain text over markdown/link syntax.
            cleaned = MainAgent._strip_markdown_noise(line.lstrip("#>*- ").strip())
            if len(cleaned) >= 24:
                return cleaned[:limit] + ("..." if len(cleaned) > limit else "")
        fallback = MainAgent._strip_markdown_noise(compact_lines[0].lstrip("#>*- ").strip())
        return fallback[:limit] + ("..." if len(fallback) > limit else "")

    @staticmethod
    def _safe_json_loads(raw: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(raw or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _infer_intent_from_tools(tool_names: List[str]) -> str:
        if "check_part_compatibility" in tool_names:
            return "compatibility"
        if "crawl_partselect_live" in tool_names:
            return "live_lookup"
        if "search_partselect_content" in tool_names:
            return "general_parts_help"
        return "general_parts_help"

    @staticmethod
    def _infer_intent_from_message(message: str) -> str:
        m = message.lower()
        if "fit" in m or "compatible" in m or ("ps" in m and "model" in m):
            return "compatibility"
        if "install" in m or "replace" in m:
            return "installation"
        if "not" in m or "broken" in m or "symptom" in m or "drain" in m:
            return "troubleshoot"
        return "general_parts_help"

    @staticmethod
    def _should_retrieve_context(message: str) -> bool:
        m = message.strip().lower()
        if len(m) < 8:
            return False
        if any(tok in m for tok in ["ps", "model", "compatible", "install", "replace", "symptom", "drain", "broken", "dishwasher", "refrigerator"]):
            return True
        return any(ch.isdigit() for ch in m)

    @staticmethod
    def _fallback_title(history: List[Dict[str, Any]]) -> str:
        for msg in history:
            content = str(msg.get("content", "")).strip()
            if content:
                compact = re.sub(r"\s+", " ", content)
                return compact[:40] + ("..." if len(compact) > 40 else "")
        return "New Chat"

    @staticmethod
    def _normalize_title(title: str) -> str:
        cleaned = re.sub(r"\s+", " ", (title or "").strip()).strip("`'\" ")
        if not cleaned:
            return "New Chat"
        return cleaned[:50]

    def summarize_title(self, history: List[Dict[str, Any]]) -> str:
        short_history: List[str] = []
        for msg in history[-10:]:
            role = str(msg.get("role", "")).strip() or "user"
            content = re.sub(r"\s+", " ", str(msg.get("content", "")).strip())
            if not content:
                continue
            short_history.append(f"{role}: {content[:500]}")
        if not short_history:
            return "New Chat"

        try:
            resp = self.client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Generate a concise chat title summary in 3-7 words. "
                            "Use plain text only. No quotes, no punctuation at the end."
                        ),
                    },
                    {
                        "role": "user",
                        "content": "Conversation:\n" + "\n".join(short_history),
                    },
                ],
                temperature=0.2,
            )
            text = (resp.choices[0].message.content or "").splitlines()[0]
            return self._normalize_title(text)
        except Exception:
            return self._fallback_title(history)

    def run_sync_from_stream(self, message: str) -> ChatResponse:
        answer_parts: List[str] = []
        intent = "general_parts_help"
        confidence = 0.62
        citations: List[Citation] = []
        traces: List[ToolTrace] = []

        for event in self.run_stream(message):
            etype = str(event.get("type", ""))
            if etype == "token":
                answer_parts.append(str(event.get("content", "")))
            elif etype == "done":
                intent = str(event.get("intent", intent))
                confidence = float(event.get("confidence", confidence))
                raw_citations = event.get("citations", []) or []
                raw_traces = event.get("traces", []) or []
                citations = [Citation(**c) for c in raw_citations if isinstance(c, dict)]
                traces = [ToolTrace(**t) for t in raw_traces if isinstance(t, dict)]

        answer = "".join(answer_parts).strip()
        if not answer:
            answer = "I can help with compatibility, troubleshooting, installation, or part search."
        return ChatResponse(
            answer=answer,
            intent=intent,
            confidence=confidence,
            citations=self._ensure_citations(citations),
            traces=traces,
        )

    def run_stream(self, message: str) -> Generator[Dict[str, Any], None, None]:
        traces: List[ToolTrace] = [ToolTrace(step="data_mode", detail=settings.data_mode)]
        citations: List[Citation] = []
        tool_names_used: List[str] = []
        used_live_tool = False
        final_text = ""
        tool_payloads: List[dict] = []

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_TEMPLATE},
            {"role": "user", "content": INSTANCE_TEMPLATE.format(message=message)},
        ]

        yield {
            "type": "thinking_step",
            "status": "running",
            "text": "Understanding request",
        }

        for step in range(1, self.step_limit + 1):
            traces.append(ToolTrace(step="llm_step", detail=str(step)))
            planning_step_index = step

            resp = self.client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                tools=self.tools.tool_schemas(),
                tool_choice="auto",
                temperature=0.2,
            )
            assistant_msg = resp.choices[0].message
            content = assistant_msg.content or ""
            tool_calls = assistant_msg.tool_calls or []

            if tool_calls:
                first = tool_calls[0]
                fn = first.function.name
                args = self._safe_json_loads(first.function.arguments)
                # Visible tool-decision trace (not hidden chain-of-thought).
                plan_text = f"Planning step {planning_step_index}: next tool `{fn}` with args {json.dumps(args, ensure_ascii=True)}"
            else:
                plan_text = f"Planning step {planning_step_index}: preparing final response"
            yield {
                "type": "thinking_step",
                "status": "running",
                "text": f"Planning step {planning_step_index}",
            }

            assistant_message: Dict[str, Any] = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_message["tool_calls"] = [tc.model_dump() for tc in tool_calls]
            messages.append(assistant_message)

            if not tool_calls:
                if self._wants_live_lookup(message) and not used_live_tool:
                    yield {
                        "type": "thinking_step",
                        "status": "running",
                        "text": "Draft answer ready; adding live verification",
                    }
                else:
                    yield {
                        "type": "thinking_step",
                        "status": "done",
                        "text": "No tool call needed; composing final response",
                    }
                final_text = content.strip()
                break

            for tc in tool_calls:
                name = tc.function.name
                args = self._safe_json_loads(tc.function.arguments)
                tool_names_used.append(name)
                traces.append(ToolTrace(step="tool_call", detail=name))
                if name in ("crawl_partselect_live", "search_partselect_content"):
                    yield {
                        "type": "thinking_step",
                        "status": "running",
                        "text": "Running and summarizing web data",
                        "domain": "www.partselect.com",
                    }
                yield {
                    "type": "thinking_step",
                    "status": "running",
                    "text": f"Running tool: {name}",
                    "domain": "www.partselect.com",
                }

                if name == "check_part_compatibility":
                    result = self.tools.check_part_compatibility(
                        model_number=str(args.get("model_number", "")),
                        partselect_number=str(args.get("partselect_number", "")),
                    )
                    payload = result.model_dump()
                    if payload.get("source_url"):
                        citations.append(
                            Citation(
                                url=payload["source_url"],
                                title="Compatibility source",
                                snippet="Compatibility lookup source page.",
                            )
                        )
                elif name == "search_partselect_content":
                    docs = self.tools.search_partselect_content(
                        query=str(args.get("query", message)),
                        limit=int(args.get("limit", 6)),
                    )
                    payload = {"count": len(docs), "results": [d.model_dump() for d in docs]}
                    for d in docs[:3]:
                        citations.append(
                            Citation(
                                url=d.url,
                                title=d.title or d.url,
                                snippet=self._citation_snippet(d.text),
                            )
                        )
                elif name == "crawl_partselect_live":
                    used_live_tool = True
                    docs = self.tools.crawl_partselect_live(
                        url=str(args.get("url", "https://www.partselect.com/")),
                        model_number=str(args.get("model_number", "")),
                        query=str(args.get("query", message)),
                        max_pages=int(args.get("max_pages", 2)),
                    )
                    payload = {"count": len(docs), "results": [d.model_dump() for d in docs]}
                    for d in docs[:3]:
                        citations.append(
                            Citation(
                                url=d.url,
                                title=d.title or d.url,
                                snippet=self._citation_snippet(d.text),
                            )
                        )
                else:
                    payload = {"error": f"unknown_tool:{name}"}
                tool_payloads.append({"tool": name, "args": args, "result": payload})

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(payload, ensure_ascii=True),
                    }
                )
                yield {
                    "type": "thinking_step",
                    "status": "done",
                    "text": f"Done: {name}",
                }

        if self._wants_live_lookup(message) and not used_live_tool:
            model_number = self._extract_model_number(message)
            part_number = self._extract_partselect_number(message)
            source_url = self._extract_url(message) or "https://www.partselect.com/"
            query = part_number or model_number or message
            traces.append(ToolTrace(step="tool_call", detail="crawl_partselect_live(forced)"))
            yield {
                "type": "thinking_step",
                "status": "running",
                "text": "Running tool: crawl_partselect_live (required for live check)",
                "domain": "www.partselect.com",
            }
            try:
                docs = self.tools.crawl_partselect_live(
                    url=source_url,
                    model_number=model_number,
                    query=query,
                    max_pages=2,
                )
                tool_names_used.append("crawl_partselect_live")
                if docs:
                    for d in docs[:3]:
                        citations.append(
                            Citation(
                                url=d.url,
                                title=d.title or d.url,
                                snippet=self._citation_snippet(d.text),
                            )
                        )
                    final_text = (
                        (final_text + "\n\n") if final_text else ""
                    ) + f"Live page check pulled {len(docs)} source page(s)."
                else:
                    final_text = (
                        (final_text + "\n\n") if final_text else ""
                    ) + "Live page check returned no results."
            except Exception as exc:
                traces.append(ToolTrace(step="tool_error", detail=f"crawl_partselect_live(forced): {exc}"))
                final_text = (
                    (final_text + "\n\n") if final_text else ""
                ) + "Live page check failed. Please verify MCP browser settings."
            yield {
                "type": "thinking_step",
                "status": "done",
                "text": "Done: crawl_partselect_live (required)",
            }

        # Final synthesis uses Responses API so UI can receive native reasoning events.
        synthesis_prompt = (
            "User request:\n"
            f"{message}\n\n"
            "Tool results (JSON):\n"
            f"{json.dumps(tool_payloads, ensure_ascii=True)[:12000]}\n\n"
            "Draft answer (if any):\n"
            f"{final_text}\n\n"
            "Write a clear final answer with sources when available."
        )
        saw_output = False
        try:
            stream = self.client.responses.create(
                model=settings.openai_model,
                instructions=SYSTEM_TEMPLATE,
                input=synthesis_prompt,
                stream=True,
                temperature=0.2,
                reasoning={"effort": "medium"},
            )
            for event in stream:
                etype = getattr(event, "type", "")
                if etype == "response.reasoning_summary_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        yield {"type": "thinking_token", "content": delta}
                elif etype == "response.output_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        saw_output = True
                        final_text += delta
                        yield {"type": "token", "content": delta}
                elif etype == "response.error":
                    err = event.model_dump() if hasattr(event, "model_dump") else {}
                    msg = str(err.get("error", "stream_error")) if isinstance(err, dict) else "stream_error"
                    yield {"type": "token", "content": f"\n\nError: {msg}"}
        except Exception:
            pass

        if not saw_output:
            if not final_text:
                final_text = "I can help with compatibility, troubleshooting, installation, or part search."
            yield {"type": "token", "content": final_text}
        intent = self._infer_intent_from_tools(tool_names_used) if tool_names_used else self._infer_intent_from_message(message)
        confidence = 0.82 if tool_names_used else 0.62
        yield {
            "type": "done",
            "intent": intent,
            "confidence": confidence,
            "citations": [c.model_dump() for c in self._ensure_citations(citations)],
            "traces": [t.model_dump() for t in traces],
        }
