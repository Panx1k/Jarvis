"""Модульные тесты голосового слоя: короткие ответы, английские команды, активатор, push-to-talk."""
from __future__ import annotations

import threading
import time

import numpy as np

from jarvis.brain.english import translate
from jarvis.tools.base import ToolResult
from jarvis.voice import responder
from jarvis.voice.audio import SAMPLE_RATE, QueueSource
from jarvis.voice.lang import detect_lang
from jarvis.voice.recorder import Recorder
from jarvis.voice.responder import ToolCall, compose
from jarvis.voice.wake import find_wake


def call(tool, ok=True, msg="", args=None, data=None, followup=None):
    return ToolCall(tool, args or {}, ToolResult(ok, msg, data or {}, followup))


def test_short_replies():
    assert compose("Открываю YouTube.", [call("open_site", args={"site": "ютуб"}, data={"site": "YouTube"})]) \
        == "YouTube открыт, сэр."
    assert compose("", [call("vpn_on")]) == "VPN включён, сэр."
    assert compose("", [call("open_app", ok=False, msg="Не нашёл приложение «фотошоп».")]) \
        == "Я не смог найти это приложение, сэр."
    assert compose("", [call("set_volume", data={"volume": 30})]) == "Громкость 30 процентов, сэр."
    assert compose("", [call("open_url", ok=False, msg="Ошибка при выполнении: WinError 1155")]) \
        == "Не удалось открыть ссылку, сэр."
    assert compose("", [call("unknown_plugin_tool")]) == "Готово, сэр."
    assert compose("", [], handled=False) == responder.NOT_UNDERSTOOD["ru"]
    assert compose("", [call("open_site", data={"site": "YouTube"}), call("search_youtube")]) == "Нашёл, сэр."


def test_short_replies_english_and_confirm():
    assert compose("", [call("vpn_off")], lang="en") == "VPN is off, sir."
    assert compose("", [call("close_app", ok=False, msg="…", followup=("kill_app", {}))]).endswith("да или нет.")
    long = "Первое предложение. " + "Очень длинное объяснение " * 20
    assert compose(long, []).endswith("Подробности на экране.")


def test_english_translation():
    assert translate("open YouTube and search there for lofi") == "открой YouTube а потом найди там lofi"
    assert translate("play the second result") == "включи второй результат"
    assert translate("set the volume to 40%") == "громкость 40"
    assert translate("tell me a joke") is None


def test_lang_detection():
    assert detect_lang("включи на ютубе imagine dragons") == "ru"
    assert detect_lang("Hey Jarvis, open Discord") == "en"
    assert detect_lang("Джарвис, open Discord") == "en"


def test_find_wake():
    assert find_wake("джервис открой ютюб")[0]
    assert find_wake("hey jarvis open youtube")[0]
    assert not find_wake("дарвин был учёным")[0]
    assert not find_wake("я вчера смотрел фильм про джарвиса и железного человека")[0]


def test_dead_microphone_triggers_switch():
    """Микрофон выдаёт цифровую тишину (выключенная гарнитура) → цикл просит переключить вход."""
    from jarvis.voice.audio import BLOCK_BYTES, AudioSource
    from jarvis.voice.loop import VoiceLoop
    from jarvis.voice.wake import WakeResult, WakeWordDetector

    class DeadMic(AudioSource):
        def read(self, timeout=0.5):
            time.sleep(0.03)
            return b"\x00" * BLOCK_BYTES

    class NoWake(WakeWordDetector):
        def finish(self, pcm):
            return WakeResult(False)

    switched = threading.Event()
    loop = VoiceLoop(lambda: DeadMic(), NoWake(), on_utterance=lambda *a: None,
                     on_dead_mic=lambda: switched.set() or True)
    loop.dead_after = 1.0
    loop.start()
    try:
        assert switched.wait(4), "переключение микрофона не запрошено"
    finally:
        loop.stop()


def _tone(seconds: float) -> bytes:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (np.sin(2 * np.pi * 220 * t) * 6000).astype(np.int16).tobytes()


def test_push_to_talk_ignores_pauses_while_held():
    rec = Recorder.__new__(Recorder)
    src = QueueSource(realtime=True)
    held = threading.Event()
    held.set()
    time.sleep(0.3)
    src.push(_tone(0.6) + b"\x00\x00" * int(1.5 * SAMPLE_RATE) + _tone(0.6), silence_after=0.5)
    threading.Timer(3.4, held.clear).start()
    t0 = time.time()
    pcm = rec.record_phrase(holding=held.is_set, source=src)
    assert pcm is not None
    seconds = len(pcm) / 2 / SAMPLE_RATE
    assert seconds > 2.4, seconds
    assert time.time() - t0 < 4.5


def test_add_sir():
    from jarvis.voice.responder import add_sir

    assert add_sir("Готово.") == "Готово, сэр."
    assert add_sir("Да, сэр. Слушаю.") == "Да, сэр. Слушаю."
    assert add_sir("Done!", "en") == "Done, sir!"
    assert add_sir("Какая громкость?") == "Какая громкость?"


def test_samples_bank_and_wake_only(tmp_path):
    import wave
    from types import SimpleNamespace as NS

    from jarvis.core.assistant import Assistant
    from jarvis.voice.samples import INDEX_FILE, SampleManager, load_wav

    for name in ("Да сэр", "Запрос выполнен сэр", "Лишний файл"):
        with wave.open(str(tmp_path / f"{name}.wav"), "wb") as w:
            w.setnchannels(2), w.setsampwidth(2), w.setframerate(48000)
            w.writeframes(b"\0\0\0\0" * 4800)
    bank = SampleManager(None, roots=[tmp_path], index_file=INDEX_FILE)
    assert bank.pick("ack").text == "Да, сэр." and bank.pick("startup") is None
    data, rate = load_wav(bank.pick("ack").path)
    assert rate == 48000 and data.shape == (4800, 2)

    short = b"\0\0" * 16000
    assert Assistant._wake_only(short, NS(text="джарвис"))
    assert not Assistant._wake_only(short, NS(text="джарвис открой ютуб"))
    assert not Assistant._wake_only(short * 3, NS(text="джарвис"))
