"""Короткие голосовые ответы.

Полный ответ (с деталями) остаётся в чате, а вслух произносится короткая естественная фраза:
«YouTube открыт.», «VPN включён.», «Готово.», «Я не смог найти это приложение.»
Технические ошибки не зачитываются. Поддерживаются русский и английский.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from jarvis.tools.base import ToolResult


@dataclass
class ToolCall:
    tool: str
    args: dict
    result: ToolResult


ACK = {"ru": "Да, сэр. Слушаю.", "en": "Yes, sir. I'm listening."}
NOT_HEARD = {"ru": "Не расслышал, повторите.", "en": "Sorry, I didn't catch that."}
NOT_UNDERSTOOD = {"ru": "Не понял команду.", "en": "I didn't understand the command."}
STT_DOWN = {"ru": "Не могу распознать речь, нет связи.", "en": "Speech recognition is offline."}
CONFIRM_SUFFIX = {"ru": " Подтвердите: да или нет.", "en": " Please confirm: yes or no."}
CANCELLED = {"ru": "Отменено.", "en": "Cancelled."}
DONE = {"ru": "Готово.", "en": "Done."}
AI_DOWN = {"ru": "Мой облачный интеллект сейчас недоступен.", "en": "My AI core is unavailable right now."}
FAILED = {"ru": "Не получилось.", "en": "That didn't work."}

_SITE_NAMES = {"youtube": "YouTube", "google": "Google", "vk": "ВКонтакте", "github": "GitHub", "gmail": "Почта"}

PHRASES: dict[str, tuple[str, str, str, str]] = {
    "open_app": ("{app} запущен.", "{app} is up.", "Я не смог найти это приложение.", "I couldn't find that app."),
    "close_app": ("{app} закрыт.", "{app} closed.", "Это приложение не запущено.", "That app isn't running."),
    "kill_app": ("Процесс завершён.", "Process terminated.", "Не удалось завершить процесс.", "Couldn't kill it."),
    "open_site": ("{site} открыт.", "{site} is open.", "Я не знаю такой сайт.", "I don't know that site."),
    "open_url": ("Открыто.", "Opened.", "Не удалось открыть ссылку.", "Couldn't open the link."),
    "search_web": ("Вот результаты.", "Here are the results.", "Поиск не удался.", "The search failed."),
    "search_youtube": ("Нашёл.", "Found it.", "Ничего не нашёл.", "Nothing found."),
    "play_youtube": ("Включаю.", "Playing.", "Не удалось включить видео.", "Couldn't play the video."),
    "youtube_latest": ("Включаю последний ролик.", "Playing the latest video.", "Я не нашёл этот канал.",
                       "I couldn't find that channel."),
    "open_result": ("Включаю.", "Playing.", "Сначала нужно что-нибудь найти.", "Search for something first."),
    "play_media": ("Включаю.", "Playing.", "Не удалось включить.", "Couldn't start playback."),
    "pause_media": ("Пауза.", "Paused.", "Сейчас ничего не играет.", "Nothing is playing."),
    "resume_media": ("Продолжаю.", "Resuming.", "Нечего продолжать.", "Nothing to resume."),
    "next_track": ("Следующий.", "Next one.", FAILED["ru"], FAILED["en"]),
    "previous_track": ("Предыдущий.", "Previous one.", FAILED["ru"], FAILED["en"]),
    "stop_media": ("Остановлено.", "Stopped.", FAILED["ru"], FAILED["en"]),
    "set_volume": ("Громкость {percent} процентов.", "Volume {percent} percent.", FAILED["ru"], FAILED["en"]),
    "change_volume": ("Готово.", "Done.", FAILED["ru"], FAILED["en"]),
    "mute": ("Звук выключен.", "Muted.", FAILED["ru"], FAILED["en"]),
    "unmute": ("Звук включён.", "Sound is on.", FAILED["ru"], FAILED["en"]),
    "vpn_on": ("VPN включён.", "VPN is on.", "Не удалось включить VPN.", "Couldn't turn on the VPN."),
    "vpn_off": ("VPN выключен.", "VPN is off.", "Не удалось выключить VPN.", "Couldn't turn off the VPN."),
    "open_folder": ("Открываю.", "Opening.", "Я не нашёл эту папку.", "I couldn't find that folder."),
    "open_file": ("Открываю.", "Opening.", "Я не нашёл этот файл.", "I couldn't find that file."),
    "search_files": ("Нашёл.", "Found them.", "Таких файлов нет.", "No such files."),
    "create_folder": ("Папка создана.", "Folder created.", "Не удалось создать папку.", "Couldn't create the folder."),
    "delete_to_recycle_bin": ("Перемещено в корзину.", "Moved to the recycle bin.", FAILED["ru"], FAILED["en"]),
    "type_text": ("Готово.", "Done.", FAILED["ru"], FAILED["en"]),
    "send_enter": ("Отправил.", "Sent.", FAILED["ru"], FAILED["en"]),
    "launch_game": ("Запускаю {app}.", "Launching {app}.", "Я не нашёл эту игру.", "I couldn't find that game."),
    "discord_toggle_mute": ("Микрофон в Discord переключён.", "Discord mic toggled.", "Discord не запущен.",
                            "Discord isn't running."),
    "discord_toggle_deafen": ("Звук в Discord переключён.", "Discord sound toggled.", "Discord не запущен.",
                              "Discord isn't running."),
    "press_key": ("Готово.", "Done.", "Не понял, какую клавишу нажать.", "I didn't get which key."),
    "lock_screen": ("Блокирую.", "Locking.", FAILED["ru"], FAILED["en"]),
    "take_screenshot": ("Скриншот сделан.", "Screenshot taken.", FAILED["ru"], FAILED["en"]),
    "cancel_shutdown": ("Выключение отменено.", "Shutdown cancelled.", "Выключение не запланировано.",
                        "No shutdown is scheduled."),
    "open_settings": ("Открываю настройки.", "Opening settings.", FAILED["ru"], FAILED["en"]),
    "run_command": ("Команда выполнена.", "Command executed.", "Команда завершилась с ошибкой.", "The command failed."),
    "shutdown_computer": ("Компьютер скоро выключится.", "Shutting down shortly.", FAILED["ru"], FAILED["en"]),
    "restart_computer": ("Перезагрузка скоро начнётся.", "Restarting shortly.", FAILED["ru"], FAILED["en"]),
    "sleep_computer": ("Перехожу в спящий режим.", "Going to sleep.", FAILED["ru"], FAILED["en"]),
}
INFORMATIVE = {"get_time", "get_date", "get_weather", "get_volume", "media_status", "vpn_status", "list_apps",
               "steam_accounts", "steam_switch_account", "steam_games", "steam_game_info", "steam_store",
               "steam_install", "steam_uninstall", "steam_open", "launch_game",
               "spotify_connect", "spotify_play", "spotify_play_liked", "spotify_now_playing", "spotify_like",
               "spotify_queue", "spotify_shuffle", "spotify_repeat", "spotify_volume", "spotify_playlists",
               "discord_open", "discord_send_message", "discord_answer_call", "discord_decline_call", "discord_status",
               "discord_screen_share", "discord_camera", "discord_disconnect", "discord_toggle_mute",
               "discord_toggle_deafen",
               "window_minimize", "window_maximize", "window_show", "show_desktop", "news_open",
               "run_preset", "create_preset", "delete_preset", "list_presets", "set_theme", "jarvis_update_check",
               "jarvis_update"}


NEWS_TOOLS = {"news_get", "news_more", "news_details", "news_source"}
NEWS_SPOKEN_LIMIT = 1500


def spoken_news(text: str) -> str:
    from jarvis.utils.text import strip_markdown

    text = strip_markdown(text or "").strip()
    if len(text) <= NEWS_SPOKEN_LIMIT:
        return text
    cut = text[:NEWS_SPOKEN_LIMIT]
    return cut[:max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? ")) + 1] or cut


def _fill(template: str, call: ToolCall) -> str:
    args = dict(call.args)
    data = call.result.data or {}
    app = data.get("app") or args.get("app", "")
    site = data.get("site") or args.get("site", "")
    site = _SITE_NAMES.get(site.lower(), site[:1].upper() + site[1:]) if site else ""
    values = {"app": app[:1].upper() + app[1:] if app else "", "site": site,
              "percent": data.get("volume", args.get("percent", ""))}
    text = template.format(**values)
    return re.sub(r"^\s+", "", text) or DONE["ru"]


def _first_sentences(text: str, limit: int = 180) -> str:
    """Не более двух коротких предложений."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    out = ""
    for p in parts[:2]:
        if len(out) + len(p) > limit and out:
            break
        out = f"{out} {p}".strip()
    return out[:limit]


