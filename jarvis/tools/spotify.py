"""Spotify: включить трек/исполнителя/альбом/плейлист по названию, «мои лайки», очередь, лайк, что играет,
перемешивание, повтор, громкость Spotify, список плейлистов.

Полная интеграция — через Spotify Web API (services/spotify.py, нужен SPOTIFY_CLIENT_ID и однократный вход).
Без подключения инструменты делают, что могут, локально: поиск в приложении, горячие клавиши Spotify
(Alt+Shift+B — лайк, Ctrl+S — перемешивание, Ctrl+R — повтор), а «что играет» — через системный медиаплеер.
Пауза/продолжить/следующий трек — общие инструменты pause_media/resume_media/next_track (работают и со Spotify).
"""
from __future__ import annotations

import os
import random
import time
import urllib.parse

from jarvis.services import spotify as sp
from jarvis.tools.base import ToolResult, tool
from jarvis.utils import winapi
from jarvis.utils.text import normalize, similarity

SPOTIFY_EXE = {"spotify.exe"}
_SP = r"(?:в\s+)?(?:спотифа\w*|спотик\w*|spotify)"
NOT_CONNECTED = ("Spotify не подключён к JARVIS, поэтому включить по названию не могу — открыл поиск в Spotify. "
                 "Чтобы подключить: впишите SPOTIFY_CLIENT_ID в .env и скажите «подключи Spotify».")


def _client() -> sp.Spotify | None:
    c = sp.get()
    return c if c.connected else None


def _hotkey(vks: list[int]) -> bool:
    hwnd = winapi.find_window("", SPOTIFY_EXE)
    if not hwnd:
        return False
    previous = winapi.user32.GetForegroundWindow()
    if not winapi.focus_window(hwnd):
        return False
    winapi.press_combo(vks)
    time.sleep(0.15)
    if previous and previous != hwnd:
        winapi.focus_window(previous)
    return True


def _describe(item: dict) -> str:
    artists = ", ".join(a["name"] for a in item.get("artists", [])[:2])
    return f"{item.get('name', '')}" + (f" — {artists}" if artists else "")


def _err(exc: Exception) -> ToolResult:
    return ToolResult(False, str(exc) if isinstance(exc, sp.SpotifyError) else "Spotify не ответил.")


@tool("spotify_connect", "Подключить Spotify к JARVIS (однократный вход через браузер).",
      announce="Подключаю Spotify", category="spotify",
      patterns=[r"^(?:подключи|авторизуй|привяжи)\s+" + _SP + "$"])
def spotify_connect(ctx) -> ToolResult:
    client = sp.get()
    if client.connected:
        try:
            name = client.me().get("display_name") or ""
        except sp.SpotifyError as exc:
            return _err(exc)
        return ToolResult(True, f"Spotify уже подключён{' (' + name + ')' if name else ''}.")
    try:
        return ToolResult(True, client.login_async())
    except sp.SpotifyError as exc:
        return _err(exc)


@tool("spotify_play", "Включить в Spotify трек, исполнителя, альбом или плейлист по названию. kind: track (песня), "
      "artist (исполнитель), album (альбом), playlist (плейлист — сначала ищется среди плейлистов пользователя).",
      params={"query": {"type": "string", "description": "Что включить"},
              "kind": {"type": "string", "enum": ["track", "artist", "album", "playlist"],
                       "description": "Тип; по умолчанию track"}},
      required=["query"], announce="Включаю в Spotify {query}", category="spotify",
      patterns=[r"^(?:включи|поставь|запусти|играй)\s+(?:песню\s+|трек\s+)?(?P<query>.+?)\s+" + _SP + "$",
                r"^(?:включи|поставь)\s+" + _SP + r"\s+(?:песню\s+|трек\s+)?(?P<query>.+)$"])
def spotify_play(ctx, query: str, kind: str = "track") -> ToolResult:
    import re

    if re.fullmatch(r"\s*(?:мне\s+)?(?:что[- ]?(?:нибудь|то)|музыку|музон|песни|песню|трек|музыка|"
                    r"что[- ]?(?:нибудь|то)\s+(?:на|в)|any(?:thing)?|some(?:thing)?|music)\s*", query or "", re.I):
        return spotify_play_default(ctx)
    client = _client()
    if not client:
        os.startfile("spotify:search:" + urllib.parse.quote(query))
        return ToolResult(False, NOT_CONNECTED)
    kind = kind if kind in ("track", "artist", "album", "playlist") else "track"
    try:
        if kind == "playlist":
            mine = client.my_playlists()
            best = max(mine, key=lambda p: similarity(query, p.get("name", "")), default=None)
            if best and (similarity(query, best["name"]) >= 0.75 or normalize(query) in normalize(best["name"])):
                st = client.play({"context_uri": best["uri"]})
                return _playing(ctx, st, f"ваш плейлист «{best['name']}»")
        items = client.search(query, kind)
        if not items:
            return ToolResult(False, f"В Spotify не нашёл «{query}».")
        item = items[0]
        if kind == "track":
            st = client.play({"uris": [item["uri"]]})
            what = _describe(item)
        else:
            st = client.play({"context_uri": item["uri"]})
            what = item.get("name", query)
    except sp.SpotifyError as exc:
        return _err(exc)
    return _playing(ctx, st, what)


