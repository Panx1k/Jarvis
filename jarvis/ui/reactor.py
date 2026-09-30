"""Анимированный индикатор состояния в стиле «дугового реактора»."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

STATE_COLORS = {
    "idle": QColor(0, 200, 255),
    "listening": QColor(0, 255, 170),
    "thinking": QColor(255, 190, 40),
    "executing": QColor(190, 120, 255),
    "speaking": QColor(90, 170, 255),
    "error": QColor(255, 80, 90),
}


class Reactor(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "idle"
        self.level = 0.0
        self._smooth = 0.0
        self._t0 = time.monotonic()
        self._color = QColor(STATE_COLORS["idle"])
        self.setMinimumSize(220, 220)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Нажмите, чтобы говорить")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(16)

    def set_state(self, state: str) -> None:
        self.state = state

    def set_level(self, level: float) -> None:
        self.level = max(0.0, min(1.0, level))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()

    def _blend(self, target: QColor) -> QColor:
        c = self._color
        k = 0.12
        self._color = QColor(int(c.red() + (target.red() - c.red()) * k),
                             int(c.green() + (target.green() - c.green()) * k),
                             int(c.blue() + (target.blue() - c.blue()) * k))
        return self._color

    def paintEvent(self, event):
        t = time.monotonic() - self._t0
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        r = min(w, h) / 2 * 0.84
        color = self._blend(STATE_COLORS.get(self.state, STATE_COLORS["idle"]))
        self._smooth += (self.level - self._smooth) * 0.25

        if self.state == "listening":
            pulse = 0.08 + self._smooth * 0.35
            speed = 1.6
        elif self.state == "thinking":
            pulse, speed = 0.05, 4.5
        elif self.state == "executing":
            pulse, speed = 0.07 + 0.04 * abs(math.sin(t * 5)), 6.0
        elif self.state == "speaking":
            pulse, speed = 0.06 + 0.05 * abs(math.sin(t * 7)), 1.2
        else:
            pulse, speed = 0.03 * (1 + math.sin(t * 1.5)), 0.5

        glow = QRadialGradient(QPointF(cx, cy), r * (1.05 + pulse))
        g = QColor(color)
        g.setAlpha(70)
        glow.setColorAt(0.55, g)
        g.setAlpha(0)
        glow.setColorAt(1.0, g)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), r * (1.05 + pulse), r * (1.05 + pulse))

        for i, (radius, width, span, direction) in enumerate(
                [(0.95, 3, 70, 1), (0.83, 5, 110, -1), (0.70, 2, 200, 1), (0.60, 7, 40, -1)]):
            pen = QPen(color)
            pen.setWidthF(width)
            c2 = QColor(color)
            c2.setAlpha(200 - i * 30)
            pen.setColor(c2)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            rr = r * radius * (1 + pulse * 0.5)
            rect = QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr)
            base = (t * speed * 60 * direction * (1 + i * 0.3)) % 360
            for k in range(3 if i % 2 == 0 else 2):
                p.drawArc(rect, int((base + k * (360 / (3 if i % 2 == 0 else 2))) * 16), int(span * 16 / (1 + i % 2)))

        tick_pen = QPen(QColor(color.red(), color.green(), color.blue(), 90))
        tick_pen.setWidthF(1.5)
        p.setPen(tick_pen)
        for k in range(60):
            a = math.radians(k * 6 + t * 10)
            r1, r2 = r * 0.47, r * (0.5 if k % 5 else 0.53)
            p.drawLine(QPointF(cx + r1 * math.cos(a), cy + r1 * math.sin(a)),
                       QPointF(cx + r2 * math.cos(a), cy + r2 * math.sin(a)))

        core_r = r * (0.34 + pulse * 0.6)
        core = QRadialGradient(QPointF(cx, cy), core_r)
        core.setColorAt(0.0, QColor(255, 255, 255, 240))
        c3 = QColor(color)
        c3.setAlpha(220)
        core.setColorAt(0.45, c3)
        c3.setAlpha(0)
        core.setColorAt(1.0, c3)
        p.setPen(Qt.NoPen)
        p.setBrush(core)
        p.drawEllipse(QPointF(cx, cy), core_r, core_r)
        p.end()
