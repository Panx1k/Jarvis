"""Главное окно — AI Command Center.

┌──────────────────────────────────────────────────────────┐
│ J.A.R.V.I.S. ● ONLINE      «Systems operational.»   12:40 │
├────────────┬─────────────────────────────┬───────────────┤
│ SYSTEM     │                             │ ACTIVITY      │
│ NETWORK    │          AI CORE            │ (timeline)    │
│ AI         │   распознанная фраза        │               │
│            │   COMMAND CENTER (кнопки)   │               │
├────────────┴─────────────────────────────┴───────────────┤
│ 🎙  > Ask J.A.R.V.I.S. ...                VOICE WAKE SEND │
└──────────────────────────────────────────────────────────┘

Все данные — из существующего ассистента: состояние, уровни звука, события приходят через Bridge
(AssistantListener → сигналы Qt), команды уходят в Assistant (submit_text, listen, run_tool, confirm).
"""
from __future__ import annotations

import datetime as dt
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from jarvis.core.assistant import Assistant, AssistantListener, voice_enabled_by_default
from jarvis.ui.core import CoreView
from jarvis.ui.widgets import Backdrop, HudPanel, Metric, StatusRow, Timeline, ToastHost, repolish

STATE_WORD = {"idle": "ONLINE", "listening": "LISTENING", "thinking": "THINKING", "executing": "EXECUTING",
              "speaking": "SPEAKING", "error": "ERROR"}
PERSONALITY = {"idle": "Systems operational.", "listening": "Listening.", "thinking": "Processing.",
               "executing": "Executing command.", "speaking": "Speaking.", "error": "Something went wrong."}
PLACEHOLDER = {"idle": "Ask J.A.R.V.I.S. ...", "listening": "Listening...", "thinking": "Processing...",
               "executing": "Executing...", "speaking": "Speaking...", "error": "Ask J.A.R.V.I.S. ..."}
TOOL_TOASTS = {"vpn_on": "VPN CONNECTED", "vpn_off": "VPN DISCONNECTED", "open_app": "APPLICATION STARTED",
               "launch_game": "GAME LAUNCHING", "close_app": "APPLICATION CLOSED", "open_site": "SITE OPENED",
               "open_url": "BROWSER OPENED", "steam_switch_account": "STEAM ACCOUNT SWITCHED",
               "spotify_play": "NOW PLAYING", "discord_send_message": "MESSAGE SENT", "take_screenshot": "SCREENSHOT SAVED"}
QUIET_TOOLS = {"get_time", "get_date", "get_volume", "media_status", "vpn_status", "steam_games", "steam_game_info",
               "steam_accounts", "spotify_now_playing", "discord_status", "list_apps", "get_weather"}


class Bridge(QObject, AssistantListener):
    """Единственный источник состояния для интерфейса: события ассистента из рабочих потоков → сигналы Qt.
    К нему подключены и главное окно, и мини-ядро, и трей."""
    state = Signal(str)
    message = Signal(str, str)
    action = Signal(str, str, str, str)
    confirm = Signal(object)
    level = Signal(float)
    out_level = Signal(float)
    status = Signal(str)
    transcript = Signal(str)
    spoken = Signal(str)
    wake = Signal()
    tool = Signal(str, str, str)
    voice_output = Signal(str, object)
    debug = Signal(str)
    news = Signal(str, object)
    restart = Signal()
    theme = Signal(str)

    def on_state(self, state): self.state.emit(state)
    def on_message(self, role, text): self.message.emit(role, text)
    def on_action(self, action_id, text, status, detail=""): self.action.emit(action_id, text, status, detail)
    def on_confirm(self, question): self.confirm.emit(question)
    def on_level(self, level): self.level.emit(level)
    def on_output_level(self, level): self.out_level.emit(level)
    def on_status(self, text): self.status.emit(text)
    def on_transcript(self, text): self.transcript.emit(text)
    def on_spoken(self, text): self.spoken.emit(text)
    def on_wake(self): self.wake.emit()
    def on_tool(self, name, status, message=""): self.tool.emit(name, status, message)
    def on_voice_output(self, kind, info): self.voice_output.emit(kind, info)
    def on_debug(self, text): self.debug.emit(text)
    def on_news(self, category, info): self.news.emit(category, info)
    def on_restart(self): self.restart.emit()
    def on_theme(self, color): self.theme.emit(color)


