"""Живые тесты AI Brain с НАСТОЯЩИМ OpenAI API (модель не подменяется).

Запускаются, только если в .env заданы OPENAI_API_KEY и OPENAI_MODEL, иначе пропускаются.
Решения принимает настоящая модель. Чтобы тесты не меняли состояние компьютера (громкость, вкладки),
инструменты с побочными эффектами выполняются «на сухую»: вызов и аргументы записываются, а результат
правдоподобный. Безопасные инструменты (время, чтение громкости) выполняются по-настоящему.
Полный реальный сценарий с настоящими действиями — scripts/openai_demo.py.

    .venv\\Scripts\\python -m pytest tests/test_openai_live.py -v
"""
from __future__ import annotations

import time

import pytest

from jarvis.brain.hybrid import HybridBrain
from jarvis.config import Settings, env
from jarvis.core.assistant import Runtime
from jarvis.core.context import DialogContext, SearchResult
from jarvis.tools.base import ToolResult, load_builtin_tools, registry

load_builtin_tools()

from jarvis.brain.key_manager import load_keys

KEYS = [k for _, k in load_keys()]
pytestmark = pytest.mark.skipif(not KEYS or not env("OPENAI_MODEL"),
                                reason="API keys (OPENAI_API_KEY_1…) / OPENAI_MODEL не заданы в .env")

SAFE = {"get_time", "get_date", "get_volume", "media_status", "vpn_status", "get_weather"}


class Recorder:
    """Исполнитель инструментов для тестов: безопасные — по-настоящему, остальные — «на сухую»."""

    def __init__(self, rt):
        self.rt = rt
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, str] = {}

    def __call__(self, name: str, args: dict) -> ToolResult:
        self.calls.append((name, dict(args)))
        if name in self.fail:
            return ToolResult(False, self.fail[name])
        if name in SAFE:
            return registry.call(name, args, self.rt)
        d = self.rt.dialog
        if name == "open_site":
            d.active_site = "youtube" if "you" in str(args).lower() or "ютуб" in str(args).lower() else "site"
            return ToolResult(True, f"Открываю {args.get('site')}.")
        if name in ("search_youtube", "play_youtube"):
            q = args.get("query", "")
            results = [SearchResult(f"{q} — видео {i}", f"https://www.youtube.com/watch?v=test{i}", "youtube",
                                    channel=f"Канал {i}") for i in range(1, 6)]
            d.active_site = "youtube"
            d.set_results("youtube", q, results)
            return ToolResult(True, f"Нашёл на YouTube «{q}». Первый результат: «{results[0].title}».",
                              {"results": "; ".join(f"{i}. {r.title}" for i, r in enumerate(results, 1))})
        if name == "open_result":
            idx = int(args.get("index", 1))
            r = d.last_results[idx - 1] if 0 < idx <= len(d.last_results) else None
            return ToolResult(bool(r), f"Включаю «{r.title}»." if r else "Нет такого результата.")
        if name == "set_volume":
            return ToolResult(True, f"Громкость {args.get('percent')} процентов.", {"volume": args.get("percent")})
        return ToolResult(True, "Готово.")

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]


def make(mode="llm"):
    import os

    os.environ["BRAIN_MODE"] = mode
    from types import SimpleNamespace

    rt = Runtime(Settings(), DialogContext(), registry, apps=SimpleNamespace(resolve=lambda name: None))
    brain = HybridBrain(rt)
    assert brain.llm.available, brain.llm.error
    return rt, brain


def turn(rt, brain, text, rec):
    """Ход диалога через сам OpenAI-мозг — без отката на локальные правила: проверяем именно модель."""
    from jarvis.brain.base import BrainUnavailable

    try:
        reply = brain.llm.respond(text, rec)
    except BrainUnavailable as exc:
        pytest.fail(f"OpenAI API: {exc}")
    assert reply.source == "llm"
    rt.dialog.add_turn(text, reply.text, reply.actions)
    print(f"\n  > {text}\n  < {reply.text}   tools={reply.actions}")
    return reply


def test_1_key_loaded_not_exposed(capsys):
    rt, brain = make()
    shown = [brain.describe(), repr(brain.llm.__dict__.get("config_error")), brain.llm.keys.status_text(),
             repr(brain.llm.keys.slots)]
    assert all(k not in s for k in KEYS for s in shown)
    assert brain.llm.available


def test_2_connection():
    _, brain = make()
    ok, msg = brain.llm.check_connection()
    assert ok, msg


def test_3_plain_text():
    rt, brain = make()
    rec = Recorder(rt)
    reply = turn(rt, brain, "Ответь одним словом: столица Франции?", rec)
    assert "париж" in reply.text.lower() or "paris" in reply.text.lower()
    assert reply.source == "llm"


