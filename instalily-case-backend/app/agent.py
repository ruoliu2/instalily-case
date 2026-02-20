from __future__ import annotations

import json
import re
import threading
from typing import Any, Dict, Generator, List
from urllib.parse import urlparse

from openai import OpenAI

from .agent_tools import AgentToolbox
from .config import settings
from .models import ChatResponse, Citation, ToolTrace
from .prompt_templates import INSTANCE_TEMPLATE, SYSTEM_TEMPLATE
from .retrieval import SampleRepository


class InterruptAgentFlow(Exception):
    def __init__(self, message: Dict[str, Any]):
        super().__init__(str(message.get("content", "InterruptAgentFlow")))
        self.message_payload = message


class Submitted(InterruptAgentFlow):
    pass


class LimitsExceeded(InterruptAgentFlow):
    pass


class MainAgent:
    def __init__(self, repo: SampleRepository, tools: AgentToolbox):
        self.repo = repo
        self.tools = tools
        self.step_limit = 6
        self.client = OpenAI(
            base_url=settings.openai_base_url, api_key=settings.openai_api_key
        )
        self._cancel_lock = threading.Lock()
        self._cancelled_runs: set[str] = set()

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
    def _normalize_partselect_live_args(args: Dict[str, Any], message: str) -> Dict[str, Any]:
        safe = dict(args or {})
        raw_url = str(safe.get("url", "")).strip()
        parsed = urlparse(raw_url) if raw_url else None
        host = (parsed.hostname or "").lower() if parsed else ""

        # Prevent speculative/offsite deep links; default to PartSelect home and let MCP interact with page UI.
        if not raw_url or not host.endswith("partselect.com"):
            safe["url"] = "https://www.partselect.com/"

        model = str(safe.get("model_number", "")).strip().upper()
        if not model:
            model = MainAgent._extract_model_number(message)
            if model:
                safe["model_number"] = model

        # If model is known, anchor to canonical model page unless URL is already a model/part detail page.
        current_url_raw = str(safe.get("url", "")).strip()
        current_url = current_url_raw.lower()
        model_token = model.lower()
        looks_model_page = f"/models/{model_token}/" in current_url or f"/models/{model_token}" in current_url
        looks_part_page = "/ps" in current_url and current_url.endswith(".htm")
        if model and not looks_model_page and not looks_part_page:
            safe["url"] = f"https://www.partselect.com/Models/{model}/"

        query = str(safe.get("query", "")).strip()
        if not query:
            part = MainAgent._extract_partselect_number(message)
            safe["query"] = part or model or message

        try:
            mp = int(safe.get("max_pages", 2))
        except Exception:
            mp = 2
        safe["max_pages"] = max(1, min(mp, 6))
        return safe

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
                "source",
                "sources",
                "link",
                "links",
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
    def _doc_is_access_denied(doc: Any) -> bool:
        title = str(getattr(doc, "title", "") or "").lower()
        text = str(getattr(doc, "text", "") or "").lower()
        return "access denied" in title or "access denied" in text

    def _docs_are_access_denied(self, docs: List[Any]) -> bool:
        return bool(docs) and all(self._doc_is_access_denied(d) for d in docs)

    @staticmethod
    def _tool_result_check_text(name: str, payload: Dict[str, Any]) -> str:
        if name == "check_part_compatibility":
            compatible = bool(payload.get("compatible"))
            confidence = payload.get("confidence")
            source = payload.get("source_url")
            return (
                f"Result check: compatible={compatible}, confidence={confidence}, "
                f"source={'present' if source else 'missing'}."
            )
        if name == "crawl_partselect_live":
            if payload.get("error") == "access_denied_by_target":
                return "Result check: live crawl blocked by target site (Access Denied)."
            return f"Result check: live crawl returned {int(payload.get('count') or 0)} page(s)."
        if name == "search_partselect_content":
            return f"Result check: indexed search returned {int(payload.get('count') or 0)} result(s)."
        if payload.get("error"):
            return f"Result check: tool error {payload.get('error')}."
        return "Result check: tool completed."

    @staticmethod
    def _tool_context_line(name: str, args: Dict[str, Any], payload: Dict[str, Any]) -> str:
        args_s = json.dumps(args or {}, ensure_ascii=True)
        if name == "check_part_compatibility":
            return (
                f"- {name} args={args_s} => compatible={bool(payload.get('compatible'))}, "
                f"confidence={payload.get('confidence')}, source_url={'yes' if payload.get('source_url') else 'no'}"
            )
        if name == "crawl_partselect_live":
            if payload.get("error") == "access_denied_by_target":
                return f"- {name} args={args_s} => blocked=access_denied_by_target"
            hint = MainAgent._crawl_query_hint_from_payload(payload)
            urls: list[str] = []
            for r in (payload.get("results", []) if isinstance(payload, dict) else [])[:2]:
                u = str((r or {}).get("url", "")).strip()
                if u:
                    urls.append(u)
            suffix = f", input_hint={hint}" if hint else ""
            url_suffix = f", urls={urls}" if urls else ""
            return f"- {name} args={args_s} => pages={int(payload.get('count') or 0)}{suffix}{url_suffix}"
        if payload.get("error"):
            return f"- {name} args={args_s} => error={payload.get('error')}"
        return f"- {name} args={args_s} => done"

    @staticmethod
    def _crawl_call_key(args: Dict[str, Any]) -> str:
        url = str(args.get("url", "")).strip().lower()
        model = str(args.get("model_number", "")).strip().upper()
        query = str(args.get("query", "")).strip().upper()
        return f"url={url}|model={model}|query={query}"

    @staticmethod
    def _crawl_query_hint_from_payload(payload: Dict[str, Any]) -> str:
        try:
            results = payload.get("results", []) if isinstance(payload, dict) else []
            for r in results[:2]:
                text = str((r or {}).get("text", "")).lower()
                if not text:
                    continue
                if "part # or model #" in text or "part number or model number" in text:
                    return "search box expects part/model id token"
        except Exception:
            return ""
        return ""

    @staticmethod
    def _iter_text_chunks(text: str, target_size: int = 80) -> Generator[str, None, None]:
        s = (text or "").strip()
        if not s:
            return
        while s:
            if len(s) <= target_size:
                yield s
                return
            cut = s.rfind(" ", 0, target_size + 1)
            if cut <= 0:
                cut = target_size
            chunk = s[:cut]
            yield chunk
            s = s[cut:].lstrip()

    @staticmethod
    def _fallback_from_collected_context(
        *,
        message: str,
        citations: List[Citation],
        compatibility_confirmed: bool,
        observed_part_ids: List[str] | None = None,
    ) -> str:
        parts = [p for p in (observed_part_ids or []) if p]
        asks_part_list = "list all parts" in message.lower() or "all parts compatible" in message.lower()
        if asks_part_list and parts:
            lines = [
                "I found compatible part IDs from the model pages crawled so far:",
                "",
            ]
            for p in parts[:60]:
                lines.append(f"- {p}")
            if len(parts) > 60:
                lines.append(f"- ...and {len(parts) - 60} more")
            if citations:
                lines.extend(["", "Sources:"])
                for c in citations[:4]:
                    lines.append(f"- {c.url}")
            return "\n".join(lines)

        if compatibility_confirmed:
            answer = "Yes — compatibility appears confirmed from retrieved sources."
        else:
            answer = "Here is what I found from retrieved sources."
        if citations:
            lines = [answer, "", "Sources:"]
            for c in citations[:4]:
                lines.append(f"- {c.url}")
            if "long instruction" in message.lower() or "all parts" in message.lower():
                lines.extend(
                    [
                        "",
                        "I can provide a full model-specific guide next, but I need one more crawl pass "
                        "focused on the model parts list sections.",
                    ]
                )
            return "\n".join(lines)
        return "I could not gather enough grounded page data in this run. Please retry with a narrower query."

    @staticmethod
    def _extract_part_ids(text: str) -> List[str]:
        ids = re.findall(r"\bPS\d{5,}\b", text or "", flags=re.I)
        seen: set[str] = set()
        out: List[str] = []
        for pid in ids:
            up = pid.upper()
            if up in seen:
                continue
            seen.add(up)
            out.append(up)
        return out

    @staticmethod
    def _decision_debug_text(tool_context_lines: List[str]) -> str:
        if not tool_context_lines:
            return "No prior tool results yet; selecting next action from user request."
        return f"Prior tool context found; using latest result to decide next step: {tool_context_lines[-1]}"

    @staticmethod
    def _tool_call_debug_text(name: str, args: Dict[str, Any]) -> str:
        return f"Executing {name} with args={json.dumps(args or {}, ensure_ascii=True)}"

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

    def cancel_run(self, run_id: str) -> bool:
        rid = (run_id or "").strip()
        if not rid:
            return False
        with self._cancel_lock:
            self._cancelled_runs.add(rid)
        return True

    def _is_cancelled(self, run_id: str) -> bool:
        rid = (run_id or "").strip()
        if not rid:
            return False
        with self._cancel_lock:
            return rid in self._cancelled_runs

    def _clear_cancelled(self, run_id: str) -> None:
        rid = (run_id or "").strip()
        if not rid:
            return
        with self._cancel_lock:
            self._cancelled_runs.discard(rid)

    def run_sync_from_stream(
        self, message: str, history: List[Dict[str, Any]] | None = None
    ) -> ChatResponse:
        answer_parts: List[str] = []
        intent = "general_parts_help"
        confidence = 0.62
        citations: List[Citation] = []
        traces: List[ToolTrace] = []

        for event in self.run_stream(message, history=history):
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

    @staticmethod
    def _normalize_history(history: List[Dict[str, Any]] | None) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        for item in history or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            out.append({"role": role, "content": content})
        return out

    def _build_messages(self, message: str, history: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
        hist = self._normalize_history(history)
        messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_TEMPLATE}]
        messages.extend(hist)

        # Avoid duplicating the current user turn when frontend already included it in history.
        if hist and hist[-1]["role"] == "user" and hist[-1]["content"] == message.strip():
            return messages

        if hist:
            messages.append({"role": "user", "content": message})
        else:
            messages.append({"role": "user", "content": INSTANCE_TEMPLATE.format(message=message)})
        return messages

    def _iter_step_thinking(
        self,
        *,
        user_message: str,
        step_text: str,
        tool_name: str = "",
        tool_args: Dict[str, Any] | None = None,
    ) -> Generator[str, None, None]:
        args_text = json.dumps(tool_args or {}, ensure_ascii=True)
        prompt = (
            "User request:\n"
            f"{user_message}\n\n"
            "Current step:\n"
            f"{step_text}\n\n"
            f"Tool name: {tool_name or 'none'}\n"
            f"Tool args: {args_text}\n"
            "Write thinking-style reasoning log for this step."
        )
        saw_reasoning = False
        try:
            stream = self.client.responses.create(
                model=settings.openai_model,
                instructions=SYSTEM_TEMPLATE,
                input=prompt,
                stream=True,
                temperature=0.2,
                reasoning={"effort": "medium"},
                timeout=6.0,
            )
            for event in stream:
                etype = getattr(event, "type", "")
                if etype == "response.reasoning_summary_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        saw_reasoning = True
                        yield delta
                elif etype == "response.output_text.delta":
                    # Fallback for models that don't emit reasoning summary events.
                    delta = getattr(event, "delta", "") or ""
                    if delta and not saw_reasoning:
                        yield delta
                        # Keep per-step thinking short and avoid long-running streams.
                        return
        except Exception:
            return

    def run_stream(
        self, message: str, run_id: str = "", history: List[Dict[str, Any]] | None = None
    ) -> Generator[Dict[str, Any], None, None]:
        traces: List[ToolTrace] = [ToolTrace(step="data_mode", detail=settings.data_mode)]
        citations: List[Citation] = []
        tool_names_used: List[str] = []
        used_live_tool = False
        live_blocked = False
        live_empty = False
        compatibility_confirmed = False
        final_text = ""
        draft_answer = ""
        n_calls = 0
        trajectory: List[Dict[str, Any]] = []
        tool_context_lines: List[str] = []
        seen_citation_urls: set[str] = set()
        repeated_no_new_info = 0
        observed_part_ids: set[str] = set()
        crawled_urls_in_run: set[str] = set()
        repeated_same_crawl_call = 0
        last_crawl_call_key = ""

        messages: List[Dict[str, Any]] = self._build_messages(message, history)
        trajectory.extend(messages)

        yield {
            "type": "thinking_step",
            "status": "running",
            "text": "Understanding request",
        }

        step = 0
        while True:
            if self._is_cancelled(run_id):
                yield {"type": "done", "status": "cancelled", "intent": "general_parts_help", "confidence": 0.0, "citations": [], "traces": [t.model_dump() for t in traces]}
                self._clear_cancelled(run_id)
                return
            try:
                step += 1
                if self.step_limit > 0 and n_calls >= self.step_limit:
                    raise LimitsExceeded(
                        {
                            "role": "exit",
                            "content": "LimitsExceeded",
                            "extra": {"exit_status": "LimitsExceeded"},
                        }
                    )
                n_calls += 1
                traces.append(ToolTrace(step="llm_step", detail=str(step)))

                decision_step_text = f"Loop {step}: deciding next action"
                yield {
                    "type": "thinking_step",
                    "status": "running",
                    "text": decision_step_text,
                }
                yield {
                    "type": "thinking_token",
                    "content": self._decision_debug_text(tool_context_lines),
                }

                loop_context = (
                    "Tool history for this run:\n"
                    + ("\n".join(tool_context_lines[-8:]) if tool_context_lines else "- none yet")
                    + "\n\nDiscovered URLs in this run:\n"
                    + ("\n".join([f"- {u}" for u in sorted(crawled_urls_in_run)[:20]]) if crawled_urls_in_run else "- none yet")
                    + "\n\nRules for this step:\n"
                    + "1) Read tool history before deciding.\n"
                    + "2) Do not call the same tool with the same args again unless prior result was explicitly insufficient.\n"
                    + "3) If compatibility and at least one source are already available, answer directly.\n"
                    + "4) If tool history indicates input_hint=search box expects part/model id token, "
                    + "use query as a single model or part id (e.g., WDT780SAEM1 or PS3406971), not a phrase.\n"
                    + "5) For pagination/navigation, prefer URLs discovered in this run; do not invent ?page=N paths.\n"
                    + f"6) Progress signal: repeated_no_new_info={repeated_no_new_info}, repeated_same_crawl_call={repeated_same_crawl_call}. "
                    + "If either is >=2, stop tool calls and provide best final answer from gathered sources."
                )
                resp = self.client.chat.completions.create(
                    model=settings.openai_model,
                    messages=[*messages, {"role": "system", "content": loop_context}],
                    tools=self.tools.tool_schemas(),
                    tool_choice="auto",
                    temperature=0.2,
                    timeout=45.0,
                )
                assistant_msg = resp.choices[0].message
                content = assistant_msg.content or ""
                tool_calls = assistant_msg.tool_calls or []
                yield {
                    "type": "thinking_step",
                    "status": "done",
                    "text": decision_step_text,
                }

                assistant_message: Dict[str, Any] = {"role": "assistant", "content": content}
                if tool_calls:
                    assistant_message["tool_calls"] = [tc.model_dump() for tc in tool_calls]
                messages.append(assistant_message)
                trajectory.append(assistant_message)

                if not tool_calls:
                    draft_answer = content.strip()
                    raise Submitted(
                        {
                            "role": "exit",
                            "content": "Submitted",
                            "extra": {"exit_status": "Submitted"},
                        }
                    )

                for i, tc in enumerate(tool_calls, start=1):
                    if self._is_cancelled(run_id):
                        yield {"type": "done", "status": "cancelled", "intent": "general_parts_help", "confidence": 0.0, "citations": [], "traces": [t.model_dump() for t in traces]}
                        self._clear_cancelled(run_id)
                        return
                    name = tc.function.name
                    args = self._safe_json_loads(tc.function.arguments)
                    tool_names_used.append(name)
                    traces.append(ToolTrace(step="tool_call", detail=name))
                    tool_step_text = f"Loop {step}: tool {i} `{name}`"
                    yield {
                        "type": "thinking_step",
                        "status": "running",
                        "text": tool_step_text,
                        "domain": "www.partselect.com",
                    }
                    yield {
                        "type": "thinking_token",
                        "content": self._tool_call_debug_text(name, args),
                    }

                    if name == "check_part_compatibility":
                        before_urls = set(seen_citation_urls)
                        result = self.tools.check_part_compatibility(
                            model_number=str(args.get("model_number", "")),
                            partselect_number=str(args.get("partselect_number", "")),
                        )
                        payload = result.model_dump()
                        compatibility_confirmed = bool(payload.get("compatible"))
                        if payload.get("source_url"):
                            citations.append(
                                Citation(
                                    url=payload["source_url"],
                                    title="Compatibility source",
                                    snippet="Compatibility lookup source page.",
                                )
                            )
                            seen_citation_urls.add(str(payload["source_url"]))
                        new_info = seen_citation_urls != before_urls
                    elif name == "search_partselect_content":
                        before_urls = set(seen_citation_urls)
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
                            seen_citation_urls.add(str(d.url))
                        new_info = seen_citation_urls != before_urls
                    elif name == "crawl_partselect_live":
                        before_urls = set(seen_citation_urls)
                        used_live_tool = True
                        args = self._normalize_partselect_live_args(args, message)
                        call_key = self._crawl_call_key(args)
                        docs = self.tools.crawl_partselect_live(
                            url=str(args.get("url", "https://www.partselect.com/")),
                            model_number=str(args.get("model_number", "")),
                            query=str(args.get("query", message)),
                            max_pages=int(args.get("max_pages", 2)),
                        )
                        if self._docs_are_access_denied(docs):
                            payload = {
                                "count": 0,
                                "results": [],
                                "error": "access_denied_by_target",
                            }
                            live_blocked = True
                            traces.append(ToolTrace(step="tool_error", detail="crawl_partselect_live: access_denied_by_target"))
                        else:
                            payload = {"count": len(docs), "results": [d.model_dump() for d in docs]}
                            live_empty = len(docs) == 0
                            for d in docs[:3]:
                                citations.append(
                                    Citation(
                                        url=d.url,
                                        title=d.title or d.url,
                                        snippet=self._citation_snippet(d.text),
                                    )
                                )
                                seen_citation_urls.add(str(d.url))
                            for d in docs:
                                if str(d.url or "").strip():
                                    crawled_urls_in_run.add(str(d.url))
                                for pid in self._extract_part_ids(str(d.text or "")):
                                    observed_part_ids.add(pid)
                                for pid in self._extract_part_ids(str(d.url or "")):
                                    observed_part_ids.add(pid)
                        new_info = seen_citation_urls != before_urls
                        if call_key == last_crawl_call_key:
                            repeated_same_crawl_call += 1
                        else:
                            repeated_same_crawl_call = 0
                        last_crawl_call_key = call_key
                    else:
                        payload = {"error": f"unknown_tool:{name}"}
                        new_info = False

                    tool_message = {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(payload, ensure_ascii=True),
                    }
                    messages.append(tool_message)
                    trajectory.append(tool_message)
                    tool_context_lines.append(self._tool_context_line(name, args, payload))
                    check_step_text = f"Loop {step}: tool {i} result check"
                    yield {
                        "type": "thinking_step",
                        "status": "running",
                        "text": check_step_text,
                        "domain": "www.partselect.com",
                    }
                    yield {
                        "type": "thinking_token",
                        "content": self._tool_result_check_text(name, payload),
                    }
                    yield {
                        "type": "thinking_step",
                        "status": "done",
                        "text": check_step_text,
                    }
                    yield {
                        "type": "thinking_step",
                        "status": "done",
                        "text": tool_step_text,
                    }
                    if name == "crawl_partselect_live":
                        if not new_info:
                            repeated_no_new_info += 1
                        else:
                            repeated_no_new_info = 0
                        if repeated_no_new_info >= 2 or repeated_same_crawl_call >= 2:
                            final_text = self._fallback_from_collected_context(
                                message=message,
                                citations=self._ensure_citations(citations),
                                compatibility_confirmed=compatibility_confirmed,
                                observed_part_ids=sorted(observed_part_ids),
                            )
                            raise Submitted(
                                {
                                    "role": "exit",
                                    "content": "Submitted",
                                    "extra": {"exit_status": "Submitted"},
                                }
                            )
            except InterruptAgentFlow as e:
                if (
                    str(e.message_payload.get("content", "")) == "LimitsExceeded"
                    and not final_text
                ):
                    final_text = self._fallback_from_collected_context(
                        message=message,
                        citations=self._ensure_citations(citations),
                        compatibility_confirmed=compatibility_confirmed,
                        observed_part_ids=sorted(observed_part_ids),
                    )
                trajectory.append(e.message_payload)
                if trajectory[-1].get("role") == "exit":
                    break

        if self._is_cancelled(run_id):
            yield {"type": "done", "status": "cancelled", "intent": "general_parts_help", "confidence": 0.0, "citations": [], "traces": [t.model_dump() for t in traces]}
            self._clear_cancelled(run_id)
            return

        saw_output = False
        allowed_part_ids = sorted(observed_part_ids)
        synthesis_prompt = (
            "User request:\n"
            f"{message}\n\n"
            "Tool history from this run:\n"
            + ("\n".join(tool_context_lines[-12:]) if tool_context_lines else "- none")
            + "\n\n"
            "Candidate source URLs:\n"
            + ("\n".join([f"- {c.url}" for c in self._ensure_citations(citations)[:8]]) if citations else "- none")
            + "\n\n"
            "Draft answer (if any):\n"
            + (draft_answer or "- none")
            + "\n\n"
            "Allowed part IDs from retrieved pages:\n"
            + (", ".join(allowed_part_ids[:120]) if allowed_part_ids else "none")
            + "\n\n"
            "Write the final user-facing answer. Be concise and factual. "
            "Do not invent part numbers. Only mention part numbers if they are in Allowed part IDs."
        )
        try:
            stream = self.client.responses.create(
                model=settings.openai_model,
                instructions=SYSTEM_TEMPLATE,
                input=synthesis_prompt,
                stream=True,
                temperature=0.2,
                timeout=45.0,
            )
            for event in stream:
                etype = getattr(event, "type", "")
                if etype == "response.output_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        saw_output = True
                        final_text += delta
                        yield {"type": "token", "content": delta}
        except Exception:
            pass

        if not saw_output:
            if draft_answer:
                final_text = draft_answer
            elif not final_text:
                final_text = "I can help with compatibility, troubleshooting, installation, or part search."
            for chunk in self._iter_text_chunks(final_text):
                yield {"type": "token", "content": chunk}

        intent = self._infer_intent_from_tools(tool_names_used) if tool_names_used else self._infer_intent_from_message(message)
        confidence = 0.82 if tool_names_used else 0.62
        if compatibility_confirmed:
            confidence = max(confidence, 0.9)
        if live_blocked:
            confidence = min(confidence, 0.65)
        elif live_empty:
            confidence = min(confidence, 0.72)
        yield {
            "type": "done",
            "intent": intent,
            "confidence": confidence,
            "citations": [c.model_dump() for c in self._ensure_citations(citations)],
            "traces": [t.model_dump() for t in traces],
        }
        self._clear_cancelled(run_id)
