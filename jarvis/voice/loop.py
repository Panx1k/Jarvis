"""Фоновый голосовой цикл: Microphone → VAD → Wake Word → (команда) → обратный вызов ассистенту.

WAITING: слушаем фон, каждая фраза проверяется на «Jarvis».
Фраза с «Jarvis» (или любая фраза в «окне продолжения» после ответа ассистента) передаётся
ассистенту целиком: он распознаёт её основным STT, отрезает активатор и выполняет команду.
Пока ассистент думает/выполняет/говорит, звук игнорируется (is_blocked), чтобы не слышать самого себя.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from jarvis.voice.audio import AudioSource, EnergyVAD, MicrophoneError
from jarvis.voice.wake import WakeResult, WakeWordDetector

log = logging.getLogger("jarvis.voiceloop")


def peak(chunk: bytes) -> int:
    import numpy as np

    samples = np.frombuffer(chunk, dtype=np.int16)
    return int(np.abs(samples.astype(np.int32)).max()) if samples.size else 0


class VoiceLoop:
    def __init__(self, source_factory: Callable[[], AudioSource], detector: WakeWordDetector, *,
                 on_utterance: Callable[[bytes, WakeResult, bool], None],
                 on_wake: Callable[[], None] | None = None,
                 on_level: Callable[[float], None] | None = None,
                 on_followup_end: Callable[[], None] | None = None,
                 on_error: Callable[[str], None] | None = None,
                 on_dead_mic: Callable[[], bool] | None = None,
                 is_blocked: Callable[[], bool] = lambda: False,
                 max_phrase: float = 12.0):
        self.on_dead_mic = on_dead_mic
        self.dead_after = 3.0
        self.restart = threading.Event()
        self.source_factory = source_factory
        self.detector = detector
        self.on_utterance = on_utterance
        self.on_wake = on_wake or (lambda: None)
        self.on_level = on_level
        self.on_followup_end = on_followup_end or (lambda: None)
        self.on_error = on_error or (lambda msg: None)
        self.is_blocked = is_blocked
        self.max_phrase = max_phrase
        self._followup_until = 0.0
        self._running = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._run, name="voice-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()

    def followup(self, seconds: float) -> None:
        """Следующая фраза в течение seconds принимается без слова-активатора."""
        self._followup_until = time.monotonic() + seconds

    def cancel_followup(self) -> None:
        self._followup_until = 0.0

    @property
    def in_followup(self) -> bool:
        return time.monotonic() < self._followup_until

    def _run(self) -> None:
        while self._running.is_set():
            try:
                with self.source_factory() as src:
                    self._listen(src)
            except MicrophoneError as exc:
                self.on_error(str(exc))
                time.sleep(3)
            except Exception:
                log.exception("Сбой голосового цикла — перезапуск")
                time.sleep(1)

    def _listen(self, src: AudioSource) -> None:
        vad = EnergyVAD(max_phrase=self.max_phrase, silence_end=0.8)
        is_followup = False
        wake_heard = False
        window_open = False
        dead_since: float | None = None
        self.restart.clear()
        while self._running.is_set() and not self.restart.is_set():
            chunk = src.read(timeout=0.5)
            if chunk is None:
                continue
            if self.on_dead_mic is not None:
                if peak(chunk) <= 2:
                    dead_since = dead_since or time.monotonic()
                    if time.monotonic() - dead_since > self.dead_after:
                        dead_since = time.monotonic() + 27
                        if self.on_dead_mic():
                            return
                else:
                    dead_since = None
            if self.is_blocked():
                if vad.speaking:
                    vad.reset()
                    is_followup = wake_heard = False
                continue
            if window_open and not self.in_followup and not vad.speaking:
                window_open = False
                self.on_followup_end()
            window_open = window_open or self.in_followup

            event = vad.process(chunk)
            if self.on_level:
                self.on_level(vad.level)
            if event == "start":
                is_followup, wake_heard = self.in_followup, False
                self.detector.start()
                for frame in vad.frames:
                    wake_heard = self.detector.feed(frame) or wake_heard
                if is_followup:
                    self._followup_until = time.monotonic() + self.max_phrase
                if is_followup or wake_heard:
                    self.on_wake()
            elif vad.speaking:
                if self.detector.feed(chunk) and not (wake_heard or is_followup):
                    wake_heard = True
                    self.on_wake()
            if event == "end":
                pcm = vad.take()
                result = self.detector.finish(pcm)
                if is_followup or wake_heard or result.detected:
                    self._followup_until = 0.0
                    window_open = False
                    self.on_utterance(pcm, result, is_followup)
                is_followup = wake_heard = False
                if self.on_level:
                    self.on_level(0.0)
