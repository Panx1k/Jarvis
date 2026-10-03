"""Источники звука и детектор речи (VAD), общие для push-to-talk и постоянного прослушивания.

Источник выдаёт блоки PCM16 mono 16 кГц по 30 мс. Микрофон можно подменить (QueueSource) —
так весь голосовой конвейер тестируется без человека.
"""
from __future__ import annotations

import collections
import queue
import time
from dataclasses import dataclass

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


NOISE_LEVELS = {"off": "Выключено", "light": "Лёгкое", "strong": "Сильное"}
LANGUAGES = {"auto": "AUTO (русский + английский)", "ru": "Русский", "en": "English"}


@dataclass
class VoiceInputSettings:
    """Настройки голосового ввода (config/settings.json → voice.input.*)."""
    silence_timeout: float = 0.9
    command_timeout: float = 12.0
    min_speech: float = 0.25
    noise_threshold: float = 350.0
    sensitivity: int = 5
    noise_suppression: str = "light"
    language: str = "auto"
    stt_threshold: float = 0.45
    continuation: float = 1.5
    push_to_talk: bool = True
    hotkey: str = "ctrl+alt+j"

    @classmethod
    def load(cls, settings) -> "VoiceInputSettings":
        raw = (settings.get("voice.input", {}) or {}) if settings is not None else {}
        out = cls()
        for name in out.__dataclass_fields__:
            if name in raw and raw[name] is not None:
                default = getattr(out, name)
                try:
                    value = type(default)(raw[name]) if not isinstance(default, bool) else bool(raw[name])
                except (TypeError, ValueError):
                    continue
                setattr(out, name, value)
        out.silence_timeout = min(3.0, max(0.3, out.silence_timeout))
        out.command_timeout = min(40.0, max(3.0, out.command_timeout))
        out.sensitivity = min(10, max(1, out.sensitivity))
        out.noise_threshold = min(3000.0, max(50.0, out.noise_threshold))
        if out.noise_suppression not in NOISE_LEVELS:
            out.noise_suppression = "light"
        if out.language not in LANGUAGES:
            out.language = "auto"
        return out

    @property
    def factor(self) -> float:
        """Во сколько раз речь должна быть громче фона: чувствительность 1 → 3.9, 5 → 2.7, 10 → 1.5."""
        return max(1.5, 4.2 - 0.3 * self.sensitivity)

    def vad(self, **overrides) -> "EnergyVAD":
        kw = dict(min_threshold=self.noise_threshold, factor=self.factor, silence_end=self.silence_timeout,
                  max_phrase=self.command_timeout, min_speech=self.min_speech)
        kw.update(overrides)
        return EnergyVAD(**kw)


class EnergyVAD:
    """Сегментация речи по энергии с адаптацией к фоновому шуму.

    process(chunk) → "start" (началась речь), "end" (фраза закончилась), "noise" (это был короткий звук —
    щелчок клавиатуры, мыши, стук — а не речь) или None. После "end" запись фразы доступна через take().

    Речь начинается, только если звук громче порога start_blocks блоков подряд (≈ 90 мс): одиночные щелчки
    клавиатуры и мыши короче. Порог подстраивается под фон (вентиляторы, шум компьютера). Фраза заканчивается
    после silence_end секунд тишины — короткие паузы между словами её не обрывают.
    """

    def __init__(self, min_threshold: float = 350.0, factor: float = 2.8, silence_end: float = 0.9,
                 max_phrase: float = 15.0, preroll: float = 0.4, calibrate: float = 0.25, min_speech: float = 0.25,
                 start_blocks: int = 3):
        self.min_threshold = min_threshold
        self.factor = factor
        self.silence_end = silence_end
        self.max_phrase = max_phrase
        self.min_speech = min_speech
        self.start_blocks = max(1, start_blocks)
        self._preroll: collections.deque[bytes] = collections.deque(maxlen=max(1, int(preroll / BLOCK_SEC)))
        self._calibrate_blocks = int(calibrate / BLOCK_SEC)
        self._noise: list[float] = []
        self.threshold = min_threshold
        self.frames: list[bytes] = []
        self.speaking = False
        self.level = 0.0
        self.hold_open = False
        self.speech_level = 1200.0
        self.voiced = 0
        self.noise_floor = 0.0
        self._loud_run = 0
        self._silence = 0.0

    def reset(self) -> None:
        self.frames = []
        self.speaking = False
        self._silence = 0.0
        self._loud_run = 0
        self.voiced = 0
        self._preroll.clear()

    @property
    def voiced_seconds(self) -> float:
        return self.voiced * BLOCK_SEC

    def _begin(self) -> str:
        self.speaking = True
        self._silence = 0.0
        self.frames = list(self._preroll)
        self.voiced = self._loud_run
        return "start"

    def process(self, chunk: bytes) -> str | None:
        level = rms(chunk)
        self.level = min(1.0, level / 3000.0)
        if len(self._noise) < self._calibrate_blocks:
            self._noise.append(level)
            self._preroll.append(chunk)
            if len(self._noise) == self._calibrate_blocks:
                noise = float(np.median(self._noise))
                self.noise_floor = noise
                if noise > self.speech_level:
                    self.threshold = self.min_threshold * 1.5
                    self._loud_run = len(self._noise)
                    return self._begin()
                self.threshold = max(self.min_threshold, noise * self.factor)
            return None
        if not self.speaking:
            self._preroll.append(chunk)
            if level > self.threshold:
                self._loud_run += 1
                if self._loud_run >= self.start_blocks:
                    return self._begin()
                return None
            self._loud_run = 0
            self.noise_floor = self.noise_floor * 0.98 + level * 0.02 if self.noise_floor else level
            self.threshold = max(self.min_threshold, self.threshold * 0.995 + level * self.factor * 0.005)
            return None
        self.frames.append(chunk)
        if level >= self.threshold * 0.75:
            self.voiced += 1
            self._silence = 0.0
        else:
            self._silence += BLOCK_SEC
        duration = len(self.frames) * BLOCK_SEC
        if duration >= self.max_phrase or (self._silence >= self.silence_end and not self.hold_open):
            self.speaking = False
            self._loud_run = 0
            if self.voiced_seconds < self.min_speech and not self.hold_open:
                self.frames = []
                self._preroll.clear()
                return "noise"
            return "end"
        return None

    def take(self) -> bytes:
        pcm = b"".join(self.frames)
        self.frames = []
        self._preroll.clear()
        return pcm


