from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from app.agent import MainAgent
from app.models import CompatibilityResult
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
        msg = self._messages[self._idx]
        self._idx += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class _FakeClient:
    def __init__(self, messages: list[SimpleNamespace]):
        self.chat = SimpleNamespace(completions=_FakeCompletions(messages))
        # Optional thinking stream path can fail safely in agent; keep stubbed.
        self.responses = SimpleNamespace(create=lambda **kwargs: [])


class _FakeToolbox:
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

    @staticmethod
    def check_part_compatibility(model_number: str, partselect_number: str) -> CompatibilityResult:
        return CompatibilityResult(
            model_number=model_number,
            partselect_number=partselect_number,
            compatible=True,
            confidence=0.98,
            source_url=f"https://www.partselect.com/Models/{model_number}/",
        )

    @staticmethod
    def crawl_partselect_live(url: str, model_number: str = "", query: str = "", max_pages: int = 2):
        return []


class AgentLoopTests(unittest.TestCase):
    def _agent(self) -> MainAgent:
        repo = SampleRepository(settings.sample_dir)
        return MainAgent(repo, _FakeToolbox())

    def test_normalize_partselect_live_args_uses_safe_defaults(self):
        message = "live check model WDT780SAEM1 part PS3406971"
        raw = {"url": "https://example.com/random", "max_pages": 99}
        got = MainAgent._normalize_partselect_live_args(raw, message)
        self.assertEqual(got["url"], "https://www.partselect.com/")
        self.assertEqual(got["model_number"], "WDT780SAEM1")
        self.assertEqual(got["query"], "PS3406971")
        self.assertEqual(got["max_pages"], 5)

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


if __name__ == "__main__":
    unittest.main()

