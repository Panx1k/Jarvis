"""Общие типы «мозга»."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from jarvis.tools.base import ToolResult

Executor = Callable[[str, dict], ToolResult]


@dataclass
class Action:
    tool: str | None
    args: dict = field(default_factory=dict)
    reply: str | None = None


@dataclass
class Plan:
    actions: list[Action]
    weak: bool = False
    source: str = "rules"


@dataclass
class BrainReply:
    text: str
    handled: bool = True
    source: str = "rules"
    actions: list[str] = field(default_factory=list)


class Brain(Protocol):
    def respond(self, text: str, execute: Executor) -> BrainReply: ...


class BrainUnavailable(Exception):
    """LLM недоступен (нет ключа, сети, лимит) — можно откатиться на правила."""
