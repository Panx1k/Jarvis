"""Источники звука и детектор речи (VAD), общие для push-to-talk и постоянного прослушивания.

Источник выдаёт блоки PCM16 mono 16 кГц по 30 мс. Микрофон можно подменить (QueueSource) —
так весь голосовой конвейер тестируется без человека.
"""
from __future__ import annotations

import collections
import queue
import time

import numpy as np

SAMPLE_RATE = 16000
BLOCK_SEC = 0.03
BLOCK = int(SAMPLE_RATE * BLOCK_SEC)
BLOCK_BYTES = BLOCK * 2


class MicrophoneError(RuntimeError):
    pass


class AudioSource:
    """Контекстный менеджер: with source: chunk = source.read()"""

    def __enter__(self) -> "AudioSource":
        return self

    def __exit__(self, *exc) -> None:
        pass

    def read(self, timeout: float = 0.5) -> bytes | None:
        raise NotImplementedError


class MicrophoneSource(AudioSource):
    def __init__(self, device: int | None = None):
        self.device = device
        self._q: queue.Queue[bytes] = queue.Queue(maxsize=400)
        self._stream = None

    def __enter__(self):
        import sounddevice as sd

        def callback(indata, frames, t, status):
            try:
                self._q.put_nowait(bytes(indata))
            except queue.Full:
                pass

        try:
            self._stream = sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=BLOCK,
                                             device=self.device, callback=callback)
            self._stream.start()
        except Exception as exc:
            raise MicrophoneError(f"Не удалось открыть микрофон: {exc}") from exc
        return self

    def __exit__(self, *exc):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def read(self, timeout: float = 0.5) -> bytes | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None


class QueueSource(AudioSource):
    """Тестовый «микрофон»: push(pcm) — «сказать» фразу; между фразами идёт тишина с лёгким шумом."""

    def __init__(self, realtime: bool = False, noise: int = 40):
        self.realtime = realtime
        self.noise = noise
        self._buf = bytearray()
        self._q: queue.Queue[bytes] = queue.Queue()
        self._rng = np.random.default_rng(0)

    def push(self, pcm: bytes, silence_after: float = 1.2) -> None:
        self._q.put(pcm + self._silence(silence_after))

    def _silence(self, seconds: float) -> bytes:
        n = int(seconds * SAMPLE_RATE)
        return self._rng.integers(-self.noise, self.noise, n, dtype=np.int16).tobytes()

    def read(self, timeout: float = 0.5) -> bytes | None:
        if len(self._buf) < BLOCK_BYTES:
            try:
                self._buf.extend(self._q.get_nowait())
            except queue.Empty:
                self._buf.extend(self._silence(BLOCK_SEC))
        chunk = bytes(self._buf[:BLOCK_BYTES])
        del self._buf[:BLOCK_BYTES]
        if self.realtime:
            time.sleep(BLOCK_SEC)
        else:
            time.sleep(0.001)
        return chunk


def rms(chunk: bytes) -> float:
    samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0


class EnergyVAD:
    """Сегментация речи по энергии с адаптацией к фоновому шуму.

    process(chunk) → "start" (началась речь), "end" (фраза закончилась) или None.
    После "end" запись фразы доступна через take().
    """

    def __init__(self, min_threshold: float = 350.0, factor: float = 2.8, silence_end: float = 0.9,
                 max_phrase: float = 15.0, preroll: float = 0.4, calibrate: float = 0.25):
        self.min_threshold = min_threshold
        self.factor = factor
        self.silence_end = silence_end
        self.max_phrase = max_phrase
        self._preroll: collections.deque[bytes] = collections.deque(maxlen=max(1, int(preroll / BLOCK_SEC)))
        self._calibrate_blocks = int(calibrate / BLOCK_SEC)
        self._noise: list[float] = []
        self.threshold = min_threshold
        self.frames: list[bytes] = []
        self.speaking = False
        self.level = 0.0
        self.hold_open = False
        self.speech_level = 1200.0
        self._silence = 0.0

    def reset(self) -> None:
        self.frames = []
        self.speaking = False
        self._silence = 0.0
        self._preroll.clear()

    def process(self, chunk: bytes) -> str | None:
        level = rms(chunk)
        self.level = min(1.0, level / 3000.0)
        if len(self._noise) < self._calibrate_blocks:
            self._noise.append(level)
            self._preroll.append(chunk)
            if len(self._noise) == self._calibrate_blocks:
                noise = float(np.median(self._noise))
                if noise > self.speech_level:
                    self.threshold = self.min_threshold * 1.5
                    self.speaking = True
                    self._silence = 0.0
                    self.frames = list(self._preroll)
                    return "start"
                self.threshold = max(self.min_threshold, noise * self.factor)
            return None
        if not self.speaking:
            self._preroll.append(chunk)
            if level > self.threshold:
                self.speaking = True
                self._silence = 0.0
                self.frames = list(self._preroll)
                return "start"
            self.threshold = max(self.min_threshold, self.threshold * 0.995 + level * self.factor * 0.005)
            return None
        self.frames.append(chunk)
        self._silence = self._silence + BLOCK_SEC if level < self.threshold * 0.75 else 0.0
        duration = len(self.frames) * BLOCK_SEC
        if duration >= self.max_phrase or (self._silence >= self.silence_end and not self.hold_open):
            self.speaking = False
            return "end"
        return None

    def take(self) -> bytes:
        pcm = b"".join(self.frames)
        self.frames = []
        self._preroll.clear()
        return pcm
