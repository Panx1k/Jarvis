"""Gemini как основной мозг с откатом на локальную модель (без обращения к API)."""
from __future__ import annotations

from jarvis.brain.base import BrainReply, BrainUnavailable
from jarvis.brain.hybrid import FallbackLLM, pick_gemini_model
from jarvis.brain.key_manager import load_keys
from jarvis.utils.secrets import redact


def test_pick_gemini_model():
    ids = ["models/gemini-2.0-flash", "models/gemini-2.5-flash", "models/gemini-2.5-flash-lite",
           "models/gemini-2.5-pro", "models/gemini-2.5-flash-image", "models/text-embedding-004",
           "models/gemini-2.5-flash-preview-tts"]
    assert pick_gemini_model(ids) == "gemini-2.5-flash"
    assert pick_gemini_model(["models/gemini-3.0-flash", "models/gemini-2.5-flash"]) == "gemini-3.0-flash"
    assert pick_gemini_model(["models/gemini-2.5-pro"]) is None


def test_gemini_keys_prefix():
    env = {"GEMINI_API_KEY": "AIzaSyTestKeyAAAAAAAAAAAAAAAAAAAAAAAAAA", "OPENAI_API_KEY_1": "ollama"}
    assert load_keys(env, prefix="GEMINI") == [(1, env["GEMINI_API_KEY"])]
    assert load_keys(env) == [(1, "ollama")]


def test_google_key_redacted():
    assert "AIzaSy" not in redact("bad key AIzaSyTestKeyAAAAAAAAAAAAAAAAAAAAAAAAAA used")


class Fake:
    def __init__(self, label, fail=False, available=True):
        self.label, self.fail, self._available = label, fail, available
        self.keys, self.error = [], None

    @property
    def available(self):
        return self._available

    def describe(self):
        return self.label

    def key_status(self):
        return []

    def respond(self, text, execute):
        if self.fail:
            raise BrainUnavailable("нет соединения")
        return BrainReply(f"{self.label}: ok", True, "llm", [])


def test_fallback_to_local_when_gemini_down():
    f = FallbackLLM(Fake("Gemini", fail=True), Fake("Локальная"))
    assert f.respond("привет", None).text == "Локальная: ok"
    f = FallbackLLM(Fake("Gemini"), Fake("Локальная"))
    assert f.respond("привет", None).text == "Gemini: ok"
    f = FallbackLLM(Fake("Gemini", available=False), Fake("Локальная"))
    assert f.available and f.respond("привет", None).text == "Локальная: ok"
