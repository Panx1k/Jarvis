"""Режим задачи: план → действия → наблюдение → самопроверка, уточняющий вопрос, продолжение задачи после ответа,
честность при ошибках инструментов. Модель подменена сценарием ответов."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from jarvis.brain.agent import needs_agent
from jarvis.brain.key_manager import KeyManager
from jarvis.brain.openai_brain import OpenAIBrain
from jarvis.config import Settings
from jarvis.core.assistant import Runtime
from jarvis.core.context import DialogContext
from jarvis.tools.base import ToolResult, load_builtin_tools, registry

load_builtin_tools()


def text(content):
    return NS(choices=[NS(message=NS(content=content, tool_calls=None, refusal=None), finish_reason="stop")])


def call(name, **args):
    return NS(choices=[NS(message=NS(content=None, refusal=None, tool_calls=[
        NS(id=f"c_{name}", type="function", function=NS(name=name, arguments=json.dumps(args, ensure_ascii=False)))]))])


class Script:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def create(self, **kw):
        self.requests.append(dict(kw, messages=[dict(m) for m in kw["messages"]]))
        return self.replies.pop(0)


@pytest.fixture
def make(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    def build(replies):
        script = Script(replies)
        rt = Runtime(Settings(), DialogContext(), registry, apps=None)
        plans = []
        rt.on_plan = lambda goal, steps: plans.append((goal, steps))
        brain = OpenAIBrain(rt, key_manager=KeyManager([(1, "sk-test-0000000000")]),
                            client_factory=lambda slot: NS(chat=NS(completions=NS(create=script.create))))
        executed = []

        def execute(name, args):
            executed.append((name, args))
            if name in ("task_plan", "ask_user"):
                return registry.call(name, args, rt)
            if name == "internet_search":
                return ToolResult(True, "1. The International 2025 — победила Team Falcons", {"results": []})
            if name == "mail_send":
                return ToolResult(False, "Не знаю адрес «Иван».")
            return ToolResult(True, "ok")

        return brain, rt, script, executed, plans, execute

    return build


@pytest.mark.parametrize("phrase, expected", [
    ("открой дискорд", False), ("какая погода", False), ("громкость 30", False),
    ("найди, кто выиграл The International 2025, и запиши в память", True),
    ("проверь почту и ответь Ивану, что я опоздаю", True),
    ("сравни цены на RTX 5070 в двух магазинах", True),
    ("открой хром, потом открой ютуб и включи музыку", True),
    ("посчитай по таблице загрузки/продажи.csv выручку по городам", True),
])
def test_needs_agent(phrase, expected):
    assert needs_agent(phrase) is expected


def test_plan_act_observe_verify(make):
    brain, rt, script, executed, plans, execute = make([
        call("task_plan", goal="Узнать победителя TI 2025", steps=["Найти в интернете", "Ответить"]),
        call("internet_search", query="победитель The International 2025"),
        text("Победила Team Falcons."),
        text("The International 2025 выиграла Team Falcons, сэр."),
    ])
    reply = brain.respond("найди, кто выиграл The International 2025, и скажи мне", execute)
    assert [n for n, _ in executed] == ["task_plan", "internet_search"]
    assert plans == [("Узнать победителя TI 2025", ["Найти в интернете", "Ответить"])]
    assert "Режим задачи" in script.requests[0]["messages"][0]["content"]
    tools = {t["function"]["name"] for t in script.requests[0]["tools"]}
    assert {"task_plan", "ask_user"} <= tools and "agent_task" not in tools
    assert "Самопроверка" in script.requests[3]["messages"][-1]["content"]
    assert reply.text == "The International 2025 выиграла Team Falcons, сэр." and rt.dialog.task is None


def test_ask_user_keeps_task_and_continues(make):
    brain, rt, script, executed, plans, execute = make([
        call("ask_user", question="Кому написать — Ивану Петрову или Ивану Смирнову?"),
        call("task_plan", goal="Письмо Ивану Петрову", steps=["Отправить письмо"]),
        text("Готово."),
        text("Отправил письмо Ивану Петрову."),
    ])
    reply = brain.respond("напиши письмо Ивану, что я опоздаю", execute)
    assert reply.text.startswith("Кому написать") and reply.source == "llm_ask"
    assert rt.dialog.task and rt.dialog.task["asked"].startswith("Кому")
    rt.dialog.add_turn("напиши письмо Ивану, что я опоздаю", reply.text, ["ask_user"])
    brain.respond("Петрову", execute)
    second = script.requests[1]["messages"]
    assert "Режим задачи" in second[0]["content"] and "Незавершённая задача" in second[-1]["content"]


def test_simple_command_has_no_agent_tools(make):
    brain, rt, script, executed, plans, execute = make([call("open_app", app="Discord")])
    reply = brain.respond("открой дискорд", execute)
    tools = {t["function"]["name"] for t in script.requests[0]["tools"]}
    assert not {"task_plan", "ask_user", "agent_task"} & tools
    assert reply.source == "llm_tools" and len(script.requests) == 1


def test_failed_tools_are_not_reported_as_done(make):
    brain, rt, script, executed, plans, execute = make([
        call("mail_send", to="Иван", body="Опоздаю"),
        text("Письмо отправлено."),
        text("Не получилось: я не знаю адрес Ивана. Назовите его почту."),
    ])
    reply = brain.respond("отправь Ивану письмо, что опоздаю", execute)
    assert "Проверка" in script.requests[2]["messages"][-1]["content"]
    assert reply.text.startswith("Не получилось")


def test_live_agent_task_runs_brain(monkeypatch):
    from jarvis.tools import task

    rt = Runtime(Settings(), DialogContext(), registry, apps=None)
    rt.agent = lambda goal: ToolResult(True, f"Итог: {goal}")
    assert registry.call("agent_task", {"goal": "разобрать почту"}, rt).message == "Итог: разобрать почту"
    rt.agent = None
    assert not task.agent_task(rt, "x").ok
