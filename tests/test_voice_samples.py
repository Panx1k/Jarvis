"""Voice Sample System: намерение ответа → подбор записи → запись / запись + синтез / обычный TTS."""
from __future__ import annotations

import json
import threading
import wave
from types import SimpleNamespace as NS

import pytest

from jarvis.tools.base import ToolResult
from jarvis.voice import responder
from jarvis.voice.intents import ResponseIntent, classify
from jarvis.voice.responder import ToolCall
from jarvis.voice.samples import SampleManager

INDEX = {"samples": [
    {"file": "hello.wav", "category": "greetings", "text": "Доброе утро.", "intents": {"GREETING_MORNING": 0.95,
     "GREETING": 0.85}, "phrases": ["доброе утро"]},
    {"file": "yes_sir.wav", "category": "acknowledgements", "text": "Да, сэр.", "prefix": True,
     "intents": {"WAKE": 0.95, "ACKNOWLEDGEMENT": 0.9}, "phrases": ["да сэр", "понял"]},
    {"file": "yes_sir2.wav", "category": "acknowledgements", "text": "Да, сэр.", "prefix": True,
     "intents": {"WAKE": 0.95, "ACKNOWLEDGEMENT": 0.9}, "phrases": ["да сэр", "понял"]},
    {"file": "at_service.wav", "category": "acknowledgements", "text": "Всегда к вашим услугам, сэр.",
     "intents": {"THANKS": 0.95}, "phrases": ["всегда к вашим услугам", "всегда пожалуйста"]},
    {"file": "done.wav", "category": "confirmations", "text": "Запрос выполнен, сэр.",
     "intents": {"TASK_COMPLETED": 0.95}, "phrases": ["запрос выполнен", "готово"]},
    {"file": "vpn.wav", "category": "system", "text": "Мы подключены и готовы.", "intents": {"VPN_CONNECTED": 0.9},
     "phrases": ["мы подключены и готовы"]},
    {"file": "loading.wav", "category": "actions", "text": "Загружаю, сэр.", "prefix": True,
     "intents": {"APPLICATION_OPENED": 0.9, "TASK_STARTED": 0.9}, "phrases": ["загружаю"]},
    {"file": "what.wav", "category": "questions", "text": "Чего вы пытаетесь добиться, сэр?",
     "intents": {"UNKNOWN": 0.85}, "phrases": ["не понял команду"]},
    {"file": "weak.wav", "category": "misc", "text": "Как пожелаете.", "intents": {"ACKNOWLEDGEMENT": 0.7},
     "phrases": ["как пожелаете"]},
]}


def _wav(path, seconds=0.2):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(b"\x10\x00" * int(16000 * seconds))


@pytest.fixture
def lib(tmp_path):
    for s in INDEX["samples"]:
        _wav(tmp_path / s["file"])
    _wav(tmp_path / "untagged phrase.wav")
    (tmp_path / "broken.wav").write_bytes(b"not a wav")
    (tmp_path / "index.json").write_text(json.dumps(INDEX, ensure_ascii=False), encoding="utf-8")
    store: dict = {}
    settings = NS(get=lambda k, d=None: store.get(k, d), store=store)
    m = SampleManager(settings, roots=[tmp_path], index_file=tmp_path / "index.json")
    m.settings_store = store
    return m


def call(tool, ok=True, msg="", data=None):
    return ToolCall(tool, {}, ToolResult(ok, msg, data or {}))


def decide(m, calls, user="", reply="", handled=True):
    spoken = responder.compose(reply, calls, "ru", handled)
    return m.find_best_sample(classify(calls, reply, spoken, user, handled), spoken)


def test_library_scan_skips_broken_and_indexes(lib):
    st = lib.stats()
    assert st["samples"] == 10 and st["broken"] == 1 and st["tagged"] == 9
    assert [s.name for s in lib.get_samples_by_intent("WAKE")] == ["yes_sir.wav", "yes_sir2.wav"]
    assert all(s.duration == 0.2 for s in lib.samples)


def test_greeting_and_acknowledgement(lib):
    d = decide(lib, [], user="привет", reply="Привет, сэр.")
    assert d.kind == "sample" and d.sample.name == "hello.wav" or d.kind == "tts"
    d = lib.find_best_sample(ResponseIntent("WAKE"))
    assert d.kind == "sample" and d.sample.name.startswith("yes_sir")
    d = decide(lib, [], user="хорошо", reply="Понял, сэр.")
    assert d.kind == "sample" and d.sample.name.startswith("yes_sir")


def test_thanks_confirmation(lib):
    d = decide(lib, [], user="спасибо", reply="Всегда пожалуйста, сэр.")
    assert d.kind == "sample" and d.sample.name == "at_service.wav" and d.confidence >= 0.8


def test_tool_success_and_vpn(lib):
    assert decide(lib, [call("vpn_on", msg="VPN включён (Happ).")]).sample.name == "vpn.wav"
    d = decide(lib, [call("vpn_off", msg="VPN выключен.")])
    assert d.kind == "sample" and d.sample.name == "done.wav" and d.via.startswith("parent:")
    d = decide(lib, [call("open_app", msg="Запускаю Discord.", data={"app": "Discord"})])
    assert d.kind == "sample" and d.sample.name == "loading.wav"


