"""Живой режим Gemini Live: выбор модели, инструменты, подтверждения (без обращения к API)."""
from __future__ import annotations

from types import SimpleNamespace as NS

from jarvis.tools.base import ToolResult, load_builtin_tools, registry
from jarvis.voice.gemini_live import GeminiLive, pick_live_model

load_builtin_tools()


def test_pick_live_model():
    names = ["models/gemini-3.5-transcribe-live", "models/gemini-2.5-flash-native-audio-latest",
             "models/gemini-3.1-flash-live-preview", "models/gemini-3.8-live",
             "models/gemini-3.8-live-extended-thinking", "models/gemini-3.5-live-translate-preview"]
    assert pick_live_model(names) == "gemini-3.8-live"
    assert pick_live_model(["models/gemini-2.5-flash-native-audio-latest"]) == "gemini-2.5-flash-native-audio-latest"
    assert pick_live_model(["models/gemini-3.5-transcribe-live"]) is None


class FakeAssistant:
    def __init__(self):
        self.rt = NS(registry=registry)
        self.pending = None
        self.calls = []
        self.listener = NS(on_confirm=lambda q: None)

    def _execute(self, name, args):
        self.calls.append((name, args))
        if name == "shutdown_computer":
            self.pending = NS(tool=name, args=args, action_id="a1")
            return ToolResult(False, "Выключить компьютер? Скажите «да» или «нет».", {"pending": True})
        return ToolResult(True, "Громкость 70 процентов.", {"volume": 70})

    def _run_tool(self, name, args, confirmed=False, action_id=None):
        self.calls.append(("RUN", name, confirmed))
        return ToolResult(True, "Компьютер выключится через 30 секунд.")

    def _cancel_pending(self, pending):
        self.calls.append(("CANCEL", pending.tool))


def make_live():
    live = GeminiLive.__new__(GeminiLive)
    live.a = FakeAssistant()
    return live


def test_tools_include_registry_and_confirm():
    live = make_live()
    decls = live._tools()[0].function_declarations
    names = {d.name for d in decls}
    assert {"open_app", "set_volume", "steam_switch_account", "confirm_action"} <= names
    assert len(names) == len(registry.all()) + 1


def test_dangerous_action_needs_voice_confirmation():
    live = make_live()
    r = live._run_tool("shutdown_computer", {})
    assert r["needs_confirmation"] and not any(c[0] == "RUN" for c in live.a.calls)
    r = live._run_tool("confirm_action", {"confirm": True})
    assert r["ok"] and ("RUN", "shutdown_computer", True) in live.a.calls


def test_confirmation_can_be_declined():
    live = make_live()
    live._run_tool("shutdown_computer", {})
    live._run_tool("confirm_action", {"confirm": False})
    assert ("CANCEL", "shutdown_computer") in live.a.calls
    assert not any(c[0] == "RUN" for c in live.a.calls)


def test_regular_tool_result_passed_to_model():
    live = make_live()
    r = live._run_tool("get_volume", {})
    assert r == {"ok": True, "message": "Громкость 70 процентов.", "data": {"volume": 70}}


def test_player_prebuffers_before_playing():
    """Ответ начинает звучать с запасом звука, а не первым крошечным куском (иначе голос заикается)."""
    import time

    from jarvis.voice.gemini_live import OUT_RATE, PREBUFFER, AudioPlayer

    p = AudioPlayer.__new__(AudioPlayer)
    import queue

    p._q = queue.Queue()
    chunk = b"\0\0" * int(OUT_RATE * 0.04)
    for _ in range(10):
        p._q.put(chunk)
    out = p._gather(chunk)
    assert len(out) >= PREBUFFER * OUT_RATE * 2
    t = time.monotonic()
    while not p._q.empty():
        p._q.get()
    assert p._gather(chunk) == chunk
    assert time.monotonic() - t < PREBUFFER + 0.2


def test_config_has_russian_transcription_hint():
    live = make_live()
    live.voice = "Charon"
    live.a.dialog = NS(snapshot=lambda: "")
    live.a.rt.dialog = NS(history=[])
    cfg = live._config()
    t = cfg.input_audio_transcription
    assert "ru-RU" in t.language_codes and "Jarvis" in t.custom_vocabulary


def _run_mic(levels, barge_in=True):
    """Прогнать _send_mic на заданных уровнях микрофона, пока «говорит» JARVIS."""
    import asyncio

    import numpy as np

    from jarvis.voice.audio import BLOCK

    chunks = [np.full(BLOCK, lvl, dtype=np.int16).tobytes() for lvl in levels]
    state = {"closed": False, "last_activity": 0.0}

    class Src:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self, timeout):
            if not chunks:
                state["closed"] = True
                return None
            return chunks.pop(0)

    class Player:
        speaking, audible_since, interrupted = True, 0.0, 0

        def interrupt(self):
            self.interrupted += 1
            self.speaking = False

    sent = []

    class Session:
        async def send_realtime_input(self, audio=None, **kw):
            sent.append(audio)

    live = make_live()
    live.barge_in = barge_in
    live.idle_seconds = 999
    live.a._audio_source_factory = Src
    live.a.speaker = NS(speaking=False)
    live.a.listener = NS(on_state=lambda s: None, on_level=lambda v: None)
    player = Player()
    asyncio.run(live._send_mic(Session(), player, state))
    return player.interrupted, len(sent)


def test_echo_does_not_interrupt():
    levels = [800, 1200, 600, 1300, 900] * 20
    assert _run_mic(levels) == (0, 0)


def test_user_voice_interrupts():
    interrupted, sent = _run_mic([800, 1200, 600, 1300, 900] * 4 + [5000] * 3 + [5000] * 5)
    assert interrupted == 1 and sent >= 3 + 5


def test_barge_in_can_be_disabled():
    assert _run_mic([800] * 20 + [5000] * 10, barge_in=False) == (0, 0)
