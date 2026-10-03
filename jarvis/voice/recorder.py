"""Выбор микрофона и запись одной фразы (кнопка микрофона / push-to-talk)."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import numpy as np

from jarvis.voice.audio import (BLOCK_SEC, SAMPLE_RATE, AudioSource, EnergyVAD, MicrophoneError, MicrophoneSource,
                                VoiceInputSettings)

log = logging.getLogger("jarvis.recorder")

__all__ = ["Recorder", "MicrophoneError", "list_microphones", "pick_microphone"]

PTT_HOLD_SEC = 0.4


def list_microphones() -> list[str]:
    import sounddevice as sd

    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            out.append(f"{i}: {d['name']} ({sd.query_hostapis(d['hostapi'])['name']})")
    return out


def _input_devices() -> list[tuple[int, str, str]]:
    import sounddevice as sd

    apis = sd.query_hostapis()
    return [(i, d["name"], apis[d["hostapi"]]["name"]) for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0]


def _probe_level(device: int, seconds: float = 0.35) -> int:
    import sounddevice as sd

    try:
        x = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=device)
        sd.wait()
        return int(np.abs(x).max())
    except Exception:
        return -1


DEAD_LEVEL = 2


def microphones() -> list[tuple[int, str]]:
    """Физические микрофоны (MME, без «Переназначения звуковых устройств»)."""
    devices = [d for d in _input_devices() if d[2] == "MME"] or _input_devices()
    return [(i, name) for i, name, _ in devices
            if "переназначение" not in name.lower() and "mapper" not in name.lower()]


def _match(name_part: str, devices: list[tuple[int, str]]) -> tuple[int, str] | None:
    p = name_part.strip().lower()
    for i, name in devices:
        if p and (p in name.lower() or name.lower() in p):
            return i, name
    return None


def pick_microphone(preferred: str | None, remembered: str | None = None,
                    exclude: set[int] | None = None) -> tuple[int | None, str]:
    """Выбор микрофона.

    preferred  — MIC_DEVICE из .env (номер или часть имени, например «fifine») — выбирается всегда;
    remembered — имя микрофона, выбранного в прошлый раз (если он живой);
    иначе системный по умолчанию, если он живой, иначе вход с самым сильным сигналом.
    «Живой» = не выдаёт цифровую тишину (выключенная гарнитура / поднятая штанга микрофона).
    """
    import sounddevice as sd

    devices = [d for d in microphones() if d[0] not in (exclude or set())]
    if preferred:
        p = preferred.strip()
        if p.isdigit():
            idx = int(p)
            return idx, sd.query_devices(idx)["name"]
        found = _match(p, devices)
        if found:
            return found
        log.warning("Микрофон «%s» не найден — выбираю автоматически", p)
    levels = {i: _probe_level(i, 0.6) for i, _ in devices}
    alive = [(i, n) for i, n in devices if levels[i] > DEAD_LEVEL]
    default_idx = sd.default.device[0]
    if remembered:
        found = _match(remembered, alive)
        if found:
            return found
    for i, n in alive:
        if i == default_idx:
            return i, n
    if alive:
        i, n = max(alive, key=lambda d: levels[d[0]])
        log.info("Системный микрофон молчит — использую «%s»", n)
        return i, n
    default_name = sd.query_devices(default_idx)["name"] if default_idx is not None and default_idx >= 0 else "?"
    log.warning("Ни один микрофон не даёт сигнала — оставляю системный «%s»", default_name)
    return None, default_name


class Recorder:
    def __init__(self, device: str | int | None = None, remembered: str | None = None):
        self.preferred = str(device) if device not in (None, "") else None
        self.device, self.device_name = pick_microphone(self.preferred, remembered)
        log.info("Микрофон: «%s»", self.device_name)

    def set_device(self, index: int | None, name: str) -> None:
        self.device, self.device_name = index, name
        log.info("Микрофон переключён: «%s»", name)

    def repick(self) -> bool:
        """Текущий микрофон молчит — найти другой живой. True, если переключились."""
        exclude = {self.device} if self.device is not None else set()
        if self.device is None:
            import sounddevice as sd
            exclude = {sd.default.device[0]}
        index, name = pick_microphone(None, None, exclude)
        if index is None or index == self.device:
            return False
        self.set_device(index, name)
        return True

    def source(self) -> AudioSource:
        return MicrophoneSource(self.device)

    def record_phrase(self, stop: threading.Event | None = None, on_level: Callable[[float], None] | None = None,
                      wait_timeout: float = 7.0, max_phrase: float = 15.0, silence_end: float = 0.9,
                      holding: Callable[[], bool] | None = None, source: AudioSource | None = None,
                      settings: VoiceInputSettings | None = None) -> bytes | None:
        """Записать одну фразу: ждёт начала речи до wait_timeout, заканчивает на паузе silence_end.

        holding — push-to-talk: пока функция возвращает True дольше PTT_HOLD_SEC, фраза не заканчивается
        по тишине; как только кнопку отпустили — запись завершается. Короткое нажатие = обычный режим.
        stop — ручная остановка (повторное нажатие кнопки).
        settings — настройки голосового ввода (тишина, длительность, чувствительность) вместо значений по умолчанию.
        """
        vad = settings.vad() if settings else EnergyVAD(silence_end=silence_end, max_phrase=max_phrase)
        t0 = time.monotonic()
        held_long = False
        started = False
        with (source or self.source()) as src:
            while True:
                if stop is not None and stop.is_set():
                    break
                if holding is not None:
                    down = holding()
                    if down and time.monotonic() - t0 >= PTT_HOLD_SEC:
                        held_long = True
                    if held_long and not down:
                        break
                    vad.hold_open = held_long
                chunk = src.read(timeout=0.5)
                if chunk is None:
                    continue
                event = vad.process(chunk)
                if on_level:
                    on_level(vad.level)
                if event == "start":
                    started = True
                elif event == "noise":
                    started = False
                elif event == "end":
                    break
                elif not started and not held_long and time.monotonic() - t0 > wait_timeout:
                    return None
        if on_level:
            on_level(0.0)
        if not started or len(vad.frames) * BLOCK_SEC < 0.3:
            return None
        return vad.take()
