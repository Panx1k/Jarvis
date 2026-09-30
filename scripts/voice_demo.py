"""Демонстрация полного голосового цикла с настоящими инструментами и голосом.

Вместо микрофона в конвейер подаётся синтезированная речь (QueueSource) — всё остальное настоящее:
VAD → офлайн-активатор → Google STT → AI Brain → Tools → Edge TTS → динамики.

    python scripts/voice_demo.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis.core.assistant import Assistant, AssistantListener
from jarvis.voice.audio import QueueSource
from tests.voice_fixtures import speech

SCRIPT = [
    ("Джарвис", False),
    ("Открой YouTube и найди музыку для учёбы", True),
    ("Включи первый результат", True),
    ("Джарвис, поставь на паузу", False),
]

T0 = time.time()


def ts() -> str:
    return f"{time.time() - T0:5.1f}s"


class Console(AssistantListener):
    def __init__(self):
        self.last_state = None
        self.spoken = 0

    def on_state(self, state):
        if state != self.last_state:
            print(f"{ts()}   [{state.upper()}]", flush=True)
            self.last_state = state

    def on_transcript(self, text):
        print(f"{ts()}   распознано: «{text}»", flush=True)

    def on_spoken(self, text):
        self.spoken += 1
        print(f"{ts()}   🔊 JARVIS: {text}", flush=True)

    def on_action(self, action_id, text, status, detail=""):
        if status != "running":
            print(f"{ts()}   {'✔' if status == 'ok' else '✖'} {text} — {detail}", flush=True)


def main():
    audio = [(text, speech(text), followup) for text, followup in SCRIPT]
    src = QueueSource(realtime=True)
    console = Console()
    a = Assistant(console, enable_voice=True, audio_source_factory=lambda: src)
    a.brain.mode = "rules"
    a.set_wake_word(True)
    time.sleep(1.0)
    global T0
    T0 = time.time()

    def wait_reply(before: int) -> None:
        deadline = time.time() + 40
        while (console.spoken == before or a.busy or not a._jobs.empty() or a.speaker.speaking) \
                and time.time() < deadline:
            time.sleep(0.05)

    for text, pcm, followup in audio:
        if followup:
            assert a.voice_loop.in_followup, "окно продолжения закрылось"
        print(f"\n{ts()} 🗣 Пользователь: «{text}»" + ("  (без «Jarvis» — продолжение диалога)" if followup else ""))
        before = console.spoken
        src.push(pcm)
        wait_reply(before)
        time.sleep(0.5)
    time.sleep(1)
    print(f"\n{ts()} контекст: активный сайт={a.dialog.active_site}, поиск={a.dialog.last_query!r}, "
          f"играло={a.dialog.now_playing!r}")
    a.shutdown()


if __name__ == "__main__":
    main()
