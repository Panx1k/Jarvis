"""Голос ElevenLabs: синтез и кэш, ошибки → обычный голос, ключ не попадает в логи (сервер подменён)."""
from __future__ import annotations

import threading
from types import SimpleNamespace as NS

import pytest

from jarvis.voice import elevenlabs, tts


@pytest.fixture
def el(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el_test_key_1234567890")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")
    monkeypatch.setattr(elevenlabs, "CACHE_DIR", tmp_path)
    played, posts = [], []
    monkeypatch.setattr(elevenlabs, "play_audio_file", lambda path, stop: played.append(path))

    def post(url, **kw):
        posts.append((url, kw))
        return NS(status_code=200, content=b"ID3fake-mp3")

    monkeypatch.setattr(elevenlabs.requests, "post", post)
    return elevenlabs.ElevenLabsTTS(), played, posts


def test_speak_synthesizes_and_caches(el):
    engine, played, posts = el
    engine.speak("Да, сэр.", threading.Event())
    engine.speak("Да, сэр.", threading.Event())
    assert len(posts) == 1 and len(played) == 2
    url, kw = posts[0]
    assert url.endswith("/text-to-speech/voice123") and kw["json"]["text"] == "Да, сэр."
    assert kw["headers"]["xi-api-key"] == "el_test_key_1234567890"


def test_errors_fall_back_to_normal_voice(el, monkeypatch):
    engine, played, posts = el
    monkeypatch.setattr(elevenlabs.requests, "post", lambda url, **kw: NS(status_code=402, content=b""))
    spoken = []
    fallback = NS(name="Edge", speak=lambda text, stop, lang="ru": spoken.append(text))
    voice = tts.FallbackTTS(engine, fallback)
    voice.speak("Готово, сэр.", threading.Event())
    assert spoken == ["Готово, сэр."] and not played
    with pytest.raises(elevenlabs.ElevenLabsError):
        engine.synthesize("ещё раз")


def test_key_is_redacted_in_logs(el):
    from jarvis.utils.secrets import redact

    assert "el_test_key_1234567890" not in redact("request failed with key el_test_key_1234567890")


def test_make_tts_selects_elevenlabs(el, monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "elevenlabs")
    engine = tts.make_tts()
    assert isinstance(engine, tts.FallbackTTS) and isinstance(engine.primary, elevenlabs.ElevenLabsTTS)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID")
    assert not isinstance(getattr(tts.make_tts(), "primary", None), elevenlabs.ElevenLabsTTS)
