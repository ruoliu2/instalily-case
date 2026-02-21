from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from app.agent import MainAgent
from app.models import CompatibilityResult, RetrievedDoc
from app.retrieval import SampleRepository
from app.config import settings


class _FakeToolCall:
    def __init__(self, name: str, arguments: dict, call_id: str = "tc_1"):
        self.function = SimpleNamespace(name=name, arguments=json.dumps(arguments))
        self.id = call_id

    def model_dump(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.function.name,
                "arguments": self.function.arguments,
            },
        }


class _FakeCompletions:
    def __init__(self, messages: list[SimpleNamespace]):
        self._messages = messages
        self._idx = 0

    def create(self, **kwargs):
        if not self._messages:
            msg = SimpleNamespace(content="", tool_calls=[])
        else:
            idx = min(self._idx, len(self._messages) - 1)
            msg = self._messages[idx]
        self._idx += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class _FakeClient:
    def __init__(self, messages: list[SimpleNamespace]):
        self.chat = SimpleNamespace(completions=_FakeCompletions(messages))
        # Optional thinking stream path can fail safely in agent; keep stubbed.
        self.responses = SimpleNamespace(create=lambda **kwargs: [])


class _FakeToolbox:
    def __init__(self, crawl_docs: list[RetrievedDoc] | None = None):
        self.crawl_docs = list(crawl_docs or [])
        self.crawl_calls: list[dict] = []

    @staticmethod
    def tool_schemas() -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "check_part_compatibility",
                    "description": "compat",
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
                    "description": "live",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "model_number": {"type": "string"},
                            "query": {"type": "string"},
                            "max_pages": {"type": "integer"},
                        },
                        "required": ["url"],
                    },
                },
            },
        ]

    def check_part_compatibility(self, model_number: str, partselect_number: str) -> CompatibilityResult:
        return CompatibilityResult(
            model_number=model_number,
            partselect_number=partselect_number,
            compatible=True,
            confidence=0.98,
            source_url=f"https://www.partselect.com/Models/{model_number}/",
        )

    def crawl_partselect_live(
        self, url: str, model_number: str = "", query: str = "", max_pages: int = 2
    ):
        self.crawl_calls.append(
            {
                "url": url,
                "model_number": model_number,
                "query": query,
                "max_pages": max_pages,
            }
        )
        return list(self.crawl_docs)


