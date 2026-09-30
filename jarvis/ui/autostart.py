"""Запуск J.A.R.V.I.S. вместе с Windows: запись в HKCU\\...\\Run (только для текущего пользователя, без прав
администратора). Запускается pythonw.exe из .venv (без консоли) с main.py --autostart."""
from __future__ import annotations

import sys
import winreg
from pathlib import Path

from jarvis.config import ROOT

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "JARVIS"


def command() -> str:
    exe = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if not exe.exists():
        exe = Path(sys.executable).with_name("pythonw.exe")
    return f'"{exe}" "{ROOT / "main.py"}" --autostart'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            value = winreg.QueryValueEx(k, VALUE)[0]
            return "main.py" in value
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, VALUE, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(k, VALUE)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
