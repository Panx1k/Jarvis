"""Намерение ответа (Response Intent): что по смыслу сообщает JARVIS в этом ходе.

Намерение определяется по РЕЗУЛЬТАТУ действия, а не только по запросу пользователя:
«включи VPN» → vpn_on ok → VPN_CONNECTED; уже был включён → VPN_ALREADY_ENABLED; ошибка → VPN_ERROR.
dynamic=True — в ответе есть конкретные данные (время, число, название трека, причина ошибки): готовая запись
их не передаст, поэтому такой ответ заменяется записью только при точном совпадении по смыслу.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

PARENTS: dict[str, list[str]] = {
    "VPN_CONNECTED": ["TASK_COMPLETED"],
    "VPN_DISCONNECTED": ["TASK_COMPLETED"],
    "APPLICATION_OPENED": ["TASK_STARTED"],
    "GAME_LAUNCHING": ["APPLICATION_OPENED"],
    "APPLICATION_CLOSED": ["TASK_COMPLETED"],
    "SITE_OPENED": ["TASK_COMPLETED"],
    "MEDIA_CONTROL": ["TASK_COMPLETED"],
    "WINDOW_CONTROL": ["TASK_COMPLETED"],
    "VOLUME_SET": ["TASK_COMPLETED"],
    "SCREENSHOT_TAKEN": ["TASK_COMPLETED"],
    "FOLDER_CREATED": ["TASK_COMPLETED"],
    "DOWNLOAD_STARTED": ["TASK_STARTED"],
    "TASK_COMPLETED": ["ACKNOWLEDGEMENT"],
    "TASK_STARTED": ["ACKNOWLEDGEMENT"],
    "CANCELLED": ["ACKNOWLEDGEMENT"],
    "CONFIRMATION": ["ACKNOWLEDGEMENT"],
    "GREETING_MORNING": ["GREETING"],
    "NEWS": ["ACKNOWLEDGEMENT"],
    "WELCOME_BACK": ["GREETING"],
}
ACTION_INTENTS = {"VPN_CONNECTED", "VPN_DISCONNECTED", "APPLICATION_OPENED", "GAME_LAUNCHING", "APPLICATION_CLOSED",
                  "SITE_OPENED", "MEDIA_CONTROL", "WINDOW_CONTROL", "VOLUME_SET", "SCREENSHOT_TAKEN", "FOLDER_CREATED",
                  "DOWNLOAD_STARTED", "TASK_COMPLETED", "TASK_STARTED", "SHUTDOWN"}

TOOL_INTENTS: dict[str, str] = {
    "open_app": "APPLICATION_OPENED", "launch_game": "GAME_LAUNCHING", "steam_open": "APPLICATION_OPENED",
    "close_app": "APPLICATION_CLOSED", "kill_app": "APPLICATION_CLOSED",
    "open_site": "SITE_OPENED", "open_url": "SITE_OPENED", "open_folder": "TASK_COMPLETED", "open_file": "TASK_COMPLETED",
    "vpn_on": "VPN_CONNECTED", "vpn_off": "VPN_DISCONNECTED",
    "pause_media": "MEDIA_CONTROL", "resume_media": "MEDIA_CONTROL", "play_media": "MEDIA_CONTROL",
    "next_track": "MEDIA_CONTROL", "previous_track": "MEDIA_CONTROL", "stop_media": "MEDIA_CONTROL",
    "mute": "MEDIA_CONTROL", "unmute": "MEDIA_CONTROL",
    "set_volume": "VOLUME_SET", "change_volume": "VOLUME_SET",
    "window_minimize": "WINDOW_CONTROL", "window_maximize": "WINDOW_CONTROL", "window_show": "WINDOW_CONTROL",
    "show_desktop": "WINDOW_CONTROL",
    "take_screenshot": "SCREENSHOT_TAKEN", "create_folder": "FOLDER_CREATED", "shutdown_computer": "SHUTDOWN",
    "steam_install": "DOWNLOAD_STARTED",
    "discord_toggle_mute": "TASK_COMPLETED", "discord_toggle_deafen": "TASK_COMPLETED",
    "discord_answer_call": "TASK_COMPLETED", "discord_decline_call": "TASK_COMPLETED", "discord_open": "TASK_COMPLETED",
    "lock_screen": "TASK_COMPLETED", "press_key": "TASK_COMPLETED", "type_text": "TASK_COMPLETED",
    "send_enter": "TASK_COMPLETED", "delete_to_recycle_bin": "TASK_COMPLETED", "cancel_shutdown": "TASK_COMPLETED",
    "spotify_like": "TASK_COMPLETED", "spotify_queue": "TASK_COMPLETED", "spotify_shuffle": "TASK_COMPLETED",
    "spotify_repeat": "TASK_COMPLETED", "spotify_volume": "VOLUME_SET",
}
DYNAMIC_TOOLS = {"get_time", "get_date", "get_weather", "get_volume", "media_status", "vpn_status", "list_apps",
                 "steam_accounts", "steam_switch_account", "steam_games", "steam_game_info", "steam_store",
                 "steam_uninstall", "search_web", "search_youtube", "play_youtube", "youtube_latest", "open_result",
                 "search_files", "spotify_play", "spotify_play_liked", "spotify_now_playing", "spotify_playlists",
                 "spotify_connect", "discord_status", "discord_send_message", "run_command", "restart_computer",
                 "sleep_computer", "create_preset", "delete_preset", "list_presets", "jarvis_update_check"}
INFO_INTENTS = {"get_time": "TIME_RESPONSE", "get_date": "TIME_RESPONSE", "vpn_status": "VPN_STATUS",
                "get_weather": "INFO_RESPONSE", "get_volume": "SYSTEM_STATUS", "steam_games": "INFO_RESPONSE"}

THANKS_RE = re.compile(r"\b(?:спасибо|спс|благодарю|thank|thanks)\b", re.I)
GREETING_RE = re.compile(r"\b(?:привет|здравствуй\w*|доброе утро|добрый (?:день|вечер)|доброй ночи|hello|hi|hey|"
                         r"good (?:morning|evening))\b", re.I)
MORNING_RE = re.compile(r"\b(?:доброе утро|good morning|с добрым утром)\b", re.I)
ACK_RE = re.compile(r"^(?:хорошо|понял|поняла|ладно|ок|окей|ok|okay|отлично|принято|ясно)[\s.,!]*$", re.I)
DIAG_RE = re.compile(r"\b(?:диагностик\w*|проверь систем\w*)\b", re.I)


@dataclass
class ResponseIntent:
    intent: str
    dynamic: bool = False
    tool: str | None = None
    ok: bool | None = None
    parents: list[str] = field(default_factory=list)

    def ancestors(self) -> list[tuple[str, int]]:
        """Более общие намерения с глубиной: [(TASK_COMPLETED, 1), (ACKNOWLEDGEMENT, 2)]."""
        out, seen, level = [], {self.intent}, [self.intent]
        depth = 0
        while level:
            depth += 1
            nxt = []
            for it in level:
                for parent in PARENTS.get(it, []):
                    if parent not in seen:
                        seen.add(parent)
                        out.append((parent, depth))
                        nxt.append(parent)
            level = nxt
        return out


def _short(text: str) -> bool:
    words = re.findall(r"\w+", text or "")
    return len(words) <= 8 and not re.search(r"\d", text or "") and "?" not in (text or "")


def classify(calls: list, reply_text: str = "", spoken: str = "", user_text: str = "", handled: bool = True,
             source: str = "rules", now: dt.datetime | None = None) -> ResponseIntent:
    """Намерение ответа по результату хода (последнее действие главное)."""
    now = now or dt.datetime.now()
    if source == "error":
        return ResponseIntent("AI_UNAVAILABLE", dynamic=True)
    if not handled and not calls:
        return ResponseIntent("UNKNOWN")
    if calls:
        last = calls[-1]
        name, r = last.tool, last.result
        msg = (r.message or "").lower().replace("ё", "е")
        if r.data.get("pending") or r.followup:
            return ResponseIntent("CONFIRMATION_REQUEST", dynamic=True, tool=name, ok=False)
        if not r.ok:
            if name.startswith("news_"):
                return ResponseIntent("NEWS_UNAVAILABLE" if r.data.get("offline") else "ERROR", dynamic=True,
                                      tool=name, ok=False)
            if name in ("vpn_on", "vpn_off"):
                return ResponseIntent("VPN_ERROR", dynamic=True, tool=name, ok=False)
            if name in ("search_web", "search_youtube", "search_files") and re.search(r"ничего|не наш", msg):
                return ResponseIntent("NO_RESULTS", dynamic=True, tool=name, ok=False)
            return ResponseIntent("ERROR", dynamic=True, tool=name, ok=False)
        if name == "news_get":
            return ResponseIntent("NEWS", dynamic=True, tool=name, ok=True)
        if name == "news_more" and r.data.get("empty"):
            return ResponseIntent("NO_MORE_INFO", tool=name, ok=True)
        if name in ("news_more", "news_details", "news_source"):
            return ResponseIntent("NEWS_DETAILS", dynamic=True, tool=name, ok=True)
        if name == "news_open":
            return ResponseIntent("SITE_OPENED", tool=name, ok=True)
        if name == "vpn_on" and "уже" in msg:
            return ResponseIntent("VPN_ALREADY_ENABLED", tool=name, ok=True)
        if name == "vpn_off" and "уже" in msg:
            return ResponseIntent("VPN_ALREADY_DISABLED", tool=name, ok=True)
        if name == "launch_game" and r.data.get("account"):
            return ResponseIntent("GAME_LAUNCHING", dynamic=True, tool=name, ok=True)
        if name in TOOL_INTENTS:
            return ResponseIntent(TOOL_INTENTS[name], tool=name, ok=True)
        if name in DYNAMIC_TOOLS or name in INFO_INTENTS:
            return ResponseIntent(INFO_INTENTS.get(name, "INFO_RESPONSE"), dynamic=True, tool=name, ok=True)
        return ResponseIntent("TASK_COMPLETED", tool=name, ok=True)

    from jarvis.voice import responder

    text = spoken or reply_text
    if text.strip() in (responder.CANCELLED["ru"], responder.CANCELLED["en"]):
        return ResponseIntent("CANCELLED")
    user = user_text or ""
    brief = _short(text)
    if THANKS_RE.search(user):
        return ResponseIntent("THANKS", dynamic=not brief)
    if GREETING_RE.search(user):
        morning = bool(MORNING_RE.search(user)) or (MORNING_RE.search(text) and 4 <= now.hour < 12)
        return ResponseIntent("GREETING_MORNING" if morning else "GREETING", dynamic=not brief)
    if DIAG_RE.search(user):
        return ResponseIntent("DIAGNOSTICS_STARTED", dynamic=not brief)
    if ACK_RE.match(user.strip()):
        return ResponseIntent("ACKNOWLEDGEMENT", dynamic=not brief)
    return ResponseIntent("CHAT", dynamic=True)
