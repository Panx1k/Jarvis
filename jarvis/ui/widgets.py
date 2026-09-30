"""Элементы HUD: стеклянные панели, строки показателей, лента событий, уведомления, фон командного центра."""
from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QProgressBar, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from jarvis.ui import theme


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


class Backdrop(QWidget):
    """Фон окна: тёмный navy-градиент, мягкое свечение в центре и тонкая сетка (кэшируется в картинку)."""

    def __init__(self):
        super().__init__()
        self._cache: QPixmap | None = None

    def resizeEvent(self, event):
        self._cache = None
        super().resizeEvent(event)

    def _render(self) -> QPixmap:
        pm = QPixmap(self.size())
        pm.fill(QColor(3, 7, 13))
        p = QPainter(pm)
        w, h = self.width(), self.height()
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0, theme.shift_rgb(4, 11, 21))
        g.setColorAt(1, QColor(2, 5, 10))
        p.fillRect(0, 0, w, h, g)
        glow = QRadialGradient(QPointF(w / 2, h * 0.45), max(w, h) * 0.55)
        glow.setColorAt(0, theme.shift_rgb(0, 120, 180, 45))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(0, 0, w, h, glow)
        pen = QPen(theme.shift_rgb(0, 150, 220, 14))
        pen.setWidth(1)
        p.setPen(pen)
        step = 32
        for x in range(0, w, step):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, step):
            p.drawLine(0, y, w, y)
        p.end()
        return pm

    def paintEvent(self, event):
        if self._cache is None or self._cache.size() != self.size():
            self._cache = self._render()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        p.end()


class HudPanel(QFrame):
    """Стеклянная панель с заголовком и HUD-уголками."""

    def __init__(self, title: str = ""):
        super().__init__()
        self.setObjectName("hudPanel")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(14, 12, 14, 12)
        self.lay.setSpacing(7)
        if title:
            t = QLabel(title)
            t.setObjectName("panelTitle")
            self.lay.addWidget(t)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(theme.shift_rgb(0, 216, 255, 150))
        pen.setWidthF(1.4)
        p.setPen(pen)
        w, h, L = self.width() - 1, self.height() - 1, 12
        for (x, y, dx, dy) in ((0, 0, 1, 1), (w, 0, -1, 1), (0, h, 1, -1), (w, h, -1, -1)):
            p.drawLine(x, y + dy * 4, x, y + dy * L)
            p.drawLine(x + dx * 4, y, x + dx * L, y)
        p.end()


class Metric(QWidget):
    """Строка показателя: НАЗВАНИЕ ............ значение + тонкая полоска."""

    def __init__(self, name: str, bar: bool = True):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.name = QLabel(name)
        self.name.setObjectName("metricName")
        self.value = QLabel("N/A")
        self.value.setObjectName("metricValue")
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.name)
        row.addStretch(1)
        row.addWidget(self.value)
        v.addLayout(row)
        self.bar = None
        if bar:
            self.bar = QProgressBar()
            self.bar.setObjectName("bar")
            self.bar.setRange(0, 100)
            self.bar.setTextVisible(False)
            self.bar.setFixedHeight(4)
            v.addWidget(self.bar)

    def set(self, text: str | None, percent: float | None = None, hot_at: float = 85) -> None:
        na = text is None
        self.value.setText("N/A" if na else text)
        if self.value.property("na") != na:
            self.value.setProperty("na", na)
            repolish(self.value)
        if self.bar is not None:
            self.bar.setValue(0 if percent is None else int(percent))
            hot = percent is not None and percent >= hot_at
            if self.bar.property("hot") != hot:
                self.bar.setProperty("hot", hot)
                repolish(self.bar)


