"""Пресеты (сценарии): «Jarvis, режим работа» — и JARVIS по очереди открывает всё нужное.

Пресет — список обычных команд, как их говорят JARVIS: «открой Chrome», «открой Telegram», «громкость 30»,
«включи VPN», «подожди 2 секунды». Хранятся в config/settings.json → presets (личное, в git не попадает).
Создать: голосом («создай пресет работа: открой хром, открой телеграм и громкость 30») или в настройках.
Опасные шаги (выключение, удаление, отправка сообщений) в пресете не выполняются — они только с подтверждением.
"""
from __future__ import annotations

import re
import time

from jarvis.tools.base import ToolResult, tool
from jarvis.utils.text import normalize

MAX_STEPS = 20
WAIT_RE = re.compile(r"^(?:подожди|жди|пауза|подождать)\s*(?P<n>\d+(?:[.,]\d+)?)?\s*(?:сек\w*|с)?$", re.I)


def _key(name: str) -> str:
    n = normalize(name or "").strip(" .!?«»\"'")
    n = re.sub(r"^(?:режим|пресет|сценарий)\s+", "", n)
    return n


def load(settings) -> dict[str, dict]:
    data = settings.get("presets", {}) or {}
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("steps")}


def find(settings, name: str) -> tuple[str, dict] | None:
    presets = load(settings)
    key = _key(name)
    if not key:
        return None
    if key in presets:
        return key, presets[key]

    import os

    words = key.split()
    for k, v in presets.items():
        kw = k.split()
        if len(kw) != len(words):
            continue
        if all(len(os.path.commonprefix([a, b])) >= max(3, int(min(len(a), len(b)) * 0.6))
               for a, b in zip(kw, words)):
            return k, v
    return None


def _invalidate() -> None:
    try:
        from jarvis.core.knowledge import invalidate

        invalidate()
    except Exception:
        pass


def split_steps(text: str) -> list[str]:
    """«открой хром, открой телеграм и громкость 30» → три шага."""
    parts = re.split(r"\s*(?:[,;\n]|\s+(?:и|а потом|потом|затем|после этого)\s+)\s*", text or "")
    return [p.strip(" .") for p in parts if p and p.strip(" .")][:MAX_STEPS]


def _plan(ctx, step: str):
    from jarvis.brain.rules import RuleParser

    return RuleParser(ctx).parse(step)


@tool("run_preset", "Запустить пресет (сценарий) пользователя по названию: «режим работа», «запусти игровой режим». "
      "Выполняет по очереди все его шаги.",
      params={"name": {"type": "string", "description": "Название пресета"}}, required=["name"],
      announce="Запускаю режим {name}", category="presets")
def run_preset(ctx, name: str) -> ToolResult:
    found = find(ctx.settings, name)
    if not found:
        names = ", ".join(load(ctx.settings)) or "пока ни одного"
        return ToolResult(False, f"Пресета «{name}» нет. Есть: {names}.")
    key, preset = found
    if ctx.execute is None:
        return ToolResult(False, "Пресеты запускаются только из JARVIS.")
    done, skipped, failed = [], [], []
    for step in preset["steps"][:MAX_STEPS]:
        wait = WAIT_RE.match(normalize(step).strip())
        if wait:
            time.sleep(min(30.0, float((wait.group("n") or "1").replace(",", "."))))
            continue
        plan = _plan(ctx, step)
        if plan is None or not [a for a in plan.actions if a.tool]:
            failed.append(step)
            continue
        for action in plan.actions:
            if not action.tool:
                continue
            tool_obj = ctx.registry.get(action.tool)
            if tool_obj is not None and tool_obj.is_dangerous(action.args):
                skipped.append(step)
                continue
            r = ctx.execute(action.tool, action.args)
            (done if r.ok else failed).append(step)
            time.sleep(0.4)
    title = preset.get("title") or key
    text = f"Режим «{title}» запущен, сэр." if done else f"Режим «{title}»: ничего не получилось выполнить."
    if failed:
        text += f" Не получилось: {', '.join(failed[:4])}."
    if skipped:
        text += f" Пропустил опасные шаги (их — только с подтверждением): {', '.join(skipped[:3])}."
    return ToolResult(bool(done), text, {"preset": title, "done": done, "failed": failed, "skipped": skipped})


