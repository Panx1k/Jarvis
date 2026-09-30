"""Индекс установленных приложений (меню «Пуск» + ярлыки) и их запуск/поиск процессов."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from jarvis.utils.text import normalize, similarity, simplify, translit

log = logging.getLogger("jarvis.apps")

CREATE_NO_WINDOW = 0x08000000

PROTECTED_PROCESSES = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "svchost.exe", "dwm.exe", "explorer.exe", "fontdrvhost.exe", "memcompression",
    "sihost.exe", "ctfmon.exe", "securityhealthservice.exe", "msmpeng.exe", "audiodg.exe", "conhost.exe",
    "startmenuexperiencehost.exe", "shellexperiencehost.exe", "searchhost.exe", "runtimebroker.exe",
}


@dataclass
class AppEntry:
    name: str
    target: str
    kind: str


@dataclass
class AppMatch:
    key: str
    display: str
    target: str
    kind: str
    processes: list[str] = field(default_factory=list)
    score: float = 1.0


class AppIndex:
    def __init__(self, settings):
        self.settings = settings
        self.entries: list[AppEntry] = []
        self.ready = threading.Event()

    def refresh_async(self) -> None:
        threading.Thread(target=self.refresh, name="app-index", daemon=True).start()

    def refresh(self) -> None:
        entries: list[AppEntry] = []
        try:
            entries.extend(self._start_apps())
        except Exception as exc:
            log.warning("Get-StartApps недоступен: %s", exc)
        entries.extend(self._shortcuts())
        seen = set()
        unique = []
        for e in entries:
            key = simplify(e.name)
            if key and key not in seen:
                seen.add(key)
                unique.append(e)
        self.entries = unique
        self.ready.set()
        log.info("Индекс приложений: %d записей", len(unique))

    @staticmethod
    def _start_apps() -> list[AppEntry]:
        cmd = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
               "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress")
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                             capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        text = out.stdout.decode("utf-8", errors="replace").strip()
        if not text:
            return []
        data = json.loads(text)
        if isinstance(data, dict):
            data = [data]
        return [AppEntry(d["Name"], d["AppID"], "startapp") for d in data if d.get("Name") and d.get("AppID")]

    @staticmethod
    def _shortcuts() -> list[AppEntry]:
        roots = [
            Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
            Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
            Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop",
            Path.home() / "Desktop",
        ]
        result = []
        for root in roots:
            if not root.is_dir():
                continue
            for pattern in ("*.lnk", "*.url"):
                for p in root.rglob(pattern):
                    name = p.stem
                    if any(w in name.lower() for w in ("uninstall", "удален", "readme", "help")):
                        continue
                    result.append(AppEntry(name, str(p), "lnk"))
        return result

    def _configured(self, query: str) -> AppMatch | None:
        q = normalize(query).strip()
        apps: dict = self.settings.get("apps", {})
        best: tuple[float, str, dict] | None = None
        for key, cfg in apps.items():
            names = [key] + list(cfg.get("aliases", []))
            for alias in names:
                a = normalize(alias)
                if q == a:
                    score = 1.0
                elif len(a) >= 4 and (q.startswith(a + " ") or q.endswith(" " + a)):
                    score = 0.9
                else:
                    score = similarity(q, a) if len(q) >= 4 else 0.0
                if best is None or score > best[0]:
                    best = (score, key, cfg)
        if not best or best[0] < 0.84:
            return None
        score, key, cfg = best
        display = cfg.get("start_name") or key
        if cfg.get("launch"):
            return AppMatch(key, display, cfg["launch"], "command", list(cfg.get("process", [])), score)
        entry = self._find_entry(cfg.get("start_name") or key, threshold=0.8)
        if entry is None:
            for alias in cfg.get("aliases", []):
                entry = self._find_entry(alias, threshold=0.86)
                if entry:
                    break
        if entry is None:
            return AppMatch(key, display, "", "missing", list(cfg.get("process", [])), score)
        return AppMatch(key, entry.name, entry.target, entry.kind, list(cfg.get("process", [])), score)

    def _find_entry(self, query: str, threshold: float = 0.72) -> AppEntry | None:
        self.ready.wait(timeout=20)
        q = simplify(query)
        qt = simplify(translit(query))
        if not q:
            return None
        best: tuple[float, AppEntry] | None = None
        for e in self.entries:
            name = simplify(e.name)
            if not name:
                continue
            if name == q or name == qt:
                score = 1.0
            elif (q in name or qt in name) and len(q) >= 4:
                score = 0.85 + 0.1 * (len(q) / len(name))
            else:
                score = similarity(query, e.name)
            if best is None or score > best[0]:
                best = (score, e)
        if best and best[0] >= threshold:
            return best[1]
        return None

    def resolve(self, query: str) -> AppMatch | None:
        query = query.strip().strip(".,!?\"'«»")
        if not query:
            return None
        match = self._configured(query)
        if match and match.kind != "missing":
            return match
        entry = self._find_entry(query)
        if entry:
            procs = match.processes if match else []
            return AppMatch(entry.name, entry.name, entry.target, entry.kind, procs)
        return match

    def list_names(self, limit: int = 200) -> list[str]:
        self.ready.wait(timeout=20)
        return sorted({e.name for e in self.entries})[:limit]

    @staticmethod
    def launch(match: AppMatch) -> None:
        target = match.target
        if match.kind == "startapp":
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"], creationflags=CREATE_NO_WINDOW)
        elif match.kind == "lnk":
            os.startfile(target)
        else:
            try:
                os.startfile(target)
            except OSError:
                subprocess.Popen(target, shell=True, creationflags=CREATE_NO_WINDOW)

    def find_processes(self, query: str, match: AppMatch | None = None) -> list[psutil.Process]:
        names = {n.lower() for n in (match.processes if match else [])}
        exe_hint = ""
        if match and match.target.lower().endswith(".exe"):
            exe_hint = Path(match.target).name.lower()
            names.add(exe_hint)
        own = {os.getpid(), os.getppid()}
        procs = []
        candidates = []
        for p in psutil.process_iter(["pid", "name", "exe"]):
            try:
                pname = (p.info["name"] or "").lower()
                if not pname or pname in PROTECTED_PROCESSES or p.pid in own:
                    continue
                if pname in names:
                    procs.append(p)
                else:
                    candidates.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if procs:
            return procs
        keys = [query] + ([match.display, match.key] if match else [])
        for p in candidates:
            stem = (p.info["name"] or "").rsplit(".", 1)[0]
            if any(similarity(k, stem) >= 0.85 for k in keys if k):
                procs.append(p)
        return procs
