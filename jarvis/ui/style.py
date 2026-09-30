"""Тема AI Command Center: тёмный navy-фон, стеклянные панели, голубое свечение, тонкие HUD-линии."""

ACCENT = "#00d8ff"
MONO = "'Cascadia Mono', 'Consolas', monospace"

QSS = """
* { font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; color: #cfeaf6; }
QMainWindow { background: #03070d; }
QToolTip { background: #07131e; color: #bff3ff; border: 1px solid #1b5d7e; padding: 5px 8px; border-radius: 6px; }

/* ---------- шапка ---------- */
#brand { font-size: 20px; font-weight: 700; letter-spacing: 7px; color: #9ff2ff; }
#brandState { font-size: 12px; font-weight: 700; letter-spacing: 4px; color: #00e1ff; }
#brandState[state="listening"] { color: #28ffdc; }
#brandState[state="thinking"] { color: #4f98ff; }
#brandState[state="executing"] { color: #a08cff; }
#brandState[state="speaking"] { color: #00e1ff; }
#brandState[state="error"] { color: #ff4f65; }
#personality { font-size: 12px; color: #4f93b1; letter-spacing: 1px; }
#clock { font-family: """ + MONO + """; font-size: 18px; color: #8fe7ff; letter-spacing: 2px; }
#date { font-size: 10px; color: #3f7894; letter-spacing: 2px; }
QPushButton#iconBtn {
    background: rgba(0, 216, 255, 0.06); border: 1px solid rgba(0, 216, 255, 0.28); border-radius: 15px;
    min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; padding: 0; font-size: 14px;
}
QPushButton#iconBtn:hover { background: rgba(0, 216, 255, 0.16); border-color: #00d8ff; }

/* ---------- панели (стекло) ---------- */
#hudPanel { background: rgba(8, 20, 34, 0.72); border: 1px solid rgba(0, 190, 255, 0.18); border-radius: 12px; }
#panelTitle { font-size: 10px; font-weight: 700; letter-spacing: 4px; color: #3fa9d4; }
#metricName { font-size: 10px; font-weight: 600; letter-spacing: 2px; color: #4b86a3; }
#metricValue { font-family: """ + MONO + """; font-size: 12px; color: #bdf3ff; }
#metricValue[na="true"] { color: #36566a; }
#dot { font-size: 11px; color: #36566a; }
#dot[ok="true"] { color: #2dffc4; }
#dot[ok="false"] { color: #ff4f65; }
QProgressBar#bar { background: rgba(0, 190, 255, 0.08); border: none; border-radius: 2px; max-height: 4px; }
QProgressBar#bar::chunk { border-radius: 2px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0077b6, stop:1 #00e5ff); }
QProgressBar#bar[hot="true"]::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #b66a00, stop:1 #ff4f65); }

/* ---------- центр ---------- */
#caption { font-size: 15px; color: #e8fbff; font-style: italic; }
#subcaption { font-size: 11px; color: #4f93b1; letter-spacing: 1px; }
QPushButton#quick {
    background: rgba(0, 216, 255, 0.05); border: 1px solid rgba(0, 216, 255, 0.22); border-radius: 8px;
    padding: 7px 10px; font-size: 10px; font-weight: 700; letter-spacing: 2px; color: #8fdcf5;
}
QPushButton#quick:hover { background: rgba(0, 216, 255, 0.14); border-color: #00d8ff; color: #e8fbff; }
QPushButton#quick:pressed { background: rgba(0, 216, 255, 0.25); }

/* ---------- лента событий ---------- */
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
#leftColumn { background: transparent; }
#timelineInner { background: transparent; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 2px 0; }
QScrollBar::handle:vertical { background: rgba(0, 190, 255, 0.25); border-radius: 3px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
#evTime { font-family: """ + MONO + """; font-size: 10px; color: #2f6a86; }
#evTag { font-size: 9px; font-weight: 700; letter-spacing: 2px; padding: 1px 6px; border-radius: 4px; }
#evTag[kind="voice"] { color: #28ffdc; background: rgba(40, 255, 220, 0.10); }
#evTag[kind="ai"] { color: #6fb5ff; background: rgba(79, 152, 255, 0.12); }
#evTag[kind="tool"] { color: #b3a2ff; background: rgba(160, 140, 255, 0.12); }
#evTag[kind="system"] { color: #8fb3c4; background: rgba(143, 179, 196, 0.10); }
#evTag[kind="success"] { color: #2dffc4; background: rgba(45, 255, 196, 0.10); }
#evTag[kind="error"] { color: #ff6b7d; background: rgba(255, 79, 101, 0.12); }
#evText { font-size: 12px; color: #d6f4ff; }
#evText[kind="ai"] { color: #bfe9ff; }
#evText[kind="error"] { color: #ffb3bd; }
#evText[kind="system"] { color: #8fb3c4; }
#evRow { border-left: 2px solid rgba(0, 190, 255, 0.16); }

/* ---------- командная строка ---------- */
#commandBar { background: rgba(6, 16, 28, 0.85); border: 1px solid rgba(0, 190, 255, 0.25); border-radius: 14px; }
#prompt { font-family: """ + MONO + """; font-size: 16px; color: #00d8ff; font-weight: 700; }
QLineEdit#command {
    background: transparent; border: none; font-family: """ + MONO + """; font-size: 14px; color: #e8fbff;
    selection-background-color: #0f5a80;
}
QPushButton#mic {
    border-radius: 20px; min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px; padding: 0;
    font-size: 17px; background: rgba(0, 216, 255, 0.10); border: 1px solid rgba(0, 216, 255, 0.5);
}
QPushButton#mic:hover { background: rgba(0, 216, 255, 0.22); }
QPushButton#mic[active="true"] { background: rgba(40, 255, 220, 0.25); border: 1px solid #28ffdc; }
QPushButton#send {
    background: rgba(0, 216, 255, 0.12); border: 1px solid rgba(0, 216, 255, 0.45); border-radius: 10px;
    padding: 7px 16px; font-size: 10px; font-weight: 700; letter-spacing: 3px; color: #bff3ff;
}
QPushButton#send:hover { background: rgba(0, 216, 255, 0.25); }
QPushButton#toggle {
    background: transparent; border: 1px solid rgba(0, 190, 255, 0.2); border-radius: 9px; padding: 4px 9px;
    font-size: 9px; font-weight: 700; letter-spacing: 2px; color: #3f7894;
}
QPushButton#toggle:checked { color: #28ffdc; border-color: rgba(40, 255, 220, 0.6); background: rgba(40, 255, 220, 0.07); }

#confirmBar { background: rgba(60, 40, 5, 0.85); border: 1px solid #a07418; border-radius: 12px; }
#confirmText { color: #ffd98a; font-size: 13px; }
QPushButton#yes { background: rgba(31, 157, 115, 0.25); border: 1px solid #1f9d73; border-radius: 9px; padding: 6px 14px; }
QPushButton#no { background: rgba(157, 47, 61, 0.25); border: 1px solid #9d2f3d; border-radius: 9px; padding: 6px 14px; }

/* ---------- уведомления ---------- */
#toast, #overlayToast {
    background: rgba(5, 18, 30, 0.92); border: 1px solid rgba(0, 216, 255, 0.55); border-radius: 9px;
    padding: 7px 14px; font-size: 10px; font-weight: 700; letter-spacing: 2px; color: #bff3ff;
}
#toast[kind="error"], #overlayToast[kind="error"] { border-color: #ff4f65; color: #ffb3bd; }
#toast[kind="success"], #overlayToast[kind="success"] { border-color: rgba(45, 255, 196, 0.7); color: #b8ffe9; }

/* ---------- меню / настройки ---------- */
QMenu { background: #06121d; border: 1px solid #1b5d7e; border-radius: 8px; padding: 5px; }
QMenu::item { padding: 6px 22px 6px 14px; border-radius: 5px; font-size: 12px; }
QMenu::item:selected { background: rgba(0, 216, 255, 0.18); }
QMenu::separator { height: 1px; background: #123149; margin: 4px 8px; }
QMenu::indicator { width: 12px; height: 12px; }
QDialog { background: #050b13; }
#settingsSection { font-size: 10px; font-weight: 700; letter-spacing: 4px; color: #3fa9d4; padding-top: 8px; }
QCheckBox { font-size: 12px; color: #a9d6e8; spacing: 10px; }
QCheckBox::indicator { width: 32px; height: 16px; border-radius: 8px; background: #0b2233; border: 1px solid #1a5475; }
QCheckBox::indicator:checked { background: #007fa3; border: 1px solid #00d8ff; }
QComboBox {
    background: #08131e; border: 1px solid #16415f; border-radius: 8px; padding: 5px 10px; font-size: 12px;
}
QComboBox:hover { border-color: #00b8e6; }
QComboBox QAbstractItemView { background: #0b1b28; selection-background-color: #0f5a80; border: 1px solid #1a5475; }
QSlider::groove:horizontal { height: 4px; background: #0b2233; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #00b8e6; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; height: 14px; margin: -6px 0; border-radius: 7px; background: #bff3ff; }
QPushButton {
    background: rgba(0, 216, 255, 0.08); border: 1px solid #1a5475; border-radius: 9px; padding: 6px 14px;
    font-size: 12px;
}
QPushButton:hover { background: rgba(0, 216, 255, 0.18); border-color: #00b8e6; }
QProgressBar#micMeter { background: #08131e; border: 1px solid #12324a; border-radius: 3px; max-height: 6px; }
QProgressBar#micMeter::chunk { background: #28ffdc; border-radius: 3px; }
"""
