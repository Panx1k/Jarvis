"""Mini Overlay: маленькое живое ядро J.A.R.V.I.S. на рабочем столе, пока главное окно скрыто.

* прозрачное круглое окно без рамки, поверх обычных окон, не в панели задач, не забирает фокус;
* то же ядро и те же сигналы состояния/громкости, что у главного окна (один источник — Bridge);
* клик / двойной клик — открыть главное окно; правый клик — меню; перетаскивание мышью;
* позиция сохраняется (config/settings.json: ui.overlay.*) и восстанавливается с учётом нескольких
  мониторов: если монитор отключён — ядро переезжает на доступный;
* поверх полноэкранных приложений (игры, видео) ядро скрывается и возвращается после выхода из них.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QPainter
from PySide6.QtWidgets import QLabel, QMenu, QWidget

from jarvis.ui.core import CoreRenderer

POSITIONS = ["bottom_right", "bottom_left", "top_right", "top_left", "custom"]
POSITION_NAMES = {"bottom_right": "Снизу справа", "bottom_left": "Снизу слева", "top_right": "Сверху справа",
                  "top_left": "Сверху слева", "custom": "Своя (перетаскиванием)"}
STATE_TIPS = {"idle": "ONLINE", "listening": "LISTENING", "thinking": "THINKING", "executing": "EXECUTING",
              "speaking": "SPEAKING", "error": "ERROR"}
MARGIN = 18


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def foreground_is_fullscreen(own_pids: set[int]) -> bool:
    """Активное окно занимает весь монитор (игра, видео на весь экран) и это не рабочий стол."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value in own_pids:
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        return False
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    mon = user32.MonitorFromWindow(hwnd, 2)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not user32.GetMonitorInfoW(mon, ctypes.byref(info)):
        return False
    m = info.rcMonitor
    covers = rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom
    if not covers:
        return False
    exact = (rect.left, rect.top, rect.right, rect.bottom) == (m.left, m.top, m.right, m.bottom)
    caption = bool(user32.GetWindowLongW(hwnd, -16) & 0x00C00000)
    return exact or not caption


class Toast(QLabel):
    """Короткое уведомление рядом с мини-ядром (исчезает само)."""

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("overlayToast")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_near(self, text: str, anchor: QRect, kind: str = "ok") -> None:
        self.setProperty("kind", kind)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setText(text)
        self.adjustSize()
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = anchor.left() - self.width() - 6
        if x < area.left():
            x = anchor.right() + 6
        y = anchor.center().y() - self.height() // 2
        y = max(area.top(), min(y, area.bottom() - self.height()))
        self.move(x, y)
        self.show()
        self.raise_()
        self._timer.start(2600)