def test_result_context_changes_intent(lib):
    """Намерение — по РЕЗУЛЬТАТУ: уже включён / ошибка — не «Мы подключены»."""
    ri = classify([call("vpn_on", msg="VPN уже включён.")])
    assert ri.intent == "VPN_ALREADY_ENABLED"
    assert lib.find_best_sample(ri, "VPN уже включён, сэр.").kind == "tts"
    ri = classify([call("vpn_on", False, "Прокси не отвечает.")])
    assert ri.intent == "VPN_ERROR" and ri.dynamic
    assert lib.find_best_sample(ri, "Не удалось включить VPN, сэр.").kind == "tts"


def test_tool_error_and_unknown(lib):
    assert decide(lib, [call("open_app", False, "Не нашёл приложение «x».")]).kind == "tts"
    d = decide(lib, [], user="бла бла", handled=False)
    assert d.kind == "sample" and d.sample.name == "what.wav"


def test_dynamic_response_uses_tts(lib):
    d = decide(lib, [call("get_time", msg="Сейчас 14:32.")])
    assert d.kind == "tts" and d.reason == "dynamic response"
    d = decide(lib, [], user="столица франции", reply="Столица Франции — Париж.")
    assert d.kind == "tts"
    d = decide(lib, [], user="сделай", reply="Готово.")
    assert d.kind == "sample" and d.sample.name == "done.wav" and d.via == "text"


def test_sample_not_found_and_low_confidence(lib):
    d = lib.find_best_sample(ResponseIntent("SHUTDOWN"))
    assert d.kind == "tts" and d.sample is None
    lib.settings_store["voice.samples"] = {"threshold": 0.95}
    d = lib.find_best_sample(ResponseIntent("CHAT", dynamic=True), "Как пожелаете, но сначала скажите время.")
    assert d.kind == "tts"
    d = lib.find_best_sample(ResponseIntent("CANCELLED"))
    assert d.kind == "sample" and d.via == "parent:ACKNOWLEDGEMENT"


def test_fallback_off_and_samples_off(lib):
    lib.settings_store["voice.samples"] = {"fallback_tts": False}
    assert lib.find_best_sample(ResponseIntent("SHUTDOWN")).kind == "none"
    lib.settings_store["voice.samples"] = {"enabled": False}
    assert lib.find_best_sample(ResponseIntent("WAKE")).kind == "tts"


def test_hybrid_only_when_enabled(lib):
    calls = [call("launch_game", msg="Переключаю Steam на аккаунт Смурф и запускаю CS2.",
                  data={"app": "Counter-Strike 2", "account": "Смурф"})]
    assert decide(lib, calls).kind == "tts"
    lib.settings_store["voice.samples"] = {"hybrid": True}
    d = decide(lib, calls)
    assert d.kind == "hybrid" and d.sample.prefix


def test_rotation_between_suitable_samples(lib):
    names = [lib.find_best_sample(ResponseIntent("WAKE")).sample.name for _ in range(4)]
    assert set(names) == {"yes_sir.wav", "yes_sir2.wav"} and names[0] != names[1]
    lib.settings_store["voice.samples"] = {"variety": False}
    fixed = {lib.find_best_sample(ResponseIntent("WAKE")).sample.name for _ in range(3)}
    assert len(fixed) == 1


def test_threshold_from_env(lib, monkeypatch):
    monkeypatch.setenv("VOICE_SAMPLE_THRESHOLD", "0.99")
    assert lib.config().threshold == 0.99
    d = lib.find_best_sample(ResponseIntent("VPN_CONNECTED"))
    assert d.kind == "sample"


class Rec:
    def __init__(self):
        self.events = []

    def __getattr__(self, name):
        if name.startswith("on_"):
            return lambda *a: self.events.append((name, a))
        raise AttributeError(name)


def make_assistant(lib):
    from jarvis.core.assistant import Assistant

    rec = Rec()
    a = Assistant(rec, enable_voice=False)
    a.samples = lib
    a.voice_replies = True
    played, said = [], []
    a.speaker = NS(play=lambda path, on_level=None: (played.append(path.name), on_level and on_level(0.5)) and True
                   or True, say=lambda text, lang="ru": said.append(text), stop=lambda: None, speaking=False,
                   last_spoken_at=0.0, name="fake")
    return a, rec, played, said


def test_assistant_plays_sample_instead_of_tts(lib):
    a, rec, played, said = make_assistant(lib)
    try:
        a._calls = [call("vpn_on", msg="VPN включён (Happ).")]
        a._reply("VPN включён (Happ).", True, "rules", "включи vpn")
        assert played == ["vpn.wav"] and said == []
        kinds = [e[1][0] for e in rec.events if e[0] == "on_voice_output"]
        assert kinds == ["sample"]
        assert ("on_state", ("speaking",)) in rec.events
        assert any(e[0] == "on_output_level" for e in rec.events)
    finally:
        a.shutdown()


