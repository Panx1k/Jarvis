"""Discord: чаты, сообщения, звонки, микрофон и звук (стандартные сочетания клавиш Discord).

Ctrl+K — быстрый переход к другу/каналу/серверу, Ctrl+Shift+M — микрофон, Ctrl+Shift+D — звук (deafen),
Ctrl+Enter — принять звонок, Esc — отклонить. Сочетания работают, когда окно Discord активно, поэтому JARVIS
на мгновение переключается на Discord и возвращает фокус обратно (в том числе в игру).
Отправка сообщений — только после подтверждения пользователя.
"""
from __future__ import annotations

import time

from jarvis.tools.base import ToolResult, tool
from jarvis.utils import winapi

DISCORD = {"discord.exe"}


def _discord_window() -> int | None:
    return winapi.find_window("Discord", DISCORD)


def _goto(target: str) -> int | None:
    """Быстрый переход Discord (Ctrl+K): набрать имя друга/канала/сервера и Enter. Возвращает окно."""
    hwnd = _discord_window()
    if not hwnd or not winapi.focus_window(hwnd):
        return None
    winapi.tap_key(0x1B)
    winapi.press_combo([0x11, 0x4B])
    time.sleep(0.35)
    winapi.type_unicode(target)
    time.sleep(0.8)
    winapi.tap_key(0x0D)
    time.sleep(0.6)
    return hwnd


def _title() -> str:
    hwnd = _discord_window()
    if not hwnd:
        return ""
    buf = winapi.ctypes.create_unicode_buffer(300)
    winapi.user32.GetWindowTextW(hwnd, buf, 300)
    return buf.value


@tool("discord_open", "Открыть в Discord личный чат с человеком, канал или сервер по названию (быстрый переход Ctrl+K).",
      params={"target": {"type": "string",
                         "description": "Имя друга, канала или сервера в именительном падеже («Вася», не «Васей»)"}},
      required=["target"],
      announce="Открываю {target} в Discord", category="discord")
def discord_open(ctx, target: str) -> ToolResult:
    if not _goto(target):
        return ToolResult(False, "Discord не запущен.")
    title = _title()
    return ToolResult(True, f"Открыл {target} в Discord." + (f" Сейчас открыто: {title}." if title else ""))


def _send_confirm(args: dict) -> str:
    return f"Отправить в Discord для {args.get('to', '?')}: «{args.get('text', '')}»?"


@tool("discord_send_message", "Отправить сообщение в Discord человеку или в канал. Сначала открывает чат, "
      "затем печатает и отправляет текст.",
      params={"to": {"type": "string", "description": "Кому: имя друга или канала в именительном падеже"},
              "text": {"type": "string", "description": "Текст сообщения"}}, required=["to", "text"],
      dangerous=True, confirm=_send_confirm, announce="Отправляю сообщение в Discord", category="discord")
def discord_send_message(ctx, to: str, text: str) -> ToolResult:
    previous = winapi.user32.GetForegroundWindow()
    if not _goto(to):
        return ToolResult(False, "Discord не запущен.")
    winapi.type_unicode(text)
    time.sleep(0.2)
    winapi.tap_key(0x0D)
    title = _title()
    time.sleep(0.2)
    if previous:
        winapi.focus_window(previous)
    return ToolResult(True, f"Отправил в Discord ({title or to}): «{text}».")


@tool("discord_answer_call", "Принять входящий звонок в Discord (Ctrl+Enter).", announce="Принимаю звонок",
      category="discord", patterns=[r"^(?:прими|ответь на|возьми)\s+(?:звонок|вызов)(?:\s+(?:в\s+)?(?:дискорд\w*|discord))?$"])
def discord_answer_call(ctx) -> ToolResult:
    if not _press_in_discord([0x11, 0x0D]):
        return ToolResult(False, "Discord не запущен.")
    return ToolResult(True, "Принял звонок в Discord.")


@tool("discord_decline_call", "Отклонить входящий звонок в Discord (Esc).", announce="Отклоняю звонок",
      category="discord", patterns=[r"^(?:отклони|сбрось)\s+(?:звонок|вызов)(?:\s+(?:в\s+)?(?:дискорд\w*|discord))?$"])
