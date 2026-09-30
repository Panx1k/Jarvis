"""Что JARVIS знает о компьютере пользователя: установленные игры Steam, запущенная игра, подключён ли Spotify.

Короткая сводка добавляется в контекст модели (живой режим и обычный мозг), чтобы на вопросы вроде
«во что поиграть?» или «что у меня есть в Steam?» модель отвечала по делу. Обновляется не чаще раза в минуту.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("jarvis.knowledge")

_cache = {"at": 0.0, "text": ""}
_lock = threading.Lock()
TTL = 60.0


def summary() -> str:
    with _lock:
        if time.monotonic() - _cache["at"] < TTL and _cache["at"]:
            return _cache["text"]
    parts = []
    try:
        from jarvis.tools.steam import library_summary

        steam = library_summary()
        if steam:
            parts.append(steam)
        from jarvis.config import Settings

        settings = Settings()
        from jarvis.tools.steam import _game_manifests

        names = {appid: name for appid, (name, _) in _game_manifests().items()}
        rules = [f"{names.get(str(game), game)} — на аккаунте {acc}"
                 for game, acc in (settings.get("steam.game_accounts", {}) or {}).items()]
        default = settings.get("steam.default_account", "")
        if rules or default:
            parts.append("Аккаунты Steam (launch_game и open_app переключают их сами, отдельно steam_switch_account "
                         "вызывать не нужно): " + "; ".join(rules)
                         + (f"; всё остальное — {default}" if default else ""))
    except Exception as exc:
        log.debug("steam: %s", exc)
    try:
        from jarvis.services import spotify

        client = spotify.get()
        parts.append("Spotify подключён к JARVIS (можно включать по названию)" if client.connected
                     else "Spotify не подключён к JARVIS (включение по названию недоступно, есть пауза/следующий)")
    except Exception as exc:
        log.debug("spotify: %s", exc)
    text = ". ".join(parts)
    with _lock:
        _cache.update(at=time.monotonic(), text=text)
    return text


def invalidate() -> None:
    with _lock:
        _cache["at"] = 0.0
