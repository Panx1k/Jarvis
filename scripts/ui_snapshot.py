"""Проверка интерфейса без человека: Command Center, состояния ядра, X → мини-ядро → окно, трей, выход.

    python scripts/ui_snapshot.py out_dir "который час" "какая громкость"

Ассистент запускается без микрофона и озвучки (не мешает работающему JARVIS). Уровни микрофона и голоса
подаются через тот же Bridge (AssistantListener), что использует настоящий голосовой цикл.
Сохраняет снимки: main.png, states_*.png, overlay.png — и печатает результаты проверок.
"""
from __future__ import annotations

import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from jarvis.core.assistant import Assistant
from jarvis.ui.app import JarvisApp, make_icon
from jarvis.ui.style import QSS

out = sys.argv[1] if len(sys.argv) > 1 else "ui_check"
commands = sys.argv[2:] or ["который час", "какая громкость"]
os.makedirs(out, exist_ok=True)

app = QApplication(sys.argv)
app.setStyleSheet(QSS)
app.setWindowIcon(make_icon())
app.setQuitOnLastWindowClosed(False)


class Quiet(Assistant):
    def greet(self):
        pass


japp = JarvisApp(app, start_minimized=False,
                 assistant_factory=lambda listener: Quiet(listener, enable_voice=False))
window, overlay, bridge = japp.window, japp.overlay, japp.bridge
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


def after(ms: int, fn):
    QTimer.singleShot(ms, fn)


def step_commands():
    window.assistant.set_voice_replies(False)
    for i, c in enumerate(commands):
        after(i * 2500, lambda c=c: window.assistant.submit_text(c))
    after(len(commands) * 2500 + 2000, step_main_snapshot)


def step_main_snapshot():
    window.grab().save(os.path.join(out, "main.png"))
    rows = window.timeline.box.count() - 1
    check("timeline has events", rows >= 4, f"{rows} событий")
    check("state word idle", window.state_word.text() == "ONLINE", window.state_word.text())
    seq = [("listening", 0.0), ("listening", 0.35), ("listening", 0.9), ("thinking", 0), ("executing", 0),
           ("speaking", 0.6), ("error", 0)]
    t = 0
    bridge.on_wake()
    for i, (state, level) in enumerate(seq):
        def apply(state=state, level=level, i=i):
            bridge.on_state(state)
            if state == "listening":
                bridge.on_level(level)
            if state == "speaking":
                bridge.on_output_level(level)
        after(t, apply)
        after(t + 450, lambda state=state, i=i, level=level: window.core.grab().save(
            os.path.join(out, f"state_{i}_{state}_{int(level * 100)}.png")))
        t += 600
    after(t, lambda: check("core states: status follows State Manager", window.state_word.text() == "ERROR",
                           window.state_word.text()))
    after(t + 100, lambda: bridge.on_state("idle"))
    after(t + 300, step_close_to_overlay)


def step_close_to_overlay():
    japp.settings.set("ui.background_mode", True)
    window.close()
    after(700, step_overlay_checks)


def step_overlay_checks():
    check("X hides main window", not window.isVisible())
    check("mini core visible", overlay.isVisible(), f"{overlay.width()}x{overlay.height()} @ {overlay.x()},{overlay.y()}")
    scr = app.primaryScreen().availableGeometry()
    inside = scr.contains(overlay.frameGeometry().center())
    check("mini core on screen (bottom right)", inside and overlay.x() > scr.width() / 2 and overlay.y() > scr.height() / 2)
    bridge.on_wake()
    bridge.on_state("listening")
    levels = [0.1, 0.4, 0.8, 0.5, 0.2]
    for i, lv in enumerate(levels):
        after(i * 80, lambda lv=lv: bridge.on_level(lv))
    after(350, lambda: overlay.grab().save(os.path.join(out, "overlay_listening.png")))
    after(500, lambda: (bridge.on_state("idle"), overlay.grab().save(os.path.join(out, "overlay_idle.png"))))
    after(700, step_drag)


def step_drag():
    overlay.move(overlay.x() - 200, overlay.y() - 150)
    overlay._save_position()
    saved = japp.settings.get("ui.overlay.pos")
    check("position saved", japp.settings.get("ui.overlay.position") == "custom" and saved == [overlay.x(), overlay.y()],
          str(saved))
    overlay.move(0, 0)
    overlay.restore_position()
    check("position restored", [overlay.x(), overlay.y()] == saved, f"{overlay.x()},{overlay.y()}")
    japp.settings.set("ui.overlay.pos", [-50000, -50000])
    overlay.restore_position()
    check("off-screen position fixed", app.primaryScreen().availableGeometry().contains(overlay.frameGeometry().center()))
    japp.settings.set("ui.overlay.position", "bottom_right")
    japp.settings.set("ui.overlay.pos", None)
    japp.set_overlay("show", False)
    check("overlay OFF hides core", not overlay.isVisible())
    check("assistant still alive after overlay OFF", window.assistant is not None and window.assistant._worker.is_alive())
    japp.set_overlay("show", True)
    check("overlay ON shows core", overlay.isVisible())
    overlay.open_requested.emit()
    after(500, step_back)


def step_back():
    check("core click opens main window", window.isVisible() and not overlay.isVisible())
    check("tray icon", japp.tray is not None and japp.tray.isVisible())
    t0 = time.monotonic()
    japp.open_settings()
    dlg = japp._settings_dialog
    after(400, lambda: (dlg.grab().save(os.path.join(out, "settings.png")), dlg.hide(),
                        check("settings dialog", True, f"{time.monotonic() - t0:.2f}s")))
    after(700, lambda: (window.resize(window.minimumSize()), None))
    after(1100, lambda: window.grab().save(os.path.join(out, "main_min.png")))
    after(1300, lambda: window.showMaximized())
    after(1900, lambda: window.grab().save(os.path.join(out, "main_max.png")))
    after(2100, step_exit)


def step_exit():
    worker = window.assistant._worker
    japp.quit()
    worker.join(3)
    finish(worker)


def finish(worker):
    check("Exit stops assistant", not worker.is_alive())
    for name, ok, detail in results:
        print(f"{'OK ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    print("saved to", out)
    app.exit(0 if all(ok for _, ok, _ in results) else 1)


def wait_ready():
    if window.assistant is None:
        after(200, wait_ready)
    else:
        step_commands()


after(300, wait_ready)
code = app.exec()
del math
sys.exit(code)
