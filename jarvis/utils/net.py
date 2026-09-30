"""Свой прокси для JARVIS: Gemini из России доступен только с зарубежного IP. Чтобы не включать VPN на весь
компьютер, JARVIS может сам ходить через локальный прокси VPN-клиента (Happ, v2rayN и т. п.), пока тот запущен,
а остальные программы работают напрямую.

JARVIS_PROXY=auto (по умолчанию) — найти работающий локальный HTTP-прокси VPN-клиента;
JARVIS_PROXY=http://127.0.0.1:10809 — свой адрес; JARVIS_PROXY=off — не использовать.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time

from jarvis.config import env

log = logging.getLogger("jarvis.net")

CANDIDATES = ["127.0.0.1:10809", "127.0.0.1:2080", "127.0.0.1:7890", "127.0.0.1:8889", "127.0.0.1:1081"]
_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
_applied: str | None = None
_user_set = any(os.environ.get(v) for v in _VARS)
_user_proxy = next((os.environ[v] for v in _VARS if os.environ.get(v)), None)


def _open(addr: str, timeout: float = 0.3) -> bool:
    host, _, port = addr.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def _registry_proxy() -> str | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as k:
            server = winreg.QueryValueEx(k, "ProxyServer")[0]
    except OSError:
        return None
    server = server.split(";")[0].split("=")[-1].strip()
    return server or None


def find_local_proxy(settings=None) -> str | None:
    cands = []
    if settings is not None:
        cands.append((settings.get("vpn.proxy", "") or "").strip())
    cands += [_registry_proxy() or ""] + CANDIDATES
    for addr in dict.fromkeys(c for c in cands if c and ":" in c):
        if addr.startswith("127.") or addr.startswith("localhost"):
            if _open(addr):
                return addr
    return None


generation = 0


def _set(url: str | None) -> None:
    global _applied, generation
    if url == _applied:
        return
    generation += 1
    for v in _VARS:
        if url:
            os.environ[v] = url
        else:
            os.environ.pop(v, None)
    if url:
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "localhost,127.0.0.1,::1"
        log.info("JARVIS выходит в интернет через прокси %s (остальные программы — напрямую)", url)
    elif _applied:
        log.info("Прокси VPN-клиента недоступен — JARVIS выходит в интернет напрямую")
    _applied = url


def apply_proxy(settings=None) -> str | None:
    """Выбрать прокси для процесса JARVIS (HTTP_PROXY/HTTPS_PROXY). Возвращает адрес или None."""
    mode = (env("JARVIS_PROXY", "auto") or "auto").strip()
    if mode.lower() in ("off", "0", "no", "none"):
        return None
    if _user_set:
        addr = _user_proxy.split("://")[-1].rstrip("/") if _user_proxy else ""
        local = addr.startswith("127.") or addr.startswith("localhost")
        _set(None if local and not _open(addr) else _user_proxy)
        return _applied
    if mode.lower() != "auto":
        _set(mode if "://" in mode else f"http://{mode}")
        return _applied
    addr = find_local_proxy(settings)
    _set(f"http://{addr}" if addr else None)
    return _applied


def watch(settings=None, every: float = 30.0) -> None:
    """Следить за прокси в фоне: VPN-клиент закрыли — работаем напрямую, запустили — снова через него."""
    def loop():
        while True:
            time.sleep(every)
            try:
                apply_proxy(settings)
            except Exception as exc:
                log.debug("proxy watch: %s", exc)

    threading.Thread(target=loop, name="proxy-watch", daemon=True).start()
