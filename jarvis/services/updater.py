"""Обновление JARVIS с GitHub без повторного скачивания.

Проверка: последний коммит ветки на GitHub сравнивается с установленным (config/update_state.json).
Обновление: скачивается архив ветки, код заменяется, пользовательское не трогается:
.env, config/settings.json, токены, models/, Voice/, Samples/, logs/, .venv/ и уже существующие
пользовательские конфиги (источники новостей, индекс голосовых записей) — новые файлы из них добавляются.
Если в папке есть .git (копия разработчика или git clone) — обновляемся через git pull, архив не используется.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

from jarvis.config import ROOT, Settings

log = logging.getLogger("jarvis.updater")

STATE_FILE = ROOT / "config" / "update_state.json"
DEFAULT_REPO = "Panx1k/jarvis"
KEEP_TOP = {".env", ".venv", "models", "voice", "samples", "logs", ".git", ".pytest_cache", "__pycache__"}
KEEP_FILES = {"config/settings.json", "config/spotify_token.bin", "config/update_state.json"}
KEEP_IF_EXISTS = {"config/news_sources.json", "config/gaming_sources.json", "assets/voice_samples/index.json",
                  ".env.example", "config/lexicon.json"}
MIRROR_DIRS = ("jarvis", "tests", "scripts")
CREATE_NO_WINDOW = 0x08000000


@dataclass
class UpdateInfo:
    available: bool
    method: str
    latest: str = ""
    installed: str = ""
    message: str = ""
    date: str = ""
    error: str = ""


def repo() -> tuple[str, str]:
    s = Settings()
    return (s.get("update.repo", DEFAULT_REPO) or DEFAULT_REPO), (s.get("update.branch", "main") or "main")


def _state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(data: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _git() -> str | None:
    found = shutil.which("git")
    if found:
        return found
    desktop = Path.home() / "AppData" / "Local" / "GitHubDesktop"
    for exe in sorted(desktop.glob("app-*/resources/app/git/cmd/git.exe"), reverse=True):
        return str(exe)
    return None


def latest_commit() -> dict:
    name, branch = repo()
    r = requests.get(f"https://api.github.com/repos/{name}/commits/{branch}", timeout=10,
                     headers={"Accept": "application/vnd.github+json", "User-Agent": "JARVIS-updater"})
    r.raise_for_status()
    data = r.json()
    return {"sha": data["sha"], "message": (data["commit"]["message"] or "").split("\n")[0][:200],
            "date": data["commit"]["committer"]["date"]}


def check() -> UpdateInfo:
    try:
        latest = latest_commit()
    except Exception as exc:
        return UpdateInfo(False, "none", error=f"Не удалось проверить обновления ({type(exc).__name__}).")
    if (ROOT / ".git").exists():
        git = _git()
        head = ""
        if git:
            try:
                head = subprocess.run([git, "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                                      timeout=10, creationflags=CREATE_NO_WINDOW).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass
        return UpdateInfo(bool(head) and head != latest["sha"], "git", latest["sha"], head, latest["message"],
                          latest["date"])
    installed = _state().get("sha", "")
    return UpdateInfo(installed != latest["sha"], "zip", latest["sha"], installed, latest["message"], latest["date"])


def check_zip() -> UpdateInfo:
    latest = latest_commit()
    return UpdateInfo(True, "zip", latest["sha"], _state().get("sha", ""), latest["message"], latest["date"])


def _same(a: bytes, b: bytes) -> bool:
    """Одинаковы по содержимому (концы строк CRLF/LF не в счёт — git на Windows меняет их сам)."""
    return a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n")


def _req_hash() -> str:
    try:
        return hashlib.sha256((ROOT / "requirements.txt").read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    except OSError:
        return ""


def _install_requirements() -> None:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")],
                   timeout=900, creationflags=CREATE_NO_WINDOW, check=False)


def _extract(archive: bytes) -> dict[str, bytes]:
    """Файлы архива без верхней папки «jarvis-main/». Пути вне проекта отбрасываются."""
    files = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            parts = Path(info.filename).parts[1:]
            if not parts or ".." in parts or Path(*parts).is_absolute():
                continue
            files["/".join(parts)] = z.read(info)
    return files


def _skip(rel: str) -> bool:
    top = rel.split("/")[0].lower()
    return top in KEEP_TOP or rel in KEEP_FILES or "__pycache__" in rel


def apply_zip(info: UpdateInfo, progress=None) -> str:
    name, branch = repo()
    say = progress or (lambda text: None)
    say("Скачиваю обновление…")
    r = requests.get(f"https://codeload.github.com/{name}/zip/refs/heads/{branch}", timeout=60)
    r.raise_for_status()
    files = _extract(r.content)
    if "main.py" not in files or not any(p.startswith("jarvis/") for p in files):
        raise RuntimeError("архив не похож на JARVIS")
    before = _req_hash()
    changed = added = removed = 0
    for rel, data in files.items():
        if _skip(rel):
            continue
        target = ROOT / rel
        if rel in KEEP_IF_EXISTS and target.exists():
            continue
        if target.exists() and _same(target.read_bytes(), data):
            continue
        added += not target.exists()
        changed += target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for folder in MIRROR_DIRS:
        for path in (ROOT / folder).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if rel not in files:
                path.unlink(missing_ok=True)
                removed += 1
    if _req_hash() != before:
        say("Устанавливаю новые зависимости…")
        _install_requirements()
    _save_state({"sha": info.latest, "message": info.message, "updated_at": time.strftime("%Y-%m-%d %H:%M")})
    return f"Обновлено: изменено файлов {changed}, новых {added}, удалено {removed}."


def _run_git(git: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run([git, "-C", str(ROOT), *args], capture_output=True, text=True, timeout=timeout,
                          creationflags=CREATE_NO_WINDOW)


def apply_git(progress=None) -> str:
    """git pull; если мешают изменённые файлы — сохранить их в update_backup/ и привести код к версии с GitHub.
    Копию со своими (неотправленными) коммитами не трогаем — это копия разработчика."""
    git = _git()
    if not git:
        return "Эта копия из git — обновите её через GitHub Desktop (Fetch → Pull) или git pull."
    say = progress or (lambda text: None)
    _, branch = repo()
    before = _req_hash()
    fetched = _run_git(git, "fetch", "origin", branch).returncode == 0
    if not fetched:
        fetched = _run_git(git, "-c", "http.sslBackend=openssl", "fetch", "origin", branch).returncode == 0
    if not fetched:
        say("git не может связаться с GitHub — скачиваю обновление архивом…")
        try:
            return apply_zip(check_zip(), progress)
        except Exception as exc:
            log.warning("Обновление архивом не удалось: %s", exc)
            return "Не удалось связаться с GitHub. Проверьте интернет (или VPN) и попробуйте ещё раз."
    upstream = f"origin/{branch}"
    if _run_git(git, "merge", "--ff-only", upstream).returncode == 0:
        if _req_hash() != before:
            _install_requirements()
        return "Обновлено."
    ahead = _run_git(git, "rev-list", "--count", f"{upstream}..HEAD").stdout.strip()
    if ahead and ahead != "0":
        return ("В этой копии есть свои коммиты, которых нет на GitHub, — обновите её через GitHub Desktop, "
                "автоматически не трогаю.")
    changed = [line[3:].strip().strip('"') for line in
               _run_git(git, "status", "--porcelain", "--untracked-files=no").stdout.splitlines() if line.strip()]
    backup = ROOT / "update_backup" / time.strftime("%Y%m%d-%H%M%S")
    for rel in changed:
        src = ROOT / rel
        if src.is_file():
            (backup / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, backup / rel)
    say("Изменённые файлы сохранены, привожу код к версии с GitHub…")
    if _run_git(git, "reset", "--hard", upstream).returncode != 0:
        return "Не удалось обновить — обновите через GitHub Desktop."
    for rel in KEEP_IF_EXISTS:
        saved = backup / rel
        if saved.is_file():
            shutil.copy2(saved, ROOT / rel)
    if _req_hash() != before:
        _install_requirements()
    note = f" Ваши изменённые файлы сохранены в {backup.relative_to(ROOT)}." if changed else ""
    return "Обновлено." + note


def apply(info: UpdateInfo | None = None, progress=None) -> tuple[bool, str]:
    info = info or check()
    if info.error:
        return False, info.error
    if not info.available:
        return False, "У вас последняя версия."
    try:
        text = apply_git(progress) if info.method == "git" else apply_zip(info, progress)
    except Exception as exc:
        log.warning("Обновление не удалось: %s", exc)
        return False, f"Обновление не удалось: {type(exc).__name__}. Попробуйте позже."
    log.info("JARVIS обновлён: %s (%s)", info.latest[:7], text)
    return True, text


DETACHED_PROCESS = 0x00000008


def relaunch_command(delay: float = 3.0, target: list[str] | None = None) -> list[str]:
    """Команда перезапуска: отдельный процесс Python ждёт и запускает JARVIS напрямую, без cmd и кавычек
    (через cmd /c кавычки экранировались как \\" и Windows пыталась открыть «\\\\»)."""
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    target = target or [str(pythonw if pythonw.exists() else python), str(ROOT / "main.py")]
    script = f"import time, subprocess; time.sleep({delay}); subprocess.Popen({target!r}, cwd={str(ROOT)!r})"
    return [str(pythonw if pythonw.exists() else python), "-c", script]


def restart() -> None:
    """Запустить JARVIS заново (через пару секунд, когда текущий процесс завершится)."""
    subprocess.Popen(relaunch_command(), cwd=str(ROOT), creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
                     close_fds=True)


def main() -> None:
    info = check()
    if info.error:
        print(info.error)
        return
    if not info.available:
        print("У вас последняя версия.")
        return
    print(f"Доступно обновление: {info.message} ({info.date[:10]})")
    ok, text = apply(info, progress=print)
    print(text)


if __name__ == "__main__":
    main()
