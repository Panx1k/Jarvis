"""Проводник, папки и файлы."""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import uuid
from ctypes import wintypes
from pathlib import Path

from jarvis.tools.base import ToolResult, tool
from jarvis.utils.text import normalize

_KNOWN = {
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def known_folder(key: str) -> Path | None:
    guid = _KNOWN.get(key)
    if not guid:
        return None
    u = uuid.UUID(guid)
    g = _GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
    ptr = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(ptr)) != 0:
        return None
    try:
        return Path(ptr.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(ptr)


def match_folder(settings, name: str) -> tuple[str, str] | None:
    """Найти папку из настроек по фразе («мои загрузки», «рабочий стол»)."""
    q = normalize(name).strip()
    folders: dict = settings.get("folders", {})
    for key, cfg in folders.items():
        for alias in cfg.get("aliases", []):
            if re.search(r"(?:^|[\s«\"'])" + re.escape(normalize(alias)), q):
                return key, cfg.get("target", "")
    return None


def _drive(text: str) -> str | None:
    m = re.search(r"\bдиск\w*\s+([a-zа-я])\b", normalize(text))
    if not m:
        return None
    letter = m.group(1)
    letter = {"ц": "c", "с": "c", "д": "d", "е": "e", "ф": "f", "г": "g", "а": "a", "б": "b", "и": "e"}.get(letter, letter)
    return f"{letter.upper()}:\\"


def resolve_path(settings, path: str) -> Path:
    """Путь с поддержкой ~, %ENV% и начала вида «загрузки/…»."""
    path = os.path.expandvars(os.path.expanduser(path.strip().strip('"')))
    p = Path(path)
    if p.is_absolute():
        return p
    head, _, rest = path.replace("/", "\\").partition("\\")
    m = match_folder(settings, head)
    if m and m[0] in _KNOWN:
        base = known_folder(m[0])
        if base:
            return base / rest if rest else base
    return Path.home() / path


@tool("open_folder", "Открыть папку в проводнике: загрузки, документы, рабочий стол, изображения, музыка, видео, "
      "корзина, этот компьютер, диск C/D или любой путь. Без аргумента — открыть проводник.",
      params={"folder": {"type": "string", "description": "Название папки или путь"}},
      announce=lambda a: f"Открываю {a.get('folder') or 'проводник'}", category="files")
def open_folder(ctx, folder: str | None = None) -> ToolResult:
    folder = (folder or "").strip()
    if not folder or normalize(folder) in ("проводник", "explorer"):
        subprocess.Popen(["explorer.exe"])
        return ToolResult(True, "Открываю проводник.")
    drive = _drive(folder)
    if drive:
        if not Path(drive).exists():
            return ToolResult(False, f"Диска {drive[0]} нет.")
        os.startfile(drive)
        return ToolResult(True, f"Открываю диск {drive[0]}.")
    m = match_folder(ctx.settings, folder)
    if m:
        key, target = m
        target = os.path.expandvars(target)
        subprocess.Popen(["explorer.exe", target] if target else ["explorer.exe"])
        pretty = {"downloads": "загрузки", "documents": "документы", "desktop": "рабочий стол",
                  "pictures": "изображения", "music": "папку «Музыка»", "videos": "папку «Видео»",
                  "recycle": "корзину", "computer": "«Этот компьютер»"}.get(key, folder)
        return ToolResult(True, f"Открываю {pretty}.")
    p = resolve_path(ctx.settings, folder)
    if p.exists():
        os.startfile(str(p))
        return ToolResult(True, f"Открываю {p}.")
    return ToolResult(False, f"Не нашёл папку «{folder}».")


@tool("open_file", "Открыть файл программой по умолчанию.",
      params={"path": {"type": "string", "description": "Путь к файлу (можно «загрузки/файл.pdf»)"}},
      required=["path"], announce="Открываю файл {path}", category="files")
def open_file(ctx, path: str) -> ToolResult:
    p = resolve_path(ctx.settings, path)
    if not p.exists():
        return ToolResult(False, f"Файл не найден: {p}")
    os.startfile(str(p))
    return ToolResult(True, f"Открываю {p.name}.")


_SKIP_DIRS = {"appdata", "node_modules", ".git", "__pycache__", "$recycle.bin", ".venv", "venv", "windows",
              "program files", "program files (x86)", "programdata", ".cache", "site-packages"}


@tool("search_files", "Найти файлы и папки на компьютере по имени (часть имени и/или расширение). Ищет в рабочем "
      "столе, документах, загрузках, изображениях, музыке, видео (или в указанной папке). Результаты "
      "запоминаются: открыть найденное — open_result с номером.",
      params={"query": {"type": "string", "description": "Часть имени, например «отчёт» или «*.pdf»"},
              "folder": {"type": "string", "description": "Необязательно: где искать (путь или «загрузки»)"},
              "extension": {"type": "string", "description": "Необязательно: расширение, например pdf"}},
      required=["query"], announce="Ищу файлы: {query}", category="files")
def search_files(ctx, query: str, folder: str | None = None, extension: str | None = None) -> ToolResult:
    import fnmatch
    import time

    from jarvis.core.context import SearchResult

    q = normalize(query).strip().strip("«»\"'")
    ext = (extension or "").lower().lstrip("*.")
    if not ext:
        m = re.match(r"^\*?\.([a-z0-9]{1,6})$", q)
        if m:
            ext, q = m.group(1), ""
    wildcard = "*" in q or "?" in q
    tokens = [t for t in re.split(r"[\s_\-.]+", q) if t] if not wildcard else []

    if folder:
        roots = [resolve_path(ctx.settings, folder)]
    else:
        roots = [known_folder(k) for k in ("desktop", "documents", "downloads", "pictures", "music", "videos")]
    roots = [r for r in roots if r and r.exists()]
    if not roots:
        return ToolResult(False, f"Папка для поиска не найдена: {folder}")

    def matches(name: str) -> bool:
        low = normalize(name)
        if ext and not low.endswith("." + ext):
            return False
        if wildcard:
            return fnmatch.fnmatch(low, q)
        return all(t in low for t in tokens)

    found: list[Path] = []
    deadline = time.monotonic() + 6.0
    seen: set[str] = set()
    for root in roots:
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            if time.monotonic() > deadline or len(found) >= 200:
                break
            dirnames[:] = [d for d in dirnames if d.lower() not in _SKIP_DIRS and not d.startswith(".")
                           and len(Path(dirpath).parts) - base_depth < 6]
            for name in dirnames + filenames:
                if matches(name):
                    p = Path(dirpath) / name
                    if str(p) not in seen:
                        seen.add(str(p))
                        found.append(p)
    if not found:
        where = folder or "пользовательских папках"
        return ToolResult(False, f"Файлы по запросу «{query}» не найдены в {where}.")

    def rank(p: Path):
        exact = 0 if normalize(p.stem) == q else 1
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0
        return exact, -mtime

    found.sort(key=rank)
    top = found[:20]
    ctx.dialog.set_results("files", query, [SearchResult(p.name, str(p), "file", channel=str(p.parent))
                                            for p in top])
    listing = "; ".join(f"{i}. {p.name}" for i, p in enumerate(top[:5], 1))
    more = f" (всего {len(found)})" if len(found) > 5 else ""
    return ToolResult(True, f"Нашёл: {listing}{more}.", {"results": [str(p) for p in top[:10]]})


@tool("create_folder", "Создать новую папку.",
      params={"path": {"type": "string", "description": "Путь новой папки (можно «рабочий стол/Проект»)"}},
      required=["path"], announce="Создаю папку {path}", category="files")
def create_folder(ctx, path: str) -> ToolResult:
    p = resolve_path(ctx.settings, path)
    if p.exists():
        return ToolResult(False, f"Папка уже существует: {p}")
    p.mkdir(parents=True)
    return ToolResult(True, f"Создал папку {p.name}.", {"path": str(p)})


@tool("delete_to_recycle_bin", "Переместить файл или папку в корзину (можно восстановить).",
      params={"path": {"type": "string", "description": "Путь к файлу/папке"}}, required=["path"],
      dangerous=True, confirm=lambda a: f"Переместить «{a.get('path')}» в корзину?",
      announce="Удаляю в корзину {path}", category="files")
def delete_to_recycle_bin(ctx, path: str) -> ToolResult:
    p = resolve_path(ctx.settings, path)
    if not p.exists():
        return ToolResult(False, f"Не найдено: {p}")
    critical = [Path(os.environ.get("SystemRoot", r"C:\Windows")), Path(r"C:\Program Files"),
                Path(r"C:\Program Files (x86)"), Path.home(), Path(p.anchor)]
    if any(p.resolve() == c.resolve() for c in critical):
        return ToolResult(False, f"Отказываюсь удалять системный путь {p}.")
    from win32com.shell import shell, shellcon

    flags = shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT | shellcon.FOF_NOERRORUI
    code, aborted = shell.SHFileOperation((0, shellcon.FO_DELETE, str(p), None, flags, None, None))
    if code != 0 or aborted:
        return ToolResult(False, f"Не удалось переместить в корзину (код {code}).")
    return ToolResult(True, f"«{p.name}» перемещён в корзину.")