class AgentLoopTests(unittest.TestCase):
    def _agent(self, toolbox: _FakeToolbox | None = None) -> MainAgent:
        repo = SampleRepository(settings.sample_dir)
        return MainAgent(repo, toolbox or _FakeToolbox())

    def test_normalize_partselect_live_args_uses_safe_defaults(self):
        message = "live check model WDT780SAEM1 part PS3406971"
        raw = {"url": "https://example.com/random", "max_pages": 99}
        got = MainAgent._normalize_partselect_live_args(raw, message)
        self.assertEqual(got["url"], "https://www.partselect.com/")
        self.assertEqual(got["model_number"], "WDT780SAEM1")
        self.assertEqual(got["query"], "PS3406971")
        self.assertEqual(got["max_pages"], 3)

    def test_normalize_partselect_live_args_tokenizes_phrase_query(self):
        message = "How can I install part number PS11752778?"
        raw = {
            "url": "https://www.partselect.com/",
            "query": "PS11752778 installation PDF",
            "max_pages": 1,
        }
        got = MainAgent._normalize_partselect_live_args(raw, message)
        self.assertEqual(got["query"], "PS11752778")
        self.assertEqual(got["max_pages"], 3)

    def test_normalize_partselect_live_args_ignores_spurious_model_for_part_lookup(self):
        message = "How can I install part number PS11752778?"
        raw = {
            "url": "https://www.partselect.com/PS11752778-Whirlpool-WPW10321304-Refrigerator-Door-Shelf-Bin.htm",
            "model_number": "WPW10321304",
            "query": "install",
            "max_pages": 1,
        }
        got = MainAgent._normalize_partselect_live_args(raw, message)
        self.assertEqual(got["query"], "PS11752778")
        self.assertEqual(got["model_number"], "")
        self.assertEqual(got["max_pages"], 3)

    def test_crawl_call_key_dedupes_same_target_even_if_query_phrase_differs(self):
        k1 = MainAgent._crawl_call_key(
            {"url": "https://www.partselect.com/", "query": "PS11752778", "model_number": ""}
        )
        k2 = MainAgent._crawl_call_key(
            {
                "url": "https://www.partselect.com/",
                "query": "PS11752778 installation PDF",
                "model_number": "",
            }
        )
        self.assertEqual(k1, k2)

    def test_run_stream_finishes_without_forced_live_call(self):
        agent = self._agent()
        agent.client = _FakeClient(
            [
                SimpleNamespace(content="Final answer", tool_calls=[]),
            ]
        )
        events = list(
            agent.run_stream(
                "Can you live-check PartSelect for model WDT780SAEM1 and part PS3406971?"
            )
        )
        steps = [e.get("text", "") for e in events if e.get("type") == "thinking_step"]
        self.assertFalse(
            any("Live-check requirement: crawl_partselect_live" in s for s in steps),
            "forced live-check branch should not run anymore",
        )
        self.assertTrue(any(e.get("type") == "done" for e in events))

    def test_run_stream_appends_tool_result_and_then_finalizes(self):
        agent = self._agent()
        tool_call = _FakeToolCall(
            "check_part_compatibility",
            {"model_number": "WDT780SAEM1", "partselect_number": "PS3406971"},
        )
        agent.client = _FakeClient(
            [
                SimpleNamespace(content="", tool_calls=[tool_call]),
                SimpleNamespace(content="Yes, compatible.", tool_calls=[]),
            ]
        )
        events = list(agent.run_stream("check compatibility"))
        result_check_tokens = [
            e.get("content", "")
            for e in events
            if e.get("type") == "thinking_token"
            and "Result check:" in e.get("content", "")
        ]
        self.assertTrue(result_check_tokens, "expected post-tool result-check thinking token")
        done = next(e for e in events if e.get("type") == "done")
        self.assertGreaterEqual(float(done.get("confidence", 0.0)), 0.9)

    def test_install_question_with_part_does_not_force_live_lookup_when_llm_skips_tools(self):
        toolbox = _FakeToolbox(
            crawl_docs=[
                RetrievedDoc(
                    url="https://www.partselect.com/",
                    title="PartSelect",
                    text="Search page",
                    score=1.0,
                ),
                RetrievedDoc(
                    url="https://www.partselect.com/PS11752778-Whirlpool-WPW10321304-Refrigerator-Door-Shelf-Bin.htm?SourceCode=3&SearchTerm=PS11752778",
                    title="PS11752778",
                    text="Installation Instructions",
                    score=0.95,
                ),
            ]
        )
        agent = self._agent(toolbox=toolbox)
        agent.client = _FakeClient(
            [
                SimpleNamespace(
                    content=(
                        "I'm not sure which appliance this is for. "
                        "Can you share your model?"
                    ),
                    tool_calls=[],
                ),
                SimpleNamespace(content="Use the source link below.", tool_calls=[]),
            ]
        )
        events = list(agent.run_stream("How can I install part number PS11752778?"))
        self.assertEqual(len(toolbox.crawl_calls), 0)
        self.assertTrue(any(e.get("type") == "done" for e in events))

    def test_compatibility_missing_part_prompts_for_part_number(self):
        agent = self._agent()
        agent.client = _FakeClient([SimpleNamespace(content="I can help with that.", tool_calls=[])])
        reply = agent.run_sync_from_stream("Is this part compatible with my WDT780SAEM1 model?")
        self.assertIn("part number", reply.answer.lower())

    def test_refrigerator_troubleshooting_missing_model_prompts_for_model_or_brand(self):
        agent = self._agent()
        agent.client = _FakeClient([SimpleNamespace(content="Try resetting the unit.", tool_calls=[])])
        reply = agent.run_sync_from_stream(
            "The ice maker on my Whirlpool fridge is not working. How can I fix it?"
        )
        lower = reply.answer.lower()
        self.assertTrue("model number" in lower or "brand" in lower)


if __name__ == "__main__":
    unittest.main()
