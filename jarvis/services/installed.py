"""Установленные программы Windows (как в «Параметры → Приложения»): название, издатель, команда удаления.

Читается из реестра (HKLM/HKCU …\\Uninstall, включая 32-битные). Системные компоненты и обновления пропускаются.
"""
from __future__ import annotations

import re
import winreg
from dataclasses import dataclass

from jarvis.utils.text import normalize, similarity, simplify, translit

ROOTS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]
CRITICAL = re.compile(r"(?i)\b(?:windows|microsoft visual c\+\+|\.net|directx|nvidia (?:graphics )?driver|"
                      r"amd software|realtek|intel\(r\)|chipset|redistributable|runtime|webview2|driver)\b")


@dataclass
class InstalledApp:
    name: str
    publisher: str
    uninstall: str
    key: str
    version: str = ""

    @property
    def critical(self) -> bool:
        """Драйверы, системные библиотеки — без них могут перестать работать другие программы."""
        return bool(CRITICAL.search(self.name))


def _value(key, name: str) -> str:
    try:
        return str(winreg.QueryValueEx(key, name)[0] or "")
    except OSError:
        return ""


def installed() -> list[InstalledApp]:
    apps: dict[str, InstalledApp] = {}
    for hive, path in ROOTS:
        try:
            root = winreg.OpenKey(hive, path)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub) as k:
                        name = _value(k, "DisplayName").strip()
                        cmd = _value(k, "UninstallString").strip()
                        if not name or not cmd or _value(k, "SystemComponent") == "1" or _value(k, "ParentKeyName"):
                            continue
                        if _value(k, "ReleaseType") in ("Update", "Hotfix", "Security Update"):
                            continue
                        apps.setdefault(simplify(name), InstalledApp(name, _value(k, "Publisher"), cmd, sub,
                                                                     _value(k, "DisplayVersion")))
                except OSError:
                    continue
    return sorted(apps.values(), key=lambda a: a.name.lower())


def find(query: str, apps: list[InstalledApp] | None = None) -> list[tuple[float, InstalledApp]]:
    """Подходящие программы по убыванию сходства (≥ 0.75). Несколько близких — значит, надо уточнить."""
    q, qt = simplify(query), simplify(translit(query))
    if not q:
        return []
    scored = []
    for app in apps if apps is not None else installed():
        n = simplify(app.name)
        if not n:
            continue
        if n in (q, qt):
            score = 1.0
        elif (n.startswith(q) or n.startswith(qt)) and len(q) >= 4:
            score = 0.9 + 0.08 * len(q) / len(n)
        elif (q in n or qt in n) and len(q) >= 4:
            score = 0.8 + 0.1 * len(q) / len(n)
        else:
            score = max(similarity(query, app.name), similarity(query, app.name.split(" ")[0]) - 0.05)
        if score >= 0.75:
            scored.append((round(score, 3), app))
    scored.sort(key=lambda s: (-s[0], len(s[1].name)))
    return scored


def uninstall_command(app: InstalledApp) -> str:
    """Команда, открывающая обычный мастер удаления программы (без «тихого» режима — пользователь видит окно).
    MsiExec /I{GUID} (изменить) превращаем в /X{GUID} (удалить)."""
    cmd = app.uninstall.strip()
    m = re.match(r'(?i)^"?msiexec(?:\.exe)?"?\s+/[IX]\s*(\{[0-9A-F-]+\})', cmd)
    if m:
        return f"MsiExec.exe /X{m.group(1)}"
    return cmd


def same(a: str, b: str) -> bool:
    return normalize(a).strip() == normalize(b).strip()
