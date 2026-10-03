"""Поток в ассистенте: уточнение при неуверенно распознанном названии, «повторите» при плохом распознавании,
обычные команды без лишних вопросов, режим подтверждений. Инструменты подменены записью вызовов."""
from __future__ import annotations

import pytest

from jarvis.core.assistant import NOT_UNDERSTOOD_VOICE, Assistant, AssistantListener
from jarvis.tools.base import ToolResult, registry
from jarvis.voice.stt import STTResult


class Log(AssistantListener):
    def __init__(self):
        self.messages, self.debug = [], []

    def on_message(self, role, text):
        self.messages.append((role, text))

    def on_debug(self, text):
        self.debug.append(text)


@pytest.fixture(scope="module")
def assistant():
    log = Log()
    a = Assistant(log, enable_voice=False)
    a.brain.mode = "rules"
    a.voice_replies = False
    yield a, log
    a.shutdown()


@pytest.fixture
def env(assistant, monkeypatch):
    a, log = assistant
    calls = []
    for name in ("open_app", "close_app", "launch_game", "open_site"):
        tool = registry.get(name)
        monkeypatch.setattr(tool, "func", lambda ctx, _n=name, **kw: calls.append((_n, kw)) or ToolResult(True, "ok"))
        monkeypatch.setattr(tool, "precheck", None)
    monkeypatch.setattr(a.apps, "resolve", lambda q: type("M", (), {"kind": "lnk", "display": q, "key": q})())
    a.pending, a.clarification = None, None
    a.dialog.history.clear()
    log.messages.clear()
    saved = dict(a.settings.data.get("confirm", {}) or {})
    yield a, log, calls
    a.settings.data["confirm"] = saved


def voice(a, text, conf=0.93):
    a._handle(text, voice=True, stt=STTResult(text, conf, [text]))


def last_reply(log):
    return [t for r, t in log.messages if r == "assistant"][-1]


def test_clear_command_runs_without_questions(env):
    a, log, calls = env
    voice(a, "Джарвис, можешь пожалуйста открыть мне дискорд?")
    assert calls == [("open_app", {"app": "Discord"})]
    assert a.clarification is None and a.pending is None


def test_unsure_name_is_clarified_then_executed(env):
    a, log, calls = env
    voice(a, "запусти стем", conf=0.6)
    assert not calls and last_reply(log) == "Вы имели в виду Steam?"
    voice(a, "да")
    assert calls == [("open_app", {"app": "Steam"})]


def test_clarification_no_asks_to_repeat(env):
    a, log, calls = env
    voice(a, "запусти стем", conf=0.6)
    voice(a, "нет")
    assert not calls and "Повторите" in last_reply(log)


def test_new_command_instead_of_answer(env):
    a, log, calls = env
    voice(a, "запусти стем", conf=0.6)
    voice(a, "закрой дискорд")
    assert calls == [("close_app", {"app": "Discord"})] and a.clarification is None


def test_bad_recognition_asks_to_repeat(env):
    a, log, calls = env
    voice(a, "открой Discord", conf=0.3)
    assert not calls and last_reply(log) == NOT_UNDERSTOOD_VOICE


def test_typed_text_never_asks_to_repeat(env):
    a, log, calls = env
    a._handle("можешь открыть дискорд")
    assert calls == [("open_app", {"app": "Discord"})]


def test_strict_mode_confirms_closing(env):
    a, log, calls = env
    a.settings.data["confirm"] = {"mode": "strict"}
    voice(a, "закрой дискорд")
    assert not calls and a.pending is not None and "подтвержда" in last_reply(log)
    voice(a, "да")
    assert calls == [("close_app", {"app": "Discord"})]


def test_normal_mode_closes_without_question(env):
    a, log, calls = env
    a.settings.data["confirm"] = {"mode": "normal"}
    voice(a, "закрой дискорд")
    assert calls == [("close_app", {"app": "Discord"})]


def test_debug_trace_when_enabled(env, monkeypatch):
    a, log, calls = env
    monkeypatch.setitem(a.settings.data, "ui", dict(a.settings.data.get("ui", {}), debug=True))
    log.debug.clear()
    voice(a, "открой дискорд пожалуйста")
    trace = log.debug[-1]
    assert "NORMALIZED: «открой Discord»" in trace and "INTENT: OPEN_APPLICATION" in trace and "TOOL: open_app" in trace
