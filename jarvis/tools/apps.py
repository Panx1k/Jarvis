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


def _excluded_check(action: str):
    def check(ctx, args: dict) -> ToolResult | None:
        from jarvis.tools.exclusions import is_excluded, refusal

        app = args.get("app") or args.get("game") or ""
        found = is_excluded(ctx, app)
        return refusal(found, action) if found else None
    return check


@tool("close_app", "Закрыть приложение штатно (как крестиком окна). Если оно не закрылось, будет предложено "
      "завершить принудительно. Программы из исключений не закрываются.",
      params={"app": {"type": "string", "description": "Название приложения"}}, required=["app"],
      announce="Закрываю {app}", category="apps", precheck=_excluded_check("закрывать"))
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
      announce="Принудительно завершаю {app}", category="apps", irreversible=True,
      precheck=_excluded_check("завершать"))
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


def _uninstall_target(ctx, args: dict) -> ToolResult | None:
    """До подтверждения: найти программу, отказаться для исключений, уточнить при нескольких похожих."""
    from jarvis.services import installed
    from jarvis.tools.exclusions import is_excluded, refusal

    app = (args.get("app") or "").strip()
    if args.get("_target"):
        return None
    found = is_excluded(ctx, app)
    if found:
        return refusal(found, "удалять")
    from jarvis.tools.exclusions import _canonical

    programs = installed.installed()
    canonical = _canonical(ctx, app)
    matches = installed.find(app, programs)
    if canonical != app.strip():
        by_name = installed.find(canonical, programs)
        if by_name and (not matches or by_name[0][0] > matches[0][0]):
            matches = by_name
    if matches and is_excluded(ctx, matches[0][1].name):
        return refusal(is_excluded(ctx, matches[0][1].name), "удалять")
    try:
        from jarvis.tools.steam import find_game

        game = find_game(app)
    except Exception:
        game = None
    if game and (not matches or matches[0][0] < 0.95):
        args.update(_target=game[1], _kind="steam", _appid=game[0])
        return None
    if not matches:
        return ToolResult(False, f"Не нашёл «{app}» среди установленных программ. Если это приложение из Microsoft "
                                 f"Store, его можно удалить в «Параметры → Приложения». Открыть?",
                          followup=("open_settings", {"page": "apps"}))
    best_score, best = matches[0]
    close = [a for s, a in matches[1:4] if best_score - s < 0.05 and not installed.same(a.name, best.name)]
    if close:
        names = [best.name] + [a.name for a in close]
        return ToolResult(False, "Нашёл несколько похожих: " + ", ".join(names) + ". Какую удалить?",
                          {"candidates": names, "clarify": True})
    args.update(_target=best.name, _kind="registry", _critical=best.critical, _publisher=best.publisher)
    return None


def _uninstall_question(args: dict) -> str:
    name = args.get("_target") or args.get("app")
    who = f" ({args['_publisher']})" if args.get("_publisher") else ""
    warn = " Это системный компонент или драйвер — после удаления другие программы могут перестать работать." \
        if args.get("_critical") else ""
    where = " из Steam" if args.get("_kind") == "steam" else ""
    return f"Удалить {name}{who}{where} с компьютера?{warn}"


@tool("uninstall_app", "Удалить (деинсталлировать) программу или игру с компьютера. Откроется обычный мастер "
      "удаления программы. Программы из исключений не удаляются. Всегда с подтверждением.",
      params={"app": {"type": "string", "description": "Название программы"}}, required=["app"],
      dangerous=True, irreversible=True, confirm=_uninstall_question, precheck=_uninstall_target,
      announce=lambda a: f"Удаляю {a.get('_target') or a.get('app')}", category="apps",
      patterns=[r"^(?:удали|снеси|деинсталлируй|удалить)\s+(?:с\s+компьютера\s+)?(?:программу|приложение|прогу)\s+"
                r"(?P<app>.+?)(?:\s+с\s+(?:компьютера|компа|пк))?$"])
def uninstall_app(ctx, app: str, _target: str = "", _kind: str = "", _appid: str = "", **_) -> ToolResult:
    import subprocess

    from jarvis.services import installed

    if _kind == "steam":
        import os

        os.startfile(f"steam://uninstall/{_appid}")
        return ToolResult(True, f"Открыл удаление {_target} в Steam — подтвердите там.", {"app": _target})
    matches = [a for _, a in installed.find(_target or app) if installed.same(a.name, _target or app)] \
        or [a for _, a in installed.find(_target or app)][:1]
    if not matches:
        return ToolResult(False, f"Программа «{_target or app}» уже не найдена среди установленных.")
    target = matches[0]
    try:
        subprocess.Popen(installed.uninstall_command(target), shell=True, creationflags=CREATE_NO_WINDOW)
    except OSError as exc:
        return ToolResult(False, f"Не удалось запустить удаление {target.name}: {exc.strerror or exc}.")
    return ToolResult(True, f"Запустил удаление {target.name}. Подтвердите в окне установщика.", {"app": target.name})


@tool("list_apps","Показать список установленных приложений, похожих на запрос (или первые из списка).",
      params={"filter": {"type": "string", "description": "Необязательный фильтр"}}, category="apps")
def list_apps(ctx, filter: str | None = None) -> ToolResult:
    names = ctx.apps.list_names(500)
    if filter:
        f = filter.lower()
        names = [n for n in names if f in n.lower()]
    shown = ", ".join(names[:40])
    return ToolResult(True, f"Найдено {len(names)} приложений: {shown}", {"count": len(names)})
