"""Интерфейс голосом: цвет JARVIS."""
from __future__ import annotations

import re

from jarvis.tools.base import ToolResult, tool

COLORS = {
    "голуб": "#00d8ff", "синий": "#2f7bff", "син": "#2f7bff", "красн": "#ff3b3b", "железн": "#ff3b3b",
    "золот": "#ffc53d", "жёлт": "#ffd23d", "желт": "#ffd23d", "оранж": "#ff8a2a", "зелён": "#2dffb4",
    "зелен": "#2dffb4", "изумруд": "#2dffb4", "фиолет": "#b07cff", "пурпур": "#b07cff", "розов": "#ff5fb0",
    "бел": "#e6f7ff", "лёд": "#e6f7ff", "лед": "#e6f7ff", "обычн": "#00d8ff", "стандарт": "#00d8ff",
    "red": "#ff3b3b", "gold": "#ffc53d", "green": "#2dffb4", "blue": "#00d8ff", "purple": "#b07cff",
    "white": "#e6f7ff", "orange": "#ff8a2a", "pink": "#ff5fb0",
}


@tool("set_theme", "Сменить цвет интерфейса JARVIS: голубой (обычный), красный (Mark III), золотой, зелёный, "
      "фиолетовый, розовый, оранжевый, белый или #rrggbb.",
      params={"color": {"type": "string", "description": "Цвет словом или #rrggbb"}}, required=["color"],
      announce="Меняю цвет интерфейса", category="interface",
      patterns=[r"^(?:поменяй|смени|измени|сделай|поставь|верни)\s+(?:мне\s+)?(?:(?:цвет\s+)?интерфейс\w*|"
                r"цвет(?:\s+интерфейс\w*)?)\s+(?:на\s+)?(?P<color>\S+)$",
                r"^(?:верни|сделай|поставь)\s+(?P<color>\S+)\s+цвет(?:\s+интерфейс\w*)?$"])
def set_theme(ctx, color: str) -> ToolResult:
    c = (color or "").lower().replace("ё", "е").strip(" .!")
    value = c if re.fullmatch(r"#[0-9a-f]{6}", c) else next((v for k, v in COLORS.items()
                                                             if c.startswith(k.replace("ё", "е"))), None)
    if value is None:
        return ToolResult(False, "Не знаю такой цвет. Есть голубой, красный, золотой, зелёный, фиолетовый, "
                                 "розовый, оранжевый и белый.")
    return ToolResult(True, "Готово, сэр. Новый цвет интерфейса.", {"theme": value})