class StatusRow(QWidget):
    """Строка статуса: НАЗВАНИЕ ● ЗНАЧЕНИЕ."""

    def __init__(self, name: str):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.name = QLabel(name)
        self.name.setObjectName("metricName")
        self.dot = QLabel("●")
        self.dot.setObjectName("dot")
        self.value = QLabel("N/A")
        self.value.setObjectName("metricValue")
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.name)
        row.addStretch(1)
        row.addWidget(self.dot)
        row.addWidget(self.value)
        self._full = ""

    def set(self, text: str | None, ok: bool | None = None, tip: str = "") -> None:
        self._full = "N/A" if text is None else text
        self._elide()
        self.value.setToolTip(tip or (text or ""))
        val = "" if ok is None else ("true" if ok else "false")
        if self.dot.property("ok") != val:
            self.dot.setProperty("ok", val)
            repolish(self.dot)
        na = text is None
        if self.value.property("na") != na:
            self.value.setProperty("na", na)
            repolish(self.value)

    def _elide(self) -> None:
        """Длинное значение сокращается «…» с конца, чтобы не вылезать за панель (полный текст — в подсказке)."""
        room = max(40, self.width() - self.name.sizeHint().width() - self.dot.sizeHint().width() - 22)
        self.value.setText(self.value.fontMetrics().elidedText(self._full, Qt.ElideRight, room))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


KIND_TAGS = {"voice": "VOICE", "ai": "AI", "tool": "TOOL", "system": "SYSTEM", "success": "SUCCESS", "error": "ERROR"}


class Timeline(QScrollArea):
    """Live Activity Timeline: события появляются плавно, новые — внизу; хранится не больше 150."""

    LIMIT = 150

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("timelineInner")
        self.box = QVBoxLayout(inner)
        self.box.setContentsMargins(2, 2, 8, 2)
        self.box.setSpacing(6)
        self.box.addStretch(1)
        self.setWidget(inner)
        self._stick = True
        self.verticalScrollBar().rangeChanged.connect(self._on_range)
        self.verticalScrollBar().valueChanged.connect(
            lambda v: setattr(self, "_stick", v >= self.verticalScrollBar().maximum() - 20))
        self.last: tuple[str, str, str] | None = None

    def _on_range(self, _lo, hi):
        if self._stick:
            self.verticalScrollBar().setValue(hi)

    def add(self, kind: str, title: str, text: str = "") -> QLabel | None:
        key = (kind, title, text)
        if key == self.last:
            return None
        self.last = key
        row = QWidget()
        row.setObjectName("evRow")
        v = QVBoxLayout(row)
        v.setContentsMargins(9, 2, 2, 2)
        v.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(7)
        tm = QLabel(dt.datetime.now().strftime("%H:%M:%S"))
        tm.setObjectName("evTime")
        tag = QLabel(title or KIND_TAGS.get(kind, kind.upper()))
        tag.setObjectName("evTag")
        tag.setProperty("kind", kind)
        head.addWidget(tm)
        head.addWidget(tag)
        head.addStretch(1)
        v.addLayout(head)
        body = None
        if text:
            body = QLabel(text)
            body.setObjectName("evText")
            body.setProperty("kind", kind)
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v.addWidget(body)
        self.box.insertWidget(self.box.count() - 1, row)
        effect = QGraphicsOpacityEffect(row)
        row.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", row)
        anim.setDuration(320)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(lambda: row.setGraphicsEffect(None))
        anim.start()
        while self.box.count() - 1 > self.LIMIT:
            item = self.box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        return body


class ToastHost:
    """Небольшие уведомления в правом нижнем углу окна: появляются, висят ~2.5 с и исчезают."""

    def __init__(self, parent: QWidget):
        self.parent = parent
        self.items: list[QLabel] = []

    def show(self, text: str, kind: str = "ok") -> None:
        t = QLabel(text, self.parent)
        t.setObjectName("toast")
        t.setProperty("kind", kind)
        t.adjustSize()
        effect = QGraphicsOpacityEffect(t)
        t.setGraphicsEffect(effect)
        self.items.append(t)
        self._layout()
        t.show()
        t.raise_()
        fade_in = QPropertyAnimation(effect, b"opacity", t)
        fade_in.setDuration(220)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.start()

        def fade_out():
            anim = QPropertyAnimation(effect, b"opacity", t)
            anim.setDuration(400)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.finished.connect(lambda: self._remove(t))
            anim.start()

        QTimer.singleShot(2600, fade_out)

    def _remove(self, t: QLabel) -> None:
        if t in self.items:
            self.items.remove(t)
        t.deleteLater()
        self._layout()

    def _layout(self) -> None:
        y = self.parent.height() - 96
        for t in reversed(self.items[-4:]):
            y -= t.height() + 6
            t.move(self.parent.width() - t.width() - 24, y)
        for t in self.items[:-4]:
            t.hide()
