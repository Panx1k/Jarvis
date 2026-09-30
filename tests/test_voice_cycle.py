"""Полный голосовой цикл без человека.

«Микрофон» подменён QueueSource, в который подаётся синтезированная речь. Дальше всё настоящее:
VAD → офлайн-активатор Vosk → Google STT → AI Brain → Tools → (записывающий) TTS.
Нужны интернет (Google STT, Edge TTS для синтеза тестовой речи) и модели Vosk в models/.
"""
from __future__ import annotations

import threading
import time

import pytest

from jarvis.config import ROOT
from jarvis.core.assistant import Assistant, AssistantListener
from jarvis.voice.audio import QueueSource
from tests.voice_fixtures import RecordingTTS, speech

pytestmark = pytest.mark.skipif(not (ROOT / "models" / "vosk-model-small-ru-0.22").is_dir(),
                                reason="нет модели Vosk (python scripts/download_vosk_model.py)")


class Log(AssistantListener):
    def __init__(self):
        self.states: list[str] = []
        self.transcripts: list[str] = []
        self.messages: list[tuple[str, str]] = []
        self.lock = threading.Lock()

    def on_state(self, state):
        with self.lock:
            if not self.states or self.states[-1] != state:
                self.states.append(state)

    def on_transcript(self, text):
        self.transcripts.append(text)

    def on_message(self, role, text):
        self.messages.append((role, text))


PHRASES = [("Джарвис", "ru-RU-DmitryNeural"), ("Какая сейчас громкость", "ru-RU-DmitryNeural"),
           ("Который час", "ru-RU-DmitryNeural"), ("Привет, как у тебя дела сегодня", "ru-RU-DmitryNeural"),
           ("Джарвис, какое сегодня число", "ru-RU-DmitryNeural"), ("Jarvis, what time is it?", "en-GB-RyanNeural")]


@pytest.fixture(scope="module")
def rig():
    for text, voice in PHRASES:
        speech(text, voice)
    src = QueueSource(realtime=True)
    log = Log()
    a = Assistant(log, enable_voice=True, audio_source_factory=lambda: src)
    tts = RecordingTTS()
    a.speaker.engine = tts
    a.brain.mode = "rules"
    a.live_enabled = False
    a.samples = None
    assert a.set_wake_word(True)
    time.sleep(0.5)
    yield a, src, log, tts
    a.shutdown()


def utter(rig, text, voice="ru-RU-DmitryNeural", timeout=25.0):
    """«Сказать» фразу и дождаться голосового ответа. Возвращает произнесённое или None."""
    a, src, log, tts = rig
    n = len(tts.said)
    src.push(speech(text, voice))
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(tts.said) > n and not a.busy and a._jobs.empty():
            return tts.said[-1][0]
        time.sleep(0.05)
    return None


def test_wake_then_command_then_followup(rig):
    a, src, log, tts = rig
    log.states.clear()
    assert utter(rig, "Джарвис") == "Да, сэр. Слушаю."
    assert a.voice_loop.in_followup
    reply = utter(rig, "Какая сейчас громкость")
    assert reply and reply.startswith("Громкость"), (reply, log.transcripts)
    s = log.states
    for st in ("listening", "thinking", "executing", "speaking"):
        assert st in s, s
    reply = utter(rig, "Который час")
    assert reply and reply.startswith("Сейчас"), (reply, log.transcripts)


def test_background_speech_ignored(rig):
    a, src, log, tts = rig
    a.voice_loop.cancel_followup()
    n = len(tts.said)
    src.push(speech("Привет, как у тебя дела сегодня"))
    time.sleep(6)
    assert len(tts.said) == n, tts.said[n:]


def test_one_breath_command(rig):
    a, src, log, tts = rig
    a.voice_loop.cancel_followup()
    reply = utter(rig, "Джарвис, какое сегодня число")
    assert reply and reply.startswith("Сегодня"), (reply, log.transcripts)


def test_english_command(rig):
    a, src, log, tts = rig
    a.voice_loop.cancel_followup()
    reply = utter(rig, "Jarvis, what time is it?", voice="en-GB-RyanNeural")
    assert reply and reply.startswith("It's"), (reply, log.transcripts)
    assert tts.said[-1][1] == "en"
    a.lang = "ru"
