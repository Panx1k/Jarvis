"""Пресеты: создание голосом/списком, запуск по названию, опасные шаги не выполняются, удаление."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from jarvis.brain.rules import RuleParser
from jarvis.core.assistant import Runtime
from jarvis.core.context import DialogContext
from jarvis.tools import presets as presets_mod
from jarvis.tools.base import ToolResult, load_builtin_tools, registry

load_builtin_tools()


class MemSettings:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(presets_mod.time, "sleep", lambda s: None)
    done = []
    rt = Runtime(MemSettings(), DialogContext(), registry, NS(resolve=lambda n: None))
    rt.execute = lambda name, args: done.append((name, args)) or ToolResult(True, "ok")
    return rt, RuleParser(rt), done


def run(rt, parser, text):
    plan = parser.parse(text)
    assert plan, text
    action = plan.actions[0]
    return action, registry.call(action.tool, action.args, rt)


def test_create_and_run_preset_by_voice(env):
    rt, p, done = env
    action, r = run(rt, p, "Создай пресет работа: открой хром, открой телеграм и громкость 30, включи VPN, бла бла")
    assert action.tool == "create_preset" and r.ok
    assert rt.settings.get("presets")["работа"]["steps"] == ["открой хром", "открой телеграм", "громкость 30",
                                                             "включи VPN"]
    assert "бла бла" in r.message
    for phrase in ("режим работа", "запусти работу", "включи рабочий режим"):
        done.clear()
        action, r = run(rt, p, phrase)
        assert action.tool == "run_preset" and r.ok, phrase
        assert [d[0] for d in done] == ["open_app", "open_app", "set_volume", "vpn_on"]
    assert run(rt, p, "запусти стим")[0].tool == "open_app"


def test_dangerous_steps_are_skipped(env):
    rt, p, done = env
    registry.call("create_preset", {"name": "ночь", "steps": ["громкость 10", "выключи компьютер"]}, rt)
    action, r = run(rt, p, "включи режим ночь")
    assert action.tool == "run_preset" and r.ok
    assert [d[0] for d in done] == ["set_volume"] and "выключи компьютер" in r.message


def test_wait_step_list_and_delete(env, monkeypatch):
    rt, p, done = env
    waits = []
    monkeypatch.setattr(presets_mod.time, "sleep", waits.append)
    registry.call("create_preset", {"name": "игры", "steps": ["открой стим", "подожди 2 секунды", "громкость 60"]}, rt)
    run(rt, p, "игровой режим")
    assert 2.0 in waits and [d[0] for d in done] == ["open_app", "set_volume"]
    assert "игры" in run(rt, p, "какие у меня пресеты")[1].message
    assert run(rt, p, "удали пресет игры")[1].ok and not presets_mod.load(rt.settings)
