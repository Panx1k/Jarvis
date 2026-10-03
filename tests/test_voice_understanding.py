"""Голосовые команды целиком: синтезированная речь → микрофон (QueueSource) → VAD → «Jarvis» (Vosk) → Google STT
→ нормализация → намерение → инструмент. Инструменты подменены записью вызовов — на компьютере ничего не
открывается, не закрывается и не удаляется. Нужны интернет (Google STT, Edge TTS) и модели Vosk."""
from __future__ import annotations

import time

import numpy as np
import pytest

from jarvis.config import ROOT
from jarvis.core.assistant import Assistant, AssistantListener
from jarvis.tools.base import ToolResult, registry
from jarvis.voice.audio import QueueSource
from tests.voice_fixtures import RecordingTTS, speech

pytestmark = pytest.mark.skipif(not (ROOT / "models" / "vosk-model-small-ru-0.22").is_dir(),
                                reason="нет модели Vosk (python scripts/download_vosk_model.py)")


class Log(AssistantListener):
    def __init__(self):
        self.transcripts = []

    def on_transcript(self, text):
        self.transcripts.append(text)


@pytest.fixture(scope="module")
def rig():
    from jarvis.tools.base import load_builtin_tools

    load_builtin_tools()
    calls: list[tuple[str, dict]] = []
    saved = {}
    for t in registry.all():
        saved[t.name] = (t.func, t.precheck)
        t.func = (lambda ctx, _n=t.name, **kw: calls.append((_n, kw)) or ToolResult(True, f"Готово ({_n})."))
        t.precheck = None
    src = QueueSource(realtime=True)
    log = Log()
    a = Assistant(log, enable_voice=True, audio_source_factory=lambda: src)
    a.speaker.engine = RecordingTTS()
    a.brain.mode = "rules"
    a.live_enabled = False
    a.samples = None
    saved_confirm = dict(a.settings.data.get("confirm", {}) or {})
    a.settings.data["confirm"] = {"mode": "normal"}
    assert a.set_wake_word(True)
    time.sleep(0.5)
    yield a, src, log, calls
    a.shutdown()
    a.settings.data["confirm"] = saved_confirm
    for name, (func, pre) in saved.items():
        tool = registry.get(name)
        tool.func, tool.precheck = func, pre


def wait_calls(rig, n_before: int, timeout: float = 25.0):
    a, src, log, calls = rig
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(calls) > n_before and not a.busy and a._jobs.empty():
            return calls[n_before:]
        if a.pending is not None and not a.busy and a._jobs.empty():
            return [("pending", {"tool": a.pending.tool, **a.pending.args})]
        time.sleep(0.05)
    return []


def say(rig, *parts: str, pause: float = 0.9, noisy: float | None = None, followup: bool = False):
    a, src, log, calls = rig
    if not followup:
        a.voice_loop.cancel_followup()
    n = len(calls)
    for i, part in enumerate(parts):
        pcm = speech(part)
        if noisy is not None:
            x = np.frombuffer(pcm, np.int16).astype(np.float32)
            noise = np.random.default_rng(3).normal(0, 1, len(x))
            noise = np.cumsum(noise)
            noise -= np.convolve(noise, np.ones(400) / 400, "same")
            noise = noise / noise.std() * np.sqrt((x ** 2).mean()) / (10 ** (noisy / 20))
            pcm = np.clip(x + noise, -32768, 32767).astype(np.int16).tobytes()
        src.push(pcm, silence_after=pause if i < len(parts) - 1 else 1.4)
    return wait_calls(rig, n)


CASES = [
    (("Джарвис, открой хром",), "open_app", "Chrome"),
    (("Джарвис, пожалуйста, запусти дискорд",), "open_app", "Discord"),
    (("Джарвис, ну давай откроем стим",), "open_app", "Steam"),
    (("Джарвис, открой мне браузер",), "open_app", None),
    (("Джарвис, можешь закрыть Discord?",), "close_app", "Discord"),
    (("Джарвис, что у меня сейчас на экране?",), "screen_read", None),
    (("Джарвис, прочитай что написано",), "screen_read", None),
    (("Джарвис, добавь дискорд в исключения",), "exclusion_add", "Discord"),
    (("Джарвис, какие у меня программы в исключениях?",), "exclusion_list", None),
    (("Джарвис, открой", "браузер"), "open_app", None),
]


@pytest.mark.parametrize("parts, tool, entity", CASES, ids=[" / ".join(c[0]) for c in CASES])
def test_voice_command(rig, parts, tool, entity):
    got = say(rig, *parts)
    assert got and got[0][0] == tool, (got, rig[2].transcripts[-3:])
    if entity:
        assert entity in got[0][1].values(), (got, rig[2].transcripts[-3:])


def test_uninstall_by_voice_asks_first(rig):
    a = rig[0]
    got = say(rig, "Джарвис, удали Steam")
    assert got and got[0][0] == "pending" and got[0][1]["tool"] == "uninstall_app"
    rig[2].transcripts.clear()
    rig[1].push(speech("нет"), silence_after=1.4)
    deadline = time.time() + 20
    while a.pending is not None and time.time() < deadline:
        time.sleep(0.1)
    assert a.pending is None, rig[2].transcripts
    assert not any(name == "uninstall_app" for name, _ in rig[3])


def test_noisy_command(rig):
    got = say(rig, "Джарвис, открой дискорд", noisy=6)
    assert got and got[0] == ("open_app", {"app": "Discord"}), (got, rig[2].transcripts[-2:])


def test_context_followup_without_wake_word(rig):
    rig[0].dialog.last_app = None
    got = say(rig, "Джарвис, открой хром")
    assert got and got[0][0] == "open_app"
    rig[0].dialog.last_app = "Google Chrome"
    got = say(rig, "Теперь ютуб", followup=True)
    assert got and got[0][0] == "open_site" and got[0][1].get("site") == "YouTube", (got, rig[2].transcripts[-2:])
