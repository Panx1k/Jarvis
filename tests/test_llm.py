"""Тест цикла tool use LLM-мозга с поддельным клиентом Anthropic (без сети)."""
from __future__ import annotations

from types import SimpleNamespace as NS

from jarvis.brain.base import BrainUnavailable
from jarvis.brain.hybrid import HybridBrain
from jarvis.brain.llm import LLMBrain
from jarvis.config import Settings
from jarvis.core.assistant import Runtime
from jarvis.core.context import DialogContext
from jarvis.tools.base import ToolResult, load_builtin_tools, registry

load_builtin_tools()


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.script.pop(0)


def make_brain(script):
    rt = Runtime(Settings(), DialogContext(), registry, apps=None)
    brain = LLMBrain(rt)
    msgs = FakeMessages(script)
    brain.client = NS(messages=msgs, beta=NS(messages=msgs))
    import anthropic
    brain._anthropic = anthropic
    return brain, msgs


def tool_use(id_, name, inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


def text(t):
    return NS(type="text", text=t)


def test_tool_loop_executes_and_answers():
    brain, msgs = make_brain([
        NS(stop_reason="tool_use", content=[tool_use("t1", "set_volume", {"percent": 30})]),
        NS(stop_reason="end_turn", content=[text("Готово, громкость 30 процентов.")]),
    ])
    calls = []

    def execute(name, args):
        calls.append((name, args))
        return ToolResult(True, "Громкость 30 процентов.")

    reply = brain.respond("сделай потише, процентов тридцать", execute)
    assert calls == [("set_volume", {"percent": 30})]
    assert reply.text == "Готово, громкость 30 процентов." and reply.actions == ["set_volume"]
    first = msgs.requests[0]
    assert first["thinking"] == {"type": "adaptive"}
    assert any(t.get("name") == "open_app" for t in first["tools"])
    last_user = msgs.requests[1]["messages"][-1]
    assert last_user["content"][0]["type"] == "tool_result" and last_user["content"][0]["tool_use_id"] == "t1"


def test_pending_confirmation_stops_loop():
    brain, msgs = make_brain([
        NS(stop_reason="tool_use", content=[tool_use("t1", "shutdown_computer", {})]),
    ])

    def execute(name, args):
        return ToolResult(False, "Выключить компьютер? Скажите «да» или «нет».", {"pending": True})

    reply = brain.respond("выруби комп", execute)
    assert "Скажите «да» или «нет»" in reply.text
    assert len(msgs.requests) == 1


def test_hybrid_falls_back_to_rules_when_llm_down():
    brain, _ = make_brain([])
    hybrid = HybridBrain(brain.rt)
    hybrid.llm = brain

    def boom(*a, **k):
        raise BrainUnavailable("нет сети")

    brain.respond = boom
    executed = []
    reply = hybrid.respond("расскажи анекдот и который час", lambda n, a: executed.append(n) or ToolResult(True, "ok"))
    assert "недоступен" in reply.text or executed
