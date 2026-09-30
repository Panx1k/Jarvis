"""Общие помощники для инструментов (модули с «_» не сканируются как инструменты)."""
from __future__ import annotations

import os
import re
import subprocess
from urllib.parse import quote_plus

from jarvis.utils.text import normalize

CREATE_NO_WINDOW = 0x08000000


BROWSER_PROCESSES = {"firefox.exe", "chrome.exe", "msedge.exe", "browser.exe", "opera.exe", "brave.exe", "vivaldi.exe"}


def _bring_browser_forward() -> None:
    """Windows не всегда даёт браузеру выйти вперёд (JARVIS в фоне, открыта игра) — выводим окно сами."""
    import time

    from jarvis.utils import winapi

    time.sleep(1.2)
    hwnd = winapi.find_window("", BROWSER_PROCESSES)
    if hwnd and winapi.user32.GetForegroundWindow() != hwnd:
        winapi.focus_window(hwnd)


def open_in_browser(url: str, bring_forward: bool = True) -> None:
    """Открыть ссылку в браузере по умолчанию и показать окно браузера."""
    import threading

    if not re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I):
        url = "https://" + url
    os.startfile(url)
    if bring_forward:
        threading.Thread(target=_bring_browser_forward, name="browser-front", daemon=True).start()


def run_hidden(args, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, creationflags=CREATE_NO_WINDOW, **kwargs)


def find_site(settings, name: str) -> tuple[str, dict] | None:
    """Найти сайт из настроек по имени/синониму."""
    q = normalize(name).strip().strip(".!?,")
    q = re.sub(r"^(сайт|страницу|страница)\s+", "", q)
    sites: dict = settings.get("sites", {})
    for key, cfg in sites.items():
        if q == key or q in [normalize(a) for a in cfg.get("aliases", [])]:
            return key, cfg
    return None


def site_search_url(settings, site_key: str, query: str) -> str | None:
    cfg = settings.get("sites", {}).get(site_key) or {}
    template = cfg.get("search")
    return template.replace("{query}", quote_plus(query)) if template else None


def default_browser_key() -> str | None:
    """Ключ приложения (из настроек apps) для браузера по умолчанию."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations"
                                                      r"\UrlAssociations\https\UserChoice") as k:
            prog = winreg.QueryValueEx(k, "ProgId")[0].lower()
    except OSError:
        return None
    for marker, key in (("firefox", "firefox"), ("chrome", "chrome"), ("msedge", "edge"), ("yandex", "yandex_browser"),
                        ("opera", "opera")):
        if marker in prog:
            return key
    return None


def looks_like_url(text: str) -> bool:
    return bool(re.match(r"^(https?://)?([\w-]+\.)+[a-zа-я]{2,}(/\S*)?$", text.strip(), re.I))
