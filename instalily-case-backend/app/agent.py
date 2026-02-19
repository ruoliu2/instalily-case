from __future__ import annotations

import json
import re
import threading
from typing import Any, Dict, Generator, List

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
            return f"- {name} args={args_s} => pages={int(payload.get('count') or 0)}"
        if payload.get("error"):
            return f"- {name} args={args_s} => error={payload.get('error')}"
        return f"- {name} args={args_s} => done"

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
            "Write a detailed thinking-style reasoning log for this step."
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
                timeout=8.0,
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

    def run_stream(self, message: str, run_id: str = "") -> Generator[Dict[str, Any], None, None]:
        traces: List[ToolTrace] = [ToolTrace(step="data_mode", detail=settings.data_mode)]
        citations: List[Citation] = []
        tool_names_used: List[str] = []
        used_live_tool = False
        live_blocked = False
        live_empty = False
        compatibility_confirmed = False
        final_text = ""
        n_calls = 0
        trajectory: List[Dict[str, Any]] = []
        tool_context_lines: List[str] = []

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_TEMPLATE},
            {"role": "user", "content": INSTANCE_TEMPLATE.format(message=message)},
        ]
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
                for chunk in self._iter_step_thinking(
                    user_message=message,
                    step_text=decision_step_text,
                ):
                    yield {"type": "thinking_token", "content": chunk}

                loop_context = (
                    "Tool history for this run:\n"
                    + ("\n".join(tool_context_lines[-8:]) if tool_context_lines else "- none yet")
                    + "\n\nRules for this step:\n"
                    + "1) Read tool history before deciding.\n"
                    + "2) Do not call the same tool with the same args again unless prior result was explicitly insufficient.\n"
                    + "3) If compatibility and at least one source are already available, answer directly."
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
                    final_text = content.strip()
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
                    for chunk in self._iter_step_thinking(
                        user_message=message,
                        step_text=tool_step_text,
                        tool_name=name,
                        tool_args=args,
                    ):
                        yield {"type": "thinking_token", "content": chunk}

                    if name == "check_part_compatibility":
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
                    else:
                        payload = {"error": f"unknown_tool:{name}"}

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
            except InterruptAgentFlow as e:
                trajectory.append(e.message_payload)
                if trajectory[-1].get("role") == "exit":
                    break

        if self._is_cancelled(run_id):
            yield {"type": "done", "status": "cancelled", "intent": "general_parts_help", "confidence": 0.0, "citations": [], "traces": [t.model_dump() for t in traces]}
            self._clear_cancelled(run_id)
            return

        if self._wants_live_lookup(message) and not used_live_tool:
            model_number = self._extract_model_number(message)
            part_number = self._extract_partselect_number(message)
            source_url = self._extract_url(message) or "https://www.partselect.com/"
            query = part_number or model_number or message
            traces.append(ToolTrace(step="tool_call", detail="crawl_partselect_live(forced)"))
            forced_step_text = "Live-check requirement: crawl_partselect_live"
            yield {
                "type": "thinking_step",
                "status": "running",
                "text": forced_step_text,
                "domain": "www.partselect.com",
            }
            for chunk in self._iter_step_thinking(
                user_message=message,
                step_text=forced_step_text,
                tool_name="crawl_partselect_live",
                tool_args={
                    "url": source_url,
                    "model_number": model_number,
                    "query": query,
                    "max_pages": 2,
                },
            ):
                yield {"type": "thinking_token", "content": chunk}
            try:
                docs = self.tools.crawl_partselect_live(
                    url=source_url,
                    model_number=model_number,
                    query=query,
                    max_pages=2,
                )
                tool_names_used.append("crawl_partselect_live")
                if self._docs_are_access_denied(docs):
                    live_blocked = True
                    traces.append(ToolTrace(step="tool_error", detail="crawl_partselect_live(forced): access_denied_by_target"))
                    final_text = (
                        (final_text + "\n\n") if final_text else ""
                    ) + "Live page check failed: PartSelect blocked automated browser access (Access Denied)."
                elif docs:
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
                    live_empty = True
                    final_text = (
                        (final_text + "\n\n") if final_text else ""
                    ) + "Live page check returned no results."
                forced_payload = (
                    {"count": 0, "results": [], "error": "access_denied_by_target"}
                    if self._docs_are_access_denied(docs)
                    else {"count": len(docs), "results": [d.model_dump() for d in docs]}
                )
                forced_check_step_text = "Live-check requirement: result check"
                yield {
                    "type": "thinking_step",
                    "status": "running",
                    "text": forced_check_step_text,
                    "domain": "www.partselect.com",
                }
                yield {
                    "type": "thinking_token",
                    "content": self._tool_result_check_text("crawl_partselect_live", forced_payload),
                }
                yield {
                    "type": "thinking_step",
                    "status": "done",
                    "text": forced_check_step_text,
                }
            except Exception as exc:
                traces.append(ToolTrace(step="tool_error", detail=f"crawl_partselect_live(forced): {exc}"))
                final_text = (
                    (final_text + "\n\n") if final_text else ""
                ) + "Live page check failed. Please verify MCP browser settings."
            yield {
                "type": "thinking_step",
                "status": "done",
                "text": forced_step_text,
            }
        if not final_text:
            final_text = "I can help with compatibility, troubleshooting, installation, or part search."
        yield {"type": "token", "content": final_text}
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
