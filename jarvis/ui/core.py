"""Живое AI Core — главный визуальный элемент J.A.R.V.I.S.

Одна отрисовка (CoreRenderer) используется и в главном окне (CoreView), и в мини-ядре на рабочем столе.
Всё, что движется, опирается на реальные данные:
  * состояние — из State Manager ассистента (AssistantListener.on_state);
  * громкость микрофона — существующий сигнал on_level (голосовой цикл / живой режим);
  * громкость голоса JARVIS — on_output_level (живой режим Gemini); если источник её не отдаёт (Edge TTS
    проигрывается системой), пульсация речи строится из огибающей, пока идёт озвучка;
  * «пробуждение» — on_wake (прозвучало «Jarvis»): вспышка и разлёт колец.

Производительность: частота кадров адаптивная — в покое ~20 к/с (мини-ядро ~12), в работе 60; скрытый
виджет не рисуется. Частицы детерминированы (без генератора случайных чисел в каждом кадре).
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QConicalGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

STATE_COLORS = {
    "idle": QColor(0, 200, 255),
    "listening": QColor(40, 255, 220),
    "thinking": QColor(70, 150, 255),
    "executing": QColor(150, 120, 255),
    "speaking": QColor(0, 225, 255),
    "error": QColor(255, 70, 90),
}
ACTIVE = {"listening", "thinking", "executing", "speaking", "error"}


class CoreRenderer:
    """Состояние анимации и отрисовка ядра в заданный круг. Ничего не знает о Qt-виджетах."""

    def __init__(self, detail: str = "full"):
        self.detail = detail
        self.state = "idle"
        self.voice_reaction = True
        self._t0 = time.monotonic()
        self._last = self._t0
        self._level = 0.0
        self._level_s = 0.0
        self._out = 0.0
        self._out_s = 0.0
        self._out_at = 0.0
        self._burst_at = -10.0
        self._state_at = self._t0
        self._color = QColor(STATE_COLORS["idle"])
        self._phase = 0.0

    def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self._state_at = time.monotonic()

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level)) if self.voice_reaction else 0.0

    def set_output_level(self, level: float) -> None:
        self._out = max(0.0, min(1.0, level))
        self._out_at = time.monotonic()

    def burst(self) -> None:
        self._burst_at = time.monotonic()

    @property
    def busy(self) -> bool:
        """Нужна ли плавная (60 к/с) анимация: активное состояние, вспышка или ещё не затихший звук."""
        now = time.monotonic()
        return (self.state in ACTIVE or now - self._burst_at < 1.2 or self._level_s > 0.02
                or self._out_s > 0.02)

    def _step(self) -> tuple[float, float]:
        now = time.monotonic()
        dt = min(0.1, now - self._last)
        self._last = now
        k = 1 - math.exp(-dt * 14)
        self._level_s += (self._level - self._level_s) * (k if self._level > self._level_s else k * 0.45)
        out = self._out if now - self._out_at < 0.25 else 0.0
        self._out_s += (out - self._out_s) * k
        speed = {"idle": 0.25, "listening": 0.6, "thinking": 2.2, "executing": 3.2, "speaking": 0.8,
                 "error": 0.15}.get(self.state, 0.3)
        self._phase += dt * speed
        from jarvis.ui import theme

        target = theme.shift(STATE_COLORS.get(self.state, STATE_COLORS["idle"]))
        c = self._color
        kc = 1 - math.exp(-dt * 6)
        self._color = QColor(int(c.red() + (target.red() - c.red()) * kc),
                             int(c.green() + (target.green() - c.green()) * kc),
                             int(c.blue() + (target.blue() - c.blue()) * kc))
        return now - self._t0, dt

    def _energy(self, t: float) -> float:
        """Амплитуда «жизни» ядра 0..1: голос пользователя, голос JARVIS или спокойное дыхание."""
        s = self.state
        if s == "listening":
            return 0.12 + self._level_s * 0.9
        if s == "speaking":
            if self._out_s > 0.01:
                return 0.15 + self._out_s * 0.85
            env = 0.5 + 0.25 * math.sin(t * 9.1) + 0.15 * math.sin(t * 13.7 + 1.3) + 0.1 * math.sin(t * 4.3)
            return 0.15 + max(0.0, env) * 0.45
        if s == "thinking":
            return 0.18 + 0.06 * math.sin(t * 3)
        if s == "executing":
            return 0.3 + 0.12 * abs(math.sin(t * 6))
        if s == "error":
            return 0.2 + 0.2 * (1 if int(t * 3) % 2 else 0)
        return 0.05 + 0.04 * math.sin(t * 1.3) + self._level_s * 0.25

    def paint(self, p: QPainter, cx: float, cy: float, r: float, opacity: float = 1.0) -> None:
        t, _dt = self._step()
        color = QColor(self._color)
        e = self._energy(t)
        mini = self.detail == "mini"
        burst = max(0.0, 1.0 - (time.monotonic() - self._burst_at) / 0.9)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(opacity)

        def col(alpha: int, c: QColor = color) -> QColor:
            q = QColor(c)
            q.setAlpha(max(0, min(255, alpha)))
            return q

        glow_r = r * (0.95 + e * 0.35 + burst * 0.35)
        g = QRadialGradient(QPointF(cx, cy), glow_r)
        g.setColorAt(0.0, col(int(90 + 90 * e + 100 * burst)))
        g.setColorAt(0.45, col(int(35 + 50 * e)))
        g.setColorAt(1.0, col(0))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy), glow_r, glow_r)

        if burst > 0:
            for i, delay in enumerate((0.0, 0.18, 0.36)):
                prog = min(1.0, max(0.0, (1 - burst) - delay) / (1 - delay))
                if 0 < prog < 1:
                    pen = QPen(col(int(230 * (1 - prog))))
                    pen.setWidthF(max(1.0, r * 0.05 * (1 - prog)))
                    p.setPen(pen)
                    p.setBrush(Qt.NoBrush)
                    rr = r * (0.45 + prog * (0.75 + i * 0.1))
                    p.drawEllipse(QPointF(cx, cy), rr, rr)

        if not mini:
            pen = QPen(col(70))
            pen.setWidthF(1.0)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), r * 0.98, r * 0.98)
            tick = QPen(col(110))
            tick.setWidthF(1.2)
            p.setPen(tick)
            rot = self._phase * 0.15
            for k in range(72):
                a = rot + k * math.tau / 72
                r1 = r * 0.93
                r2 = r * (0.965 if k % 6 else 0.99)
                p.drawLine(QPointF(cx + r1 * math.cos(a), cy + r1 * math.sin(a)),
                           QPointF(cx + r2 * math.cos(a), cy + r2 * math.sin(a)))

        rings = ([(0.86, 2.2, 3, 50, 1.0), (0.76, 4.0, 2, 95, -1.4), (0.66, 1.6, 4, 38, 2.1)] if not mini
                 else [(0.84, 2.0, 3, 60, 1.0), (0.70, 2.6, 2, 90, -1.5)])
        for i, (rad, width, count, span, direction) in enumerate(rings):
            rr = r * rad * (1 + e * 0.08 + burst * 0.18)
            pen = QPen(col(210 - i * 35))
            pen.setWidthF(max(1.0, width * (r / 160 if not mini else r / 40)))
            pen.setCapStyle(Qt.FlatCap)
            p.setPen(pen)
            rect = QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr)
            base = math.degrees(self._phase * direction * (1 + i * 0.35))
            for k in range(count):
                start = base + k * 360 / count
                p.drawArc(rect, int(start * 16), int(span * 16))

        wave_amp = 0.0
        if self.state == "listening":
            wave_amp = self._level_s
        elif self.state == "speaking":
            wave_amp = e - 0.15
        elif self.state == "idle":
            wave_amp = self._level_s * 0.3
        if wave_amp > 0.01:
            base_r = r * 0.53
            path = QPainterPath()
            n = 48 if mini else 120
            for k in range(n + 1):
                a = k * math.tau / n
                d = (math.sin(a * 6 + t * 5) * 0.55 + math.sin(a * 11 - t * 7.3) * 0.3
                     + math.sin(a * 3 + t * 2.1) * 0.15)
                rr = base_r * (1 + wave_amp * 0.32 * d)
                pt = QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a))
                path.moveTo(pt) if k == 0 else path.lineTo(pt)
            pen = QPen(col(int(120 + 130 * min(1.0, wave_amp * 1.5))))
            pen.setWidthF(max(1.0, r / (60 if mini else 110)))
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

        if self.state in ("thinking", "executing"):
            sweep = QConicalGradient(QPointF(cx, cy), -math.degrees(self._phase * 2.2) % 360)
            sweep.setColorAt(0.0, col(150 if self.state == "executing" else 110))
            sweep.setColorAt(0.12, col(0))
            sweep.setColorAt(1.0, col(0))
            p.setPen(Qt.NoPen)
            p.setBrush(sweep)
            p.drawEllipse(QPointF(cx, cy), r * 0.9, r * 0.9)

        if self.state in ("executing", "listening") and not (mini and self.state == "listening"):
            period = 0.55 if self.state == "executing" else 1.1
            for k in range(3):
                prog = ((t / period) + k / 3) % 1.0
                pen = QPen(col(int(150 * (1 - prog) * (0.5 + e))))
                pen.setWidthF(1.2)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                rr = r * (0.4 + prog * 0.55)
                p.drawEllipse(QPointF(cx, cy), rr, rr)

        if not mini:
            count = 26
            speed = {"executing": 2.6, "thinking": 1.6, "listening": 0.9}.get(self.state, 0.35)
            p.setPen(Qt.NoPen)
            for k in range(count):
                orbit = r * (0.6 + 0.3 * ((k * 37) % 11) / 11)
                a = k * 2.39996 + t * speed * (0.6 + (k % 5) * 0.15) * (1 if k % 2 else -1)
                size = 1.2 + (k % 3) * 0.7 + e * 1.5
                p.setBrush(col(int(90 + 120 * ((k * 53) % 7) / 7)))
                p.drawEllipse(QPointF(cx + orbit * math.cos(a), cy + orbit * math.sin(a)), size, size)

        core_r = r * (0.28 + e * 0.14 + burst * 0.12)
        cg = QRadialGradient(QPointF(cx, cy), core_r)
        cg.setColorAt(0.0, QColor(255, 255, 255, int(230 + 25 * burst)))
        cg.setColorAt(0.35, col(235, color.lighter(130)))
        cg.setColorAt(0.75, col(150))
        cg.setColorAt(1.0, col(0))
        p.setPen(Qt.NoPen)
        p.setBrush(cg)
        p.drawEllipse(QPointF(cx, cy), core_r, core_r)
        pen = QPen(col(200))
        pen.setWidthF(max(1.0, r / (45 if mini else 90)))
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        rim = r * (0.36 + e * 0.1 + burst * 0.1)
        p.drawEllipse(QPointF(cx, cy), rim, rim)
        p.setOpacity(1.0)


class CoreView(QWidget):
    """Большое ядро в центре главного окна."""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.renderer = CoreRenderer("full")
        self.setMinimumSize(260, 260)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Нажмите, чтобы говорить")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def set_state(self, state: str) -> None:
        self.renderer.set_state(state)
        self._tick()

    def set_level(self, level: float) -> None:
        self.renderer.set_level(level)

    def set_output_level(self, level: float) -> None:
        self.renderer.set_output_level(level)

    def burst(self) -> None:
        self.renderer.burst()
        self._tick()

    def _tick(self) -> None:
        if not self.isVisible():
            return
        want = 16 if self.renderer.busy else 50
        if self._timer.interval() != want:
            self._timer.setInterval(want)
        self.update()

    def showEvent(self, event):
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        self.renderer.paint(p, w / 2, h / 2, min(w, h) / 2 * 0.9)
        p.end()
