"""Глобальная горячая клавиша (RegisterHotKey) в отдельном потоке."""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

from jarvis.utils.winapi import KEY_NAMES

log = logging.getLogger("jarvis.hotkey")
user32 = ctypes.WinDLL("user32", use_last_error=True)
MODS = {"alt": 0x1, "альт": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
WM_HOTKEY, WM_QUIT, MOD_NOREPEAT = 0x0312, 0x0012, 0x4000


def parse_hotkey(spec: str) -> tuple[int, int]:
    mods, vk = 0, None
    for part in spec.lower().replace(" ", "").split("+"):
        if part in MODS:
            mods |= MODS[part]
        elif part in KEY_NAMES:
            vk = KEY_NAMES[part]
        else:
            raise ValueError(f"неизвестная клавиша: {part}")
    if vk is None:
        raise ValueError("не указана основная клавиша")
    return mods, vk


class GlobalHotkey:
    def __init__(self, spec: str, callback: Callable[[], None]):
        self.spec = spec
        self.callback = callback
        self._thread_id = None
        self.ok = False
        self._ready = threading.Event()
        self.held = threading.Event()
        self._vk = 0
        threading.Thread(target=self._run, name="hotkey", daemon=True).start()
        self._ready.wait(2)

    def _run(self) -> None:
        try:
            mods, vk = parse_hotkey(self.spec)
        except ValueError as exc:
            log.warning("Горячая клавиша %s: %s", self.spec, exc)
            self._ready.set()
            return
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk):
            log.warning("Не удалось зарегистрировать %s (занята другим приложением?)", self.spec)
            self._ready.set()
            return
        self.ok = True
        self._vk = vk
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.held.set()
                threading.Thread(target=self._watch_release, daemon=True).start()
                try:
                    self.callback()
                except Exception:
                    log.exception("hotkey callback")
        user32.UnregisterHotKey(None, 1)

    def _watch_release(self) -> None:
        """Push-to-talk: следим, пока основная клавиша удерживается."""
        import time

        while user32.GetAsyncKeyState(self._vk) & 0x8000:
            time.sleep(0.02)
        self.held.clear()

    def is_held(self) -> bool:
        return self.held.is_set()

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
