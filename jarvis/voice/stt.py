"""Распознавание речи. Движки взаимозаменяемы: реализуйте STTEngine и добавьте в make_stt()."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from jarvis.config import ROOT, env

log = logging.getLogger("jarvis.stt")


class STTError(RuntimeError):
    pass


class STTEngine:
    name = "base"

    def available(self) -> bool:
        return True

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        raise NotImplementedError


class GoogleSTT(STTEngine):
    """Бесплатный онлайн-распознаватель Google (через SpeechRecognition). Без ключа.

    Двуязычный режим: фраза распознаётся на основном языке (ru-RU); если результат в основном
    латиницей (сказано по-английски), она повторно распознаётся как en-US для точности.
    """
    name = "Google"

    def __init__(self, language: str = "ru-RU", second_language: str | None = "en-US"):
        import speech_recognition as sr

        self.sr = sr
        self.language = language
        self.second_language = second_language
        self.recognizer = sr.Recognizer()
        if second_language:
            self.name = f"Google ({language[:2]}+{second_language[:2]})"

    def _recognize(self, audio, language: str) -> str:
        try:
            return self.recognizer.recognize_google(audio, language=language)
        except self.sr.UnknownValueError:
            return ""
        except self.sr.RequestError as exc:
            raise STTError(f"сервис Google недоступен: {exc}") from exc

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        from jarvis.voice.lang import detect_lang

        audio = self.sr.AudioData(pcm, sample_rate, 2)
        text = self._recognize(audio, self.language)
        if self.second_language and (not text or detect_lang(text) == "en"):
            second = self._recognize(audio, self.second_language)
            if second and (not text or detect_lang(second) == "en"):
                return second
        return text


class VoskSTT(STTEngine):
    """Офлайн-распознавание (Vosk). Модель: python scripts/download_vosk_model.py"""
    name = "Vosk"

    def __init__(self, model_path: str):
        path = Path(model_path)
        if not path.is_absolute():
            path = ROOT / path
        self.path = path
        self._model = None

    def available(self) -> bool:
        return (self.path / "am").exists() or (self.path / "conf").exists()

    def _load(self):
        if self._model is None:
            if not self.available():
                raise STTError(f"модель Vosk не найдена: {self.path}")
            import vosk

            vosk.SetLogLevel(-1)
            self._model = vosk.Model(str(self.path))
        return self._model

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        import vosk

        rec = vosk.KaldiRecognizer(self._load(), sample_rate)
        rec.AcceptWaveform(pcm)
        return json.loads(rec.FinalResult()).get("text", "")


class AutoSTT(STTEngine):
    """Google, а при недоступности сети — Vosk (если модель скачана)."""

    def __init__(self, primary: STTEngine, fallback: STTEngine | None):
        self.primary, self.fallback = primary, fallback
        self.name = primary.name + (f" → {fallback.name}" if fallback else "")

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        try:
            return self.primary.transcribe(pcm, sample_rate)
        except STTError as exc:
            if not self.fallback:
                raise
            log.warning("%s — переключаюсь на %s", exc, self.fallback.name)
            return self.fallback.transcribe(pcm, sample_rate)


def load_class(spec: str):
    """«package.module:ClassName» → класс. Так подключаются собственные STT/TTS-движки."""
    import importlib

    module, _, cls = spec.partition(":")
    return getattr(importlib.import_module(module), cls)


def make_stt(language: str = "ru-RU") -> STTEngine:
    raw = env("STT_ENGINE", "auto") or "auto"
    if ":" in raw:
        return load_class(raw)()
    engine = raw.lower()
    vosk_engine = VoskSTT(env("VOSK_MODEL_PATH", "models/vosk-model-small-ru-0.22"))
    if engine == "vosk":
        return vosk_engine
    second = env("STT_SECOND_LANGUAGE", "en-US")
    google = GoogleSTT(language, None if second in ("", "none", "off") else second)
    if engine == "google":
        return google
    return AutoSTT(google, vosk_engine if vosk_engine.available() else None)
