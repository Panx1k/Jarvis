"""Контекст диалога: что открыто, что найдено, что играет — чтобы понимать «там», «первый результат» и т. п."""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class SearchResult:
    title: str
    url: str
    source: str
    channel: str = ""
    duration: str = ""

    def label(self) -> str:
        extra = f" — {self.channel}" if self.channel else ""
        return f"{self.title}{extra}"


@dataclass
class Turn:
    user: str
    reply: str
    actions: list[str] = field(default_factory=list)
    at: float = field(default_factory=time.time)


class DialogContext:
    """Разделяемое состояние разговора. Инструменты обновляют его, «мозг» читает."""

    def __init__(self, max_turns: int = 20):
        self._lock = threading.RLock()
        self.active_site: str | None = None
        self.last_results: list[SearchResult] = []
        self.last_query: str | None = None
        self.last_results_source: str | None = None
        self.last_app: str | None = None
        self.last_url: str | None = None
        self.now_playing: str | None = None
        self.last_tool: str | None = None
        self.last_mails: list = []
        self.last_mail = None
        self.task: dict | None = None
        self.history: deque[Turn] = deque(maxlen=max_turns)
        self.persist_path = None

    def set_results(self, source: str, query: str, results: list[SearchResult]) -> None:
        with self._lock:
            self.last_results = list(results)
            self.last_query = query
            self.last_results_source = source

    def add_turn(self, user: str, reply: str, actions: list[str]) -> None:
        with self._lock:
            self.history.append(Turn(user, reply, actions))
        if self.persist_path is not None:
            self.save()

    def save(self) -> None:
        """Последние реплики на диске — чтобы после перезапуска JARVIS помнил, о чём шла речь."""
        import json

        with self._lock:
            items = [{"user": t.user, "reply": t.reply, "actions": t.actions, "at": t.at} for t in self.history]
        try:
            self.persist_path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def load(self, path, max_age: float = 12 * 3600) -> int:
        """Подхватить сохранённый разговор (только свежий — не старше max_age секунд)."""
        import json

        self.persist_path = path
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        now = time.time()
        with self._lock:
            for it in items if isinstance(items, list) else []:
                if isinstance(it, dict) and now - float(it.get("at", 0)) < max_age:
                    self.history.append(Turn(str(it.get("user", "")), str(it.get("reply", "")),
                                             list(it.get("actions", [])), float(it["at"])))
        return len(self.history)

    def snapshot(self) -> str:
        """Краткое текстовое описание состояния для LLM."""
        with self._lock:
            lines = []
            if self.active_site:
                lines.append(f"Активный сайт (куда относится «там/тут»): {self.active_site}")
            if self.last_app:
                lines.append(f"Последнее открытое приложение: {self.last_app}")
            if self.now_playing:
                lines.append(f"Последнее запущенное видео/музыка: {self.now_playing}")
            if self.last_results:
                lines.append(f"Последний поиск ({self.last_results_source}) «{self.last_query}», результаты:")
                for i, r in enumerate(self.last_results[:8], 1):
                    lines.append(f"  {i}. {r.label()}")
            if self.last_mails:
                lines.append("Последний список писем (номера для mail_read / mail_reply):")
                for i, m in enumerate(self.last_mails[:10], 1):
                    lines.append(f"  {i}. {m.headline()}")
            if self.last_mail is not None:
                lines.append(f"Последнее прочитанное письмо: {self.last_mail.headline()}, "
                             f"адрес {self.last_mail.sender_address}")
            if self.task:
                t = self.task
                plan = "; ".join(f"{i}. {s}" for i, s in enumerate(t.get("steps", []), 1))
                lines.append(f"Незавершённая задача: {t.get('goal', '')}" + (f". План: {plan}" if plan else "")
                             + (f". Вопрос пользователю: {t['asked']}" if t.get("asked") else ""))
            return "\n".join(lines) if lines else "Контекст пуст (это начало разговора)."

    def clear(self) -> None:
        with self._lock:
            path = self.persist_path
            self.__init__(self.history.maxlen or 20)
            self.persist_path = path
