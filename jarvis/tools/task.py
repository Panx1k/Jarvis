"""Инструменты режима задачи: план (видно в ленте), уточняющий вопрос пользователю, передача сложной задачи
из живого режима агенту."""
from __future__ import annotations

import time

from jarvis.tools.base import ToolResult, tool


@tool("task_plan", "Режим задачи: записать цель и план (2–6 коротких шагов) перед выполнением. План показывается "
      "пользователю в ленте.",
      params={"goal": {"type": "string"},
              "steps": {"type": "array", "items": {"type": "string"}, "description": "Шаги по порядку"}},
      required=["goal", "steps"], announce="Составляю план", category="task")
def task_plan(ctx, goal: str, steps=None) -> ToolResult:
    if isinstance(steps, str):
        steps = [s.strip(" -•") for s in steps.replace(";", "\n").splitlines() if s.strip(" -•")]
    steps = [str(s).strip() for s in (steps or []) if str(s).strip()][:8]
    ctx.dialog.task = {"goal": goal, "steps": steps, "started": time.time()}
    show = getattr(ctx, "on_plan", None)
    if show:
        show(goal, steps)
    return ToolResult(True, "План принят. Выполняй шаги по порядку и проверяй результат каждого.",
                      {"goal": goal, "steps": steps})


@tool("ask_user", "Задать пользователю один короткий уточняющий вопрос, когда без ответа нельзя правильно "
      "выполнить задачу (неясно кому, что именно, какой файл). После вызова ничего не делай — жди ответа.",
      params={"question": {"type": "string"}}, required=["question"], announce="Уточняю", category="task")
def ask_user(ctx, question: str) -> ToolResult:
    task = ctx.dialog.task or {"goal": "", "steps": [], "started": time.time()}
    task["asked"] = question
    ctx.dialog.task = task
    return ToolResult(True, question, {"ask": True})


@tool("agent_task", "Сложная задача из нескольких шагов (почта, поиск и чтение сайтов, данные из файлов, несколько "
      "действий подряд с проверкой результата) — передай её целиком: goal — что хочет пользователь, своими "
      "словами, со всеми деталями. Вернётся итог — перескажи его.",
      params={"goal": {"type": "string"}}, required=["goal"], announce="Выполняю задачу", category="task")
def agent_task(ctx, goal: str) -> ToolResult:
    runner = getattr(ctx, "agent", None)
    if runner is None:
        return ToolResult(False, "Режим задач сейчас недоступен (нужен облачный AI Brain).")
    return runner(goal)