def _english_info(call: ToolCall) -> str | None:
    import datetime as dt

    now = dt.datetime.now()
    if call.tool == "get_time":
        return f"It's {now:%H:%M}."
    if call.tool == "get_date":
        return f"Today is {now:%A, %B %d}."
    if call.tool == "get_volume" and "volume" in call.result.data:
        return f"Volume is {call.result.data['volume']} percent."
    return None


def add_sir(text: str, lang: str = "ru") -> str:
    """«YouTube открыт.» → «YouTube открыт, сэр.» — обращение в духе JARVIS (если его ещё нет)."""
    word = "sir" if lang == "en" else "сэр"
    if not text or len(text) > 160 or re.search(rf"\b{word}\b", text, re.I):
        return text
    m = re.search(r"[.!]+\s*$", text)
    if not m:
        return text
    return text[:m.start()].rstrip(" ,") + f", {word}" + text[m.start():].rstrip()


def compose(reply_text: str, calls: list[ToolCall], lang: str = "ru", handled: bool = True) -> str:
    """Короткая фраза для озвучки по итогам хода."""
    lang = "en" if lang == "en" else "ru"
    if calls and calls[-1].tool in NEWS_TOOLS:
        return _compose(reply_text, calls, lang, handled)
    if calls and not (calls[-1].result.data.get("pending") or calls[-1].result.followup):
        return add_sir(_compose(reply_text, calls, lang, handled), lang)
    return _compose(reply_text, calls, lang, handled)


