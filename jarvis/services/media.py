"""Управление воспроизведением через системные медиа-сессии Windows (SMTC).

Работает с браузерами (YouTube), Spotify, плеерами и т. д. Если WinRT недоступен —
используются глобальные медиа-клавиши.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from jarvis.utils import winapi

log = logging.getLogger("jarvis.media")

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _Manager,
    )
    HAS_WINRT = True
except Exception:
    HAS_WINRT = False

PLAYING, PAUSED = 4, 5


@dataclass
class MediaSession:
    app: str
    status: int
    title: str
    artist: str

    @property
    def playing(self) -> bool:
        return self.status == PLAYING

    def label(self) -> str:
        if self.title and self.artist:
            return f"«{self.title}» — {self.artist}"
        return f"«{self.title}»" if self.title else self.app


def _run(coro):
    return asyncio.run(coro)


async def _manager():
    return await _Manager.request_async()


async def _sessions_async():
    mgr = await _manager()
    items = []
    for s in mgr.get_sessions():
        try:
            info = s.get_playback_info()
            props = await s.try_get_media_properties_async()
            items.append((s, MediaSession(s.source_app_user_model_id or "", int(info.playback_status),
                                          props.title or "", props.artist or "")))
        except Exception as exc:
            log.debug("Сессия пропущена: %s", exc)
    current = mgr.get_current_session()
    current_id = current.source_app_user_model_id if current else None
    items.sort(key=lambda it: 0 if it[1].app == current_id else 1)
    return items


def sessions() -> list[MediaSession]:
    if not HAS_WINRT:
        return []
    try:
        return [m for _, m in _run(_sessions_async())]
    except Exception as exc:
        log.warning("SMTC недоступен: %s", exc)
        return []


async def _pause_all():
    paused = []
    for s, m in await _sessions_async():
        if m.playing and await s.try_pause_async():
            paused.append(m)
    return paused


async def _resume():
    items = await _sessions_async()
    for s, m in items:
        if m.status == PAUSED:
            if await s.try_play_async():
                return m
    return None


async def _skip(forward: bool):
    items = await _sessions_async()
    target = next((it for it in items if it[1].playing), items[0] if items else None)
    if not target:
        return None
    s, m = target
    ok = await (s.try_skip_next_async() if forward else s.try_skip_previous_async())
    return m if ok else None


async def _stop():
    stopped = []
    for s, m in await _sessions_async():
        if m.playing:
            if await s.try_stop_async() or await s.try_pause_async():
                stopped.append(m)
    return stopped


def pause() -> tuple[bool, list[MediaSession]]:
    if HAS_WINRT:
        try:
            paused = _run(_pause_all())
            if paused:
                return True, paused
            if any(m.playing for m in sessions()):
                raise RuntimeError("сессия не поставилась на паузу")
            return False, []
        except Exception as exc:
            log.warning("pause через SMTC не удался (%s) — медиа-клавиша", exc)
    winapi.tap_key(winapi.VK_MEDIA_PLAY_PAUSE)
    return True, []


def resume() -> tuple[bool, MediaSession | None]:
    if HAS_WINRT:
        try:
            m = _run(_resume())
            if m:
                return True, m
            if any(s.playing for s in sessions()):
                return True, None
            return False, None
        except Exception as exc:
            log.warning("resume через SMTC не удался (%s) — медиа-клавиша", exc)
    winapi.tap_key(winapi.VK_MEDIA_PLAY_PAUSE)
    return True, None


def skip(forward: bool = True) -> tuple[bool, MediaSession | None]:
    if HAS_WINRT:
        try:
            m = _run(_skip(forward))
            if m:
                return True, m
        except Exception as exc:
            log.warning("skip через SMTC не удался: %s", exc)
    winapi.tap_key(winapi.VK_MEDIA_NEXT_TRACK if forward else winapi.VK_MEDIA_PREV_TRACK)
    return True, None


def stop() -> tuple[bool, list[MediaSession]]:
    if HAS_WINRT:
        try:
            stopped = _run(_stop())
            return bool(stopped), stopped
        except Exception as exc:
            log.warning("stop через SMTC не удался: %s", exc)
    winapi.tap_key(winapi.VK_MEDIA_STOP)
    return True, []
