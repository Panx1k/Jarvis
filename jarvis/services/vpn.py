"""Управление VPN.

Поддерживаемые способы (config/settings.json → "vpn"):
  * windows — встроенное VPN-подключение Windows (rasdial);
  * proxy   — клиенты вроде Happ / v2rayN / Nekoray, работающие как системный прокси:
              включение = запуск приложения + включение системного прокси, выключение = отключение прокси;
  * command — ваши собственные команды on_command / off_command (CLI любого VPN-клиента);
  * app     — просто открыть приложение VPN;
  * auto    — определить автоматически.
"""
from __future__ import annotations

import ctypes
import logging
import socket
import subprocess
import time
import winreg
from dataclasses import dataclass

import psutil

log = logging.getLogger("jarvis.vpn")
CREATE_NO_WINDOW = 0x08000000
INET_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

PROXY_CLIENTS = {
    "Happ": ["Happ.exe", "happd.exe"],
    "v2rayN": ["v2rayN.exe"],
    "Nekoray": ["nekoray.exe"],
    "NekoBox": ["nekobox.exe"],
    "Hiddify": ["Hiddify.exe", "HiddifyCli.exe"],
    "Clash Verge": ["clash-verge.exe", "Clash Verge.exe"],
    "Throne": ["Throne.exe"],
}
VPN_APPS = ["AmneziaVPN", "Outline", "WireGuard", "ProtonVPN", "Windscribe", "NordVPN", "Surfshark", "OpenVPN",
            "Kaspersky VPN", "AdGuard VPN", "Planet VPN", "Psiphon"]


@dataclass
class VpnStatus:
    connected: bool | None
    detail: str


def _run(cmd, timeout=30, shell=False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, shell=shell, creationflags=CREATE_NO_WINDOW)


def _decode(b: bytes) -> str:
    for enc in ("cp866", "utf-8", "cp1251"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def proxy_settings() -> tuple[bool, str]:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INET_KEY) as k:
        try:
            enabled = bool(winreg.QueryValueEx(k, "ProxyEnable")[0])
        except FileNotFoundError:
            enabled = False
        try:
            server = winreg.QueryValueEx(k, "ProxyServer")[0]
        except FileNotFoundError:
            server = ""
    return enabled, server


def set_system_proxy(enabled: bool, server: str | None = None) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INET_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enabled else 0)
        if server:
            winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server)
    wininet = ctypes.windll.wininet
    wininet.InternetSetOptionW(None, 39, None, 0)
    wininet.InternetSetOptionW(None, 37, None, 0)


def port_open(server: str, timeout: float = 0.5) -> bool:
    host, _, port = server.rpartition(":")
    if not host or not port.isdigit():
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def running(process_names: list[str]) -> bool:
    names = {n.lower() for n in process_names}
    for p in psutil.process_iter(["name"]):
        if (p.info["name"] or "").lower() in names:
            return True
    return False


def windows_vpn_names() -> list[str]:
    cmd = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-VpnConnection | Select-Object -ExpandProperty Name"
    r = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd])
    return [line.strip() for line in r.stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]


