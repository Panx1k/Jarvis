"""Системные действия: время, блокировка, сон, выключение, скриншот, команды."""
from __future__ import annotations

import ctypes
import datetime as dt
import subprocess
import time

from jarvis.tools._common import CREATE_NO_WINDOW, run_hidden
from jarvis.tools.base import ToolResult, tool
from jarvis.utils import winapi

_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
           "ноября", "декабря"]
_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


@tool("get_time", "Сказать текущее время.", announce="Смотрю время", category="system",
      patterns=[r"(?:который час|сколько (?:сейчас )?времени|скажи время|текущее время)"])
def get_time(ctx) -> ToolResult:
    now = dt.datetime.now()
    return ToolResult(True, f"Сейчас {now:%H:%M}.")


@tool("get_date", "Сказать сегодняшнюю дату и день недели.", announce="Смотрю дату", category="system",
      patterns=[r"(?:какое сегодня число|какой сегодня день|какая (?:сегодня )?дата|сегодняшняя дата)"])
def get_date(ctx) -> ToolResult:
    now = dt.datetime.now()
    return ToolResult(True, f"Сегодня {_WEEKDAYS[now.weekday()]}, {now.day} {_MONTHS[now.month - 1]} {now.year} года.")


@tool("lock_screen", "Заблокировать компьютер (экран блокировки).", announce="Блокирую компьютер", category="system",
      patterns=[r"^(?:заблокируй|заблокировать|блокируй)\s+(?:компьютер|комп|пк|экран|систему)$",
                r"^блокировка(?: экрана)?$"])
def lock_screen(ctx) -> ToolResult:
    winapi.lock_workstation()
    return ToolResult(True, "Блокирую компьютер.")


@tool("sleep_computer", "Перевести компьютер в спящий режим.", dangerous=True,
      confirm="Перевести компьютер в спящий режим?", announce="Перевожу компьютер в сон", category="system")
def sleep_computer(ctx) -> ToolResult:
    time.sleep(1.5)
    ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
    return ToolResult(True, "Перевожу компьютер в спящий режим.")


@tool("shutdown_computer", "Выключить компьютер (через заданное число секунд, по умолчанию 30; "
      "можно отменить командой cancel_shutdown).",
      params={"delay": {"type": "integer", "description": "Задержка в секундах"}}, dangerous=True,
      confirm=lambda a: f"Выключить компьютер через {int(a.get('delay') or 30)} секунд?",
      announce="Выключаю компьютер", category="system")
def shutdown_computer(ctx, delay: int = 30) -> ToolResult:
    delay = max(5, int(delay or 30))
    run_hidden(["shutdown", "/s", "/t", str(delay)])
    return ToolResult(True, f"Компьютер выключится через {delay} секунд. Скажите «отмени выключение», чтобы отменить.")


@tool("restart_computer", "Перезагрузить компьютер (через заданное число секунд, по умолчанию 30).",
      params={"delay": {"type": "integer", "description": "Задержка в секундах"}}, dangerous=True,
      confirm=lambda a: f"Перезагрузить компьютер через {int(a.get('delay') or 30)} секунд?",
      announce="Перезагружаю компьютер", category="system")
def restart_computer(ctx, delay: int = 30) -> ToolResult:
    delay = max(5, int(delay or 30))
    run_hidden(["shutdown", "/r", "/t", str(delay)])
    return ToolResult(True, f"Перезагрузка через {delay} секунд. Скажите «отмени выключение», чтобы отменить.")


@tool("cancel_shutdown", "Отменить запланированное выключение/перезагрузку.", announce="Отменяю выключение",
      category="system",
      patterns=[r"(?:отмени|отменить|отмена)\s+(?:выключени\w*|перезагрузк\w*)", r"не выключай(?:ся)?"])
def cancel_shutdown(ctx) -> ToolResult:
    r = run_hidden(["shutdown", "/a"])
    if r.returncode == 0:
        return ToolResult(True, "Выключение отменено.")
    return ToolResult(False, "Запланированного выключения нет.")


@tool("take_screenshot", "Сделать снимок экрана (сохраняется в Изображения\\Снимки экрана).",
      announce="Делаю скриншот", category="system",
      patterns=[r"(?:сделай|сними|заскринь)\s*(?:мне\s+)?(?:скриншот|скрин|снимок экрана)", r"^скриншот$"])
def take_screenshot(ctx) -> ToolResult:
    time.sleep(0.4)
    winapi.press_combo([0x5B, 0x2C])
    return ToolResult(True, "Скриншот сохранён в папку «Снимки экрана».")


@tool("open_settings", "Открыть страницу настроек Windows: sound, display, bluetooth, wifi, network, update, "
      "apps, privacy, personalization, battery и т. п.",
      params={"page": {"type": "string", "description": "Раздел (англ. код ms-settings), например sound"}},
      announce="Открываю настройки", category="system")
def open_settings(ctx, page: str | None = None) -> ToolResult:
    aliases = {"звук": "sound", "экран": "display", "дисплей": "display", "блютуз": "bluetooth",
               "bluetooth": "bluetooth", "wifi": "network-wifi", "вайфай": "network-wifi", "сеть": "network",
               "обновления": "windowsupdate", "update": "windowsupdate", "приложения": "appsfeatures",
               "apps": "appsfeatures", "персонализация": "personalization", "батарея": "batterysaver",
               "network": "network", "wi-fi": "network-wifi", "vpn": "network-vpn", "впн": "network-vpn"}
    code = aliases.get((page or "").lower().strip(), (page or "").strip())
    uri = f"ms-settings:{code}" if code else "ms-settings:"
    subprocess.Popen(["explorer.exe", uri], creationflags=CREATE_NO_WINDOW)
    return ToolResult(True, "Открываю настройки.")


@tool("run_command", "Выполнить команду в командной строке Windows (cmd) и вернуть вывод. Используй только "
      "когда нет специального инструмента.",
      params={"command": {"type": "string", "description": "Команда cmd"}}, required=["command"],
      dangerous=True, confirm=lambda a: f"Выполнить команду: {a.get('command')}?",
      announce="Выполняю команду", category="system")
def run_command(ctx, command: str) -> ToolResult:
    try:
        r = subprocess.run(command, shell=True, capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return ToolResult(False, "Команда выполнялась дольше минуты и была прервана.")
    out = (r.stdout or b"").decode("cp866", errors="replace").strip()
    err = (r.stderr or b"").decode("cp866", errors="replace").strip()
    text = (out or err or "(пустой вывод)")[-2000:]
    return ToolResult(r.returncode == 0, f"Команда завершилась с кодом {r.returncode}.", {"output": text})
