"""Локальный нейросинтез речи Coqui XTTS-v2 (модель в папке Voice/).

Лицензия модели — Coqui Public Model License (только некоммерческое использование), см. Voice/LICENSE.txt.

Голос:
    TTS_XTTS_SPEAKER      — встроенный голос модели (по умолчанию «Damien Black»; список: python main.py --voices)
    TTS_XTTS_SPEAKER_WAV  — свой образец голоса (WAV 6–30 с) — только голос, на который у вас есть права
                            (ваш собственный или записанный с согласия человека)
    TTS_XTTS_EFFECT       — none | jarvis (лёгкое «динамиковое» эхо)
    TTS_XTTS_MODEL_DIR    — папка модели (по умолчанию Voice)

Модель грузится в фоне (~13 с); пока она не готова, говорит запасной движок (Edge/SAPI).
Звук воспроизводится потоково — первые слова звучат, пока остальная фраза ещё синтезируется.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

from jarvis.config import ROOT, env
from jarvis.voice.tts import TTSEngine

log = logging.getLogger("jarvis.xtts")

SAMPLE_RATE = 24000
LANGS = {"ru": "ru", "en": "en"}


class NotReady(RuntimeError):
    """Модель ещё загружается — пусть скажет запасной движок."""


class JarvisEffect:
    """Лёгкие ранние отражения («голос из динамиков костюма»). Работает по кускам потока (overlap-add)."""

    def __init__(self, rate: int = SAMPLE_RATE):
        ir = np.zeros(int(0.05 * rate), dtype=np.float32)
        ir[0] = 1.0
        for delay_ms, gain in ((17, 0.22), (29, 0.14), (43, 0.08)):
            ir[int(delay_ms / 1000 * rate)] = gain
        self.ir = ir
        self.tail = np.zeros(len(ir) - 1, dtype=np.float32)

    def process(self, chunk: np.ndarray) -> np.ndarray:
        out = np.convolve(chunk, self.ir)
        out[:len(self.tail)] += self.tail[:len(out)]
        n = len(chunk)
        self.tail = out[n:].copy()
        return (out[:n] / 1.3).astype(np.float32)


class TailAligner:
    """XTTS после конца фразы иногда договаривает лишние звуки («…лишнее слово»). Офлайн-модель Vosk
    в режиме грамматики (только слова этой фразы) находит конец последнего настоящего слова (~0.1 с),
    и всё после него отрезается."""

    def __init__(self):
        self._models: dict[str, object] = {}
        self._lock = threading.Lock()

    def _model(self, lang: str):
        import vosk

        with self._lock:
            if lang not in self._models:
                path = env("VOSK_MODEL_PATH_EN" if lang == "en" else "VOSK_MODEL_PATH",
                           "models/vosk-model-small-en-us-0.15" if lang == "en" else "models/vosk-model-small-ru-0.22")
                p = Path(path) if Path(path).is_absolute() else ROOT / path
                vosk.SetLogLevel(-1)
                self._models[lang] = vosk.Model(str(p)) if p.is_dir() else None
            return self._models[lang]

    def cut(self, wav: np.ndarray, text: str, lang: str, rate: int = SAMPLE_RATE) -> np.ndarray | None:
        """Обрезанный звук или None, если последнее слово не нашлось (тогда — обрезка по громкости)."""
        import audioop
        import json
        import re

        import vosk

        model = self._model("en" if lang == "en" else "ru")
        pattern = r"[a-z']+" if lang == "en" else r"[а-яё]+"
        words = re.findall(pattern, text.lower().replace("ё", "е"))
        if model is None or not words:
            return None
        pcm = (np.clip(wav, -1, 1) * 32767).astype(np.int16).tobytes()
        pcm16 = audioop.ratecv(pcm, 2, 1, rate, 16000, None)[0]
        rec = vosk.KaldiRecognizer(model, 16000, json.dumps(sorted(set(words)) + ["[unk]"], ensure_ascii=False))
        rec.SetWords(True)
        rec.AcceptWaveform(pcm16)
        result = json.loads(rec.FinalResult()).get("result", [])
        ends = [w["end"] for w in result if w.get("word") == words[-1]]
        if not ends:
            return None
        out = wav[:min(wav.size, int((ends[-1] + 0.15) * rate))].copy()
        fade = min(int(0.04 * rate), out.size)
        out[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
        return out


class GpuMonitor:
    """Загрузка видеокарты (NVML). Если GPU занят игрой, нейроголос на нём будет тормозить и отнимет кадры
    у игры — тогда фразу лучше сказать облачным голосом."""

    def __init__(self, threshold: int):
        self.threshold = threshold
        self._handle = None
        self._cache = (0.0, False)
        try:
            import pynvml

            pynvml.nvmlInit()
            self._nvml = pynvml
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:
            self._nvml = None

    def busy(self) -> bool:
        import time

        if self._handle is None or self.threshold <= 0:
            return False
        now = time.monotonic()
        if now - self._cache[0] < 3:
            return self._cache[1]
        samples = []
        for _ in range(3):
            samples.append(self._nvml.nvmlDeviceGetUtilizationRates(self._handle).gpu)
            time.sleep(0.03)
        busy = min(samples) >= self.threshold
        self._cache = (now, busy)
        return busy


class XttsTTS(TTSEngine):
    def __init__(self, model_dir: str | None = None, speaker: str | None = None, speaker_wav: str | None = None,
                 effect: str | None = None):
        d = Path(model_dir or env("TTS_XTTS_MODEL_DIR", "Voice"))
        self.model_dir = d if d.is_absolute() else ROOT / d
        self.speaker = speaker or env("TTS_XTTS_SPEAKER", "Damien Black")
        wav = speaker_wav or env("TTS_XTTS_SPEAKER_WAV")
        self.speaker_wav = (Path(wav) if Path(wav).is_absolute() else ROOT / wav) if wav else None
        self.effect = (effect or env("TTS_XTTS_EFFECT", "none") or "none").lower()
        self.name = f"XTTS ({self.speaker_wav.stem if self.speaker_wav else self.speaker})"
        self.model = None
        self.device = "cpu"
        self.error: str | None = None
        self._latents = None
        self._aligner = TailAligner()
        self.gpu = GpuMonitor(int(env("TTS_XTTS_GPU_BUSY", "40") or 40))
        self._ready = threading.Event()
        self._lock = threading.Lock()
        if not (self.model_dir / "model.pth").exists():
            self.error = f"модель XTTS не найдена в {self.model_dir}"
            self._ready.set()
        else:
            threading.Thread(target=self._load, name="xtts-load", daemon=True).start()

    def _load(self) -> None:
        try:
            import torch
            from TTS.tts.configs.xtts_config import XttsConfig
            from TTS.tts.models.xtts import Xtts

            cfg = XttsConfig()
            cfg.load_json(str(self.model_dir / "config.json"))
            model = Xtts.init_from_config(cfg)
            model.load_checkpoint(cfg, checkpoint_dir=str(self.model_dir), eval=True, use_deepspeed=False)
            if torch.cuda.is_available():
                try:
                    model.cuda()
                    self.device = "cuda"
                except RuntimeError as exc:
                    log.warning("XTTS: GPU недоступен (%s) — синтез на CPU", exc)
                    model.cpu()
            self.model = model
            self._latents = self._conditioning()
            self._full(self.prepare_text("Готов.", "ru"), "ru")
            self._full(self.prepare_text("Ready.", "en"), "en")
            log.info("XTTS готов: голос «%s», устройство %s", self.name, self.device)
        except Exception as exc:
            self.error = f"XTTS не загрузился: {type(exc).__name__}: {exc}"
            log.exception("XTTS")
        finally:
            self._ready.set()

    def _conditioning(self):
        if self.speaker_wav:
            if not self.speaker_wav.exists():
                raise FileNotFoundError(f"образец голоса не найден: {self.speaker_wav}")
            return self.model.get_conditioning_latents(audio_path=[str(self.speaker_wav)])
        speakers = self.model.speaker_manager.speakers
        if self.speaker not in speakers:
            log.warning("XTTS: голоса «%s» нет — беру «Damien Black»", self.speaker)
            self.speaker = "Damien Black"
            self.name = f"XTTS ({self.speaker})"
        s = speakers[self.speaker]
        return s["gpt_cond_latent"], s["speaker_embedding"]

    def speakers(self) -> list[str]:
        self._ready.wait(60)
        return list(self.model.speaker_manager.speakers) if self.model else []

    @property
    def ready(self) -> bool:
        return self.model is not None

    SHORT = 220
    TEMPERATURE = 0.5

    @staticmethod
    def prepare_text(text: str, lang: str) -> str:
        """XTTS обрывает последнее слово коротких фраз («Гото…» вместо «Готово») и дорисовывает звуки.
        Если ключевое слово не последнее — звучит чисто; «, сэр» к тому же в духе JARVIS."""
        t = text.strip()
        if t and t[-1] not in ".!?…":
            t += "."
        words = len(t.split())
        if words <= 4 and len(t) <= 40:
            low = t.lower()
            if lang == "en" and "sir" not in low:
                t = t[:-1] + ", sir" + t[-1]
            elif lang != "en" and "сэр" not in low:
                t = t[:-1] + ", сэр" + t[-1]
        return t

    @staticmethod
    def trim_tail(wav: np.ndarray, rate: int = SAMPLE_RATE) -> np.ndarray:
        """Обрезать хвост после последнего слова: XTTS иногда добавляет тихое бормотание/шум в конце."""
        if wav.size < rate // 2:
            return wav
        frame = int(0.02 * rate)
        n = wav.size // frame
        energy = np.sqrt(np.mean(wav[:n * frame].reshape(n, frame) ** 2, axis=1))
        peak = energy.max() or 1.0
        loud = np.where(energy > peak * 0.12)[0]
        if loud.size == 0:
            return wav
        runs, start = [], loud[0]
        for a, b in zip(loud, loud[1:]):
            if b != a + 1:
                runs.append((start, a))
                start = b
        runs.append((start, loud[-1]))
        speech = [r for r in runs if r[1] - r[0] >= 2] or runs
        end = min(wav.size, (speech[-1][1] + 1) * frame + int(0.18 * rate))
        out = wav[:end].copy()
        fade = min(int(0.05 * rate), out.size)
        out[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
        return out

    def _chunks(self, text: str, lang: str):
        gpt_cond_latent, speaker_embedding = self._latents
        for chunk in self.model.inference_stream(text, LANGS.get(lang, "ru"), gpt_cond_latent, speaker_embedding,
                                                 stream_chunk_size=20, enable_text_splitting=True,
                                                 temperature=self.TEMPERATURE):
            yield chunk.detach().cpu().numpy().astype(np.float32).reshape(-1)

    def _full(self, text: str, lang: str) -> np.ndarray:
        gpt_cond_latent, speaker_embedding = self._latents
        out = self.model.inference(text, LANGS.get(lang, "ru"), gpt_cond_latent, speaker_embedding,
                                   temperature=self.TEMPERATURE, enable_text_splitting=True,
                                   max_new_tokens=int(min(600, len(text) * 2.8 + 20)))
        wav = out["wav"]
        wav = (wav.detach().cpu().numpy() if hasattr(wav, "detach") else np.asarray(wav)).astype(np.float32).reshape(-1)
        try:
            aligned = self._aligner.cut(wav, text, lang)
        except Exception as exc:
            log.debug("выравнивание хвоста: %s", exc)
            aligned = None
        return aligned if aligned is not None else self.trim_tail(wav)

    def synthesize(self, text: str, lang: str = "ru") -> np.ndarray:
        if not self.ready:
            raise NotReady(self.error or "XTTS загружается")
        with self._lock:
            return self._full(self.prepare_text(text, lang), lang)

    def speak(self, text: str, stop: threading.Event, lang: str = "ru") -> None:
        if not self._ready.is_set() or not self.ready:
            raise NotReady(self.error or "XTTS ещё загружается")
        if self.device == "cuda" and self.gpu.busy():
            raise NotReady("видеокарта занята (игра) — говорит запасной голос")
        import sounddevice as sd

        text = self.prepare_text(text, lang)
        effect = JarvisEffect() if self.effect == "jarvis" else None
        with self._lock:
            if len(text) <= self.SHORT:
                wav = self._full(text, lang)
                if stop.is_set():
                    return
                if effect:
                    wav = np.concatenate([effect.process(wav), effect.tail])
                sd.play(np.clip(wav, -1, 1), SAMPLE_RATE)
                while sd.get_stream().active:
                    if stop.is_set():
                        sd.stop()
                        break
                    sd.sleep(30)
                return
            with sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as out:
                for chunk in self._chunks(text, lang):
                    if stop.is_set():
                        break
                    if effect:
                        chunk = effect.process(chunk)
                    out.write(np.clip(chunk, -1, 1).reshape(-1, 1))
                if effect is not None and not stop.is_set():
                    out.write(np.clip(effect.tail, -1, 1).reshape(-1, 1))

    def prewarm(self, phrases) -> None:
        self._ready.wait(120)