class VpnController:
    def __init__(self, settings, apps=None):
        self.settings = settings
        self.apps = apps

    @property
    def cfg(self) -> dict:
        return self.settings.get("vpn", {}) or {}

    def detect(self) -> dict:
        """Определить способ управления VPN (для type=auto)."""
        cfg = dict(self.cfg)
        if cfg.get("type", "auto") != "auto":
            return cfg
        names = windows_vpn_names()
        if names:
            return {**cfg, "type": "windows", "name": cfg.get("name") or names[0]}
        enabled, server = proxy_settings()
        for app, procs in PROXY_CLIENTS.items():
            if running(procs) or (self.apps and self.apps._find_entry(app, threshold=0.9)):
                return {**cfg, "type": "proxy", "app": cfg.get("app") or app, "processes": procs,
                        "proxy": cfg.get("proxy") or server or "127.0.0.1:10809"}
        if self.apps:
            for app in VPN_APPS:
                if self.apps._find_entry(app, threshold=0.85):
                    return {**cfg, "type": "app", "app": app}
        return {**cfg, "type": "none"}

    def status(self) -> VpnStatus:
        cfg = self.detect()
        t = cfg.get("type")
        if t == "windows":
            out = _decode(_run(["rasdial"]).stdout)
            connected = cfg["name"].lower() in out.lower()
            return VpnStatus(connected, f"VPN «{cfg['name']}» {'подключён' if connected else 'отключён'}.")
        if t == "proxy":
            enabled, server = proxy_settings()
            alive = port_open(cfg["proxy"])
            connected = enabled and alive
            state = "включён" if connected else "выключен"
            return VpnStatus(connected, f"VPN ({cfg['app']}, системный прокси {cfg['proxy']}) {state}.")
        if t == "command" and cfg.get("status_command"):
            r = _run(cfg["status_command"], shell=True)
            return VpnStatus(r.returncode == 0, _decode(r.stdout).strip()[:200] or "Статус получен.")
        if t == "app":
            return VpnStatus(None, f"Статус неизвестен: VPN управляется через приложение {cfg.get('app')}.")
        return VpnStatus(None, "VPN не настроен.")

    def turn_on(self) -> tuple[bool, str]:
        cfg = self.detect()
        t = cfg.get("type")
        if t == "windows":
            r = _run(["rasdial", cfg["name"]], timeout=60)
            ok = r.returncode == 0
            return ok, f"VPN «{cfg['name']}» подключён." if ok else f"Не удалось подключить VPN: {_decode(r.stdout).strip()[-200:]}"
        if t == "proxy":
            if not running(cfg.get("processes", [])) and not port_open(cfg["proxy"]):
                self._open_app(cfg["app"])
                for _ in range(40):
                    if port_open(cfg["proxy"]):
                        break
                    time.sleep(0.5)
            if not port_open(cfg["proxy"]):
                return False, (f"{cfg['app']} запущен, но прокси {cfg['proxy']} не отвечает. "
                               f"Нажмите «Подключить» в окне {cfg['app']}.")
            set_system_proxy(True, cfg["proxy"])
            return True, f"VPN включён ({cfg['app']})."
        if t == "command":
            if not cfg.get("on_command"):
                return False, "Не задана команда включения VPN (vpn.on_command в config/settings.json)."
            r = _run(cfg["on_command"], shell=True, timeout=60)
            return r.returncode == 0, "VPN включён." if r.returncode == 0 else f"Команда VPN вернула код {r.returncode}."
        if t == "app":
            self._open_app(cfg["app"])
            return True, f"Открыл {cfg['app']} — нажмите «Подключить»."
        return False, ("VPN не настроен. Укажите способ в config/settings.json, раздел «vpn» "
                       "(подробности в README).")

    def turn_off(self) -> tuple[bool, str]:
        cfg = self.detect()
        t = cfg.get("type")
        if t == "windows":
            r = _run(["rasdial", cfg["name"], "/disconnect"])
            ok = r.returncode == 0
            return ok, "VPN отключён." if ok else "Не удалось отключить VPN."
        if t == "proxy":
            enabled, _ = proxy_settings()
            if not enabled:
                return True, "VPN уже выключен."
            set_system_proxy(False)
            return True, f"VPN выключен: системный прокси {cfg['app']} отключён."
        if t == "command":
            if not cfg.get("off_command"):
                return False, "Не задана команда выключения VPN (vpn.off_command)."
            r = _run(cfg["off_command"], shell=True, timeout=60)
            return r.returncode == 0, "VPN выключен." if r.returncode == 0 else f"Команда VPN вернула код {r.returncode}."
        if t == "app":
            self._open_app(cfg["app"])
            return True, f"Открыл {cfg['app']} — нажмите «Отключить»."
        return False, "VPN не настроен (см. раздел «vpn» в config/settings.json)."

    def _open_app(self, name: str) -> None:
        if self.apps:
            m = self.apps.resolve(name)
            if m and m.kind != "missing":
                self.apps.launch(m)
                return
        raise RuntimeError(f"Приложение {name} не найдено")