NOISE_PARAMS = {"strong": (1.0, 0.4)}


def enhance(pcm: bytes, noise: str = "light", agc: bool = True, highpass: float = 100.0) -> tuple[bytes, str]:
    """Подготовить фразу к распознаванию.

    light (по умолчанию): убрать гул ниже highpass Гц (вентиляторы, корпус, сеть 50 Гц) и выровнять громкость
    тихой речи (AGC). strong: дополнительно мягко приглушить постоянный фон (spectral gating по самым тихим
    участкам фразы, не ниже 40 %). Сильный режим только для очень шумных мест: проверка на Google STT
    показала, что при громком ровном шуме он может съесть слово — у онлайн-распознавателя своё шумоподавление.
    Возвращает (pcm, описание для диагностики).
    """
    if noise == "off":
        return pcm, "без обработки"
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if x.size < SAMPLE_RATE // 4:
        return pcm, "слишком коротко"
    n, hop = 512, 256
    win = np.hanning(n + 1)[:-1].astype(np.float32)
    padded = np.concatenate([np.zeros(n, np.float32), x, np.zeros(n + hop, np.float32)])
    count = 1 + (len(padded) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(count)[:, None]
    spec = np.fft.rfft(padded[idx] * win, axis=1)
    freqs = np.fft.rfftfreq(n, 1.0 / SAMPLE_RATE)
    spec[:, freqs < highpass] = 0
    notes = [f"срез < {int(highpass)} Гц"]
    if noise in NOISE_PARAMS:
        over, floor = NOISE_PARAMS[noise]
        mag = np.abs(spec)
        energy = mag.sum(axis=1)
        quiet = energy <= np.percentile(energy, 15)
        if quiet.sum() >= 3:
            profile = mag[quiet].mean(axis=0)
            gain = np.clip(1.0 - over * profile / (mag + 1e-6), floor, 1.0)
            kernel = np.ones(3, np.float32) / 3
            gain = np.apply_along_axis(lambda g: np.convolve(g, kernel, mode="same"), 0, gain)
            spec *= gain
            speech = energy[~quiet].mean() if (~quiet).any() else energy.mean()
            snr = 10 * np.log10(max(speech, 1e-6) / max(energy[quiet].mean(), 1e-6))
            notes.append(f"шумоподавление {NOISE_LEVELS.get(noise, noise).lower()} (речь/фон {snr:.0f} дБ)")
    frames = np.fft.irfft(spec, n=n, axis=1).astype(np.float32)
    out = np.zeros(len(padded), np.float32)
    for i in range(count):
        out[i * hop:i * hop + n] += frames[i]
    y = out[n:n + x.size]
    if agc:
        block = SAMPLE_RATE // 50
        usable = y[: len(y) // block * block].reshape(-1, block) if len(y) >= block else y.reshape(1, -1)
        levels = np.sqrt((usable ** 2).mean(axis=1))
        loud = levels[levels >= np.percentile(levels, 70)]
        speech_rms = float(loud.mean()) if loud.size else float(np.sqrt((y ** 2).mean()))
        if speech_rms > 1:
            gain = float(np.clip(3000.0 / speech_rms, 0.5, 6.0))
            peak = float(np.abs(y).max()) or 1.0
            gain = min(gain, 30000.0 / peak)
            if abs(gain - 1) > 0.1:
                y = y * gain
                notes.append(f"усиление ×{gain:.1f}")
    return np.clip(y, -32768, 32767).astype(np.int16).tobytes(), ", ".join(notes)
