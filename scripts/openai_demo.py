"""Реальный сценарий с OpenAI и НАСТОЯЩИМИ действиями на компьютере (голосовой цикл).

Voice → STT → OpenAI → Tool → Result → OpenAI → TTS. Вместо микрофона подаётся синтезированная речь,
всё остальное настоящее: открывается YouTube, выполняется поиск, меняется громкость (потом
возвращается как было), включается видео и ставится на паузу.

    python scripts/openai_demo.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jarvis.config

os.environ.setdefault("BRAIN_MODE", "llm")
from jarvis.core.assistant import Assistant, AssistantListener
from jarvis.services import audio
from jarvis.voice.audio import QueueSource
from tests.voice_fixtures import speech

SCRIPT = [
    ("Джарвис, открой YouTube, найди музыку для учёбы и поставь громкость 30 процентов.", False),
    ("Включи второй результат.", True),
    ("Джарвис, поставь видео на паузу.", False),
]
T0 = time.time()


def ts() -> str:
    return f"{time.time() - T0:5.1f}s"


class Console(AssistantListener):
    spoken = 0
    last = None

    def on_state(self, state):
        if state != self.last:
            print(f"{ts()}   [{state.upper()}]", flush=True)
            self.last = state

    def on_transcript(self, text):
        print(f"{ts()}   STT: «{text}»", flush=True)

    def on_action(self, action_id, text, status, detail=""):
        if status != "running":
            print(f"{ts()}   {'✔' if status == 'ok' else '✖'} tool: {text} — {detail[:90]}", flush=True)

    def on_spoken(self, text):
        Console.spoken += 1
        print(f"{ts()}   🔊 JARVIS: {text}", flush=True)

    def on_message(self, role, text):
        if role == "system":
            print(f"{ts()}   [!] {text}", flush=True)


def main():
    audio_in = [(t, speech(t), f) for t, f in SCRIPT]
    volume_before = audio.get_volume()
    src = QueueSource(realtime=True)
    console = Console()
    a = Assistant(console, enable_voice=True, audio_source_factory=lambda: src)
    if not a.brain.llm.available:
        print(f"[!] {a.brain.llm.error}")
        return
    ok, msg = a.brain.llm.check_connection()
    print(f"AI Brain: {a.brain.describe()} — {msg}")
    if not ok:
        return
    a.set_wake_word(True)
    time.sleep(1)
    global T0
    T0 = time.time()
    try:
        for text, pcm, followup in audio_in:
            if followup and not a.voice_loop.in_followup:
                print("   (окно продолжения закрылось — фраза пойдёт с активатором)")
            print(f"\n{ts()} 🗣 «{text}»")
            before = console.spoken
            src.push(pcm)
            deadline = time.time() + 90
            while (console.spoken == before or a.busy or not a._jobs.empty() or a.speaker.speaking) \
                    and time.time() < deadline:
                time.sleep(0.05)
            time.sleep(0.4)
        print(f"\nКонтекст: сайт={a.dialog.active_site}, поиск={a.dialog.last_query!r}, "
              f"играло={a.dialog.now_playing!r}")
    finally:
        audio.set_volume(volume_before)
        print(f"Громкость возвращена: {volume_before}%")
        a.shutdown()


if __name__ == "__main__":
    main()
