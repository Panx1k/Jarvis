"""Эмуляция клавиатуры: печать текста и сочетания клавиш."""
from __future__ import annotations

import re
import time

from jarvis.tools.base import ToolResult, tool
from jarvis.utils import winapi
from jarvis.utils.text import normalize


def parse_keys(spec: str) -> list[int]:
    """«ctrl+shift+esc», «контрол c», «alt f4», «стрелка вниз» → список VK-кодов."""
    s = normalize(spec).strip().strip(".")
    s = re.sub(r"^(клавиш[уиа]|кнопк[уиа]|сочетание)\s+", "", s)
    s = s.replace("+", " ").replace("плюс", " ")
    tokens = s.split()
    vks: list[int] = []
    i = 0
    while i < len(tokens):
        pair = " ".join(tokens[i:i + 2])
        if i + 1 < len(tokens) and pair in winapi.KEY_NAMES:
            vks.append(winapi.KEY_NAMES[pair])
            i += 2
            continue
        tok = tokens[i]
        if tok in ("и", "потом"):
            i += 1
            continue
        if tok not in winapi.KEY_NAMES:
            raise ValueError(f"не знаю клавишу «{tok}»")
        vks.append(winapi.KEY_NAMES[tok])
        i += 1
    if not vks:
        raise ValueError("не указаны клавиши")
    return vks


@tool("type_text", "Напечатать текст в активном окне (как будто его набрал пользователь).",
      params={"text": {"type": "string", "description": "Текст для ввода"},
              "press_enter": {"type": "boolean", "description": "Нажать Enter после ввода"}},
      required=["text"], announce="Печатаю текст", category="keyboard")
def type_text(ctx, text: str, press_enter: bool = False) -> ToolResult:
    time.sleep(0.3)
    winapi.restore_foreground()
    winapi.type_unicode(text)
    if press_enter:
        winapi.tap_key(0x0D)
    return ToolResult(True, "Напечатал.")


@tool("send_enter", "Отправить введённое сообщение/подтвердить — нажать Enter в активном окне.",
      announce="Нажимаю Enter", category="keyboard",
      patterns=[r"^(?:отправь|отправить|отправляй|отправь его|отправь это|отправь сообщение|жми enter|энтер)$"])
def send_enter(ctx) -> ToolResult:
    time.sleep(0.2)
    winapi.restore_foreground()
    winapi.tap_key(0x0D)
    return ToolResult(True, "Отправил.")


@tool("press_key", "Нажать клавишу или сочетание клавиш: enter, esc, space, tab, ctrl+c, alt+tab, win+d, f5, "
      "стрелки и т. п.",
      params={"keys": {"type": "string", "description": "Например «ctrl+w», «alt+f4», «enter»"},
              "times": {"type": "integer", "description": "Сколько раз нажать (по умолчанию 1)"}},
      required=["keys"], announce="Нажимаю {keys}", category="keyboard")
def press_key(ctx, keys: str, times: int = 1) -> ToolResult:
    try:
        vks = parse_keys(keys)
    except ValueError as exc:
        return ToolResult(False, f"Не понял клавиши: {exc}.")
    time.sleep(0.2)
    winapi.restore_foreground()
    for _ in range(max(1, min(int(times or 1), 50))):
        if len(vks) == 1:
            winapi.tap_key(vks[0])
        else:
            winapi.press_combo(vks)
        time.sleep(0.05)
    return ToolResult(True, f"Нажал {keys}.")
