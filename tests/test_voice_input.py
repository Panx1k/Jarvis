"""Голосовой ввод: VAD (щелчки, паузы, шум), склейка оборванной фразы, шумоподавление, шумовой гейт живого
режима, настройки. Микрофон подменён синтетическим звуком."""
from __future__ import annotations

import threading
import time

import numpy as np

from jarvis.voice.audio import BLOCK, SAMPLE_RATE, EnergyVAD, QueueSource, VoiceInputSettings, enhance
from jarvis.voice.gemini_live import NoiseGate
from jarvis.voice.loop import VoiceLoop, incomplete
from jarvis.voice.wake import WakeResult, WakeWordDetector

rng = np.random.default_rng(7)


def tone(seconds: float, amp: float = 4000, freq: float = 220) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    voice = np.sin(2 * np.pi * freq * t) + 0.5 * np.sin(2 * np.pi * freq * 2.7 * t)
    return amp * voice * (0.75 + 0.25 * np.sin(2 * np.pi * 3 * t))


def noise(seconds: float, amp: float = 60) -> np.ndarray:
    return rng.normal(0, amp, int(seconds * SAMPLE_RATE))


def pcm(x: np.ndarray) -> bytes:
    return np.clip(x, -32768, 32767).astype(np.int16).tobytes()


def events(vad: EnergyVAD, signal: np.ndarray) -> list[str]:
    data = pcm(signal)
    out = []
    for i in range(0, len(data) - BLOCK * 2 + 1, BLOCK * 2):
        e = vad.process(data[i:i + BLOCK * 2])
        if e:
            out.append(e)
    return out


def test_keyboard_clicks_are_not_speech():
    x = noise(1.0)
    for pos in range(3000, len(x) - 400, 2400):
        x[pos:pos + 240] += rng.normal(0, 9000, 240) * np.exp(-np.arange(240) / 50)
    assert "start" not in events(EnergyVAD(), x)


def test_short_bang_is_noise_not_command():
    x = np.concatenate([noise(0.5), tone(0.12, 6000), noise(1.5)])
    assert events(EnergyVAD(min_speech=0.25), x) == ["start", "noise"]


def test_pause_between_words_does_not_split_phrase():
    x = np.concatenate([noise(0.4), tone(0.6), noise(0.5), tone(0.7), noise(1.3)])
    vad = EnergyVAD(silence_end=0.9)
    assert events(vad, x) == ["start", "end"]
    assert len(vad.take()) / 2 / SAMPLE_RATE > 1.7


def test_long_silence_ends_phrase():
    x = np.concatenate([noise(0.4), tone(0.6), noise(1.2), tone(0.6), noise(1.2)])
    assert events(EnergyVAD(silence_end=0.9), x) == ["start", "end", "start", "end"]


def test_adaptive_threshold_ignores_fan():
    fan = noise(3.0, 700) + 500 * np.sin(2 * np.pi * 50 * np.arange(3 * SAMPLE_RATE) / SAMPLE_RATE)
    vad = EnergyVAD()
    assert "start" not in events(vad, fan)
    speech = np.concatenate([fan[:8000] + tone(0.5, 9000)[:8000], fan[:24000]])
    assert "start" in events(vad, speech)


def test_settings_map_to_vad():
    class S:
        def get(self, key, default=None):
            return {"voice.input": {"silence_timeout": 1.6, "sensitivity": 9, "command_timeout": 100,
                                    "noise_suppression": "weird", "language": "ru"}}.get(key, default)

    cfg = VoiceInputSettings.load(S())
    vad = cfg.vad()
    assert vad.silence_end == 1.6 and cfg.command_timeout == 40.0 and cfg.noise_suppression == "light"
    assert cfg.language == "ru" and vad.factor < VoiceInputSettings().factor


def test_incomplete_phrase_detection():
    assert incomplete("джарвис открой")
    assert incomplete("включи музыку в")
    assert not incomplete("джарвис открой браузер")
    assert not incomplete("джарвис")


class ScriptedDetector(WakeWordDetector):
    """Офлайн-активатор, «распознающий» фразы по порядку."""

    def __init__(self, texts):
        self.texts = list(texts)

    def finish(self, pcm):
        text = self.texts.pop(0) if self.texts else ""
        return WakeResult("джарвис" in text, text)


def run_loop(segments, texts, continuation=1.3):
    src = QueueSource(realtime=False)
    got = []
    done = threading.Event()
    cfg = VoiceInputSettings(continuation=continuation)
    loop = VoiceLoop(lambda: src, ScriptedDetector(texts),
                     on_utterance=lambda p, r, f: (got.append((len(p), r.text)), done.set()),
                     settings=lambda: cfg)
    for seg, pause in segments:
        src.push(pcm(seg), silence_after=pause)
    loop.start()
    done.wait(10)
    time.sleep(0.3)
    loop.stop()
    return got


def test_command_split_by_pause_is_merged():
    got = run_loop([(tone(0.8), 1.1), (tone(0.6), 1.5)], ["джарвис открой", "браузер"])
    assert len(got) == 1 and got[0][1] == "джарвис открой браузер", got


def test_complete_command_is_not_held():
    got = run_loop([(tone(0.8), 1.5)], ["джарвис открой браузер"])
    assert got == [(got[0][0], "джарвис открой браузер")]


def test_incomplete_command_sent_after_wait():
    got = run_loop([(tone(0.8), 3.0)], ["джарвис открой"], continuation=0.6)
    assert [t for _, t in got] == ["джарвис открой"]


def test_enhance_keeps_length_and_boosts_quiet_speech():
    quiet = np.concatenate([noise(0.3, 20), tone(1.0, 300), noise(0.3, 20)])
    out, note = enhance(pcm(quiet), "light")
    y = np.frombuffer(out, np.int16).astype(np.float32)
    assert len(out) == len(pcm(quiet))
    assert np.sqrt((y ** 2).mean()) > 3 * np.sqrt((quiet ** 2).mean()) and "усиление" in note


def test_enhance_removes_hum():
    t = np.arange(SAMPLE_RATE) / SAMPLE_RATE
    hum = 3000 * np.sin(2 * np.pi * 50 * t)
    out, _ = enhance(pcm(hum + tone(1.0, 2000)), "light")
    spec = np.abs(np.fft.rfft(np.frombuffer(out, np.int16).astype(np.float32)))
    freqs = np.fft.rfftfreq(SAMPLE_RATE, 1 / SAMPLE_RATE)
    assert spec[np.argmin(abs(freqs - 50))] < 0.05 * spec[np.argmin(abs(freqs - 220))]


def test_enhance_off_is_passthrough():
    data = pcm(tone(0.5))
    assert enhance(data, "off")[0] == data


def test_noise_gate_silences_background_and_passes_speech():
    gate = NoiseGate(min_threshold=350, factor=2.7, hangover=0.6)
    block = BLOCK * 2
    bg = pcm(noise(1.0, 120))
    sent = []
    for i in range(0, len(bg) - block + 1, block):
        c = bg[i:i + block]
        sent += gate.process(c, float(np.sqrt((np.frombuffer(c, np.int16).astype(float) ** 2).mean())), 120)
    assert all(not any(c) for c in sent)
    speech = pcm(tone(0.5, 3000))
    out = []
    for i in range(0, len(speech) - block + 1, block):
        out += gate.process(speech[i:i + block], 2100.0, 120)
    assert gate.open and sum(len(c) for c in out) >= len(speech) - block
    assert any(any(c) for c in out)