def test_4_tool_calling():
    rt, brain = make()
    rec = Recorder(rt)
    reply = turn(rt, brain, "Какая сейчас громкость?", rec)
    assert "get_volume" in rec.names()
    low = reply.text.lower()
    assert any(ch.isdigit() for ch in low) or any(w in low for w in ("полн", "максим", "выключ", "mute")), reply.text


def test_5_multi_step():
    rt, brain = make()
    rec = Recorder(rt)
    reply = turn(rt, brain, "Jarvis, открой YouTube, найди музыку для учёбы и поставь громкость 30%.", rec)
    names = rec.names()
    assert "search_youtube" in names and "set_volume" in names, rec.calls
    assert dict(rec.calls)["set_volume"].get("percent") == 30
    assert "музык" in dict(rec.calls)["search_youtube"].get("query", "").lower()
    if "open_site" in names:
        assert names.index("open_site") < names.index("search_youtube")
    assert reply.text


LOCAL_MODEL = "localhost" in (env("OPENAI_BASE_URL") or "") or "127.0.0.1" in (env("OPENAI_BASE_URL") or "")


@pytest.mark.xfail(LOCAL_MODEL, strict=False,
                   reason="маленькая локальная модель (7B) не всегда вызывает инструмент для «найди там»; "
                          "в рабочем режиме hybrid это покрывают правила — см. test_6b")
def test_6_context():
    rt, brain = make()
    rec = Recorder(rt)
    turn(rt, brain, "Открой YouTube.", rec)
    turn(rt, brain, "Найди там музыку.", rec)
    assert "search_youtube" in rec.names(), rec.calls
    rec.calls.clear()
    turn(rt, brain, "Включи второй результат.", rec)
    assert ("open_result", {"index": 2}) in rec.calls, rec.calls


def test_6b_context_hybrid():
    rt, brain = make(mode="hybrid")
    rec = Recorder(rt)

    def say(text):
        reply = brain.respond(text, rec)
        rt.dialog.add_turn(text, reply.text, reply.actions)
        print(f"\n  > {text}\n  < {reply.text}   tools={reply.actions} [{reply.source}]")
        return reply

    say("Открой YouTube.")
    say("Найди там музыку.")
    assert "search_youtube" in rec.names(), rec.calls
    rec.calls.clear()
    say("Включи второй результат.")
    assert ("open_result", {"index": 2}) in rec.calls, rec.calls
    reply = say("Что ты сейчас нашёл для меня? Ответь одним предложением.")
    assert reply.source == "llm" and reply.text


def test_7_tool_error_handled():
    rt, brain = make()
    rec = Recorder(rt)
    rec.fail["open_app"] = "Не нашёл приложение «Photoshop» (оно не установлено)."
    reply = brain.respond("Открой Photoshop.", rec)
    print(f"\n  > Открой Photoshop.\n  < {reply.text}   tools={reply.actions} [{reply.source}]")
    assert "open_app" in rec.names()
    low = reply.text.lower()
    assert "traceback" not in low and "exception" not in low
    import re
    assert not re.search(r"\bоткрыт\b|\bзапущен\b", low), reply.text
    assert any(w in low for w in ("не", "нет", "установ", "not")), reply.text


def test_8_full_voice_cycle():
    import os

    from jarvis.config import ROOT
    from jarvis.core.assistant import Assistant, AssistantListener
    from jarvis.voice.audio import QueueSource
    from tests.voice_fixtures import RecordingTTS, speech

    if not (ROOT / "models" / "vosk-model-small-ru-0.22").is_dir():
        pytest.skip("нет модели Vosk")
    os.environ["BRAIN_MODE"] = "llm"
    pcm = speech("Джарвис, какая сейчас громкость?")
    src = QueueSource(realtime=True)
    a = Assistant(AssistantListener(), enable_voice=True, audio_source_factory=lambda: src)
    tts = RecordingTTS()
    a.speaker.engine = tts
    tools_used = []
    original = a._run_tool

    def spy(name, args, **kw):
        tools_used.append(name)
        return original(name, args, **kw)

    a._run_tool = spy
    llm_replies = []
    llm_respond = a.brain.llm.respond

    def llm_spy(text, execute):
        reply = llm_respond(text, execute)
        llm_replies.append(reply)
        return reply

    a.brain.llm.respond = llm_spy
    a.live_enabled = False
    try:
        assert a.brain.llm.available
        a.set_wake_word(True)
        time.sleep(0.5)
        src.push(pcm)
        deadline = time.time() + 60
        while time.time() < deadline and not (tts.said and not a.busy):
            time.sleep(0.1)
        assert tts.said, "ассистент не ответил голосом"
        spoken = tts.said[-1][0]
        print(f"\n  🔊 {spoken}   tools={tools_used}")
        assert llm_replies, "ответ сформировали локальные правила, а не OpenAI (API недоступен?)"
        assert "get_volume" in tools_used
        assert any(ch.isdigit() for ch in spoken) or "процент" in spoken.lower()
        assert a.dialog.history[-1].actions == ["get_volume"]
    finally:
        a.shutdown()
