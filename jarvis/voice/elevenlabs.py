"""Голос ElevenLabs (TTS_ENGINE=elevenlabs).

Ключ и голос — только в .env: ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID (список голосов вашего аккаунта:
python main.py --elevenlabs-voices). Модель: ELEVENLABS_MODEL (по умолчанию eleven_multilingual_v2 — говорит
по-русски; eleven_flash_v2_5 — быстрее). Короткие частые фразы кэшируются на диске, чтобы не тратить лимит.
При ошибке (нет ключа, кончился лимит, нет сети) JARVIS говорит обычным голосом и пару минут не дёргает ElevenLabs.

Используйте только голоса, на которые у вас есть права: из библиотеки ElevenLabs или свой собственный.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time

import requests

from jarvis.config import env
from jarvis.utils.secrets import register_secret
from jarvis.utils.text import strip_markdown
from jarvis.voice.tts import CACHE_DIR, TTSEngine, play_audio_file

log = logging.getLogger("jarvis.elevenlabs")

API = "https://api.elevenlabs.io/v1"
CACHE_MAX_CHARS = 80


class ElevenLabsError(RuntimeError):
    pass


class ElevenLabsTTS(TTSEngine):
    def __init__(self):
        self.key = env("ELEVENLABS_API_KEY") or ""
        self.voice = env("ELEVENLABS_VOICE_ID") or ""
        self.model = env("ELEVENLABS_MODEL", "eleven_multilingual_v2") or "eleven_multilingual_v2"
        self.stability = float(env("ELEVENLABS_STABILITY", "0.5") or 0.5)
        self.similarity = float(env("ELEVENLABS_SIMILARITY", "0.75") or 0.75)
        self.name = f"ElevenLabs ({self.voice[:8] or 'голос не выбран'})"
        self._pause_until = 0.0
        if self.key:
            register_secret(self.key)

    @property
    def ready(self) -> bool:
        return bool(self.key and self.voice)

    def _cache_path(self, text: str):
        key = hashlib.sha1(f"{self.voice}|{self.model}|{text}".encode("utf-8")).hexdigest()[:20]
        return CACHE_DIR / f"el_{key}.mp3"

    def synthesize(self, text: str) -> bytes:
        if not self.ready:
            raise ElevenLabsError("нет ELEVENLABS_API_KEY или ELEVENLABS_VOICE_ID в .env")
        if time.monotonic() < self._pause_until:
            raise ElevenLabsError("временно недоступен")
        try:
            r = requests.post(f"{API}/text-to-speech/{self.voice}", params={"output_format": "mp3_44100_128"},
                              headers={"xi-api-key": self.key, "accept": "audio/mpeg"},
                              json={"text": text, "model_id": self.model,
                                    "voice_settings": {"stability": self.stability,
                                                       "similarity_boost": self.similarity}},
                              timeout=20)
        except requests.RequestException as exc:
            self._pause_until = time.monotonic() + 60
            raise ElevenLabsError(f"нет связи ({type(exc).__name__})") from None
        if r.status_code != 200:
            reason = {401: "ключ не подошёл", 402: "закончился лимит", 403: "доступ запрещён (из России — нужен VPN)",
                      404: "голос не найден (ELEVENLABS_VOICE_ID)", 429: "слишком много запросов / лимит"}
            self._pause_until = time.monotonic() + (600 if r.status_code in (401, 402, 404) else 120)
            raise ElevenLabsError(reason.get(r.status_code, f"ошибка сервиса (HTTP {r.status_code})"))
        return r.content

    def speak(self, text: str, stop: threading.Event, lang: str = "ru") -> None:
        text = strip_markdown(text).strip()
        if not text:
            return
        path = self._cache_path(text)
        if not path.exists():
            audio = self.synthesize(text)
            if stop.is_set():
                return
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            if len(text) <= CACHE_MAX_CHARS:
                path.write_bytes(audio)
            else:
                path = CACHE_DIR / "el_last.mp3"
                path.write_bytes(audio)
        play_audio_file(str(path), stop)

    def prewarm(self, phrases: list[tuple[str, str]]) -> None:
        for text, _lang in phrases:
            try:
                if len(text) <= CACHE_MAX_CHARS and not self._cache_path(text).exists() and self.ready:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    self._cache_path(text).write_bytes(self.synthesize(text))
            except ElevenLabsError as exc:
                log.info("ElevenLabs: заготовка фраз пропущена (%s)", exc)
                return


def list_voices() -> list[dict]:
    key = env("ELEVENLABS_API_KEY") or ""
    if not key:
        raise ElevenLabsError("нет ELEVENLABS_API_KEY в .env")
    register_secret(key)
    r = requests.get(f"{API}/voices", headers={"xi-api-key": key}, timeout=15)
    if r.status_code != 200:
        raise ElevenLabsError(f"HTTP {r.status_code}")
    return [{"id": v["voice_id"], "name": v.get("name", ""), "labels": v.get("labels") or {},
             "category": v.get("category", "")} for v in r.json().get("voices", [])]