def discord_decline_call(ctx) -> ToolResult:
    if not _press_in_discord([0x1B]):
        return ToolResult(False, "Discord не запущен.")
    return ToolResult(True, "Отклонил звонок в Discord.")


@tool("discord_status", "Что сейчас открыто в Discord (чат/канал/сервер) и запущен ли он.",
      announce="Смотрю Discord", category="discord")
def discord_status(ctx) -> ToolResult:
    title = _title()
    if not title:
        return ToolResult(False, "Discord не запущен.")
    return ToolResult(True, f"Discord запущен, открыто: {title.removesuffix(' - Discord')}.", {"title": title})


def _press_in_discord(vks: list[int]) -> bool:
    hwnd = winapi.find_window("Discord", DISCORD)
    if not hwnd:
        return False
    previous = winapi.user32.GetForegroundWindow()
    if not winapi.focus_window(hwnd):
        return False
    winapi.press_combo(vks)
    time.sleep(0.15)
    if previous and previous != hwnd:
        winapi.focus_window(previous)
    return True


DS = r"(?:дискорд\w*|discord|дс\w*|дсе)"
OFF_WORDS = ("выключи", "отключи", "заглуши", "замьють", "выруби", "мут", "замутить", "замуть", "убери", "останови",
             "прекрати", "выключить", "оглуши", "off")
ON_WORDS = ("включи", "размьють", "вруби", "размуть", "верни", "запусти", "начни", "включить", "on")
ACTION = {"type": "string", "enum": ["on", "off", "toggle"],
          "description": "on — включить, off — выключить, toggle — переключить"}


def _want(action: str | None) -> str:
    a = (action or "").lower().strip()
    if a in ("on", "off", "toggle"):
        return a
    if any(a.startswith(w) for w in OFF_WORDS):
        return "off"
    if any(a.startswith(w) for w in ON_WORDS):
        return "on"
    return "toggle"


def _ui():
    from jarvis.services import discord_ui

    return discord_ui.get() if _discord_window() else None


@tool("discord_toggle_mute", "Микрофон в Discord: action=off — выключить (заглушить), on — включить, toggle — "
      "переключить. Смотрит текущее состояние, окно Discord не переключает.",
      params={"action": ACTION}, announce="Микрофон в Discord", category="discord",
      patterns=[r"^(?P<action>выключи|включи|отключи|заглуши|замьють|размьють|вруби|выруби)\s+(?:мне\s+)?(?:мой\s+)?"
                r"(?:микрофон|микро|микрик|мик)(?:\s+(?:в|на)\s+" + DS + r")?$",
                r"^(?P<action>мут|замутить|замуть|размуть)\s+(?:в\s+)?" + DS + "$"])
def discord_toggle_mute(ctx, action: str | None = None) -> ToolResult:
    want = _want(action)
    ui = _ui()
    if ui is not None:
        from jarvis.services import discord_ui

        ok, muted = ui.set_toggle(discord_ui.MIC, None if want == "toggle" else want == "off")
        if ok and muted is not None:
            return ToolResult(True, "Микрофон в Discord выключен." if muted else "Микрофон в Discord включён.",
                              {"muted": muted})
    if not _press_in_discord([0x11, 0x10, 0x4D]):
        return ToolResult(False, "Discord не запущен.")
    return ToolResult(True, "Переключил микрофон в Discord.")


@tool("discord_toggle_deafen", "Звук/наушники в Discord (deafen): action=off — выключить звук, on — включить, "
      "toggle — переключить.",
      params={"action": ACTION}, announce="Звук в Discord", category="discord",
      patterns=[r"^(?P<action>выключи|включи|отключи|заглуши|верни|вруби|выруби)\s+(?:мне\s+)?(?:звук|наушники|уши)"
                r"\s+(?:в|на)\s+" + DS + "$",
                r"^(?P<action>выключи|включи|отключи|верни|вруби|выруби)\s+(?:мне\s+)?наушники$",
                r"^(?P<action>заглуши|оглуши)\s+" + DS + "$"])