class MiniCore(QWidget):
    open_requested = Signal()
    settings_requested = Signal()
    exit_requested = Signal()
    voice_toggled = Signal(bool)
    overlay_toggled = Signal(bool)

    def __init__(self, settings):
        super().__init__(None)
        self.settings = settings
        self.renderer = CoreRenderer("mini")
        self._press: QPoint | None = None
        self._dragged = False
        self._fullscreen_hidden = False
        self.voice_on = True
        self.toast = Toast()
        self._apply_flags()
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.apply_settings()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._fs_timer = QTimer(self)
        self._fs_timer.timeout.connect(self._check_fullscreen)
        self._fs_timer.start(1500)
        QGuiApplication.instance().screenRemoved.connect(lambda _s: self.restore_position())
        QGuiApplication.instance().screenAdded.connect(lambda _s: self.restore_position())

    def cfg(self, key: str, default=None):
        return self.settings.get(f"ui.overlay.{key}", default)

    @property
    def enabled(self) -> bool:
        return bool(self.cfg("show", True))

    def _apply_flags(self) -> None:
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        if self.settings.get("ui.overlay.on_top", True):
            flags |= Qt.WindowStaysOnTopHint
        visible = self.isVisible()
        self.setWindowFlags(flags)
        if visible:
            self.show()

    def apply_settings(self) -> None:
        size = int(self.cfg("size", 64))
        self.core_size = max(40, min(120, size))
        pad = int(self.core_size * 0.45)
        self.setFixedSize(self.core_size + 2 * pad, self.core_size + 2 * pad)
        self.setWindowOpacity(max(0.25, min(1.0, float(self.cfg("opacity", 0.95)))))
        self.renderer.voice_reaction = bool(self.cfg("voice_reaction", True))
        self._apply_flags()
        self.restore_position()

    def _preset_point(self, preset: str, screen=None) -> QPoint:
        screen = screen or QGuiApplication.primaryScreen()
        a = screen.availableGeometry()
        w, h = self.width(), self.height()
        pad = int(self.core_size * 0.45)
        m = MARGIN - pad
        x = a.right() - w - m if "right" in preset else a.left() + m
        y = a.bottom() - h - m if "bottom" in preset else a.top() + m
        return QPoint(x, y)

    def restore_position(self) -> None:
        preset = self.cfg("position", "bottom_right")
        if preset == "custom" and self.cfg("pos"):
            x, y = self.cfg("pos")
            point = QPoint(int(x), int(y))
            center = point + QPoint(self.width() // 2, self.height() // 2)
            screen = QGuiApplication.screenAt(center)
            if screen is None:
                point = self._preset_point("bottom_right")
            else:
                a = screen.availableGeometry()
                point = QPoint(max(a.left() - self.width() // 3, min(point.x(), a.right() - self.width() * 2 // 3)),
                               max(a.top() - self.height() // 3, min(point.y(), a.bottom() - self.height() * 2 // 3)))
            self.move(point)
        else:
            self.move(self._preset_point(preset if preset in POSITIONS else "bottom_right"))

    def _save_position(self) -> None:
        self.settings.set("ui.overlay.position", "custom")
        self.settings.set("ui.overlay.pos", [self.x(), self.y()])

    def show_core(self) -> None:
        if not self.enabled:
            return
        self.restore_position()
        self.show()
        self._timer.start(66)

    def hide_core(self) -> None:
        self.hide()
        self.toast.hide()
        self._timer.stop()

    def _check_fullscreen(self) -> None:
        if not self.isVisible() and not self._fullscreen_hidden:
            return
        import os

        full = foreground_is_fullscreen({os.getpid()})
        if full and self.isVisible():
            self._fullscreen_hidden = True
            self.hide()
            self._timer.stop()
        elif not full and self._fullscreen_hidden:
            self._fullscreen_hidden = False
            if self.enabled:
                self.show()
                self._timer.start(66)

    @property
    def active(self) -> bool:
        """Ядро должно быть на экране (показано или временно спрятано из-за полноэкранного окна)."""
        return self.isVisible() or self._fullscreen_hidden

    def set_state(self, state: str) -> None:
        self.renderer.set_state(state)
        if self.isVisible():
            self._tick()

    def set_level(self, level: float) -> None:
        self.renderer.set_level(level)

    def set_output_level(self, level: float) -> None:
        self.renderer.set_output_level(level)

    def burst(self) -> None:
        self.renderer.burst()

    def notify(self, text: str, kind: str = "ok") -> None:
        if self.isVisible():
            self.toast.show_near(text, self.frameGeometry(), kind)

    def _tick(self) -> None:
        want = 16 if self.renderer.busy else 80
        if self._timer.interval() != want:
            self._timer.setInterval(want)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        self.renderer.paint(p, w / 2, h / 2, self.core_size / 2 * 1.05)
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.globalPosition().toPoint() - self.pos()
            self._dragged = False
        elif event.button() == Qt.RightButton:
            self._menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        if self._press is not None and event.buttons() & Qt.LeftButton:
            target = event.globalPosition().toPoint() - self._press
            if self._dragged or (target - self.pos()).manhattanLength() > 4:
                self._dragged = True
                self.move(target)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press is not None:
            if self._dragged:
                self._save_position()
            else:
                self.open_requested.emit()
        self._press = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.open_requested.emit()

    def enterEvent(self, event):
        from PySide6.QtWidgets import QToolTip

        state = STATE_TIPS.get(self.renderer.state, "ONLINE")
        QToolTip.showText(self.mapToGlobal(QPoint(0, 0)), f"J.A.R.V.I.S.\n{state}\nSay «Jarvis»…", self)
        super().enterEvent(event)

    def _menu(self, at: QPoint) -> None:
        menu = QMenu(self)
        menu.setObjectName("overlayMenu")
        menu.addAction("Open JARVIS", self.open_requested.emit)
        voice = QAction("Voice ON" if not self.voice_on else "Voice OFF", menu)
        voice.triggered.connect(lambda: self.voice_toggled.emit(not self.voice_on))
        menu.addAction(voice)
        menu.addAction("Overlay OFF", lambda: self.overlay_toggled.emit(False))
        menu.addAction("Settings", self.settings_requested.emit)
        menu.addSeparator()
        menu.addAction("Exit JARVIS", self.exit_requested.emit)
        menu.exec(at)
