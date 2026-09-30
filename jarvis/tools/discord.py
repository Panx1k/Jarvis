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


@tool("discord_toggle_mute", "Включить/выключить свой микрофон в Discord (переключатель, Ctrl+Shift+M).",
      announce="Переключаю микрофон в Discord", category="discord",
      patterns=[r"^(?:выключи|включи|отключи|заглуши|замьють|размьють|вруби|выруби)\s+(?:мне\s+)?(?:мой\s+)?"
                r"(?:микрофон|микро|микрик|мик)(?:\s+(?:в|на)\s+(?:дискорд\w*|discord))?$",
                r"^(?:мут|замутить|замуть|размуть)\s+(?:в\s+)?(?:дискорд\w*|discord)$"])
def discord_toggle_mute(ctx) -> ToolResult:
    if not _press_in_discord([0x11, 0x10, 0x4D]):
        return ToolResult(False, "Discord не запущен.")
    return ToolResult(True, "Переключил микрофон в Discord.")


@tool("discord_toggle_deafen", "Включить/выключить звук (deafen) в Discord (переключатель, Ctrl+Shift+D).",
      announce="Переключаю звук в Discord", category="discord",
      patterns=[r"^(?:выключи|включи|отключи|заглуши|верни)\s+(?:звук|наушники)\s+(?:в|на)\s+(?:дискорд\w*|discord)$",
                r"^(?:заглуши|оглуши)\s+(?:дискорд\w*|discord)$"])
def discord_toggle_deafen(ctx) -> ToolResult:
    if not _press_in_discord([0x11, 0x10, 0x44]):
        return ToolResult(False, "Discord не запущен.")
    return ToolResult(True, "Переключил звук в Discord.")
