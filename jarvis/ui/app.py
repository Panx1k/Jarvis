"""Запуск графического интерфейса: Command Center + Mini Overlay + System Tray.

    J.A.R.V.I.S. (Assistant: AI Brain, Voice, Wake Word, STT, TTS, Tools, State)
        │  AssistantListener
        ▼
      Bridge (один источник состояния)
        ├── MainWindow   — командный центр
        ├── MiniCore     — живое ядро на рабочем столе, когда окно скрыто
        └── Tray         — меню в области уведомлений

Крестик окна: при «Работать в фоне» окно скрывается, JARVIS продолжает слушать, в углу появляется мини-ядро.
Полный выход — «Exit» в меню трея или мини-ядра.
"""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from jarvis.config import Settings, env
from jarvis.core.assistant import Assistant
from jarvis.ui import autostart
from jarvis.ui.main_window import Bridge, MainWindow
from jarvis.ui.monitor import SystemMonitor
from jarvis.ui.overlay import MiniCore
from jarvis.ui.style import QSS
from jarvis.utils import winapi


def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QRadialGradient(QPointF(32, 32), 30)
    g.setColorAt(0.0, QColor(255, 255, 255))
    g.setColorAt(0.35, QColor(0, 216, 255))
    g.setColorAt(1.0, QColor(0, 40, 70))
    p.setBrush(g)
    p.setPen(QColor(0, 216, 255))
    p.drawEllipse(3, 3, 58, 58)
    p.end()
    return QIcon(pm)


