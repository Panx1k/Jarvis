"""Программы-исключения: JARVIS не закрывает, не завершает принудительно и не удаляет их — даже по просьбе.

«Добавь Discord в исключения», «убери Steam из исключений», «какие у меня программы в исключениях?».
Список хранится в config/settings.json → exclusions.
"""
from __future__ import annotations

from jarvis.tools.base import ToolResult, tool
from jarvis.utils.text import normalize, similarity

KEY = "exclusions"


def load(settings) -> list[str]:
    return [str(x) for x in (settings.get(KEY, []) or []) if str(x).strip()]


def _canonical(ctx, app: str) -> str:
    """Название как его знают словарь и список приложений: «дискорд» → Discord."""
    from jarvis.nlu import lexicon

    m = lexicon.get(ctx.settings).match(app, ("apps", "games"), threshold=0.84)
    if m.best and not m.ambiguous:
        return m.best.name
    apps = getattr(ctx, "apps", None)
    match = apps.resolve(app) if apps else None
    if match is not None and match.kind != "missing":
        return match.display
    return app.strip()


def is_excluded(ctx, app: str, *extra_names: str) -> str | None:
    """Какое исключение запрещает трогать приложение (или None)."""
    names = [n for n in (app, *extra_names) if n]
    if not names:
        return None
    try:
        names.append(_canonical(ctx, app))
    except Exception:
        pass
    for excluded in load(ctx.settings):
        e = normalize(excluded)
        for n in names:
            nn = normalize(n)
            if nn == e or similarity(nn, e) >= 0.86 or (len(e) >= 4 and (e in nn or nn in e)):
                return excluded
    return None


def refusal(name: str, action: str) -> ToolResult:
    return ToolResult(False, f"{name} в исключениях — {action} не буду. Уберите его из исключений, если нужно.",
                      {"excluded": name})


@tool("exclusion_add", "Добавить программу в исключения: JARVIS не будет её закрывать, завершать и удалять.",
      params={"app": {"type": "string", "description": "Название программы"}}, required=["app"],
      announce="Добавляю {app} в исключения", category="exclusions",
      patterns=[r"^(?:добавь|внеси|занеси|поставь|запиши)\s+(?P<app>.+?)\s+(?:в|во)\s+(?:список\s+)?исключени\w*$"])
def exclusion_add(ctx, app: str) -> ToolResult:
    name = _canonical(ctx, app)
    items = load(ctx.settings)
    if any(normalize(x) == normalize(name) for x in items):
        return ToolResult(True, f"{name} уже в исключениях.", {"exclusions": items})
    items.append(name)
    ctx.settings.set(KEY, items)
    return ToolResult(True, f"Добавил {name} в исключения — закрывать и удалять его не буду.", {"exclusions": items})


@tool("exclusion_remove", "Убрать программу из исключений.",
      params={"app": {"type": "string", "description": "Название программы"}}, required=["app"],
      announce="Убираю {app} из исключений", category="exclusions",
      patterns=[r"^(?:убери|удали|вычеркни|исключи)\s+(?P<app>.+?)\s+из\s+(?:списка\s+)?исключени\w*$"])
def exclusion_remove(ctx, app: str) -> ToolResult:
    items = load(ctx.settings)
    found = is_excluded(ctx, app)
    if not found:
        return ToolResult(False, f"{_canonical(ctx, app)} нет в исключениях.", {"exclusions": items})
    items = [x for x in items if x != found]
    ctx.settings.set(KEY, items)
    return ToolResult(True, f"Убрал {found} из исключений.", {"exclusions": items})


@tool("exclusion_list", "Какие программы в исключениях (их JARVIS не закрывает и не удаляет).",
      announce="Смотрю исключения", category="exclusions",
      patterns=[r"(?:какие|что)\s+(?:у\s+меня\s+)?(?:программы\s+|приложения\s+)?(?:есть\s+)?(?:в|во)\s+"
                r"(?:списке\s+)?исключени\w*",
                r"^(?:покажи|назови|список)\s+(?:мои\s+)?исключени\w*$", r"^исключения$"])
def exclusion_list(ctx) -> ToolResult:
    items = load(ctx.settings)
    if not items:
        return ToolResult(True, "Исключений нет. Скажите, например: «добавь Discord в исключения».", {"exclusions": []})
    return ToolResult(True, "В исключениях: " + ", ".join(items) + ".", {"exclusions": items})
