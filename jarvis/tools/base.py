"""Система инструментов.

Инструмент — обычная функция, помеченная декоратором @tool. Он автоматически попадает в реестр,
становится доступен LLM (через JSON-схему) и локальному разборщику команд (через patterns).

    @tool("say_hello", "Поздороваться с человеком",
          params={"name": {"type": "string", "description": "Имя"}}, required=["name"],
          patterns=[r"^поздоровайся с (?P<name>.+)$"])
    def say_hello(ctx, name):
        return ToolResult(True, f"Привет, {name}!")
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import pkgutil
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("jarvis.tools")


@dataclass
class ToolResult:
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    followup: tuple[str, dict] | None = None

    def for_llm(self) -> str:
        text = self.message
        if self.data:
            details = {k: v for k, v in self.data.items() if k not in ("pending",)}
            if details:
                text += f"\nДанные: {details}"
        return text


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., ToolResult]
    params: dict[str, dict] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    dangerous: bool | Callable[[dict], bool] = False
    confirm: str | Callable[[dict], str] | None = None
    announce: str | Callable[[dict], str] | None = None
    patterns: list[re.Pattern] = field(default_factory=list)
    category: str = "general"

    def is_dangerous(self, args: dict) -> bool:
        return bool(self.dangerous(args)) if callable(self.dangerous) else bool(self.dangerous)

    def _fmt(self, template: str | Callable[[dict], str] | None, args: dict, fallback: str) -> str:
        if template is None:
            return fallback
        if callable(template):
            return template(args)
        try:
            return template.format(**{k: args.get(k, "") for k in self.params} | args)
        except (KeyError, IndexError, ValueError):
            return template

    def confirm_text(self, args: dict) -> str:
        return self._fmt(self.confirm, args, f"Выполнить действие «{self.name}»?")

    def announce_text(self, args: dict) -> str:
        pretty = ", ".join(f"{k}={v}" for k, v in args.items())
        return self._fmt(self.announce, args, f"{self.name}({pretty})")

    def _llm_description(self) -> str:
        description = self.description
        if self.dangerous:
            description += " Требует подтверждения пользователя — система спросит его сама."
        return description

    def _json_schema(self) -> dict:
        return {"type": "object", "properties": self.params, "required": list(self.required)}

    def anthropic_schema(self) -> dict:
        return {"name": self.name, "description": self._llm_description(), "input_schema": self._json_schema()}

    def openai_schema(self) -> dict:
        """Формат function calling OpenAI (Chat Completions)."""
        return {"type": "function",
                "function": {"name": self.name, "description": self._llm_description(),
                             "parameters": self._json_schema()}}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, t: Tool) -> None:
        if t.name in self._tools:
            log.debug("Инструмент %s переопределён", t.name)
        self._tools[t.name] = t

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return sorted(self._tools)

    def anthropic_schemas(self) -> list[dict]:
        return [t.anthropic_schema() for t in sorted(self._tools.values(), key=lambda t: t.name)]

    def openai_schemas(self) -> list[dict]:
        return [t.openai_schema() for t in sorted(self._tools.values(), key=lambda t: t.name)]

    def call(self, name: str, args: dict, ctx: Any) -> ToolResult:
        t = self._tools.get(name)
        if t is None:
            return ToolResult(False, f"Неизвестный инструмент: {name}")
        missing = [p for p in t.required if args.get(p) in (None, "")]
        if missing:
            return ToolResult(False, f"Не хватает параметров для {name}: {', '.join(missing)}")
        sig = inspect.signature(t.func)
        accepts_kwargs = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
        clean = args if accepts_kwargs else {k: v for k, v in args.items() if k in sig.parameters}
        try:
            result = t.func(ctx, **clean)
        except Exception as exc:
            log.exception("Ошибка инструмента %s", name)
            return ToolResult(False, f"Ошибка при выполнении «{name}»: {exc}")
        if not isinstance(result, ToolResult):
            result = ToolResult(True, str(result) if result is not None else "Готово.")
        return result


registry = ToolRegistry()


def tool(name: str, description: str, *, params: dict[str, dict] | None = None, required: list[str] | None = None,
         dangerous: bool | Callable[[dict], bool] = False, confirm: str | Callable[[dict], str] | None = None,
         announce: str | Callable[[dict], str] | None = None, patterns: list[str] | None = None,
         category: str = "general") -> Callable:
    """Декоратор регистрации инструмента."""

    def decorator(func: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
        registry.register(Tool(
            name=name, description=description, func=func, params=params or {}, required=required or [],
            dangerous=dangerous, confirm=confirm, announce=announce,
            patterns=[re.compile(p, re.I) for p in (patterns or [])], category=category,
        ))
        return func

    return decorator


def load_builtin_tools() -> None:
    """Импортирует все модули пакета jarvis.tools — декораторы регистрируют инструменты."""
    import jarvis.tools as pkg

    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_") or info.name == "base":
            continue
        importlib.import_module(f"jarvis.tools.{info.name}")


def load_plugins(directory: Path) -> list[str]:
    """Загружает пользовательские инструменты из папки plugins/*.py."""
    loaded = []
    if not directory.is_dir():
        return loaded
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        mod_name = f"jarvis_plugin_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            loaded.append(path.stem)
        except Exception:
            log.exception("Не удалось загрузить плагин %s", path.name)
    return loaded
