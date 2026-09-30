"""Запуск и закрытие приложений."""
from __future__ import annotations

import subprocess

import psutil

from jarvis.tools._common import CREATE_NO_WINDOW, find_site
from jarvis.tools.base import ToolResult, tool


@tool("open_app", "Запустить установленное приложение (Discord, Telegram, Steam, Chrome, Spotify, блокнот, "
      "калькулятор и любые другие из меню «Пуск»).",
      params={"app": {"type": "string", "description": "Название приложения"}}, required=["app"],
      announce="Запускаю {app}", category="apps")
def open_app(ctx, app: str) -> ToolResult:
    match = ctx.apps.resolve(app)
    if match is None or match.kind == "missing":
        site = find_site(ctx.settings, app)
        if site:
            from jarvis.tools.web import open_site
            return open_site(ctx, app)
        hint = f" ({match.display} не установлен)" if match else ""
        return ToolResult(False, f"Не нашёл приложение «{app}»{hint}.")
    if match.key == "steam":
        from jarvis.tools.steam import prepare_steam_launch

        switched = prepare_steam_launch(ctx)
        if switched and ctx.apps.find_processes(app, match):
            ctx.dialog.last_app = match.display
            return ToolResult(True, f"Переключаю Steam на аккаунт {switched.persona or switched.account}.",
                              {"app": match.display})
        if switched:
            ctx.apps.launch(match)
            ctx.dialog.last_app = match.display
            return ToolResult(True, f"Запускаю Steam на аккаунте {switched.persona or switched.account}.",
                              {"app": match.display})
    ctx.apps.launch(match)
    ctx.dialog.last_app = match.display
    return ToolResult(True, f"Запускаю {match.display}.", {"app": match.display})


def _find(ctx, app: str) -> tuple[str, list]:
    """(название для ответа, процессы). Игры Steam ищутся по папке установки: у CS2 процесс — cs2.exe."""
    match = ctx.apps.resolve(app)
    procs = ctx.apps.find_processes(app, match)
    display = match.display if match else app
    if not procs:
        from jarvis.tools.steam import game_processes

        try:
            game = game_processes(app)
        except OSError:
            game = None
        if game and game[1]:
            display, procs = game
    return display, procs


def _describe(procs) -> str:
    names = sorted({p.info.get("name") or p.name() for p in procs})
    return ", ".join(names)


@tool("close_app", "Закрыть приложение штатно (как крестиком окна). Если оно не закрылось, будет предложено "
      "завершить принудительно.",
      params={"app": {"type": "string", "description": "Название приложения"}}, required=["app"],
      announce="Закрываю {app}", category="apps")
def close_app(ctx, app: str) -> ToolResult:
    display, procs = _find(ctx, app)
    if not procs:
        return ToolResult(False, f"{display} сейчас не запущен.")
    names = sorted({(p.info.get("name") or "") for p in procs if p.info.get("name")})
    for name in names:
        subprocess.run(["taskkill", "/IM", name], capture_output=True, creationflags=CREATE_NO_WINDOW)
    _, alive = psutil.wait_procs(procs, timeout=4)
    alive = [p for p in alive if p.is_running()]
    if alive:
        return ToolResult(False, f"{display} не закрылся штатно (возможно, свернулся в трей). "
                                 f"Завершить процесс принудительно?",
                          followup=("kill_app", {"app": app}))
    if ctx.dialog.last_app == display:
        ctx.dialog.last_app = None
    return ToolResult(True, f"Закрыл {display}.")


@tool("kill_app", "Принудительно завершить процессы приложения (несохранённые данные будут потеряны).",
      params={"app": {"type": "string", "description": "Название приложения"}}, required=["app"],
      dangerous=True, confirm="Принудительно завершить {app}? Несохранённые данные будут потеряны.",
      announce="Принудительно завершаю {app}", category="apps")
def kill_app(ctx, app: str) -> ToolResult:
    display, procs = _find(ctx, app)
    if not procs:
        return ToolResult(False, f"{display} не запущен.")
    for p in procs:
        try:
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _, alive = psutil.wait_procs(procs, timeout=3)
    if any(p.is_running() for p in alive):
        return ToolResult(False, f"Не удалось завершить {display}: процесс не отвечает или нужны права администратора.")
    return ToolResult(True, f"Процессы {display} завершены.")


@tool("list_apps", "Показать список установленных приложений, похожих на запрос (или первые из списка).",
      params={"filter": {"type": "string", "description": "Необязательный фильтр"}}, category="apps")
def list_apps(ctx, filter: str | None = None) -> ToolResult:
    names = ctx.apps.list_names(500)
    if filter:
        f = filter.lower()
        names = [n for n in names if f in n.lower()]
    shown = ", ".join(names[:40])
    return ToolResult(True, f"Найдено {len(names)} приложений: {shown}", {"count": len(names)})
