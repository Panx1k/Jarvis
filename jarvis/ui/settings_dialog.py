"""Настройки J.A.R.V.I.S.: общие (фон, автозапуск), голос, Mini Overlay. Всё сохраняется в config/settings.json
(через существующий Settings) и применяется сразу."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QSlider, QVBoxLayout)

from jarvis.ui import autostart
from jarvis.ui.overlay import POSITION_NAMES, POSITIONS


def _section(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("settingsSection")
    return lbl


class SettingsDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.c = controller
        s = controller.settings
        self.setWindowTitle("J.A.R.V.I.S. — настройки")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 16, 22, 18)
        lay.setSpacing(8)

        lay.addWidget(_section("ОБЩИЕ"))
        self.background = QCheckBox("Работать в фоне после закрытия окна")
        self.background.setToolTip("Крестик скрывает окно, а JARVIS продолжает слушать в виде мини-ядра в углу.\n"
                                   "Выключено — крестик полностью завершает JARVIS.")
        self.background.setChecked(bool(s.get("ui.background_mode", True)))
        self.background.toggled.connect(lambda v: s.set("ui.background_mode", v))
        lay.addWidget(self.background)
        self.autostart = QCheckBox("Запускать JARVIS вместе с Windows")
        self.autostart.setChecked(autostart.is_enabled())
        self.autostart.toggled.connect(controller.set_autostart)
        lay.addWidget(self.autostart)
        self.minimized = QCheckBox("Запускать свёрнутым (сразу мини-ядро)")
        self.minimized.setChecked(bool(s.get("ui.start_minimized", False)))
        self.minimized.toggled.connect(lambda v: s.set("ui.start_minimized", v))
        lay.addWidget(self.minimized)

        lay.addWidget(_section("ГОЛОС"))
        a = controller.assistant
        self.replies = QCheckBox("Отвечать голосом (выключено — только текст)")
        self.replies.setChecked(bool(a.voice_replies) if a else True)
        self.replies.toggled.connect(controller.set_voice_replies)
        lay.addWidget(self.replies)
        self.wake = QCheckBox("Постоянно ждать «Jarvis» (микрофон)")
        self.wake.setChecked(bool(a and a.wake_enabled))
        self.wake.toggled.connect(controller.set_voice)
        lay.addWidget(self.wake)
        self.live = QCheckBox("Живой голос Gemini Live")
        live_info = a.info().get("live") if a else None
        self.live.setEnabled(bool(live_info))
        if live_info:
            self.live.setText(f"Живой голос {live_info}")
        self.live.setChecked(bool(a and getattr(a, "live_enabled", False)))
        self.live.toggled.connect(controller.set_live)
        lay.addWidget(self.live)
        mic_row = QFormLayout()
        self.mic = QComboBox()
        self.mic.setToolTip("Какой микрофон слушает JARVIS. Если выбранный молчит — переключится сам.")
        if a and a.recorder:
            current = a.recorder.device_name.strip()
            for index, name in a.microphones():
                self.mic.addItem(name.strip(), (index, name))
                if name.strip() == current or a.recorder.device == index:
                    self.mic.setCurrentIndex(self.mic.count() - 1)
        else:
            self.mic.setEnabled(False)
        self.mic.activated.connect(lambda row: controller.set_microphone(*self.mic.itemData(row)))
        mic_row.addRow("Микрофон", self.mic)
        lay.addLayout(mic_row)
        self.meter = QProgressBar()
        self.meter.setObjectName("micMeter")
        self.meter.setRange(0, 100)
        self.meter.setTextVisible(False)
        self.meter.setToolTip("Уровень микрофона: если при речи полоска не двигается — JARVIS вас не слышит")
        lay.addWidget(self.meter)
        controller.bridge.level.connect(self._meter)

        lay.addWidget(_section("ГОЛОСОВЫЕ СЕМПЛЫ"))
        cfg = a.samples.config() if a and a.samples else None
        self.samples_on = self._sample_check("Голосовые семплы JARVIS (подходящие ответы — записью)", "enabled",
                                             cfg.enabled if cfg else True)
        self.variety = self._sample_check("Разнообразие (не повторять одну запись)", "variety",
                                          cfg.variety if cfg else True)
        self.hybrid = self._sample_check("Запись + синтез для ответов с данными", "hybrid", cfg.hybrid if cfg else False)
        self.fallback = self._sample_check("Синтез, если подходящей записи нет", "fallback_tts",
                                           cfg.fallback_tts if cfg else True)
        for w in (self.samples_on, self.variety, self.hybrid, self.fallback):
            lay.addWidget(w)
        th_row = QFormLayout()
        self.threshold = QSlider(Qt.Horizontal)
        self.threshold.setRange(0, 100)
        self.threshold.setValue(int((cfg.threshold if cfg else 0.8) * 100))
        self.threshold_label = QLabel(f"{self.threshold.value() / 100:.2f}")
        self.threshold.valueChanged.connect(self._threshold_changed)
        th_box = QHBoxLayout()
        th_box.addWidget(self.threshold, 1)
        th_box.addWidget(self.threshold_label)
        th_row.addRow("Порог уверенности", th_box)
        lay.addLayout(th_row)
        stats = a.samples.stats() if a and a.samples else None
        if stats:
            info = QLabel(f"Библиотека: {stats['samples']} записей, со смысловыми тегами {stats['tagged']}"
                          + (f", повреждённых пропущено {stats['broken']}" if stats["broken"] else "") + ".")
            info.setObjectName("subcaption")
            lay.addWidget(info)
        self.debug = QCheckBox("Режим разработчика (отладка выбора записей в ленте)")
        self.debug.setChecked(bool(s.get("ui.debug", False)))
        self.debug.toggled.connect(lambda v: controller.set_setting("ui.debug", v))
        lay.addWidget(self.debug)

        lay.addWidget(_section("ОБНОВЛЕНИЯ"))
        up_row = QHBoxLayout()
        self.update_label = QLabel("Проверьте, вышла ли новая версия на GitHub.")
        self.update_label.setObjectName("subcaption")
        self.update_label.setWordWrap(True)
        self.check_btn = QPushButton("Проверить")
        self.update_btn = QPushButton("Обновить")
        self.update_btn.setEnabled(False)
        self.check_btn.clicked.connect(self._check_updates)
        self.update_btn.clicked.connect(self._install_update)
        up_row.addWidget(self.update_label, 1)
        up_row.addWidget(self.check_btn)
        up_row.addWidget(self.update_btn)
        lay.addLayout(up_row)
        info = getattr(controller, "update_info", None)
        if info is not None:
            self._show_update(info)

        lay.addWidget(_section("OVERLAY · МИНИ-ЯДРО"))
        self.show_core = self._check("Показывать мини-ядро", "show", True)
        self.on_top = self._check("Поверх окон", "on_top", True)
        self.reaction = self._check("Реакция на голос", "voice_reaction", True)
        for w in (self.show_core, self.on_top, self.reaction):
            lay.addWidget(w)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(48, 110)
        self.size.setValue(int(s.get("ui.overlay.size", 64)))
        self.size.valueChanged.connect(lambda v: controller.set_overlay("size", v))
        form.addRow("Размер", self.size)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setValue(int(float(s.get("ui.overlay.opacity", 0.95)) * 100))
        self.opacity.valueChanged.connect(lambda v: controller.set_overlay("opacity", v / 100))
        form.addRow("Прозрачность", self.opacity)
        self.position = QComboBox()
        for key in POSITIONS:
            self.position.addItem(POSITION_NAMES[key], key)
        cur = s.get("ui.overlay.position", "bottom_right")
        self.position.setCurrentIndex(max(0, POSITIONS.index(cur) if cur in POSITIONS else 0))
        self.position.activated.connect(lambda row: controller.set_overlay("position", self.position.itemData(row)))
        form.addRow("Позиция", self.position)
        lay.addLayout(form)
        hint = QLabel("Мини-ядро можно перетащить мышью — позиция запомнится.")
        hint.setObjectName("subcaption")
        lay.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Готово")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        lay.addSpacing(6)
        lay.addLayout(buttons)

    def _check(self, text: str, key: str, default: bool) -> QCheckBox:
        box = QCheckBox(text)
        box.setChecked(bool(self.c.settings.get(f"ui.overlay.{key}", default)))
        box.toggled.connect(lambda v: self.c.set_overlay(key, v))
        return box

    def _check_updates(self) -> None:
        self.update_label.setText("Проверяю…")
        self.check_btn.setEnabled(False)
        self.c.check_updates(manual=True, done=self._show_update)

    def _show_update(self, info) -> None:
        self.check_btn.setEnabled(True)
        if info.error:
            self.update_label.setText(info.error)
        elif info.available and info.method == "git":
            self.update_label.setText("Есть новая версия. Эта копия из git — обновите через GitHub Desktop (Pull).")
        elif info.available:
            self.update_label.setText(f"Доступно обновление: {info.message or info.latest[:7]} ({info.date[:10]}).")
            self.update_btn.setEnabled(True)
        else:
            self.update_label.setText("У вас последняя версия.")

    def _install_update(self) -> None:
        self.update_btn.setEnabled(False)
        self.check_btn.setEnabled(False)
        self.update_label.setText("Скачиваю и устанавливаю… JARVIS перезапустится сам.")
        self.c.install_update(done=lambda ok, text: self.update_label.setText(text))

    def _sample_check(self, text: str, key: str, value: bool) -> QCheckBox:
        box = QCheckBox(text)
        box.setChecked(bool(value))
        box.toggled.connect(lambda v: self.c.set_setting(f"voice.samples.{key}", v))
        return box

    def _threshold_changed(self, v: int) -> None:
        self.threshold_label.setText(f"{v / 100:.2f}")
        self.c.set_setting("voice.samples.threshold", round(v / 100, 2))

    def _meter(self, level: float) -> None:
        if self.isVisible():
            self.meter.setValue(int(min(1.0, level * 3) * 100))

    def sync(self) -> None:
        """Обновить переключатели, если их поменяли из меню трея/ядра."""
        a = self.c.assistant
        for box, value in ((self.wake, bool(a and a.wake_enabled)),
                           (self.show_core, bool(self.c.settings.get("ui.overlay.show", True))),
                           (self.autostart, autostart.is_enabled())):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        cur = self.c.settings.get("ui.overlay.position", "bottom_right")
        if cur in POSITIONS:
            self.position.setCurrentIndex(POSITIONS.index(cur))
