"""Проверка живого режима Gemini Live без человека.

Вместо микрофона подаётся синтезированная фраза «Джарвис, …»; дальше всё настоящее: офлайн-активатор,
сессия Gemini Live, инструменты JARVIS, голосовой ответ в динамики, закрытие сессии после тишины.

    python scripts/live_demo.py "Джарвис, какая сейчас громкость?"
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jarvis.config

os.environ.setdefault("LIVE_IDLE_SECONDS", "5")

from jarvis.core.assistant import Assistant, AssistantListener
from jarvis.voice.audio import QueueSource
from tests.voice_fixtures import speech

T0 = time.time()


class Console(AssistantListener):
    last = None

    def on_state(self, state):
        if state != self.last:
            print(f"{time.time() - T0:5.1f}s [{state.upper()}]", flush=True)
            self.last = state

    def on_message(self, role, text):
        print(f"{time.time() - T0:5.1f}s {role}: {text}", flush=True)

    def on_action(self, action_id, text, status, detail=""):
        if status != "running":
            print(f"{time.time() - T0:5.1f}s   tool: {text} — {detail}", flush=True)

    def on_voice_output(self, kind, info):
        detail = f" {info.get('intent')} · {info.get('file')}" if info.get("file") else ""
        print(f"{time.time() - T0:5.1f}s   voice: {kind.upper()}{detail}", flush=True)


def main():
    phrase = sys.argv[1] if len(sys.argv) > 1 else "Джарвис, какая сейчас громкость?"
    pcm = speech(phrase)
    wake_src = QueueSource(realtime=True)
    sources = iter([wake_src])
    factory = lambda: next(sources, None) or QueueSource(realtime=True)
    a = Assistant(Console(), enable_voice=True, audio_source_factory=factory)
    if not a.live:
        print("Живой режим недоступен:", a.voice_error or "нет ключа Gemini")
        return
    a.live_enabled = True
    a.set_wake_word(True)
    time.sleep(1)
    global T0
    T0 = time.time()
    print(f"🗣 «{phrase}» (длительность фразы {len(pcm) / 32000:.1f} с)")
    wake_src.push(pcm)
    deadline = time.time() + 60
    while time.time() < deadline and not (a.live.active or a.dialog.history):
        time.sleep(0.1)
    while time.time() < deadline and a.live.active:
        time.sleep(0.1)
    time.sleep(0.5)
    print("история:", [(t.user, t.reply, t.actions) for t in a.dialog.history])
    print("ошибка live:", a.live.error)
    a.shutdown()


if __name__ == "__main__":
    main()
