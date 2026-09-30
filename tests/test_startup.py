"""Приложение должно запускаться: разбор аргументов и импорт GUI без ошибок."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_cli_help_starts():
    r = subprocess.run([sys.executable, "main.py", "--help"], cwd=ROOT, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-2000:]


def test_gui_modules_import():
    r = subprocess.run([sys.executable, "-c", "import jarvis.ui.app, jarvis.voice.xtts"], cwd=ROOT,
                       capture_output=True, timeout=120)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-2000:]
