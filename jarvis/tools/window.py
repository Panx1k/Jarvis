"""Окна приложений: свернуть, развернуть на весь экран, показать (вывести на передний план)."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

import psutil

from jarvis.tools.base import ToolResult, tool
from jarvis.utils import winapi

user32 = ctypes.windll.user32
SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE = 6, 3, 9
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

APP = {"app": {"type": "string", "description": "Приложение или игра; пусто — активное окно"}}
_APP_RE = r"(?:\s+(?:окно\s+)?(?P<app>.+?))?"


def _windows_of(pids: set[int]) -> list[int]:
    """Главные окна процессов: видимые, с заголовком, без владельца (не диалоги и не всплывашки)."""
    found: list[int] = []

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4) or not user32.GetWindowTextLengthW(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            found.append(hwnd)
        return True

    user32.EnumWindows(_EnumProc(callback), 0)
    return found


def find_app_windows(ctx, app: str | None) -> tuple[str, list[int]]:
    """(название, окна). Без названия — активное окно (не окно самого JARVIS)."""
    if not app or app.strip().lower() in ("это", "его", "её", "ее", "окно", "текущее", "активное"):
        tracker = winapi.foreground
        hwnd = user32.GetForegroundWindow()
        if tracker is not None and tracker.own_window_active():
            hwnd = tracker.last_foreign
        return "окно", [hwnd] if hwnd else []
    from jarvis.tools.apps import _find

    display, procs = _find(ctx, app)
    pids = {p.pid for p in procs}
    for p in list(procs):
        try:
            pids.update(c.pid for c in p.children(recursive=True))
        except psutil.Error:
            pass
    return display, _windows_of(pids) if pids else []


@tool("window_minimize", "Свернуть окно приложения или игры (или активное окно).", params=APP,
      announce="Сворачиваю {app}", category="windows",
      patterns=[r"^(?:сверни|сворачивай|спрячь|убери)(?!\s+(?:все|всё|на\s+рабочий))" + _APP_RE + r"$"])
def window_minimize(ctx, app: str | None = None) -> ToolResult:
    name, wins = find_app_windows(ctx, app)
    if not wins:
        return ToolResult(False, f"Не нашёл открытого окна {name}." if app else "Нет активного окна.")
    for h in wins:
        user32.ShowWindow(h, SW_MINIMIZE)
    return ToolResult(True, f"Свернул {name}.", {"app": name})


@tool("window_maximize", "Развернуть окно приложения на весь экран и показать его.", params=APP,
      announce="Разворачиваю {app}", category="windows",
      patterns=[r"^(?:разверни|раскрой)" + _APP_RE + r"(?:\s+на\s+(?:весь|полный)\s+экран)?$",
                r"^(?:открой|сделай)\s+(?P<app>.+?)\s+на\s+(?:весь|полный)\s+экран$"])
def window_maximize(ctx, app: str | None = None) -> ToolResult:
    name, wins = find_app_windows(ctx, app)
    if not wins:
        return ToolResult(False, f"Окно {name} не открыто." if app else "Нет активного окна.")
    user32.ShowWindow(wins[0], SW_MAXIMIZE)
    winapi.focus_window(wins[0])
    return ToolResult(True, f"Развернул {name} на весь экран.", {"app": name})


@tool("window_show", "Показать окно приложения: восстановить из свёрнутого и вывести на передний план.",
      params=APP, required=["app"], announce="Показываю {app}", category="windows",
      patterns=[r"^(?:переключись на|покажи окно|верни окно)\s+(?P<app>.+?)$"])
def window_show(ctx, app: str) -> ToolResult:
    name, wins = find_app_windows(ctx, app)
    if not wins:
        return ToolResult(False, f"Окно {name} не открыто.")
    user32.ShowWindow(wins[0], SW_RESTORE)
    ok = winapi.focus_window(wins[0])
    return ToolResult(True, f"Показываю {name}." if ok else f"Восстановил окно {name}.", {"app": name})


@tool("show_desktop", "Свернуть все окна и показать рабочий стол (Win+D).", announce="Показываю рабочий стол",
      category="windows",
      patterns=[r"^(?:сверни все(?:\s+окна)?|покажи рабочий стол|сверни на рабочий стол|на рабочий стол)$"])
def show_desktop(ctx) -> ToolResult:
    winapi.press_combo([0x5B, 0x44])
    return ToolResult(True, "Показываю рабочий стол.")
