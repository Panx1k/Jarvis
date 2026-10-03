"""Распознавание речи. Движки взаимозаменяемы: реализуйте STTEngine и добавьте в make_stt()."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.config import ROOT, env

log = logging.getLogger("jarvis.stt")


class STTError(RuntimeError):
    pass


@dataclass
class STTResult:
    """Что сказал пользователь: лучший вариант, уверенность распознавателя (если он её даёт) и другие варианты."""
    text: str
    confidence: float | None = None
    alternatives: list[str] = field(default_factory=list)
    lang: str = "ru"
    engine: str = ""
    audio_note: str | None = None


class STTEngine:
    name = "base"

    def available(self) -> bool:
        return True

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        raise NotImplementedError

    def recognize(self, pcm: bytes, sample_rate: int = 16000) -> STTResult:
        """Распознать с вариантами. Движки без вариантов возвращают один текст."""
        text = self.transcribe(pcm, sample_rate)
        return STTResult(text, None, [text] if text else [], engine=self.name)


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
        return self.recognize(pcm, sample_rate).text

    def _recognize_all(self, audio, language: str) -> STTResult:
        try:
            raw = self.recognizer.recognize_google(audio, language=language, show_all=True)
        except self.sr.UnknownValueError:
            return STTResult("", None, [], language[:2], self.name)
        except self.sr.RequestError as exc:
            raise STTError(f"сервис Google недоступен: {exc}") from exc
        alts = (raw or {}).get("alternative", []) if isinstance(raw, dict) else []
        texts = [a.get("transcript", "").strip() for a in alts if a.get("transcript", "").strip()]
        conf = next((a.get("confidence") for a in alts if "confidence" in a), None)
        return STTResult(texts[0] if texts else "", float(conf) if conf is not None else None,
                         list(dict.fromkeys(texts)), language[:2], self.name)

    def recognize(self, pcm: bytes, sample_rate: int = 16000) -> STTResult:
        from jarvis.voice.lang import detect_lang

        audio = self.sr.AudioData(pcm, sample_rate, 2)
        result = self._recognize_all(audio, self.language)
        if self.second_language and (not result.text or detect_lang(result.text) == "en"):
            second = self._recognize_all(audio, self.second_language)
            if second.text and (not result.text or detect_lang(second.text) == "en"):
                return second
        return result


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
        return self.recognize(pcm, sample_rate).text

    def recognize(self, pcm: bytes, sample_rate: int = 16000) -> STTResult:
        try:
            return self.primary.recognize(pcm, sample_rate)
        except STTError as exc:
            if not self.fallback:
                raise
            log.warning("%s — переключаюсь на %s", exc, self.fallback.name)
            result = self.fallback.recognize(pcm, sample_rate)
            result.confidence = 0.6 if result.text else None
            return result


def load_class(spec: str):
    """«package.module:ClassName» → класс. Так подключаются собственные STT/TTS-движки."""
    import importlib

    module, _, cls = spec.partition(":")
    return getattr(importlib.import_module(module), cls)


def make_stt(language: str = "ru-RU", mode: str = "auto") -> STTEngine:
    """mode: auto — русский, а фраза на английском распознаётся ещё раз по-английски; ru — только русский;
    en — только английский."""
    raw = env("STT_ENGINE", "auto") or "auto"
    if ":" in raw:
        return load_class(raw)()
    engine = raw.lower()
    vosk_engine = VoskSTT(env("VOSK_MODEL_PATH", "models/vosk-model-small-ru-0.22"))
    if engine == "vosk":
        return vosk_engine
    second = env("STT_SECOND_LANGUAGE", "en-US")
    second = None if second in ("", "none", "off") else second
    if mode == "ru":
        language, second = "ru-RU", None
    elif mode == "en":
        language, second = "en-US", None
    google = GoogleSTT(language, second)
    if engine == "google":
        return google
    return AutoSTT(google, vosk_engine if vosk_engine.available() else None)
