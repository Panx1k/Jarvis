"""Интерфейс голосом: цвет JARVIS, режим подтверждений, звуки и голосовые записи."""
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


@tool("set_confirmation_mode", "Режим подтверждений: strict — спрашивать чаще (опасные действия, закрытие программ, "
      "VPN, смена аккаунта), normal — только опасные действия (по умолчанию), minimal — только необратимые "
      "(удаление, выключение, команды, отправка сообщений).",
      params={"mode": {"type": "string", "enum": ["strict", "normal", "minimal"]}}, required=["mode"],
      dangerous=lambda a: _policy().parse_mode(a.get("mode", "")) == "minimal",
      confirm="Спрашивать подтверждение только для необратимых действий?",
      announce="Меняю режим подтверждений", category="interface",
      patterns=[r"(?:режим|уровень)\s+подтверждени\w*\s+(?:на\s+)?(?P<mode>\S+)",
                r"^(?:поставь|сделай|включи)\s+(?P<mode>\S+)\s+режим\s+подтверждени\w*$"])
def set_confirmation_mode(ctx, mode: str) -> ToolResult:
    policy = _policy()
    value = policy.parse_mode(mode) or (mode if mode in policy.MODES else None)
    if value is None:
        return ToolResult(False, "Есть три режима подтверждений: строгий, обычный и минимальный.")
    ctx.settings.set("confirm.mode", value)
    return ToolResult(True, f"Режим подтверждений: {policy.MODES[value].lower()} — {policy.MODE_HINTS[value]}.",
                      {"confirm_mode": value})


def _policy():
    from jarvis.core import policy

    return policy


SOUND_KINDS = {"samples": ("voice.samples.enabled", "Голосовые вставки JARVIS"),
               "system": ("voice.system_sounds", "Системные звуки JARVIS (приветствие при запуске и «Да, сэр»)")}


@tool("sound_settings", "Включить или выключить звуки JARVIS: kind=samples — голосовые вставки (фразы JARVIS из "
      "записей), kind=system — системные звуки (приветствие при запуске, «Да, сэр» на обращение).",
      params={"kind": {"type": "string", "enum": ["samples", "system"]}, "enabled": {"type": "boolean"}},
      required=["enabled"], announce="Меняю звуки", category="interface",
      patterns=[r"^(?P<enabled>включи|выключи|отключи|верни|убери)\s+(?:все\s+)?(?:звуковые\s+вставки|голосовые\s+"
                r"(?:вставки|записи)|семплы|сэмплы|вставки)$",
                r"^(?P<enabled>включи|выключи|отключи|верни|убери)\s+(?P<kind>системные)\s+звуки(?:\s+джарвиса)?$"])
def sound_settings(ctx, kind: str = "samples", enabled=True) -> ToolResult:
    if isinstance(enabled, str):
        enabled = not enabled.lower().startswith(("выкл", "откл", "убер", "off", "false", "нет"))
    key = "system" if (kind or "").lower().startswith(("system", "систем")) else "samples"
    setting, title = SOUND_KINDS[key]
    ctx.settings.set(setting, bool(enabled))
    return ToolResult(True, f"{title} {'включены' if enabled else 'выключены'}.", {"sound": key, "enabled": enabled})