def _short(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


class MainWindow(QMainWindow):
    def __init__(self, assistant_factory, bridge: Bridge | None = None):
        super().__init__()
        self.setWindowTitle("J.A.R.V.I.S.")
        self.resize(1320, 820)
        self.setMinimumSize(1040, 660)
        self.bridge = bridge or Bridge()
        self.state = "idle"
        self.on_close = None
        self.open_settings = None
        self._net = None
        self._tool_running = False
        self._build()
        self._connect()
        self.set_state("thinking")
        self.caption.setText("Инициализация систем…")
        self.assistant: Assistant | None = None
        QTimer.singleShot(50, lambda: self._init_assistant(assistant_factory))

    def _build(self) -> None:
        root = Backdrop()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 12, 18, 14)
        outer.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(14)
        brand = QLabel("J.A.R.V.I.S.")
        brand.setObjectName("brand")
        self.state_word = QLabel("ONLINE")
        self.state_word.setObjectName("brandState")
        self.personality = QLabel("")
        self.personality.setObjectName("personality")
        header.addWidget(brand)
        header.addWidget(self.state_word)
        header.addSpacing(10)
        header.addWidget(self.personality, 1)
        clock_box = QVBoxLayout()
        clock_box.setSpacing(0)
        self.clock = QLabel("--:--:--")
        self.clock.setObjectName("clock")
        self.clock.setAlignment(Qt.AlignRight)
        self.date = QLabel("")
        self.date.setObjectName("date")
        self.date.setAlignment(Qt.AlignRight)
        clock_box.addWidget(self.clock)
        clock_box.addWidget(self.date)
        header.addLayout(clock_box)
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("iconBtn")
        self.settings_btn.setToolTip("Настройки")
        self.hide_btn = QPushButton("◉")
        self.hide_btn.setObjectName("iconBtn")
        self.hide_btn.setToolTip("Свернуть в мини-ядро (JARVIS продолжит работать)")
        header.addWidget(self.settings_btn)
        header.addWidget(self.hide_btn)
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(14)

        left = QWidget()
        left.setObjectName("leftColumn")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(12)
        sysp = HudPanel("SYSTEM")
        self.m_cpu, self.m_ram, self.m_gpu, self.m_vram = (Metric("CPU"), Metric("RAM"), Metric("GPU"),
                                                            Metric("VRAM"))
        self.m_temp = Metric("TEMP", bar=False)
        self.m_net = Metric("NETWORK", bar=False)
        self.m_up = Metric("UPTIME", bar=False)
        for m in (self.m_cpu, self.m_ram, self.m_gpu, self.m_vram, self.m_temp, self.m_net, self.m_up):
            sysp.lay.addWidget(m)
        ll.addWidget(sysp)
        netp = HudPanel("NETWORK")
        self.n_vpn, self.n_inet, self.n_ping, self.n_ip = (StatusRow("VPN"), StatusRow("INTERNET"),
                                                           StatusRow("PING"), StatusRow("IP"))
        for r in (self.n_vpn, self.n_inet, self.n_ping, self.n_ip):
            netp.lay.addWidget(r)
        ll.addWidget(netp)
        aip = HudPanel("AI SYSTEMS")
        self.a_brain, self.a_voice, self.a_wake, self.a_keys = (StatusRow("BRAIN"), StatusRow("VOICE"),
                                                                StatusRow("WAKE"), StatusRow("KEYS"))
        self.a_samples = StatusRow("SAMPLES")
        for r in (self.a_brain, self.a_voice, self.a_wake, self.a_samples, self.a_keys):
            aip.lay.addWidget(r)
        ll.addWidget(aip)
        self.newsp = HudPanel("NEWS")
        self.news_rows: dict[str, StatusRow] = {}
        for cat in ("general", "gaming", "ai"):
            self._news_row(cat)
        ll.addWidget(self.newsp)
        ll.addStretch(1)
        left_scroll = QScrollArea()
        left_scroll.viewport().setAutoFillBackground(False)
        left_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        left_scroll.setWidget(left)
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setFixedWidth(262)
        body.addWidget(left_scroll)

        center = QVBoxLayout()
        center.setSpacing(6)
        self.core = CoreView()
        self.core.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        center.addWidget(self.core, 1)
        self.caption = QLabel("")
        self.caption.setObjectName("caption")
        self.caption.setAlignment(Qt.AlignCenter)
        self.caption.setWordWrap(True)
        self.caption.setMinimumHeight(22)
        center.addWidget(self.caption)
        self.subcaption = QLabel("")
        self.subcaption.setObjectName("subcaption")
        self.subcaption.setAlignment(Qt.AlignCenter)
        self.subcaption.setWordWrap(True)
        center.addWidget(self.subcaption)
        center.addSpacing(6)
        quick = QGridLayout()
        quick.setSpacing(8)
        self.quick_buttons = {}
        for i, (key, text) in enumerate([("browser", "BROWSER"), ("youtube", "YOUTUBE"), ("vpn", "VPN"),
                                         ("files", "FILES"), ("system", "SYSTEM"), ("settings", "SETTINGS")]):
            b = QPushButton(text)
            b.setObjectName("quick")
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setMinimumWidth(b.sizeHint().width())
            quick.addWidget(b, 0, i)
            self.quick_buttons[key] = b
        self._quick_grid = quick
        self._quick_cols = 6
        self.quick_buttons["browser"].setToolTip("Открыть браузер")
        self.quick_buttons["youtube"].setToolTip("Открыть YouTube")
        self.quick_buttons["vpn"].setToolTip("Включить / выключить VPN")
        self.quick_buttons["files"].setToolTip("Открыть проводник")
        self.quick_buttons["system"].setToolTip("Диспетчер задач")
        self.quick_buttons["settings"].setToolTip("Настройки J.A.R.V.I.S.")
        qwrap = QWidget()
        qwrap.setLayout(quick)
        qwrap.setMaximumWidth(720)
        qrow = QHBoxLayout()
        qrow.addStretch(1)
        qrow.addWidget(qwrap, 10)
        qrow.addStretch(1)
        center.addLayout(qrow)
        body.addLayout(center, 1)

        right = HudPanel("ACTIVITY")
        self.timeline = Timeline()
        right.lay.addWidget(self.timeline, 1)
        right.setMinimumWidth(300)
        right.setMaximumWidth(400)
        right.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        body.addWidget(right)
        outer.addLayout(body, 1)

        self.confirm_bar = QFrame()
        self.confirm_bar.setObjectName("confirmBar")
        cb = QHBoxLayout(self.confirm_bar)
        cb.setContentsMargins(14, 8, 8, 8)
        self.confirm_text = QLabel("")
        self.confirm_text.setObjectName("confirmText")
        self.confirm_text.setWordWrap(True)
        cb.addWidget(self.confirm_text, 1)
        self.yes_btn = QPushButton("Да, выполнить")
        self.yes_btn.setObjectName("yes")
        self.no_btn = QPushButton("Отмена")
        self.no_btn.setObjectName("no")
        cb.addWidget(self.yes_btn)
        cb.addWidget(self.no_btn)
        self.confirm_bar.hide()
        outer.addWidget(self.confirm_bar)

        bar = QFrame()
        bar.setObjectName("commandBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 7, 10, 7)
        bl.setSpacing(10)
        self.mic_btn = QPushButton("🎙")
        self.mic_btn.setObjectName("mic")
        self.mic_btn.setToolTip("Push-to-Talk: удерживайте и говорите (или Ctrl+Alt+J).\n"
                                "Короткое нажатие — слушать до паузы.")
        bl.addWidget(self.mic_btn)
        prompt = QLabel(">")
        prompt.setObjectName("prompt")
        bl.addWidget(prompt)
        self.input = QLineEdit()
        self.input.setObjectName("command")
        self.input.setPlaceholderText(PLACEHOLDER["idle"])
        bl.addWidget(self.input, 1)
        self.voice_toggle = QPushButton("VOICE")
        self.voice_toggle.setObjectName("toggle")
        self.voice_toggle.setCheckable(True)
        self.voice_toggle.setChecked(True)
        self.voice_toggle.setToolTip("Ответы голосом (выключено — только текст)")
        self.wake_toggle = QPushButton("WAKE")
        self.wake_toggle.setObjectName("toggle")
        self.wake_toggle.setCheckable(True)
        self.wake_toggle.setToolTip("Постоянно ждать «Jarvis»")
        bl.addWidget(self.voice_toggle)
        bl.addWidget(self.wake_toggle)
        self.send_btn = QPushButton("SEND")
        self.send_btn.setObjectName("send")
        bl.addWidget(self.send_btn)
        outer.addWidget(bar)

        self.setCentralWidget(root)
        self.toasts = ToastHost(root)

    def _connect(self) -> None:
        b = self.bridge
        b.state.connect(self.set_state)
        b.level.connect(self.core.set_level)
        b.out_level.connect(self.core.set_output_level)
        b.wake.connect(self._on_wake)
        b.message.connect(self._on_message)
        b.action.connect(self._on_action)
        b.tool.connect(self._on_tool)
        b.confirm.connect(self._on_confirm)
        b.status.connect(self._on_status)
        b.transcript.connect(self._on_transcript)
        b.spoken.connect(lambda t: self.subcaption.setText(_short(f"🔊 {t}", 140)))
        b.voice_output.connect(self._on_voice_output)
        b.debug.connect(lambda text: self.timeline.add("system", "DEBUG", text))
        b.news.connect(self._on_news)
        self.send_btn.clicked.connect(self.send)
        self.input.returnPressed.connect(self.send)
        self._ptt = threading.Event()
        self.mic_btn.pressed.connect(self._ptt_pressed)
        self.mic_btn.released.connect(self._ptt.clear)
        self.core.clicked.connect(self.listen)
        self.yes_btn.clicked.connect(lambda: self._confirm(True))
        self.no_btn.clicked.connect(lambda: self._confirm(False))
        self.voice_toggle.toggled.connect(self._voice_replies_changed)
        self.wake_toggle.toggled.connect(self._wake_changed)
        self.settings_btn.clicked.connect(lambda: self.open_settings and self.open_settings())
        self.quick_buttons["settings"].clicked.connect(lambda: self.open_settings and self.open_settings())
        self.hide_btn.clicked.connect(self.close)
        self.quick_buttons["browser"].clicked.connect(
            lambda: self._tool("open_url", {"url": "https://www.google.com"}, "Открой браузер"))
        self.quick_buttons["youtube"].clicked.connect(lambda: self._tool("open_site", {"site": "youtube"},
                                                                         "Открой YouTube"))
        self.quick_buttons["vpn"].clicked.connect(self._toggle_vpn)
        self.quick_buttons["files"].clicked.connect(lambda: self._tool("open_folder", {}, "Открой проводник"))
        self.quick_buttons["system"].clicked.connect(lambda: self._tool("open_app", {"app": "диспетчер задач"},
                                                                        "Открой диспетчер задач"))
        QShortcut(QKeySequence("Escape"), self, activated=self._escape)
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self.listen)
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start(1000)
        self._tick_clock()

    def _init_assistant(self, factory) -> None:
        try:
            self.assistant = factory(self.bridge)
        except Exception as exc:
            self.set_state("error")
            self.timeline.add("error", "ERROR", f"Не удалось запустить ассистента: {exc}")
            return
        info = self.assistant.info()
        self.a_brain.set(_short(info["brain"], 40), True, info["brain"])
        voice = info.get("live") if getattr(self.assistant, "live_enabled", False) else info["tts"]
        self.a_voice.set(_short(voice or "N/A", 34), bool(voice), voice or "")
        self._wake_name = info.get("wake") or "N/A"
        self.a_wake.set(_short(self._wake_name, 34), None, f"Активатор: {self._wake_name}\n"
                                                         f"Микрофон: {info.get('mic')}")
        self.set_state("idle")
        self.caption.setText("")
        self.subcaption.setText("Скажите «Jarvis», удерживайте 🎙 / Ctrl+Alt+J или напишите команду")
        if self.assistant.voice_error:
            self.timeline.add("error", "VOICE", f"Голосовой ввод недоступен: {self.assistant.voice_error}")
        for notice in info.get("notices", []):
            self.timeline.add("system", "SYSTEM", notice)
        self.assistant.verify_brain()
        self._refresh_keys()
        self._keys_timer = QTimer(self)
        self._keys_timer.timeout.connect(self._refresh_keys)
        self._keys_timer.start(5000)
        self.timeline.add("system", "SYSTEM", f"Systems operational. Инструментов: {info['tools']}.")
        lib = info.get("samples")
        if lib:
            ready = "READY" if lib["ready"] else "EMPTY"
            self.a_samples.set(f"{lib['samples']} · {ready}", lib["ready"],
                               f"Голосовая библиотека: {lib['samples']} записей, со смысловыми тегами: {lib['tagged']}"
                               + (f", пропущено повреждённых: {lib['broken']}" if lib["broken"] else ""))
            self.timeline.add("system", "VOICE LIBRARY", f"{lib['samples']} samples · {ready}")
        else:
            self.a_samples.set(None)
        wake_on = self.assistant.settings.get("voice.wake_enabled", voice_enabled_by_default())
        if wake_on and self.assistant.voice_loop:
            self.wake_toggle.setChecked(True)
        else:
            self._sync_wake()

    def set_state(self, state: str) -> None:
        prev, self.state = self.state, state
        self.core.set_state(state)
        waiting = state == "idle" and self.assistant is not None and self.assistant.wake_enabled
        word = STATE_WORD.get(state, state.upper())
        if state == "speaking" and getattr(self, "_voice_kind", None):
            word += f" · {self._voice_kind}"
        elif state != "speaking":
            self._voice_kind = None
        self.state_word.setText(word)
        self.state_word.setProperty("state", state)
        repolish(self.state_word)
        self.personality.setText(PERSONALITY.get(state, ""))
        self.input.setPlaceholderText(PLACEHOLDER.get(state, PLACEHOLDER["idle"]))
        self.mic_btn.setProperty("active", "true" if state == "listening" else "false")
        repolish(self.mic_btn)
        if state == "thinking" and prev in ("idle", "listening"):
            self.timeline.add("ai", "ANALYZING")
        if state == "idle":
            self.core.set_level(0)
            if prev in ("speaking", "executing"):
                self.personality.setText("Task completed.")
            if self.subcaption.text().endswith("…") or self.subcaption.text().startswith("🔊"):
                self.subcaption.setText("Жду «Jarvis»" if waiting else "Готов к командам")

    def _on_wake(self) -> None:
        self.core.burst()
        self.caption.setText("")
        self.timeline.add("voice", "WAKE WORD", "«Jarvis»")

    def _on_transcript(self, text: str) -> None:
        self.caption.setText(_short(f"«{text}»", 180))
        self.input.setPlaceholderText(_short(text, 90))
        self.timeline.add("voice", "VOICE INPUT", f"«{text}»")
        self._last_voice = text

    def _on_message(self, role: str, text: str) -> None:
        if role == "user":
            if text and text == getattr(self, "_last_voice", None):
                return
            self.timeline.add("voice", "COMMAND", f"«{text}»")
        elif role == "assistant":
            self.timeline.add("ai", "J.A.R.V.I.S.", text)
        else:
            self.timeline.add("system", "SYSTEM", text)
            self._refresh_keys()

    def _on_action(self, action_id: str, text: str, status: str, detail: str) -> None:
        if status == "running":
            self._tool_running = True
            self.timeline.add("tool", "TOOL", text)
        elif status == "ok":
            self._tool_running = False
            self.timeline.add("success", "RESULT", _short(detail or "Готово", 200))
        elif status == "error":
            self._tool_running = False
            self.timeline.add("error", "ERROR", _short(f"{text}: {detail}" if detail else text, 200))
        elif status == "confirm":
            self.timeline.add("system", "CONFIRM", text)

    def _on_tool(self, name: str, status: str, message: str) -> None:
        if status == "error":
            self.notify("ERROR", "error")
        elif status == "ok" and name not in QUIET_TOOLS:
            self.notify(TOOL_TOASTS.get(name, "COMMAND COMPLETED"), "success")
        if name in ("vpn_on", "vpn_off") and status != "running" and hasattr(self, "monitor_refresh"):
            self.monitor_refresh()

    VOICE_KIND = {"sample": "VOICE SAMPLE", "hybrid": "HYBRID", "tts": "TTS", "live": "GEMINI VOICE"}

    def _on_voice_output(self, kind: str, info: dict) -> None:
        """Чем озвучен ответ: записью JARVIS, записью + синтезом или обычным TTS."""
        label = self.VOICE_KIND.get(kind)
        if not label:
            return
        self._voice_kind = label
        if self.state == "speaking":
            self.state_word.setText(f"SPEAKING · {label}")
        if kind in ("sample", "hybrid"):
            self.timeline.add("ai", label, f"{info.get('intent', '')}  ·  {info.get('file', '')}")
        else:
            self.timeline.add("ai", label)

    NEWS_LABELS = {"general": "GENERAL", "gaming": "GAMING", "ai": "AI", "tech": "TECH", "science": "SCIENCE",
                   "space": "SPACE", "business": "BUSINESS", "sports": "SPORTS", "politics": "POLITICS"}

    def _news_row(self, cat: str) -> StatusRow:
        row = self.news_rows.get(cat)
        if row is None:
            row = StatusRow(self.NEWS_LABELS.get(cat, cat.upper()))
            row.set(None)
            self.newsp.lay.addWidget(row)
            self.news_rows[cat] = row
        return row

    def _on_news(self, category: str, info: dict) -> None:
        """NEWS HUD: сколько свежих историй; в ленте — только при запросе пользователя (не фоновое обновление)."""
        count, stale = info.get("count", 0), info.get("stale")
        when = dt.datetime.now().strftime("%H:%M")
        text = f"{count} stories · {'saved' if stale else when}"
        self._news_row(category).set(text, not stale and count > 0,
                                     f"Источников ответило: {info.get('sources', 0)}. Обновлено в {when}.")
        if not info.get("background"):
            label = self.NEWS_LABELS.get(category, category.upper()).capitalize()
            status = "saved data" if stale else "Updated just now"
            self.timeline.add("system", "NEWS", f"{label} · {info.get('sources', 0)} sources · {status}")

    def _on_status(self, text: str) -> None:
        self.subcaption.setText(_short(text, 140))

    def _on_confirm(self, question) -> None:
        if question:
            self.confirm_text.setText(f"⚠ {question}")
            self.confirm_bar.show()
        else:
            self.confirm_bar.hide()

    def notify(self, text: str, kind: str = "ok") -> None:
        """Уведомление: в окне — если оно открыто; иначе его покажет мини-ядро (подписка в приложении)."""
        if self.isVisible() and not self.isMinimized():
            self.toasts.show(text, kind)
        elif hasattr(self, "notify_background"):
            self.notify_background(text, kind)

    def show_stats(self, s) -> None:
        self.m_cpu.set(None if s.cpu is None else f"{s.cpu:.0f}%", s.cpu)
        self.m_ram.set(None if s.ram is None else f"{s.ram:.0f}%  {s.ram_text}", s.ram)
        self.m_gpu.set(None if s.gpu is None else f"{s.gpu:.0f}%", s.gpu)
        self.m_vram.set(None if s.vram is None else f"{s.vram:.0f}%  {s.vram_text}", s.vram)
        self.m_temp.set(None if s.temp is None else f"GPU {s.temp:.0f}°C")
        if s.net_down is None:
            self.m_net.set(None)
        else:
            def fmt(v):
                return f"{v / 1024:.1f} МБ/с" if v >= 1024 else f"{v:.0f} КБ/с"
            self.m_net.set(f"↓ {fmt(s.net_down)}  ↑ {fmt(s.net_up)}")
        self.m_up.set(s.uptime)

    def show_net(self, n) -> None:
        self._net = n
        self.n_vpn.set(n.vpn_text if n.vpn is not None else None, n.vpn)
        self.n_inet.set("ONLINE" if n.online else ("OFFLINE" if n.online is False else None), n.online)
        self.n_ping.set(None if n.ping_ms is None else f"{n.ping_ms:.0f} ms",
                        None if n.ping_ms is None else n.ping_ms < 150)
        self.n_ip.set(n.ip)

    def _refresh_keys(self) -> None:
        """Статус API keys: только номер и состояние — сами ключи в интерфейс не попадают."""
        if not self.assistant:
            return
        status = self.assistant.key_status()
        if not status:
            self.a_keys.set(None)
            return
        active = sum(1 for s in status if s["state"] == "ACTIVE")
        lines = [f"Key #{s['number']} — {s['state']}" + (f" ({s['seconds_left']} с)" if s["seconds_left"] else "")
                 for s in status]
        self.a_keys.set(f"{active}/{len(status)} ACTIVE", active > 0, "API Keys:\n" + "\n".join(lines))

    def _sync_wake(self) -> None:
        on = bool(self.assistant and self.assistant.wake_enabled)
        self.wake_toggle.blockSignals(True)
        self.wake_toggle.setChecked(on)
        self.wake_toggle.blockSignals(False)
        self.a_wake.set("LISTENING «JARVIS»" if on else "OFF", on,
                        f"Активатор: {getattr(self, '_wake_name', 'N/A')}")

    def _tick_clock(self) -> None:
        now = dt.datetime.now()
        self.clock.setText(now.strftime("%H:%M:%S"))
        self.date.setText(now.strftime("%d.%m.%Y").upper())

    def send(self) -> None:
        text = self.input.text().strip()
        if not text or not self.assistant:
            return
        self.input.clear()
        self.caption.setText(_short(f"«{text}»", 180))
        self.assistant.submit_text(text)

    def listen(self) -> None:
        if self.assistant:
            self.assistant.listen()

    def _tool(self, name: str, args: dict, label: str) -> None:
        if self.assistant:
            self.timeline.add("voice", "COMMAND", label)
            self.assistant.run_tool(name, args, label)

    def _toggle_vpn(self) -> None:
        on = bool(self._net and self._net.vpn)
        self._tool("vpn_off" if on else "vpn_on", {}, "Выключи VPN" if on else "Включи VPN")

    def _ptt_pressed(self) -> None:
        self._ptt.set()
        if self.assistant:
            self.assistant.listen(hold=self._ptt.is_set)

    def _confirm(self, yes: bool) -> None:
        if self.assistant:
            self.assistant.confirm(yes)
        self.confirm_bar.hide()

    def _escape(self) -> None:
        if self.assistant:
            self.assistant.stop_speaking()
            if self.assistant.listening:
                self.assistant.listen()

    def _voice_replies_changed(self, voice: bool) -> None:
        if self.assistant:
            self.assistant.set_voice_replies(voice)
        self.subcaption.setText("Ответы озвучиваются" if voice else "Текстовый режим: ответы без озвучки")
        if not voice:
            self.input.setFocus()

    def _wake_changed(self, enabled: bool) -> None:
        self.set_wake(enabled)

    def set_wake(self, enabled: bool) -> None:
        """Voice ON/OFF — постоянное ожидание «Jarvis» (из окна, трея, меню мини-ядра)."""
        if not self.assistant:
            return
        if self.assistant.set_wake_word(enabled):
            if self.assistant.settings.get("voice.wake_enabled") != enabled:
                self.assistant.settings.set("voice.wake_enabled", enabled)
        self._sync_wake()
        self.set_state(self.state)
        self.subcaption.setText("Скажите «Jarvis, …»" if self.assistant.wake_enabled
                                else "Push-to-Talk: удерживайте 🎙 или Ctrl+Alt+J")

    def set_voice_replies(self, voice: bool) -> None:
        self.voice_toggle.setChecked(voice)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.toasts._layout()
        need = sum(b.minimumWidth() for b in self.quick_buttons.values()) + 8 * 5 + 40
        center_w = self.width() - 262 - 400 - 80
        cols = 6 if center_w >= need else 3
        if cols != self._quick_cols:
            self._quick_cols = cols
            for i, b in enumerate(self.quick_buttons.values()):
                self._quick_grid.removeWidget(b)
                self._quick_grid.addWidget(b, i // cols, i % cols)

    def closeEvent(self, event):
        if self.on_close is not None and self.on_close(event):
            return
        if self.assistant:
            self.assistant.shutdown()
        super().closeEvent(event)
