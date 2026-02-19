from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlparse, urlunparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


REF_RE = re.compile(r"ref=([A-Za-z0-9:_-]+)")


@dataclass
class MCPBrowserRunner:
    command: str
    args: list[str]

    async def run_live_lookup(self, url: str, query: str = "", max_pages: int = 2) -> list[dict]:
        server = StdioServerParameters(command=self.command, args=self.args)
        async with stdio_client(server) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tool_specs = await session.list_tools()
                tools = self._tools_map(tool_specs)

                docs: list[dict] = []
                await self._call_tool(session, tools, ["browser_navigate", "navigate"], {"url": url})
                snapshot = await self._snapshot_text(session, tools)
                docs.append({"url": url, "title": "Live MCP source", "text": snapshot[:4000], "score": 1.0})

                if query and len(docs) < max_pages:
                    submitted = await self._submit_query_via_form(session, tools, query, snapshot)
                    if not submitted:
                        search_url = self._build_search_url(query)
                        await self._call_tool(
                            session,
                            tools,
                            ["browser_navigate", "navigate"],
                            {"url": search_url},
                        )
                    snapshot2 = await self._snapshot_text(session, tools)
                    docs.append(
                        {
                            "url": self._build_search_url(query),
                            "title": "Live MCP search result",
                            "text": snapshot2[:4000],
                            "score": 0.9,
                        }
                    )
                return docs[: max(1, min(max_pages, 5))]

    async def _submit_query_via_form(
        self, session: ClientSession, tools: dict[str, Any], query: str, snapshot_text: str
    ) -> bool:
        input_refs = self._candidate_input_refs(snapshot_text)
        if not input_refs:
            return False
        type_tool = self._find_tool_name(tools, ["browser_type", "type", "fill"])
        if not type_tool:
            return False
        key_tool = self._find_tool_name(tools, ["browser_press_key", "press_key", "key"])
        click_tool = self._find_tool_name(tools, ["browser_click", "click"])

        for ref in input_refs[:5]:
            typed = await self._call_tool(
                session,
                tools,
                [type_tool],
                self._build_args_for_tool(tools[type_tool], {"ref": ref, "text": query, "element": ref}),
                swallow=True,
            )
            if not typed:
                continue
            if key_tool:
                pressed = await self._call_tool(
                    session,
                    tools,
                    [key_tool],
                    self._build_args_for_tool(tools[key_tool], {"key": "Enter", "text": "Enter"}),
                    swallow=True,
                )
                if pressed:
                    return True
            if click_tool:
                clicked = await self._call_tool(
                    session,
                    tools,
                    [click_tool],
                    self._build_args_for_tool(tools[click_tool], {"element": ref, "ref": ref}),
                    swallow=True,
                )
                if clicked:
                    return True
        return False

    def _candidate_input_refs(self, snapshot_text: str) -> list[str]:
        lines = [ln.strip() for ln in snapshot_text.splitlines() if ln.strip()]
        ranked: list[tuple[int, str]] = []
        for ln in lines:
            lower = ln.lower()
            if not any(tok in lower for tok in ("textbox", "search", "input", "model", "part")):
                continue
            refs = REF_RE.findall(ln)
            if not refs:
                continue
            score = 0
            for tok, weight in (("search", 5), ("model", 4), ("part", 3), ("textbox", 2), ("input", 1)):
                if tok in lower:
                    score += weight
            ranked.append((score, refs[0]))
        ranked.sort(reverse=True)
        seen: set[str] = set()
        out: list[str] = []
        for _, ref in ranked:
            if ref in seen:
                continue
            seen.add(ref)
            out.append(ref)
        return out

    async def _snapshot_text(self, session: ClientSession, tools: dict[str, Any]) -> str:
        result = await self._call_tool(session, tools, ["browser_snapshot", "snapshot"], {})
        return self._extract_text(result)

    @staticmethod
    def _build_search_url(query: str) -> str:
        q = query.strip()
        parsed = urlparse("https://www.partselect.com/Search/")
        built = parsed._replace(query=urlencode({"SearchTerm": q}))
        return urlunparse(built)

    @staticmethod
    def _tools_map(tool_specs: Any) -> dict[str, Any]:
        tools = {}
        for t in getattr(tool_specs, "tools", []) or []:
            name = getattr(t, "name", "")
            if name:
                tools[name] = t
        return tools

    @staticmethod
    def _find_tool_name(tools: dict[str, Any], aliases: list[str]) -> str | None:
        for a in aliases:
            if a in tools:
                return a
        for name in tools:
            low = name.lower()
            if any(a.lower() in low for a in aliases):
                return name
        return None

    async def _call_tool(
        self,
        session: ClientSession,
        tools: dict[str, Any],
        aliases: list[str],
        args: dict[str, Any],
        swallow: bool = False,
    ) -> Any:
        tool_name = self._find_tool_name(tools, aliases)
        if not tool_name:
            if swallow:
                return None
            raise RuntimeError(f"MCP tool missing for aliases: {aliases}")
        try:
            return await session.call_tool(tool_name, self._build_args_for_tool(tools[tool_name], args))
        except Exception:
            if swallow:
                return None
            raise

    @staticmethod
    def _build_args_for_tool(tool_spec: Any, candidates: dict[str, Any]) -> dict[str, Any]:
        schema = getattr(tool_spec, "inputSchema", None) or getattr(tool_spec, "input_schema", None) or {}
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        if not isinstance(props, dict) or not props:
            return {}
        out: dict[str, Any] = {}
        for key in props.keys():
            if key in candidates:
                out[key] = candidates[key]
        if not out and props:
            for key in props.keys():
                low = key.lower()
                if "url" in low and "url" in candidates:
                    out[key] = candidates["url"]
                elif ("element" in low or "ref" in low or "selector" in low) and (
                    "element" in candidates or "ref" in candidates
                ):
                    out[key] = candidates.get("element") or candidates.get("ref")
                elif ("text" in low or "value" in low) and ("text" in candidates):
                    out[key] = candidates["text"]
                elif "key" in low and "key" in candidates:
                    out[key] = candidates["key"]
        return out

    @staticmethod
    def _extract_text(result: Any) -> str:
        parts: list[str] = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if text:
                parts.append(str(text))
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        if not parts and hasattr(result, "structuredContent"):
            try:
                parts.append(json.dumps(getattr(result, "structuredContent"), ensure_ascii=True))
            except Exception:
                pass
        return "\n".join(parts).strip()
