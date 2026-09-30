"""KeyManager — пул API-ключей пользователя с состояниями, cooldown и безопасным логированием.

Ключи берутся из окружения / .env: OPENAI_API_KEY_1, OPENAI_API_KEY_2, … (номера не ограничены) и,
для совместимости, OPENAI_API_KEY. Используются только ключи, которые пользователь сам указал.

Состояния:
    ACTIVE        — можно использовать;
    RATE_LIMITED  — получен rate limit, ключ отдыхает (Retry-After или RATE_LIMIT_COOLDOWN);
    COOLDOWN      — квота исчерпана или ключ стабильно получает ошибки сервера — длинная пауза;
    INVALID       — ошибка аутентификации: ключ отключён до перезапуска.

Сами ключи наружу не выдаются: в логах, статусе и интерфейсе — только номер («Key #2») и состояние.
"""
from __future__ import annotations

import enum
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping

log = logging.getLogger("jarvis.keys")

PLACEHOLDERS = {"", "your_api_key_here", "sk-...", "changeme"}
_KEY_VAR = re.compile(r"^OPENAI_API_KEY_(\d+)$")


class KeyState(enum.Enum):
    ACTIVE = "ACTIVE"
    RATE_LIMITED = "RATE_LIMITED"
    COOLDOWN = "COOLDOWN"
    INVALID = "INVALID"


@dataclass
class KeySlot:
    number: int
    _secret: str = field(repr=False)
    state: KeyState = KeyState.ACTIVE
    until: float = 0.0
    errors: int = 0
    successes: int = 0
    last_error: str = ""

    @property
    def label(self) -> str:
        return f"#{self.number}"

    def secret(self) -> str:
        return self._secret

    def __repr__(self) -> str:
        return f"KeySlot(#{self.number}, {self.state.value})"

    __str__ = __repr__


def load_keys(environ: Mapping[str, str] | None = None, prefix: str = "OPENAI") -> list[tuple[int, str]]:
    """Все непустые ключи из окружения: <PREFIX>_API_KEY_<n> по возрастанию n, затем <PREFIX>_API_KEY.
    Дубликаты отбрасываются, номера сохраняются как в .env."""
    environ = os.environ if environ is None else environ
    key_var = _KEY_VAR if prefix == "OPENAI" else re.compile(rf"^{prefix}_API_KEY_(\d+)$")
    numbered = []
    for name, value in environ.items():
        m = key_var.match(name)
        if m and (value or "").strip() not in PLACEHOLDERS:
            numbered.append((int(m.group(1)), value.strip()))
    numbered.sort()
    keys, seen = [], set()
    for number, value in numbered:
        if value not in seen:
            seen.add(value)
            keys.append((number, value))
    legacy = (environ.get(f"{prefix}_API_KEY") or "").strip()
    if legacy not in PLACEHOLDERS and legacy not in seen:
        used = {n for n, _ in keys}
        number = 1 if 1 not in used else max(used) + 1
        keys.append((number, legacy))
        keys.sort()
    return keys


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


