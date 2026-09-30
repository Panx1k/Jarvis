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
        self.history: deque[Turn] = deque(maxlen=max_turns)

    def set_results(self, source: str, query: str, results: list[SearchResult]) -> None:
        with self._lock:
            self.last_results = list(results)
            self.last_query = query
            self.last_results_source = source

    def add_turn(self, user: str, reply: str, actions: list[str]) -> None:
        with self._lock:
            self.history.append(Turn(user, reply, actions))

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
            return "\n".join(lines) if lines else "Контекст пуст (это начало разговора)."

    def clear(self) -> None:
        with self._lock:
            self.__init__(self.history.maxlen or 20)
