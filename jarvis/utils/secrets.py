"""Защита секретов: API-ключи никогда не попадают в логи, консоль и интерфейс."""
from __future__ import annotations

import logging
import os
import re

import threading

SECRET_ENV = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
_SECRET_ENV_RE = re.compile(r"^(?:(?:OPENAI|GEMINI)_API_KEY(?:_\d+)?|ANTHROPIC_API_KEY|ELEVENLABS_API_KEY)$")
_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-*]{8,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-*]{20,}"),
    re.compile(r"\bAQ\.[0-9A-Za-z_\-*.]{20,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-.]{12,}"),
]
_registered: set[str] = set()
_lock = threading.Lock()


def register_secret(value: str) -> None:
    """Запомнить секрет (например, ключ из пула), чтобы вырезать его, даже если он не похож на sk-…"""
    if value and len(value) >= 6:
        with _lock:
            _registered.add(value)


def _known_secrets() -> list[str]:
    values = {v for k, v in os.environ.items() if _SECRET_ENV_RE.match(k) and v and len(v) >= 6}
    with _lock:
        values |= _registered
    return sorted(values, key=len, reverse=True)


def redact(text: str) -> str:
    """Убирает из текста значения ключей и всё, что похоже на ключ (в т. ч. частично замаскированный)."""
    if not text:
        return text
    for value in _known_secrets():
        if value in text:
            text = text.replace(value, "***")
    for p in _PATTERNS:
        text = p.sub(lambda m: (m.group(1) if m.groups() else "") + "***", text)
    return text


class RedactingFilter(logging.Filter):
    """Фильтр для обработчиков логов: секреты вырезаются из сообщений и трассировок."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        clean = redact(message)
        if clean != message:
            record.msg, record.args = clean, ()
        if record.exc_info:
            import traceback

            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True