def test_assistant_tts_fallback_and_hybrid(lib):
    a, rec, played, said = make_assistant(lib)
    try:
        a._calls = [call("get_time", msg="Сейчас 14:32.")]
        a._reply("Сейчас 14:32.", True, "rules", "который час")
        assert played == [] and said == ["Сейчас 14:32, сэр."]
        lib.settings_store["voice.samples"] = {"hybrid": True}
        a._calls = [call("launch_game", msg="Переключаю Steam на аккаунт Смурф и запускаю CS2.",
                         data={"account": "Смурф"})]
        a._reply("Переключаю…", True, "rules", "запусти кс")
        assert played and said[-1].startswith("Переключаю")
        kinds = [e[1][0] for e in rec.events if e[0] == "on_voice_output"]
        assert kinds == ["tts", "hybrid"]
    finally:
        a.shutdown()


def test_debug_info_only_in_developer_mode(lib, monkeypatch):
    a, rec, played, said = make_assistant(lib)
    try:
        a._say_only(responder.NOT_UNDERSTOOD["ru"], ResponseIntent("UNKNOWN"))
        assert not any(e[0] == "on_debug" for e in rec.events)
        monkeypatch.setenv("JARVIS_DEBUG", "1")
        a._say_only(responder.NOT_UNDERSTOOD["ru"], ResponseIntent("UNKNOWN"))
        debug = [e[1][0] for e in rec.events if e[0] == "on_debug"]
        assert debug and "User intent: UNKNOWN" in debug[0] and "Selected: what.wav" in debug[0]
    finally:
        a.shutdown()


def test_wake_ack_rotates_by_intent(lib):
    a, rec, played, said = make_assistant(lib)
    try:
        a._ack()
        a._ack()
        assert set(played) == {"yes_sir.wav", "yes_sir2.wav"} and not said
    finally:
        a.shutdown()


def test_real_playback_emits_levels(lib):
    """Настоящее проигрывание записи (тихий звук 0.2 с) через тот же Speaker, что и TTS."""
    from jarvis.voice.tts import Speaker

    sp = Speaker(None)
    levels = []
    ok = sp.play(lib.samples[0].path, on_level=levels.append)
    assert ok and levels and levels[-1] == 0.0 and not sp.speaking
    threading.Event()


def _live(lib):
    from jarvis.voice.gemini_live import GeminiLive

    live = GeminiLive.__new__(GeminiLive)
    events, played = [], []
    live.a = NS(samples=lib, voice_replies=True, listener=Rec(), speaker=NS(stop=lambda: None),
                _debug_voice=lambda d: None)
    live._turn_calls, live._turn_override = [], None
    live._play_sample = lambda d: played.append(d.sample.name)
    return live, played


class FakePlayer:
    def __init__(self):
        self.chunks = []
        self.speaking = False

    def add(self, c):
        self.chunks.append(c)


def _turn(live, user="", calls=None):
    import time as _t

    live._turn_calls = list(calls or [])
    state = {"user_text": user, "reply_text": "", "actions": [], "last_activity": 0}
    player = FakePlayer()
    live._handle_audio(b"a1", state, player)
    live._handle_audio(b"a2", state, player)
    if state.get("held"):
        state["hold_until"] = _t.monotonic() - 1
        live._handle_audio(None, state, player)
    return state, player


def test_live_sample_replaces_gemini_voice(lib):
    import time as _t

    live, played = _live(lib)
    state, player = _turn(live, "Спасибо.")
    _t.sleep(0.05)
    assert state["turn_mode"] == "sample" and played == ["at_service.wav"] and player.chunks == []
    state, player = _turn(live, "", [call("vpn_on", msg="VPN включён.")])
    _t.sleep(0.05)
    assert state["turn_mode"] == "sample" and played[-1] == "vpn.wav"
    state, player = _turn(live, "Jarvis.")
    _t.sleep(0.05)
    assert state["turn_mode"] == "sample" and played[-1].startswith("yes_sir")


def test_live_keeps_gemini_voice_for_content(lib):
    live, played = _live(lib)
    state, player = _turn(live, "Какие новости?", [call("search_web", msg="Вот результаты: …")])
    assert state["turn_mode"] == "gemini" and player.chunks == [b"a1", b"a2"] and not played
    state, player = _turn(live, "Спасибо, а какая завтра погода в Москве?")
    assert state["turn_mode"] == "gemini" and not played


def test_live_holds_start_until_transcript(lib):
    live, played = _live(lib)
    state = {"user_text": "", "reply_text": "", "actions": [], "last_activity": 0}
    player = FakePlayer()
    live._handle_audio(b"a1", state, player)
    assert state.get("turn_mode") is None and state["held"] == [b"a1"] and not player.chunks
    state["user_text"] = "Спасибо."
    live._handle_audio(None, state, player)
    assert state["turn_mode"] == "sample" and not player.chunks
