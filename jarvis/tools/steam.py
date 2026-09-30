"""Steam: список сохранённых аккаунтов и переключение между ними.

Работает так же, как менеджеры аккаунтов Steam: пароли не используются и не хранятся. Переключение
возможно только на аккаунты, уже сохранённые в этом Steam с «Запомнить меня» (config/loginusers.vdf):
в реестре задаётся AutoLoginUser, Steam штатно закрывается и запускается снова.
"""
from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
import winreg
from dataclasses import dataclass
from pathlib import Path

import psutil

from jarvis.tools._common import CREATE_NO_WINDOW
from jarvis.tools.base import ToolResult, tool
from jarvis.utils.text import normalize, similarity

log = logging.getLogger("jarvis.steam")
REG_KEY = r"Software\Valve\Steam"
HELPERS = {"steam.exe", "steamwebhelper.exe", "steamservice.exe", "gameoverlayui.exe", "steamerrorreporter.exe",
           "gameoverlayui64.exe", "steamerrorreporter64.exe"}


@dataclass
class SteamAccount:
    steam_id: str
    account: str
    persona: str
    remember: bool


def steam_dir() -> Path | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
            return Path(winreg.QueryValueEx(k, "SteamPath")[0])
    except OSError:
        return None


def read_accounts(vdf: Path | None = None) -> list[SteamAccount]:
    vdf = vdf or ((steam_dir() or Path()) / "config" / "loginusers.vdf")
    if not vdf.exists():
        return []
    text = vdf.read_text(encoding="utf-8", errors="replace")
    out = []
    for sid, body in re.findall(r'"(\d{17})"\s*\{(.*?)\}', text, re.S):
        kv = {k.lower(): v for k, v in re.findall(r'"(\w+)"\s*"([^"]*)"', body)}
        out.append(SteamAccount(sid, kv.get("accountname", ""), kv.get("personaname", ""),
                                kv.get("rememberpassword", "0") == "1"))
    return out


def current_login() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
            return winreg.QueryValueEx(k, "AutoLoginUser")[0]
    except OSError:
        return ""


def set_auto_login(account: str) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "AutoLoginUser", 0, winreg.REG_SZ, account)
        winreg.SetValueEx(k, "RememberPassword", 0, winreg.REG_DWORD, 1)


def mark_most_recent(vdf: Path, steam_id: str) -> None:
    """В loginusers.vdf пометить выбранный аккаунт последним (Steam предлагает его первым)."""
    text = vdf.read_text(encoding="utf-8", errors="replace")

    def fix(m: re.Match) -> str:
        head, sid, body, tail = m.group(1), m.group(2), m.group(3), m.group(4)
        value = "1" if sid == steam_id else "0"
        body = re.sub(r'("mostrecent"\s*")\d(")', lambda mm: mm.group(1) + value + mm.group(2), body, flags=re.I)
        return head + body + tail

    new = re.sub(r'("(\d{17})"\s*\{)(.*?)(\})', fix, text, flags=re.S)
    if new != text:
        vdf.write_text(new, encoding="utf-8")


def steam_processes() -> list[psutil.Process]:
    return [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "steam.exe"]


def running_steam_games() -> list[str]:
    """Процессы, запущенные Steam (игры), — кроме служебных."""
    games = []
    for sp in steam_processes():
        try:
            for child in sp.children(recursive=True):
                name = child.name().lower()
                if name not in HELPERS:
                    games.append(child.name())
        except psutil.Error:
            continue
    return sorted(set(games))


GAME_ALIASES = {"кс": "counter-strike 2", "кс2": "counter-strike 2", "кс 2": "counter-strike 2", "контра": "counter-strike 2",
                "контру": "counter-strike 2", "cs": "counter-strike 2", "cs2": "counter-strike 2", "cs 2": "counter-strike 2",
                "counter strike": "counter-strike 2", "каунтер страйк": "counter-strike 2", "дота": "dota 2",
                "доту": "dota 2", "дота 2": "dota 2", "доту 2": "dota 2", "пабг": "pubg", "раст": "rust", "апекс": "apex",
                "гта": "grand theft auto v", "гта 5": "grand theft auto v", "gta 5": "grand theft auto v",
                "gta v": "grand theft auto v", "киберпанк": "cyberpunk 2077", "элден ринг": "elden ring",
                "майнкрафт": "minecraft", "фортнайт": "fortnite", "валорант": "valorant", "варфейс": "warface",
                "шедул": "schedule i", "шедул 1": "schedule i", "обои": "wallpaper engine", "саундпад": "soundpad"}


