"""Тонкие обёртки над WinAPI (ctypes): эмуляция клавиатуры, медиа-клавиши, блокировка и т. п."""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_VOLUME_MUTE = 0xAD

_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x6F, 0x90,
             0xA3, 0xA5, 0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3, 0x2C}

KEY_NAMES: dict[str, int] = {
    "enter": 0x0D, "энтер": 0x0D, "ввод": 0x0D, "интер": 0x0D, "return": 0x0D,
    "space": 0x20, "пробел": 0x20, "спейс": 0x20,
    "esc": 0x1B, "escape": 0x1B, "эскейп": 0x1B, "эскейт": 0x1B, "искейп": 0x1B, "эск": 0x1B,
    "tab": 0x09, "таб": 0x09, "табуляция": 0x09,
    "backspace": 0x08, "бэкспейс": 0x08, "бекспейс": 0x08, "стереть": 0x08,
    "delete": 0x2E, "del": 0x2E, "делит": 0x2E, "делete": 0x2E, "удалить": 0x2E,
    "insert": 0x2D, "инсерт": 0x2D,
    "up": 0x26, "вверх": 0x26, "стрелка вверх": 0x26, "стрелку вверх": 0x26,
    "down": 0x28, "вниз": 0x28, "стрелка вниз": 0x28, "стрелку вниз": 0x28,
    "left": 0x25, "влево": 0x25, "стрелка влево": 0x25, "стрелку влево": 0x25,
    "right": 0x27, "вправо": 0x27, "стрелка вправо": 0x27, "стрелку вправо": 0x27,
    "home": 0x24, "хоум": 0x24, "end": 0x23, "энд": 0x23,
    "pageup": 0x21, "page up": 0x21, "пейдж ап": 0x21, "pagedown": 0x22, "page down": 0x22, "пейдж даун": 0x22,
    "ctrl": 0x11, "control": 0x11, "контрол": 0x11, "контрл": 0x11, "ктрл": 0x11,
    "alt": 0x12, "альт": 0x12, "shift": 0x10, "шифт": 0x10,
    "win": 0x5B, "windows": 0x5B, "вин": 0x5B, "виндовс": 0x5B, "виндоус": 0x5B,
    "printscreen": 0x2C, "print screen": 0x2C, "принтскрин": 0x2C, "принт скрин": 0x2C,
    "capslock": 0x14, "капслок": 0x14,
    "плей": VK_MEDIA_PLAY_PAUSE, "play": VK_MEDIA_PLAY_PAUSE,
    "эй": 0x41, "си": 0x43, "ви": 0x56, "икс": 0x58, "зет": 0x5A, "зэт": 0x5A, "эс": 0x53, "ти": 0x54,
    "дабл ю": 0x57, "эф": 0x46, "эн": 0x4E, "ар": 0x52, "пи": 0x50, "кью": 0x51, "ди": 0x44,
}
for _i in range(1, 13):
    KEY_NAMES[f"f{_i}"] = 0x6F + _i
    KEY_NAMES[f"ф{_i}"] = 0x6F + _i
for _c in "abcdefghijklmnopqrstuvwxyz0123456789":
    KEY_NAMES.setdefault(_c, ord(_c.upper()))


def _key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    inp = INPUT(type=INPUT_KEYBOARD)
    if vk in _EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp.u.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def _send(inputs: list[INPUT]) -> None:
    if not inputs:
        return
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise OSError(ctypes.get_last_error(), "SendInput не смог отправить ввод (возможно, активно окно администратора)")


def tap_key(vk: int) -> None:
    _send([_key_input(vk), _key_input(vk, flags=KEYEVENTF_KEYUP)])


def press_combo(vks: list[int]) -> None:
    """Нажать сочетание: все клавиши вниз, затем вверх в обратном порядке."""
    downs = [_key_input(vk) for vk in vks]
    ups = [_key_input(vk, flags=KEYEVENTF_KEYUP) for vk in reversed(vks)]
    _send(downs + ups)


def type_unicode(text: str, delay: float = 0.004) -> None:
    """Печать произвольного текста (включая кириллицу) независимо от раскладки."""
    for ch in text:
        if ch == "\n":
            tap_key(0x0D)
        else:
            data = ch.encode("utf-16-le")
            units = [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
            events = []
            for unit in units:
                events.append(_key_input(0, unit, KEYEVENTF_UNICODE))
                events.append(_key_input(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
            _send(events)
        if delay:
            time.sleep(delay)


user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.IsWindow.argtypes = (wintypes.HWND,)


class ForegroundTracker:
    """Запоминает последнее активное окно другого приложения, чтобы вернуть ему фокус
    перед эмуляцией клавиатуры (иначе текст напечатается в окно самого ассистента)."""

    def __init__(self) -> None:
        import os
        import threading

        self._pid = os.getpid()
        self.last_foreign: int | None = None
        self._stop = threading.Event()
        threading.Thread(target=self._loop, name="fg-tracker", daemon=True).start()

    def _window_pid(self, hwnd) -> int:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def own_window_active(self) -> bool:
        hwnd = user32.GetForegroundWindow()
        return bool(hwnd) and self._window_pid(hwnd) == self._pid

    def _loop(self) -> None:
        while not self._stop.wait(0.3):
            hwnd = user32.GetForegroundWindow()
            if hwnd and self._window_pid(hwnd) != self._pid:
                self.last_foreign = hwnd

    def restore(self) -> bool:
        """Если активно окно ассистента — переключиться на предыдущее окно."""
        if not self.own_window_active() or not self.last_foreign or not user32.IsWindow(self.last_foreign):
            return False
        tap_key(0x12)
        user32.SetForegroundWindow(self.last_foreign)
        time.sleep(0.25)
        return True


foreground = None

_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_window(title_part: str, process_names: set[str]) -> int | None:
    """Первое видимое окно указанных процессов, в заголовке которого есть title_part."""
    import psutil

    found: list[int] = []
    want = title_part.lower()

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if want not in buf.value.lower():
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            if psutil.Process(pid.value).name().lower() in process_names:
                found.append(hwnd)
                return False
        except Exception:
            pass
        return True

    user32.EnumWindows(_EnumProc(callback), 0)
    return found[0] if found else None


def focus_window(hwnd: int) -> bool:
    """Вывести окно на передний план (Alt снимает запрет Windows на смену фокуса из фонового процесса)."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    tap_key(0x12)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    return user32.GetForegroundWindow() == hwnd


def restore_foreground() -> None:
    if foreground is not None:
        foreground.restore()


def lock_workstation() -> bool:
    return bool(user32.LockWorkStation())


def enable_dark_titlebar(hwnd: int) -> None:
    try:
        dwm = ctypes.WinDLL("dwmapi")
        value = ctypes.c_int(1)
        for attr in (20, 19):
            if dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except OSError:
        pass