class JarvisApp:
    def __init__(self, qt_app: QApplication, start_minimized: bool = False, assistant_factory=None):
        self.qt = qt_app
        self.settings = Settings()
        self.bridge = Bridge()
        self._quitting = False
        self._settings_dialog = None
        self._factory = assistant_factory or (lambda listener: Assistant(listener))

        self.window = MainWindow(self._make_assistant, self.bridge)
        self.window.on_close = self._on_close
        self.window.open_settings = self.open_settings
        self.window.notify_background = self._notify_background

        self.overlay = MiniCore(self.settings)
        self.overlay.open_requested.connect(self.show_main)
        self.overlay.settings_requested.connect(self.open_settings)
        self.overlay.exit_requested.connect(self.quit)
        self.overlay.voice_toggled.connect(self.set_voice)
        self.overlay.overlay_toggled.connect(lambda v: self.set_overlay("show", v))
        b = self.bridge
        b.state.connect(self.overlay.set_state)
        b.level.connect(self.overlay.set_level)
        b.out_level.connect(self.overlay.set_output_level)
        b.wake.connect(self.overlay.burst)

        self.monitor = SystemMonitor(self.settings)
        self.monitor.stats.connect(self.window.show_stats)
        self.monitor.net.connect(self.window.show_net)
        self.window.monitor_refresh = self.monitor.refresh_network

        self._news_timer = QTimer()
        self._news_timer.timeout.connect(self._refresh_news)
        self._news_timer.start(30 * 60 * 1000)
        QTimer.singleShot(20000, self._refresh_news)

        self.update_info = None
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self.check_updates)
        self._update_timer.start(6 * 3600 * 1000)
        QTimer.singleShot(60000, self.check_updates)
        self.bridge.restart.connect(self.restart_after_update)
        self.bridge.theme.connect(self.set_accent)

        self._make_tray()
        if start_minimized:
            self.hide_to_background(first=True)
        else:
            self.show_main()

    def _make_assistant(self, listener):
        assistant = self._factory(listener)
        assistant.greet()
        self.monitor.apps = assistant.apps
        QTimer.singleShot(0, self._sync_menus)
        return assistant

    @property
    def assistant(self) -> Assistant | None:
        return self.window.assistant

    def check_updates(self, manual: bool = False, done=None) -> None:
        """Проверить GitHub в фоне; есть новая версия — сказать в ленте и в трее (один раз на версию)."""
        import threading

        from jarvis.services import updater

        def work():
            info = updater.check()
            QTimer.singleShot(0, lambda: self._update_checked(info, manual, done))

        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _update_checked(self, info, manual: bool, done) -> None:
        self.update_info = info
        if done:
            done(info)
        if not info.available or info.method != "zip":
            return
        if not manual and self.settings.get("update.notified") == info.latest:
            return
        self.settings.set("update.notified", info.latest)
        text = f"Доступно обновление JARVIS{': ' + info.message if info.message else ''}. Скажите «Jarvis, обновись»."
        self.window.timeline.add("system", "UPDATE", text)
        if self.tray:
            self.tray.showMessage("J.A.R.V.I.S.", text, make_icon(), 8000)

    def install_update(self, done=None) -> None:
        """Кнопка «Обновить» в настройках: скачать, установить и перезапустить."""
        import threading

        from jarvis.services import updater

        def work():
            ok, text = updater.apply(self.update_info)
            QTimer.singleShot(0, lambda: (done and done(ok, text), ok and self.restart_after_update()))

        threading.Thread(target=work, name="update-apply", daemon=True).start()

    def restart_after_update(self) -> None:
        from jarvis.services import updater

        updater.restart()
        self.quit()

    def _refresh_news(self) -> None:
        import threading

        from jarvis.services import news as news_service

        threading.Thread(target=news_service.get().refresh_counts, name="news-hud", daemon=True).start()

    def show_main(self) -> None:
        self.overlay.hide_core()
        self.window.showNormal() if self.window.isMinimized() else self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.monitor.start()
        winapi.enable_dark_titlebar(int(self.window.winId()))

    def hide_to_background(self, first: bool = False) -> None:
        self.window.hide()
        self.monitor.pause()
        self.overlay.show_core()
        if not first and not self.settings.get("ui.background_hint_shown", False) and self.tray:
            self.tray.showMessage("J.A.R.V.I.S.", "Я продолжаю работать в фоне. Скажите «Jarvis» или нажмите на ядро.",
                                  make_icon(), 4000)
            self.settings.set("ui.background_hint_shown", True)

    def _on_close(self, event) -> bool:
        """Крестик окна. True — событие обработано (окно скрыто, JARVIS работает в фоне)."""
        if self._quitting or not self.settings.get("ui.background_mode", True):
            self.quit()
            event.accept()
            return True
        event.ignore()
        self.hide_to_background()
        return True

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self.monitor.stop()
        self.overlay.hide_core()
        if self.tray:
            self.tray.hide()
        if self.assistant:
            self.assistant.shutdown()
        self.window.hide()
        self.qt.quit()

    def _notify_background(self, text: str, kind: str) -> None:
        self.overlay.notify(text, kind)

    def open_settings(self) -> None:
        from jarvis.ui.settings_dialog import SettingsDialog

        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(self, self.window if self.window.isVisible() else None)
        else:
            self._settings_dialog.sync()
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def set_overlay(self, key: str, value) -> None:
        self.settings.set(f"ui.overlay.{key}", value)
        if key == "show":
            if value and not self.window.isVisible():
                self.overlay.show_core()
            elif not value:
                self.overlay.hide_core()
        else:
            self.overlay.apply_settings()
        self._sync_menus()

    def set_accent(self, color: str) -> None:
        """Сменить цвет интерфейса сразу, без перезапуска (окно, ядро, мини-ядро, трей-меню)."""
        from jarvis.ui import theme
        from jarvis.ui.style import QSS

        theme.set_accent(color)
        self.settings.set("ui.theme.accent", theme.accent())
        self.qt.setStyleSheet(theme.qss(QSS))
        root = self.window.centralWidget()
        if hasattr(root, "_cache"):
            root._cache = None
        self.window.update()
        for w in self.window.findChildren(type(self.window.newsp)):
            w.update()
        self.overlay.update()

    def set_setting(self, key: str, value) -> None:
        """Настройка, которую читает сам ассистент (голосовые записи, режим разработчика) — через его Settings,
        чтобы изменение применилось сразу, без перезапуска."""
        target = self.assistant.settings if self.assistant else self.settings
        target.set(key, value)
        if target is not self.settings:
            self.settings.set(key, value)

    def set_voice(self, enabled: bool) -> None:
        self.window.set_wake(enabled)
        self._sync_menus()

    def set_voice_replies(self, enabled: bool) -> None:
        self.window.set_voice_replies(enabled)

    def set_live(self, enabled: bool) -> None:
        if self.assistant and self.assistant.set_live_mode(enabled):
            info = self.assistant.info()
            voice = info.get("live") if enabled else info.get("tts")
            self.window.a_voice.set(voice or "N/A", bool(voice), voice or "")

    def set_microphone(self, index: int, name: str) -> None:
        if self.assistant:
            self.assistant.set_microphone(index, name)

    def set_autostart(self, enabled: bool) -> None:
        autostart.set_enabled(enabled)
        self._sync_menus()

    def _make_tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(make_icon(), self.qt)
        self.tray.setToolTip("J.A.R.V.I.S. — ONLINE")
        menu = QMenu()
        menu.addAction("Open JARVIS", self.show_main)
        self.act_voice = QAction("Voice", menu, checkable=True)
        self.act_voice.triggered.connect(self.set_voice)
        self.act_overlay = QAction("Overlay", menu, checkable=True)
        self.act_overlay.triggered.connect(lambda v: self.set_overlay("show", v))
        self.act_autostart = QAction("Start with Windows", menu, checkable=True)
        self.act_autostart.triggered.connect(self.set_autostart)
        menu.addAction(self.act_voice)
        menu.addAction(self.act_overlay)
        menu.addAction(self.act_autostart)
        menu.addAction("Settings", self.open_settings)
        menu.addSeparator()
        menu.addAction("Exit", self.quit)
        menu.aboutToShow.connect(self._sync_menus)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.bridge.state.connect(lambda s: self.tray.setToolTip(
            f"J.A.R.V.I.S. — {({'idle': 'ONLINE'}).get(s, s.upper())}"))
        self.tray.show()
        self._sync_menus()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_main()

    def _sync_menus(self) -> None:
        voice_on = bool(self.assistant and self.assistant.wake_enabled)
        self.overlay.voice_on = voice_on
        if self.tray:
            self.act_voice.setChecked(voice_on)
            self.act_voice.setText(f"Voice: {'ON' if voice_on else 'OFF'}")
            show = bool(self.settings.get("ui.overlay.show", True))
            self.act_overlay.setChecked(show)
            self.act_overlay.setText(f"Overlay: {'ON' if show else 'OFF'}")
            self.act_autostart.setChecked(autostart.is_enabled())
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.sync()


def run_gui(start_minimized: bool | None = None) -> int:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Jarvis.Assistant")
    except OSError:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("J.A.R.V.I.S.")
    from jarvis.ui import theme

    theme.set_accent(Settings().get("ui.theme.accent", theme.BASE.name()) or theme.BASE.name())
    app.setStyleSheet(theme.qss(QSS))
    app.setWindowIcon(make_icon())
    app.setQuitOnLastWindowClosed(False)
    winapi.foreground = winapi.ForegroundTracker()

    if start_minimized is None:
        start_minimized = bool(Settings().get("ui.start_minimized", False))
    jarvis_app = JarvisApp(app, start_minimized=start_minimized)
    window = jarvis_app.window

    hotkey = None
    spec = env("HOTKEY", "ctrl+alt+j")
    if spec:
        from jarvis.ui.hotkey import GlobalHotkey
        holder: list = []
        hotkey = GlobalHotkey(spec, lambda: _hotkey_listen(window, holder[0].is_held))
        holder.append(hotkey)
    code = app.exec()
    if hotkey:
        hotkey.stop()
    return code


def _hotkey_listen(window: MainWindow, is_held) -> None:
    if window.assistant:
        window.assistant.listen(hold=is_held)
