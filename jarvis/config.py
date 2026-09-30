"""Конфигурация: секреты и параметры из .env, пользовательские данные из config/settings.json."""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
LOG_DIR = ROOT / "logs"
PLUGINS_DIR = ROOT / "plugins"
MODELS_DIR = ROOT / "models"

load_dotenv(ROOT / ".env")

log = logging.getLogger("jarvis.config")


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "да")


DEFAULT_SETTINGS: dict[str, Any] = {
    "assistant_name": "Джарвис",
    "wake_words": ["джарвис", "jarvis", "джарвиз", "жарвис", "джервис", "джарвис,"],
    "stt_language": "ru-RU",
    "home_city": "Москва",
    "search_url": "https://www.google.com/search?q={query}",
    "music": {
        "default_query": "lofi hip hop radio",
    },
    "sites": {
        "youtube": {"url": "https://www.youtube.com", "search": "https://www.youtube.com/results?search_query={query}",
                    "aliases": ["youtube", "ютуб", "ютюб", "ютубчик", "ютубе", "you tube"]},
        "google": {"url": "https://www.google.com", "search": "https://www.google.com/search?q={query}",
                   "aliases": ["google", "гугл", "гугле", "гугол"]},
        "yandex": {"url": "https://ya.ru", "search": "https://yandex.ru/search/?text={query}",
                   "aliases": ["яндекс", "yandex", "ya.ru"]},
        "vk": {"url": "https://vk.com", "search": "https://vk.com/search?c%5Bq%5D={query}",
               "aliases": ["вк", "вконтакте", "в контакте", "vk", "вэка"]},
        "github": {"url": "https://github.com", "search": "https://github.com/search?q={query}",
                   "aliases": ["github", "гитхаб", "гит хаб"]},
        "gmail": {"url": "https://mail.google.com", "aliases": ["gmail", "джимейл", "почту", "почта", "гугл почту"]},
        "wikipedia": {"url": "https://ru.wikipedia.org", "search": "https://ru.wikipedia.org/w/index.php?search={query}",
                      "aliases": ["википедию", "википедия", "wikipedia", "вики"]},
        "twitch": {"url": "https://www.twitch.tv", "search": "https://www.twitch.tv/search?term={query}",
                   "aliases": ["twitch", "твич"]},
        "chatgpt": {"url": "https://chatgpt.com", "aliases": ["chatgpt", "чат гпт", "чатгпт"]},
        "claude": {"url": "https://claude.ai", "aliases": ["claude", "клод"]},
        "maps": {"url": "https://yandex.ru/maps", "search": "https://yandex.ru/maps/?text={query}",
                 "aliases": ["карты", "карту", "яндекс карты"]},
        "translate": {"url": "https://translate.yandex.ru", "aliases": ["переводчик", "переводчике"]},
        "kinopoisk": {"url": "https://www.kinopoisk.ru", "search": "https://www.kinopoisk.ru/index.php?kp_query={query}",
                      "aliases": ["кинопоиск"]},
        "yandex_music": {"url": "https://music.yandex.ru", "search": "https://music.yandex.ru/search?text={query}",
                         "aliases": ["яндекс музыку", "яндекс музыка"]},
        "discord_web": {"url": "https://discord.com/app", "aliases": ["дискорд в браузере", "веб дискорд"]},
        "telegram_web": {"url": "https://web.telegram.org", "aliases": ["телеграм в браузере", "веб телеграм"]},
        "steam_store": {"url": "https://store.steampowered.com", "aliases": ["магазин стим", "стим магазин"]},
    },
    "apps": {
        "discord": {"aliases": ["discord", "дискорд", "дискорда", "дискорде", "дис"], "start_name": "Discord",
                    "process": ["Discord.exe"]},
        "telegram": {"aliases": ["telegram", "телеграм", "телеграмм", "телега", "телегу", "тг", "телеграмма",
                                 "ayugram", "аюграм"],
                     "start_name": "Telegram", "process": ["Telegram.exe", "AyuGram.exe", "Kotatogram.exe"]},
        "steam": {"aliases": ["steam", "стим", "стима", "стиме"], "start_name": "Steam", "process": ["steam.exe"]},
        "chrome": {"aliases": ["chrome", "хром", "хрома", "гугл хром", "google chrome"], "start_name": "Google Chrome",
                   "process": ["chrome.exe"]},
        "firefox": {"aliases": ["firefox", "фаерфокс", "файрфокс", "мозилла", "мозиллу", "огнелис"],
                    "start_name": "Firefox", "process": ["firefox.exe"]},
        "edge": {"aliases": ["edge", "эдж", "эйдж", "майкрософт эдж", "microsoft edge"], "start_name": "Microsoft Edge",
                 "process": ["msedge.exe"]},
        "yandex_browser": {"aliases": ["яндекс браузер", "яндекс-браузер"], "start_name": "Yandex",
                           "process": ["browser.exe"]},
        "opera": {"aliases": ["opera", "опера", "оперу"], "start_name": "Opera", "process": ["opera.exe"]},
        "spotify": {"aliases": ["spotify", "спотифай", "спотифая"], "start_name": "Spotify", "process": ["Spotify.exe"]},
        "vscode": {"aliases": ["vs code", "vscode", "вс код", "вскод", "visual studio code", "визуал студио код"],
                   "start_name": "Visual Studio Code", "process": ["Code.exe"]},
        "obs": {"aliases": ["obs", "обс"], "start_name": "OBS Studio", "process": ["obs64.exe"]},
        "notepad": {"aliases": ["блокнот", "notepad"], "launch": "notepad.exe", "process": ["notepad.exe", "Notepad.exe"]},
        "calculator": {"aliases": ["калькулятор", "calculator"], "launch": "calc.exe",
                       "process": ["CalculatorApp.exe", "calc.exe"]},
        "paint": {"aliases": ["paint", "пейнт", "паинт"], "launch": "mspaint.exe", "process": ["mspaint.exe"]},
        "task_manager": {"aliases": ["диспетчер задач", "диспетчер", "task manager"], "launch": "taskmgr.exe",
                         "process": ["Taskmgr.exe"]},
        "cmd": {"aliases": ["командную строку", "командная строка", "cmd", "консоль"], "launch": "cmd.exe",
                "process": ["cmd.exe"]},
        "powershell": {"aliases": ["powershell", "пауэршелл", "повершелл"], "launch": "powershell.exe",
                       "process": ["powershell.exe"]},
        "terminal": {"aliases": ["терминал", "terminal"], "launch": "wt.exe", "process": ["WindowsTerminal.exe"]},
        "settings": {"aliases": ["настройки", "параметры", "параметры windows", "настройки windows"],
                     "launch": "ms-settings:", "process": ["SystemSettings.exe"]},
        "control_panel": {"aliases": ["панель управления"], "launch": "control.exe", "process": []},
        "explorer": {"aliases": ["проводник", "explorer"], "launch": "explorer.exe", "process": []},
        "word": {"aliases": ["word", "ворд"], "start_name": "Word", "process": ["WINWORD.EXE"]},
        "excel": {"aliases": ["excel", "эксель"], "start_name": "Excel", "process": ["EXCEL.EXE"]},
    },
    "folders": {
        "downloads": {"target": "shell:Downloads", "aliases": ["загрузк", "скачанн", "downloads", "закачк"]},
        "documents": {"target": "shell:Personal", "aliases": ["документ", "documents"]},
        "desktop": {"target": "shell:Desktop", "aliases": ["рабочий стол", "рабочем столе", "рабочего стола", "desktop"]},
        "pictures": {"target": "shell:My Pictures", "aliases": ["изображени", "картинк", "фотографи", "pictures"]},
        "music": {"target": "shell:My Music", "aliases": ["папку музык", "папка музык", "мою музык", "мои музык"]},
        "videos": {"target": "shell:My Video", "aliases": ["папку видео", "мои видео", "видеозапис"]},
        "screenshots": {"target": "shell:My Pictures\\Screenshots", "aliases": ["скриншот", "снимки экрана"]},
        "computer": {"target": "shell:MyComputerFolder", "aliases": ["этот компьютер", "мой компьютер"]},
        "recycle": {"target": "shell:RecycleBinFolder", "aliases": ["корзин"]},
        "startup": {"target": "shell:Startup", "aliases": ["автозагрузк"]},
        "appdata": {"target": "%APPDATA%", "aliases": ["appdata", "апдату", "ап дата"]},
        "explorer": {"target": "", "aliases": ["проводник"]},
    },
    "spotify": {
        "default": {"uri": "spotify:album:4ydl8Ci7OsndhI2ALnrpIv",
                    "name": "музыку из «Железного человека» (AC/DC — Iron Man 2)"},
    },
    "voice_startup": {
        "sample": "Джарвис - приветствие.wav",
    },
    "steam": {
        "default_account": "",
        "game_accounts": {},
    },
    "vpn": {
        "type": "auto",
        "name": "",
        "on_command": "",
        "off_command": "",
        "status_command": "",
        "app": "",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class Settings:
    """Пользовательские настройки (JSON). Потокобезопасное чтение/запись."""

    def __init__(self, path: Path = SETTINGS_FILE):
        self.path = path
        self._lock = threading.RLock()
        self.data: dict[str, Any] = copy.deepcopy(DEFAULT_SETTINGS)
        self.user_data: dict[str, Any] = {}
        if path.exists():
            try:
                self.user_data = json.loads(path.read_text(encoding="utf-8-sig"))
                self.data = _deep_merge(self.data, self.user_data)
            except Exception as exc:
                log.error("Не удалось прочитать %s: %s — используются значения по умолчанию", path, exc)
        else:
            self._write_template()

    def _write_template(self) -> None:
        template = {
            "_comment": "Переопределения настроек Jarvis. Любые ключи из DEFAULT_SETTINGS (jarvis/config.py) "
                        "можно добавить сюда — они сольются со значениями по умолчанию.",
            "home_city": DEFAULT_SETTINGS["home_city"],
            "music": DEFAULT_SETTINGS["music"],
            "vpn": DEFAULT_SETTINGS["vpn"],
            "apps": {},
            "sites": {},
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
            self.user_data = template
        except OSError as exc:
            log.warning("Не удалось создать %s: %s", self.path, exc)

    def get(self, dotted: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self.data
            for part in dotted.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return node

    def set(self, dotted: str, value: Any) -> None:
        """Изменить значение и сохранить его в пользовательский файл."""
        with self._lock:
            for target in (self.data, self.user_data):
                node = target
                parts = dotted.split(".")
                for part in parts[:-1]:
                    node = node.setdefault(part, {})
                node[parts[-1]] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.user_data, ensure_ascii=False, indent=2), encoding="utf-8")