def _game_manifests() -> dict[str, tuple[str, Path | None]]:
    """appid → (название, папка установки) для игр из всех библиотек Steam."""
    steam = steam_dir()
    if not steam:
        return {}
    libs = {steam}
    lf = steam / "steamapps" / "libraryfolders.vdf"
    if lf.exists():
        for p in re.findall(r'"path"\s*"([^"]+)"', lf.read_text(encoding="utf-8", errors="replace")):
            libs.add(Path(p.replace("\\\\", "\\")))
    games = {}
    for lib in libs:
        for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
            text = acf.read_text(encoding="utf-8", errors="replace")
            appid = re.search(r'"appid"\s*"(\d+)"', text)
            name = re.search(r'"name"\s*"([^"]+)"', text)
            folder = re.search(r'"installdir"\s*"([^"]+)"', text)
            if appid and name and "redistributable" not in name.group(1).lower() \
                    and "steamworks" not in name.group(1).lower():
                path = lib / "steamapps" / "common" / folder.group(1) if folder else None
                games[appid.group(1)] = (name.group(1), path)
    return games


def installed_games() -> list[tuple[str, str]]:
    """(appid, название) установленных игр из всех библиотек Steam."""
    return sorted(((appid, name) for appid, (name, _) in _game_manifests().items()), key=lambda it: it[1])


def game_processes(query: str) -> tuple[str, list[psutil.Process]] | None:
    """Процессы запущенной игры Steam: exe внутри папки установки (у CS2 процесс cs2.exe, а не «Counter-Strike 2»)."""
    found = find_game(query)
    if not found:
        return None
    name, folder = _game_manifests().get(found[0], (found[1], None))
    if not folder:
        return name, []
    root = str(folder).lower().rstrip("\\") + "\\"
    procs = []
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            if (p.info["exe"] or "").lower().startswith(root):
                procs.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return name, procs


def find_game(query: str) -> tuple[str, str] | None:
    q = normalize(query).strip(" .!?«»\"'")
    q = re.sub(r"^(?:игру|игра|в)\s+", "", q)
    target = GAME_ALIASES.get(q, q)
    best = None
    for appid, name in installed_games():
        n = normalize(name)
        score = 1.0 if n == target else (0.92 if target and (target in n or n in target) and len(target) >= 3
                                          else similarity(target, name))
        if best is None or score > best[0]:
            best = (score, appid, name)
    return (best[1], best[2]) if best and best[0] >= 0.75 else None


@tool("launch_game", "Запустить игру из библиотеки Steam по названию (Counter-Strike 2 — «кс», Dota 2 — «дота» и т. п.). "
      "Если для игры задан свой аккаунт Steam, сам переключит Steam на него и запустит игру после входа.",
      params={"game": {"type": "string", "description": "Название игры"}}, required=["game"],
      announce="Запускаю игру {game}", category="steam")
def launch_game(ctx, game: str) -> ToolResult:
    import os

    found = find_game(game)
    if not found:
        names = ", ".join(n for _, n in installed_games()[:15])
        return ToolResult(False, f"Не нашёл игру «{game}» в Steam." + (f" Установлены: {names}." if names else ""))
    appid, name = found
    ctx.dialog.last_app = name
    target = account_for(ctx, appid, name)
    if target and not on_account(target):
        busy = running_game()
        if busy:
            return ToolResult(False, f"Сейчас запущена {busy}. Чтобы запустить {name} на аккаунте "
                                     f"{_who(target)}, сначала закройте её.")
        r = switch_to(ctx, target)
        if not r.ok:
            return r
        threading.Thread(target=_launch_after_login, args=(appid, target), name="steam-launch", daemon=True).start()
        return ToolResult(True, f"Переключаю Steam на аккаунт {_who(target)} и запускаю {name}. Это займёт около "
                                f"минуты.", {"app": name, "account": _who(target)})
    os.startfile(f"steam://rungameid/{appid}")
    return ToolResult(True, f"Запускаю {name}." + (f" Аккаунт {_who(target)}." if target else ""), {"app": name})


def _who(a: SteamAccount) -> str:
    return a.persona or a.account


def _id32(a: SteamAccount) -> int:
    return int(a.steam_id) - STEAM_ID64_BASE