@tool("create_preset", "Создать или переписать пресет: название и шаги — обычные команды JARVIS по порядку "
      "(«открой Chrome», «открой Telegram», «громкость 30», «включи VPN», «подожди 2 секунды»).",
      params={"name": {"type": "string", "description": "Название, например «работа»"},
              "steps": {"type": "array", "items": {"type": "string"}, "description": "Команды по порядку"}},
      required=["name", "steps"], announce="Создаю пресет {name}", category="presets",
      patterns=[r"^(?:создай|сделай|добавь|запомни|сохрани)\s+(?:новый\s+)?(?:пресет|режим|сценарий)\s+(?P<name>[^:,]+?)"
                r"\s*[:,—-]\s*(?P<steps>.+)$"])
def create_preset(ctx, name: str, steps) -> ToolResult:
    key = _key(name)
    if not key:
        return ToolResult(False, "Как назвать пресет?")
    items = split_steps(steps) if isinstance(steps, str) else [str(s).strip() for s in steps if str(s).strip()]
    good, bad = [], []
    for step in items[:MAX_STEPS]:
        (good if WAIT_RE.match(normalize(step).strip()) or _plan(ctx, step) else bad).append(step)
    if not good:
        return ToolResult(False, "Не понял ни одного шага. Скажите команды так же, как обычно: «открой хром, "
                                 "громкость 30».")
    presets = dict(ctx.settings.get("presets", {}) or {})
    presets[key] = {"title": name.strip(" .!?«»\"'"), "steps": good}
    ctx.settings.set("presets", presets)
    _invalidate()
    text = f"Пресет «{presets[key]['title']}» сохранён: {len(good)} шаг(ов). Запуск — «режим {key}»."
    if bad:
        text += f" Не понял: {', '.join(bad[:3])} — эти шаги не добавил."
    return ToolResult(True, text, {"preset": key, "steps": good, "unknown": bad})


@tool("delete_preset", "Удалить пресет пользователя.",
      params={"name": {"type": "string"}}, required=["name"], announce="Удаляю пресет {name}", category="presets",
      patterns=[r"^(?:удали|убери|сотри)\s+(?:пресет|режим|сценарий)\s+(?P<name>.+)$"])
def delete_preset(ctx, name: str) -> ToolResult:
    found = find(ctx.settings, name)
    if not found:
        return ToolResult(False, f"Пресета «{name}» нет.")
    presets = dict(ctx.settings.get("presets", {}) or {})
    presets.pop(found[0], None)
    ctx.settings.set("presets", presets)
    _invalidate()
    return ToolResult(True, f"Пресет «{found[0]}» удалён.")


@tool("list_presets", "Какие пресеты есть у пользователя и что в них.", announce="Смотрю пресеты",
      category="presets",
      patterns=[r"^(?:какие|покажи|перечисли)\s+(?:у меня\s+)?(?:есть\s+)?(?:пресеты|режимы|сценарии)$"])
def list_presets(ctx) -> ToolResult:
    presets = load(ctx.settings)
    if not presets:
        return ToolResult(True, "Пресетов пока нет. Скажите, например: «создай пресет работа: открой хром, открой "
                                "телеграм и громкость 30».")
    parts = [f"{v.get('title') or k} — {', '.join(v['steps'][:4])}{'…' if len(v['steps']) > 4 else ''}"
             for k, v in presets.items()]
    return ToolResult(True, "Ваши пресеты: " + "; ".join(parts) + ".", {"presets": list(presets)})