class KeyManager:
    def __init__(self, keys: list[tuple[int, str]], *, clock: Callable[[], float] = time.monotonic,
                 rate_limit_cooldown: float | None = None, quota_cooldown: float | None = None,
                 error_cooldown: float | None = None):
        from jarvis.utils.secrets import register_secret

        self._slots = [KeySlot(n, s) for n, s in keys]
        for _, s in keys:
            register_secret(s)
        self._clock = clock
        self._lock = threading.RLock()
        self._next = 0
        self.rate_limit_cooldown = rate_limit_cooldown if rate_limit_cooldown is not None else \
            _float_env("OPENAI_RATE_LIMIT_COOLDOWN", 30)
        self.quota_cooldown = quota_cooldown if quota_cooldown is not None else \
            _float_env("OPENAI_QUOTA_COOLDOWN", 1800)
        self.error_cooldown = error_cooldown if error_cooldown is not None else \
            _float_env("OPENAI_ERROR_COOLDOWN", 60)
        if self._slots:
            log.info("OpenAI: найдено API keys: %d (%s)", len(self._slots),
                     ", ".join(s.label for s in self._slots))

    @classmethod
    def from_env(cls, prefix: str = "OPENAI", **kwargs) -> "KeyManager":
        return cls(load_keys(prefix=prefix), **kwargs)

    def __len__(self) -> int:
        return len(self._slots)

    @property
    def slots(self) -> list[KeySlot]:
        return list(self._slots)

    def _refresh(self, slot: KeySlot) -> None:
        """Возврат ключа в строй после cooldown."""
        if slot.state in (KeyState.RATE_LIMITED, KeyState.COOLDOWN) and self._clock() >= slot.until:
            log.info("API key %s снова доступен (cooldown завершён)", slot.label)
            slot.state = KeyState.ACTIVE
            slot.until = 0.0

    def available(self) -> list[KeySlot]:
        with self._lock:
            for s in self._slots:
                self._refresh(s)
            return [s for s in self._slots if s.state is KeyState.ACTIVE]

    def acquire(self, exclude: set[int] | None = None) -> KeySlot | None:
        """Следующий ключ по кругу (round-robin) среди доступных, кроме уже опробованных в этом запросе."""
        exclude = exclude or set()
        with self._lock:
            n = len(self._slots)
            for i in range(n):
                slot = self._slots[(self._next + i) % n]
                self._refresh(slot)
                if slot.state is KeyState.ACTIVE and slot.number not in exclude:
                    self._next = (self._slots.index(slot) + 1) % n
                    return slot
            return None

    def peek_next(self, exclude: set[int] | None = None) -> KeySlot | None:
        """Какой ключ будет следующим (без сдвига очереди) — для строки лога «switching to key #N»."""
        with self._lock:
            saved = self._next
            slot = self.acquire(exclude)
            self._next = saved
            return slot

    def report_success(self, slot: KeySlot) -> None:
        with self._lock:
            slot.successes += 1
            slot.last_error = ""

    def _disable(self, slot: KeySlot, state: KeyState, seconds: float, reason: str) -> None:
        with self._lock:
            slot.errors += 1
            slot.last_error = reason
            slot.state = state
            slot.until = self._clock() + seconds if state is not KeyState.INVALID else float("inf")

    def report_rate_limit(self, slot: KeySlot, retry_after: float | None = None) -> None:
        self._disable(slot, KeyState.RATE_LIMITED, max(retry_after or 0, self.rate_limit_cooldown), "rate limit")

    def report_quota(self, slot: KeySlot) -> None:
        self._disable(slot, KeyState.COOLDOWN, self.quota_cooldown, "quota exhausted")

    def report_server_errors(self, slot: KeySlot) -> None:
        self._disable(slot, KeyState.COOLDOWN, self.error_cooldown, "server errors")

    def report_invalid(self, slot: KeySlot) -> None:
        self._disable(slot, KeyState.INVALID, 0, "authentication failed")

    def report_error(self, slot: KeySlot, reason: str) -> None:
        """Ошибка, не связанная с самим ключом (сеть, некорректный запрос): только счётчик."""
        with self._lock:
            slot.errors += 1
            slot.last_error = reason

    def status(self) -> list[dict]:
        with self._lock:
            out = []
            for s in self._slots:
                self._refresh(s)
                left = max(0.0, s.until - self._clock()) if s.state in (KeyState.RATE_LIMITED,
                                                                          KeyState.COOLDOWN) else 0.0
                out.append({"number": s.number, "state": s.state.value, "seconds_left": round(left),
                            "errors": s.errors, "successes": s.successes})
            return out

    def status_text(self) -> str:
        lines = ["API Keys:"]
        for st in self.status():
            extra = f" ({st['seconds_left']} с)" if st["seconds_left"] else ""
            lines.append(f"Key #{st['number']} — {st['state']}{extra}")
        if len(lines) == 1:
            lines.append("не настроены")
        return "\n".join(lines)