def _playing(ctx, state: dict, what: str) -> ToolResult:
    """Ответ по факту: что реально заиграло (Spotify уже подтвердил воспроизведение)."""
    item = (state or {}).get("item") or {}
    now = _describe(item) if item else what
    ctx.dialog.now_playing = now
    extra = f" Сейчас играет: {now}." if item and normalize(now) != normalize(what) else ""
    return ToolResult(True, f"Играет в Spotify: {what}.{extra}", {"app": "Spotify", "playing": now})


_VAGUE = r"(?:что[- ]?(?:нибудь|то)|музыку|музон|песни|трек\w*)"


@tool("spotify_play_default", "Включить музыку в Spotify, когда пользователь НЕ назвал, что именно "
      "(«включи что-нибудь в Spotify», «включи музыку в Spotify», «музыку из Железного человека»). "
      "По умолчанию — саундтрек «Железного человека» (AC/DC, Iron Man 2).",
      announce="Включаю музыку в Spotify", category="spotify",
      patterns=[r"^(?:включи|поставь|вруби|запусти)\s+(?:мне\s+)?(?:что[- ]?(?:нибудь|то)\s+)?" + _VAGUE + r"?\s*"
                + r"(?:(?:в|на|из)\s+)?(?:спотифа\w*|спотик\w*|spotify)$",
                r"^(?:включи|поставь|вруби)\s+(?:мне\s+)?(?:музыку|песни|саундтрек)\s+(?:из\s+)?(?:фильма\s+)?"
                r"(?:«)?(?:железн\w+ человек\w*|iron man)(?:»)?(?:\s+(?:в|на)\s+(?:спотифа\w*|spotify))?$"])
def spotify_play_default(ctx) -> ToolResult:
    default = (ctx.settings.get("spotify.default", {}) if getattr(ctx, "settings", None) else {}) or {}
    uri = default.get("uri") or "spotify:album:4ydl8Ci7OsndhI2ALnrpIv"
    name = default.get("name") or "музыку из «Железного человека»"
    client = _client()
    if not client:
        os.startfile(uri)
        return ToolResult(False, f"Открыл {name} в Spotify — нажмите Play. Чтобы включалось само, подключите Spotify.")
    try:
        st = client.play({"context_uri": uri} if ":track:" not in uri else {"uris": [uri]})
    except sp.SpotifyError as exc:
        return _err(exc)
    return _playing(ctx, st, name)


@tool("spotify_play_liked", "Включить в Spotify «Любимые треки» (лайкнутые песни), вперемешку — только если "
      "пользователь прямо попросил свои любимые/лайкнутые треки.",
      announce="Включаю ваши любимые треки", category="spotify",
      patterns=[r"^(?:включи|поставь)\s+(?:мои\s+)?(?:любимые|лайкнутые|сохран[её]нные)\s+(?:треки|песни)"
                r"(?:\s+" + _SP + ")?$", r"^(?:включи|поставь)\s+(?:мои\s+)?лайки(?:\s+" + _SP + ")?$"])
def spotify_play_liked(ctx) -> ToolResult:
    client = _client()
    if not client:
        os.startfile("spotify:collection:tracks")
        return ToolResult(False, "Открыл «Любимые треки» в Spotify. Чтобы включать их голосом, подключите Spotify.")
    try:
        page = client.api("GET", "/me/tracks", params={"limit": 50}) or {}
        uris = [i["track"]["uri"] for i in page.get("items", []) if i.get("track")]
        if not uris:
            return ToolResult(False, "В «Любимых треках» пусто.")
        random.shuffle(uris)
        st = client.play({"uris": uris})
    except sp.SpotifyError as exc:
        return _err(exc)
    return _playing(ctx, st, "ваши любимые треки вперемешку")


@tool("spotify_now_playing", "Что сейчас играет в Spotify (трек, исполнитель, альбом).",
      announce="Смотрю, что играет", category="spotify",
      patterns=[r"^(?:что|какая|какой)\s+(?:сейчас\s+)?(?:играет|за песня|за трек|песня|трек)(?:\s+(?:играет\s+)?"
                + _SP + ")?$"])
def spotify_now_playing(ctx) -> ToolResult:
    client = _client()
    if not client:
        from jarvis.tools.base import registry
        return registry.call("media_status", {}, ctx)
    try:
        cur = client.current()
    except sp.SpotifyError as exc:
        return _err(exc)
    if not cur or not cur.get("item"):
        return ToolResult(False, "В Spotify сейчас ничего не играет.")
    item = cur["item"]
    what = _describe(item)
    album = (item.get("album") or {}).get("name", "")
    state = "играет" if cur.get("is_playing") else "на паузе"
    return ToolResult(True, f"Сейчас {state}: {what}" + (f", альбом «{album}»" if album else "") + ".",
                      {"playing": what})


