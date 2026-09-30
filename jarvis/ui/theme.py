"""Цвет интерфейса. Весь интерфейс построен вокруг голубого (#00d8ff): тема сдвигает оттенок всех голубых и синих
цветов (стили, ядро, мини-ядро, фон) к выбранному акценту. Красные ошибки и зелёные успехи не трогаются."""
from __future__ import annotations

import re

from PySide6.QtGui import QColor

BASE = QColor("#00d8ff")
PRESETS = {
    "jarvis": ("JARVIS — голубой", "#00d8ff"),
    "mark3": ("Mark III — красный", "#ff3b3b"),
    "gold": ("Золото", "#ffc53d"),
    "emerald": ("Изумруд", "#2dffb4"),
    "violet": ("Фиолетовый", "#b07cff"),
    "ice": ("Лёд — белый", "#e6f7ff"),
}
_accent = QColor(BASE)


def set_accent(value: str) -> None:
    global _accent
    c = QColor(value)
    _accent = c if c.isValid() else QColor(BASE)


def accent() -> str:
    return _accent.name()


def _hsv(c: QColor) -> tuple[float, float, float]:
    h, s, v, _ = c.getHsvF()
    return (h * 360 if h >= 0 else -1), s, v


def shift(c: QColor) -> QColor:
    """Голубые/синие оттенки (140°–240°) переносятся к акценту; остальное как есть."""
    if _accent.name() == BASE.name():
        return QColor(c)
    h, s, v = _hsv(c)
    if h < 140 or h > 240 or s < 0.12:
        return QColor(c)
    bh, bs, _ = _hsv(BASE)
    ah, as_, _ = _hsv(_accent)
    nh = (h + (ah - bh)) % 360
    ns = max(0.0, min(1.0, s * (as_ / bs if bs else 1)))
    out = QColor.fromHsvF(nh / 360, ns, v)
    out.setAlpha(c.alpha())
    return out


def shift_rgb(r: int, g: int, b: int, a: int = 255) -> QColor:
    return shift(QColor(r, g, b, a))


_HEX = re.compile(r"#([0-9a-fA-F]{6})\b")
_RGBA = re.compile(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)")


def qss(base: str) -> str:
    """Стили с перекрашенными цветами. Селекторы вида #brand не трогаются (у них не 6 шестнадцатеричных цифр
    перед пробелом или скобкой — а у тех, у кого есть, проверяется, что это значение свойства)."""
    if _accent.name() == BASE.name():
        return base

    def hex_sub(m: re.Match) -> str:
        start = m.start()
        before = base[max(0, start - 2):start]
        if not re.search(r"[:\s,]$", before):
            return m.group(0)
        return shift(QColor("#" + m.group(1))).name()

    def rgba_sub(m: re.Match) -> str:
        c = shift_rgb(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return f"rgba({c.red()}, {c.green()}, {c.blue()}, {m.group(4)})"

    return _RGBA.sub(rgba_sub, _HEX.sub(hex_sub, base))