def active_id32() -> int:
    """Под каким аккаунтом сейчас вошёл запущенный Steam (0 — не вошёл / не запущен)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY + r"\ActiveProcess") as k:
            return int(winreg.QueryValueEx(k, "ActiveUser")[0] or 0)
    except OSError:
        return 0


def account_for(ctx, appid: str | None, name: str | None) -> SteamAccount | None:
    """Аккаунт, на котором должна запускаться игра (настройки steam.game_accounts / steam.default_account)."""
    settings = getattr(ctx, "settings", None)
    if settings is None:
        return None
    per_game = {str(k).lower(): v for k, v in (settings.get("steam.game_accounts", {}) or {}).items()}
    wanted = per_game.get(str(appid or "").lower()) or per_game.get((name or "").lower()) \
        or settings.get("steam.default_account", "")
    if not wanted:
        return None
    return find_account(wanted, read_accounts())


def on_account(target: SteamAccount) -> bool:
    """Steam уже работает (или запустится) на этом аккаунте."""
    if steam_processes():
        active = active_id32()
        if active:
            return active == _id32(target)
    return current_login().lower() == target.account.lower()


def _launch_after_login(appid: str, target: SteamAccount, timeout: float = 120) -> None:
    """Дождаться, пока Steam войдёт в нужный аккаунт, и запустить игру."""
    import os

    deadline = time.time() + timeout
    while time.time() < deadline:
        if steam_processes() and active_id32() == _id32(target):
            time.sleep(4)
            os.startfile(f"steam://rungameid/{appid}")
            log.info("Steam: вошёл в %s — запускаю игру %s", _who(target), appid)
            return
        time.sleep(1.5)
    log.warning("Steam: не дождался входа в аккаунт %s — игра не запущена", _who(target))


def prepare_steam_launch(ctx) -> SteamAccount | None:
    """Перед открытием самого Steam: если он не запущен — заранее выбрать аккаунт по умолчанию.
    Если запущен на другом аккаунте и игр нет — переключить. Возвращает аккаунт, если он менялся."""
    target = account_for(ctx, None, None)
    if not target or on_account(target):
        return None
    if steam_processes():
        if running_game():
            return None
        return target if switch_to(ctx, target).ok else None
    steam = steam_dir()
    set_auto_login(target.account)
    if steam:
        try:
            mark_most_recent(steam / "config" / "loginusers.vdf", target.steam_id)
        except OSError as exc:
            log.warning("loginusers.vdf: %s", exc)
    return target


STEAM_ID64_BASE = 76561197960265728


@dataclass
class SteamGame:
    appid: str
    name: str
    folder: Path | None
    size_gb: float
    playtime_h: float
    recent_h: float
    last_played: float


def _user_apps() -> dict:
    """Статистика игр текущего аккаунта из userdata/<id>/config/localconfig.vdf (время в игре, последний запуск)."""
    from jarvis.utils import vdf

    steam = steam_dir()
    cur = current_login().lower()
    acc = next((a for a in read_accounts() if a.account.lower() == cur), None)
    if not steam or not acc:
        return {}
    path = steam / "userdata" / str(int(acc.steam_id) - STEAM_ID64_BASE) / "config" / "localconfig.vdf"
    try:
        data = vdf.loads(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}
    return vdf.get(data, "UserLocalConfigStore", "Software", "Valve", "Steam", "apps", default={}) or {}


def library() -> list[SteamGame]:
    """Установленные игры с размером и статистикой, недавно запущенные — первыми."""
    from jarvis.utils import vdf

    stats = _user_apps()
    games = []
    for appid, (name, folder) in _game_manifests().items():
        if name.lower() in ("steamvr", "steam linux runtime"):
            continue
        size, last = 0.0, 0.0
        if folder:
            acf = folder.parent.parent / f"appmanifest_{appid}.acf"
            try:
                st = vdf.get(vdf.loads(acf.read_text(encoding="utf-8", errors="replace")), "AppState", default={})
                size = int(st.get("sizeondisk", 0) or 0) / 1024 ** 3
                last = float(st.get("lastplayed", 0) or 0)
            except (OSError, ValueError):
                pass
        s = stats.get(appid, {}) if isinstance(stats.get(appid), dict) else {}
        last = max(last, float(s.get("lastplayed", 0) or 0))
        games.append(SteamGame(appid, name, folder, round(size, 1), round(int(s.get("playtime", 0) or 0) / 60, 1),
                               round(int(s.get("playtime2wks", 0) or 0) / 60, 1), last))
    return sorted(games, key=lambda g: -g.last_played)


def _ago(ts: float) -> str:
    if not ts:
        return "не запускалась"
    days = (time.time() - ts) / 86400
    if days < 1:
        return "сегодня"
    if days < 2:
        return "вчера"
    return f"{int(days)} дн. назад"


NON_GAMES = {"wallpaper engine", "soundpad", "steamvr", "fpsvr", "lossless scaling", "rgb fusion"}


def running_game() -> str | None:
    """Название запущенной игры Steam (по процессу из папки установки)."""
    folders = {str(g.folder).lower().rstrip("\\") + "\\": g.name for g in library()
               if g.folder and g.name.lower() not in NON_GAMES}
    for p in psutil.process_iter(["exe"]):
        exe = (p.info.get("exe") or "").lower()
        for root, name in folders.items():
            if exe.startswith(root):
                return name
    return None


def library_summary(limit: int = 15) -> str:
    """Короткая сводка для контекста модели: какие игры установлены и что запущено."""
    try:
        games = library()
    except OSError:
        return ""
    if not games:
        return ""
    items = [f"{g.name} ({'программа, ' if g.name.lower() in NON_GAMES else ''}{g.playtime_h:g} ч, "
             f"{_ago(g.last_played)})" for g in games[:limit]]
    now = running_game()
    return "Игры в Steam: " + "; ".join(items) + (f". Сейчас запущена: {now}" if now else "")


@tool("steam_games", "Список установленных игр Steam: сколько часов наиграно, когда запускалась, размер, какая "
      "игра запущена сейчас. Используй, чтобы ответить, что есть в Steam, во что поиграть, сколько наиграно.",
      announce="Смотрю библиотеку Steam", category="steam",
      patterns=[r"(?:какие|что за|список|покажи)\s+(?:у меня\s+)?игр\w*(?:\s+(?:есть|установлен\w*))?"
                r"(?:\s+(?:в|на)\s+(?:стим\w*|steam))?$",
                r"^что\s+(?:у меня\s+)?(?:есть|установлено)\s+(?:в|на)\s+(?:стим\w*|steam)$"])
def steam_games(ctx) -> ToolResult:
    games = library()
    if not games:
        return ToolResult(False, "Установленных игр Steam не нашёл.")
    now = running_game()
    parts = [f"{g.name}{' (программа)' if g.name.lower() in NON_GAMES else ''} — {g.playtime_h:g} ч" + (f" (за 2 недели {g.recent_h:g} ч)" if g.recent_h else "")
             + f", {_ago(g.last_played)}, {g.size_gb:g} ГБ" for g in games]
    text = f"Установлено игр: {len(games)}. " + "; ".join(parts) + "."
    if now:
        text += f" Сейчас запущена {now}."
    return ToolResult(True, text, {"games": [g.name for g in games], "running": now})


@tool("steam_game_info", "Подробности об игре Steam: установлена ли, сколько часов наиграно, когда запускалась, "
      "размер, запущена ли сейчас.",
      params={"game": {"type": "string", "description": "Название игры"}}, required=["game"],
      announce="Смотрю игру {game}", category="steam")
def steam_game_info(ctx, game: str) -> ToolResult:
    found = find_game(game)
    if not found:
        return ToolResult(False, f"Игра «{game}» в Steam не установлена.")
    g = next((x for x in library() if x.appid == found[0]), None)
    if not g:
        return ToolResult(False, f"Игра «{game}» в Steam не установлена.")
    running = running_game() == g.name
    return ToolResult(True, f"{g.name}: наиграно {g.playtime_h:g} ч, за 2 недели {g.recent_h:g} ч, последний запуск — "
                            f"{_ago(g.last_played)}, размер {g.size_gb:g} ГБ." + (" Сейчас запущена." if running else ""),
                      {"app": g.name, "running": running})


def store_search(query: str) -> tuple[str, str] | None:
    """(appid, название) игры в магазине Steam — публичный поиск магазина, без ключей."""
    import requests

    q = normalize(GAME_ALIASES.get(normalize(query).strip(" .!?"), query))

    def score(item) -> float:
        name = normalize(item["name"])
        s = 1.0 if name == q else similarity(q, item["name"])
        if re.search(r"demo|soundtrack|dlc|pack|bundle|artbook|демо", name) and not re.search(r"demo|демо", q):
            s -= 0.5
        return s

    best = None
    for cc in ("RU", "US"):
        try:
            r = requests.get("https://store.steampowered.com/api/storesearch/",
                             params={"term": q, "l": "russian", "cc": cc}, timeout=8)
            items = r.json().get("items") or []
        except (requests.RequestException, ValueError):
            continue
        if items:
            cand = max(items, key=score)
            if best is None or score(cand) > score(best):
                best = cand
            if score(best) >= 0.8:
                break
    return (str(best["id"]), best["name"]) if best and score(best) >= 0.5 else None


@tool("steam_store", "Открыть страницу игры в магазине Steam (цена, описание, купить).",
      params={"game": {"type": "string", "description": "Название игры"}}, required=["game"],
      announce="Открываю {game} в магазине Steam", category="steam")
def steam_store(ctx, game: str) -> ToolResult:
    import os

    found = find_game(game) or store_search(game)
    if not found:
        return ToolResult(False, f"Не нашёл игру «{game}» в магазине Steam.")
    os.startfile(f"steam://store/{found[0]}")
    return ToolResult(True, f"Открываю {found[1]} в магазине Steam.", {"app": found[1]})


@tool("steam_install", "Установить игру из Steam (откроется окно установки Steam; игра должна быть в библиотеке).",
      params={"game": {"type": "string", "description": "Название игры"}}, required=["game"],
      announce="Устанавливаю {game}", category="steam")
def steam_install(ctx, game: str) -> ToolResult:
    import os

    if find_game(game):
        return ToolResult(False, f"{find_game(game)[1]} уже установлена.")
    found = store_search(game)
    if not found:
        return ToolResult(False, f"Не нашёл игру «{game}» в Steam.")
    os.startfile(f"steam://install/{found[0]}")
    return ToolResult(True, f"Открыл установку {found[1]} в Steam.", {"app": found[1]})


@tool("steam_uninstall", "Удалить установленную игру Steam с компьютера (Steam сам ещё раз спросит).",
      params={"game": {"type": "string", "description": "Название игры"}}, required=["game"],
      dangerous=True, confirm="Удалить игру {game} с компьютера?", announce="Удаляю {game}", category="steam")
def steam_uninstall(ctx, game: str) -> ToolResult:
    import os

    found = find_game(game)
    if not found:
        return ToolResult(False, f"Игра «{game}» не установлена.")
    os.startfile(f"steam://uninstall/{found[0]}")
    return ToolResult(True, f"Открыл удаление {found[1]} в Steam — подтвердите в окне Steam.", {"app": found[1]})


STEAM_PAGES = {"library": "steam://nav/games", "store": "steam://store", "downloads": "steam://nav/downloads",
               "friends": "steam://open/friends", "settings": "steam://open/settings"}
STEAM_PAGE_NAMES = {"library": "библиотеку", "store": "магазин", "downloads": "загрузки", "friends": "друзей",
                    "settings": "настройки"}


@tool("steam_open", "Открыть раздел Steam: library (библиотека игр), store (магазин), downloads (загрузки), "
      "friends (друзья), settings (настройки).",
      params={"page": {"type": "string", "enum": list(STEAM_PAGES), "description": "Раздел"}}, required=["page"],
      announce="Открываю Steam", category="steam")
def steam_open(ctx, page: str = "library") -> ToolResult:
    import os

    page = page if page in STEAM_PAGES else "library"
    if not steam_dir():
        return ToolResult(False, "Steam не установлен.")
    os.startfile(STEAM_PAGES[page])
    ctx.dialog.last_app = "Steam"
    return ToolResult(True, f"Открываю {STEAM_PAGE_NAMES[page]} Steam.")


def find_account(query: str, accounts: list[SteamAccount]) -> SteamAccount | None:
    q = normalize(query).strip(" .!?«»\"'")
    best = None
    for a in accounts:
        for name in (a.persona, a.account):
            if not name:
                continue
            score = 1.0 if normalize(name) == q else similarity(q, name)
            if best is None or score > best[0]:
                best = (score, a)
    return best[1] if best and best[0] >= 0.7 else None


@tool("steam_accounts", "Показать аккаунты Steam, сохранённые на этом компьютере, и какой сейчас активен.",
      announce="Смотрю аккаунты Steam", category="steam",
      patterns=[r"(?:какие|покажи|список)\s+(?:у меня\s+)?аккаунт\w*\s+(?:в\s+)?(?:стим\w*|steam)"])
def steam_accounts(ctx) -> ToolResult:
    accounts = read_accounts()
    if not accounts:
        return ToolResult(False, "Сохранённых аккаунтов Steam не найдено.")
    cur = current_login().lower()
    names = [f"{a.persona or a.account}{' (сейчас)' if a.account.lower() == cur else ''}" for a in accounts]
    return ToolResult(True, "Аккаунты Steam: " + ", ".join(names) + ".", {"accounts": [a.persona for a in accounts]})


def _switch_needs_confirm(args: dict) -> bool:
    return bool(running_steam_games())


@tool("steam_switch_account", "Переключить Steam на другой сохранённый аккаунт (по имени профиля или логину). "
      "Без имени — на другой аккаунт, если их два. Steam будет перезапущен. Пароли не нужны: работают только "
      "аккаунты, сохранённые в Steam с «Запомнить меня».",
      params={"account": {"type": "string", "description": "Имя профиля или логин аккаунта Steam"}},
      dangerous=_switch_needs_confirm,
      confirm=lambda a: f"В Steam запущена игра ({', '.join(running_steam_games())}). Закрыть Steam и "
                        f"переключить аккаунт{' на ' + a['account'] if a.get('account') else ''}?",
      announce=lambda a: f"Переключаю Steam на аккаунт {a.get('account') or 'другой'}", category="steam",
      patterns=[r"^(?:смени|переключи|поменяй|сменить|переключить)\s+(?:аккаунт|акк|учетн\w*\s+запис\w*)"
                r"(?:\s+(?:в\s+)?(?:стим\w*|steam))?(?:\s+на\s+(?:аккаунт\s+)?(?P<account>.+?))?"
                r"(?:\s+(?:в\s+)?(?:стим\w*|steam))?$",
                r"^(?:зайди|войди|перейди|переключись)\s+(?:на|в)\s+(?:аккаунт|акк)\s+(?P<account>.+?)"
                r"(?:\s+(?:в|на)\s+(?:стим\w*|steam))?$",
                r"^(?:зайди|войди)\s+в\s+(?:стим\w*|steam)\s+(?:под|на|с)\s+(?:аккаунт\w*\s+)?(?P<account>.+)$"])
def steam_switch_account(ctx, account: str | None = None) -> ToolResult:
    steam = steam_dir()
    accounts = read_accounts()
    if not steam or not accounts:
        return ToolResult(False, "Не нашёл сохранённых аккаунтов Steam.")
    remembered = [a for a in accounts if a.remember]
    if account:
        target = find_account(account, accounts)
        if target is None:
            names = ", ".join(a.persona or a.account for a in accounts)
            return ToolResult(False, f"Не нашёл аккаунт «{account}». Сохранённые аккаунты: {names}.")
    else:
        others = [a for a in remembered if not on_account(a)]
        if len(others) != 1:
            names = ", ".join(a.persona or a.account for a in accounts)
            return ToolResult(False, f"На какой аккаунт переключить? Сохранённые: {names}.")
        target = others[0]
    who = target.persona or target.account
    if not target.remember:
        return ToolResult(False, f"Аккаунт {who} не сохранён с «Запомнить меня» — войдите в него один раз вручную.")
    if on_account(target) and steam_processes():
        return ToolResult(True, f"Steam уже открыт на аккаунте {who}.")
    return switch_to(ctx, target)


def switch_to(ctx, target: SteamAccount) -> ToolResult:
    """Штатно закрыть Steam, выбрать аккаунт и запустить Steam снова."""
    steam = steam_dir()
    if not steam:
        return ToolResult(False, "Steam не установлен.")
    who = _who(target)
    if not target.remember:
        return ToolResult(False, f"Аккаунт {who} не сохранён с «Запомнить меня» — войдите в него один раз вручную.")
    exe = steam / "steam.exe"
    if steam_processes():
        subprocess.Popen([str(exe), "-shutdown"], creationflags=CREATE_NO_WINDOW)
        deadline = time.time() + 25
        while steam_processes() and time.time() < deadline:
            time.sleep(0.5)
        if steam_processes():
            return ToolResult(False, "Steam не закрылся (возможно, идёт загрузка или открыта игра). "
                                     "Закройте его и повторите команду.")
    set_auto_login(target.account)
    try:
        mark_most_recent(steam / "config" / "loginusers.vdf", target.steam_id)
    except OSError as exc:
        log.warning("loginusers.vdf: %s", exc)
    subprocess.Popen([str(exe)], creationflags=CREATE_NO_WINDOW)
    ctx.dialog.last_app = "Steam"
    return ToolResult(True, f"Переключаю Steam на аккаунт {who}. Steam перезапускается.", {"account": who})