def _compose(reply_text: str, calls: list[ToolCall], lang: str, handled: bool) -> str:
    if calls:
        last = calls[-1]
        r = last.result
        if last.tool in NEWS_TOOLS:
            return spoken_news(r.message)
        if r.data.get("pending") or r.followup:
            question = re.sub(r"\s*Скажите «да» или «нет»\.?", "", r.message).strip()
            if lang == "en":
                return "This needs your confirmation. Yes or no?"
            return _first_sentences(question, 140) + CONFIRM_SUFFIX[lang]
        if last.tool in INFORMATIVE:
            if lang == "en":
                return _english_info(last) or _first_sentences(r.message)
            return _first_sentences(r.message, 220)
        phrases = PHRASES.get(last.tool)
        if phrases:
            ok_ru, ok_en, fail_ru, fail_en = phrases
            template = (ok_en if lang == "en" else ok_ru) if r.ok else (fail_en if lang == "en" else fail_ru)
            return _fill(template, last)
        return DONE[lang] if r.ok else FAILED[lang]
    if not handled:
        return NOT_UNDERSTOOD[lang]
    if len(reply_text) > 220:
        brief = _first_sentences(reply_text, 160)
        return brief + (" Подробности на экране." if lang == "ru" else " Details are on screen.")
    return _first_sentences(reply_text, 220) or DONE[lang]
