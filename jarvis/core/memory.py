"""Долговременная память JARVIS: факты о пользователе, которые он сам попросил запомнить.

«Запомни, что я люблю рок», «запомни: почта Ивана — ivan@mail.ru», «что ты обо мне знаешь?», «забудь про рок».
Хранится локально в config/memory.json (в git не попадает). Пароли, ключи, коды и номера карт не запоминаются.
В каждый запрос к AI Brain подмешиваются только факты, относящиеся к теме разговора.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

from jarvis.config import CONFIG_DIR
from jarvis.utils.text import normalize

FILE = CONFIG_DIR / "memory.json"
LIMIT = 300
SECRET = re.compile(r"(?i)(?:парол|password|passwd|пин[- ]?код|\bpin\b|cvv|cvc|токен|token|api[ _-]?key|ключ api|"
                    r"секретн\w* (?:слов|вопрос|ключ|код)|код из смс|сид[- ]?фраз|seed phrase|\bsk-[a-z0-9]|"
                    r"\bAIza|(?:\d[ -]?){13,19})")
STOP = {"что", "это", "как", "мне", "меня", "мой", "моя", "мое", "мои", "про", "для", "когда", "где", "есть", "был",
        "была", "его", "она", "они", "так", "уже", "ещё", "еще", "очень", "только", "the", "and", "запомни", "помни"}


def _stems(text: str) -> set[str]:
    words = re.findall(r"[a-zа-я0-9]+", normalize(text))
    return {w[:4] for w in words if len(w) >= 3 and w not in STOP}


class Memory:
    def __init__(self, path: Path = FILE):
        self.path = path
        self._lock = threading.Lock()
        self.facts: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.facts = [f for f in data if isinstance(f, dict) and f.get("text")]
        except (OSError, ValueError):
            self.facts = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.facts, ensure_ascii=False, indent=1), encoding="utf-8")

    @staticmethod
    def is_secret(text: str) -> bool:
        return bool(SECRET.search(text or ""))

    def add(self, text: str) -> tuple[bool, str]:
        text = " ".join((text or "").split()).strip(" .")
        if not text:
            return False, "Что запомнить?"
        if self.is_secret(text):
            return False, ("Пароли, коды, ключи и номера карт я не запоминаю — храните их в менеджере паролей, "
                           "а ключи для JARVIS — в файле .env.")
        with self._lock:
            key = _stems(text)
            for f in self.facts:
                if normalize(f["text"]) == normalize(text):
                    return True, "Я это уже помню."
            replaced = None
            for f in self.facts:
                old = _stems(f["text"])
                if key and old and len(key & old) / max(len(key | old), 1) >= 0.75:
                    replaced = f
                    break
            if replaced is not None:
                replaced.update(text=text, updated=time.time())
            else:
                self.facts.append({"id": uuid.uuid4().hex[:8], "text": text, "created": time.time()})
                self.facts = self.facts[-LIMIT:]
            self._save()
        return True, "Обновил в памяти." if replaced else "Запомнил."

    def search(self, query: str = "", limit: int = 5) -> list[dict]:
        """Факты по теме (общие слова). Пустой запрос — последние факты."""
        with self._lock:
            facts = list(self.facts)
        if not query:
            return facts[-limit:][::-1]
        q = _stems(query)
        scored = []
        for i, f in enumerate(facts):
            overlap = len(q & _stems(f["text"]))
            if overlap:
                scored.append((overlap, i, f))
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        return [f for _, _, f in scored[:limit]]

    def forget(self, query: str) -> list[str]:
        q = _stems(query)
        if not q:
            return []
        with self._lock:
            removed = [f for f in self.facts if q <= _stems(f["text"]) or
                       len(q & _stems(f["text"])) >= max(1, len(q) - 1) and len(q) <= 3]
            if removed:
                self.facts = [f for f in self.facts if f not in removed]
                self._save()
        return [f["text"] for f in removed]

    def remove(self, fact_id: str) -> bool:
        with self._lock:
            before = len(self.facts)
            self.facts = [f for f in self.facts if f.get("id") != fact_id]
            if len(self.facts) != before:
                self._save()
            return len(self.facts) != before

    def clear(self) -> int:
        with self._lock:
            n = len(self.facts)
            self.facts = []
            self._save()
        return n

    def context(self, text: str, limit: int = 6) -> str:
        """Строки для подсказки AI Brain: факты по теме, а если фактов мало — все."""
        with self._lock:
            facts = list(self.facts)
        chosen = facts if len(facts) <= limit else self.search(text, limit)
        return "\n".join(f"- {f['text']}" for f in chosen)


_shared: Memory | None = None


def get() -> Memory:
    global _shared
    if _shared is None:
        _shared = Memory()
    return _shared
