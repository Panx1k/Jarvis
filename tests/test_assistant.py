"""Интеграционные тесты оркестратора: подтверждения опасных действий, wake word, контекст."""
from __future__ import annotations

import threading

import pytest

from jarvis.core.assistant import Assistant, AssistantListener
from jarvis.tools.base import ToolResult, tool

CALLS: list[dict] = []


@tool("test_danger", "Тестовое опасное действие", params={"x": {"type": "string"}}, dangerous=True,
      confirm="Точно выполнить тест?", patterns=[r"^опасный тест(?: (?P<x>\w+))?$"])
def _test_danger(ctx, x: str = ""):
    CALLS.append({"x": x})
    return ToolResult(True, "Тест выполнен.")


class Collector(AssistantListener):
    def __init__(self):
        self.messages: list[tuple[str, str]] = []
        self.confirms: list = []
        self.actions: list[tuple[str, str]] = []
        self.idle = threading.Event()

    def on_message(self, role, text):
        self.messages.append((role, text))

    def on_confirm(self, question):
        self.confirms.append(question)

    def on_action(self, action_id, text, status, detail=""):
        self.actions.append((action_id, status))

    def on_state(self, state):
        if state == "idle":
            self.idle.set()


@pytest.fixture(scope="module")
def env():
    listener = Collector()
    a = Assistant(listener, enable_voice=False)
    a.brain.mode = "rules"
    yield a, listener
    a.shutdown()


def say(env, text):
    a, listener = env
    listener.idle.clear()
    a.submit_text(text)
    assert listener.idle.wait(20), "ассистент не ответил"
    return listener.messages[-1][1]


def test_confirmation_yes(env):
    CALLS.clear()
    reply = say(env, "опасный тест один")
    assert "Точно выполнить тест?" in reply and not CALLS
    assert env[0].pending is not None
    assert say(env, "да") == "Тест выполнен."
    assert CALLS == [{"x": "один"}]
    assert env[0].pending is None


def test_confirmation_no(env):
    CALLS.clear()
    say(env, "опасный тест")
    assert say(env, "нет") == "Отменено."
    assert not CALLS
    assert env[1].actions[-1][1] == "error"


def test_other_command_cancels_pending(env):
    CALLS.clear()
    say(env, "опасный тест")
    reply = say(env, "который час")
    assert reply.startswith("Сейчас")
    assert env[0].pending is None and not CALLS


def test_wake_word_stripped(env):
    assert say(env, "Джарвис, который час").startswith("Сейчас")
    assert say(env, "Джарвис") == "Да, сэр. Слушаю."
    assert say(env, "Jarvis, который час").startswith("Сейчас")


def test_dialog_history(env):
    say(env, "какое сегодня число")
    turn = env[0].dialog.history[-1]
    assert turn.user == "какое сегодня число" and "get_date" in turn.actions