@tool("spotify_like", "Добавить текущий трек Spotify в «Любимые треки» (лайк).", announce="Ставлю лайк",
      category="spotify",
      patterns=[r"^(?:лайкни|поставь лайк|добавь в (?:любимые|избранное)|сохрани)(?:\s+(?:эту\s+)?(?:песню|трек))?"
                r"(?:\s+" + _SP + ")?$"])
def spotify_like(ctx) -> ToolResult:
    client = _client()
    if not client:
        if _hotkey([0x12, 0x10, 0x42]):
            return ToolResult(True, "Поставил лайк текущему треку в Spotify.")
        return ToolResult(False, "Spotify не запущен.")
    try:
        cur = client.current()
        if not cur or not cur.get("item"):
            return ToolResult(False, "Сейчас ничего не играет.")
        item = cur["item"]
        client.api("PUT", "/me/tracks", params={"ids": item["id"]})
    except sp.SpotifyError as exc:
        return _err(exc)
    return ToolResult(True, f"Добавил в любимые: {_describe(item)}.")


@tool("spotify_queue", "Добавить трек в очередь Spotify (сыграет следующим).",
      params={"query": {"type": "string", "description": "Название трека"}}, required=["query"],
      announce="Добавляю в очередь {query}", category="spotify",
      patterns=[r"^(?:добавь|поставь)\s+(?P<query>.+?)\s+в\s+очередь(?:\s+" + _SP + ")?$"])
def spotify_queue(ctx, query: str) -> ToolResult:
    client = _client()
    if not client:
        return ToolResult(False, "Для очереди нужно подключить Spotify: SPOTIFY_CLIENT_ID в .env и «подключи Spotify».")
    try:
        items = client.search(query, "track")
        if not items:
            return ToolResult(False, f"В Spotify не нашёл «{query}».")
        client.player("POST", "/queue", params={"uri": items[0]["uri"]})
    except sp.SpotifyError as exc:
        return _err(exc)
    return ToolResult(True, f"Добавил в очередь: {_describe(items[0])}.")


@tool("spotify_shuffle", "Включить или выключить перемешивание в Spotify.",
      params={"on": {"type": "boolean", "description": "true — включить, false — выключить"}}, required=["on"],
      announce="Перемешивание", category="spotify")
def spotify_shuffle(ctx, on: bool = True) -> ToolResult:
    client = _client()
    if not client:
        if _hotkey([0x11, 0x53]):
            return ToolResult(True, "Переключил перемешивание в Spotify.")
        return ToolResult(False, "Spotify не запущен.")
    try:
        client.player("PUT", "/shuffle", params={"state": "true" if on else "false"})
    except sp.SpotifyError as exc:
        return _err(exc)
    return ToolResult(True, "Перемешивание включено." if on else "Перемешивание выключено.")


@tool("spotify_repeat", "Повтор в Spotify: track — повторять трек, context — плейлист/альбом, off — выключить.",
      params={"mode": {"type": "string", "enum": ["track", "context", "off"]}}, required=["mode"],
      announce="Повтор", category="spotify")
def spotify_repeat(ctx, mode: str = "track") -> ToolResult:
    client = _client()
    if not client:
        if _hotkey([0x11, 0x52]):
            return ToolResult(True, "Переключил режим повтора в Spotify.")
        return ToolResult(False, "Spotify не запущен.")
    mode = mode if mode in ("track", "context", "off") else "track"
    try:
        client.player("PUT", "/repeat", params={"state": mode})
    except sp.SpotifyError as exc:
        return _err(exc)
    return ToolResult(True, {"track": "Повторяю трек.", "context": "Повторяю плейлист.", "off": "Повтор выключен."}[mode])


@tool("spotify_volume", "Громкость самого Spotify (0–100), не влияя на общую громкость системы.",
      params={"percent": {"type": "integer", "description": "0–100"}}, required=["percent"],
      announce="Громкость Spotify {percent}", category="spotify")
def spotify_volume(ctx, percent: int) -> ToolResult:
    client = _client()
    if not client:
        return ToolResult(False, "Для громкости Spotify нужно подключить Spotify. Могу изменить общую громкость.")
    percent = max(0, min(100, int(percent)))
    try:
        client.player("PUT", "/volume", params={"volume_percent": percent})
    except sp.SpotifyError as exc:
        return _err(exc)
    return ToolResult(True, f"Громкость Spotify {percent} процентов.")


@tool("spotify_playlists", "Список плейлистов пользователя в Spotify.", announce="Смотрю плейлисты",
      category="spotify",
      patterns=[r"^(?:какие|покажи)\s+(?:у меня\s+)?плейлист\w*(?:\s+" + _SP + ")?$"])
def spotify_playlists(ctx) -> ToolResult:
    client = _client()
    if not client:
        return ToolResult(False, "Spotify не подключён: впишите SPOTIFY_CLIENT_ID в .env и скажите «подключи Spotify».")
    try:
        items = client.my_playlists()
    except sp.SpotifyError as exc:
        return _err(exc)
    if not items:
        return ToolResult(False, "Плейлистов нет.")
    names = [p["name"] for p in items]
    return ToolResult(True, f"Плейлистов: {len(names)}. " + ", ".join(names[:25]) + ".", {"playlists": names})