def discord_toggle_deafen(ctx, action: str | None = None) -> ToolResult:
    want = _want(action)
    ui = _ui()
    if ui is not None:
        from jarvis.services import discord_ui

        ok, deaf = ui.set_toggle(discord_ui.DEAFEN, None if want == "toggle" else want == "off")
        if ok and deaf is not None:
            return ToolResult(True, "Звук в Discord выключен." if deaf else "Звук в Discord включён.",
                              {"deafened": deaf})
    if not _press_in_discord([0x11, 0x10, 0x44]):
        return ToolResult(False, "Discord не запущен.")
    return ToolResult(True, "Переключил звук в Discord.")


@tool("discord_screen_share", "Демонстрация экрана («демка», стрим) в голосовом канале Discord: action=on — "
      "начать (выбирается основной экран), off — прекратить.",
      params={"action": ACTION}, announce="Демонстрация экрана в Discord", category="discord",
      patterns=[r"^(?P<action>включи|вруби|запусти|начни|выключи|выруби|останови|прекрати|убери|отключи)\s+(?:мне\s+)?"
                r"(?:демк\w*|демонстраци\w*(?:\s+экрана)?|стрим\w*|трансляци\w*|шеринг)(?:\s+(?:в|на)\s+" + DS + r")?$",
                r"^(?P<action>покажи)\s+(?:мой\s+)?экран\s+(?:в|на)\s+" + DS + "$"])
def discord_screen_share(ctx, action: str | None = None) -> ToolResult:
    want = _want(action)
    if action and action.lower().startswith("покажи"):
        want = "on"
    ui = _ui()
    if ui is None:
        return ToolResult(False, "Discord не запущен.")
    if want == "toggle":
        want = "off" if ui.state() and ui.state().get("sharing") else "on"
    result = ui.share_screen(want == "on")
    messages = {
        "started": (True, "Демонстрация экрана в Discord запущена."),
        "stopped": (True, "Демонстрация экрана остановлена."),
        "already": (True, "Демонстрация экрана уже идёт."),
        "not_sharing": (True, "Демонстрация экрана и так не идёт."),
        "no_call": (False, "Сначала зайдите в голосовой канал Discord — демонстрация запускается из звонка."),
        "picker": (True, "Открыл выбор экрана в Discord — выберите, что показать, и нажмите «Прямой эфир»."),
        "failed": (False, "Не получилось нажать кнопку демонстрации в Discord."),
    }
    ok, text = messages.get(result, (False, "Не получилось."))
    return ToolResult(ok, text, {"result": result})


@tool("discord_camera", "Камера в голосовом канале Discord: action=on — включить, off — выключить.",
      params={"action": ACTION}, announce="Камера в Discord", category="discord",
      patterns=[r"^(?P<action>включи|выключи|вруби|выруби|отключи)\s+(?:мне\s+)?(?:камеру|вебку|вебкамеру)"
                r"(?:\s+(?:в|на)\s+" + DS + r")?$"])
def discord_camera(ctx, action: str | None = None) -> ToolResult:
    from jarvis.services import discord_ui

    ui = _ui()
    if ui is None:
        return ToolResult(False, "Discord не запущен.")
    want = _want(action)
    names = discord_ui.CAMERA_OFF if want == "off" else discord_ui.CAMERA_ON
    b = ui._find_fresh(names, exact=False)
    if b is None:
        if want == "off":
            return ToolResult(True, "Камера и так выключена.")
        return ToolResult(False, "Не вижу кнопку камеры — вы в голосовом канале?")
    ui.click(b)
    return ToolResult(True, "Камера включена." if want != "off" else "Камера выключена.")


@tool("discord_disconnect", "Выйти из голосового канала Discord (положить трубку).",
      announce="Выхожу из голосового канала", category="discord",
      patterns=[r"^(?:выйди|ливни|отключись|уйди|выкинь меня)\s+(?:из\s+)?(?:войса|голосового(?:\s+канала)?|звонка|"
                r"голосовухи|канала)(?:\s+(?:в\s+)?" + DS + r")?$", r"^(?:положи трубку|сбрось звонок)$"])
def discord_disconnect(ctx) -> ToolResult:
    from jarvis.services import discord_ui

    ui = _ui()
    if ui is None:
        return ToolResult(False, "Discord не запущен.")
    b = ui._find_fresh(discord_ui.DISCONNECT)
    if b is None:
        return ToolResult(True, "Вы и так не в голосовом канале.")
    ui.click(b)
    return ToolResult(True, "Вышел из голосового канала.")
