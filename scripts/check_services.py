"""Диагностика: проверяет сервисы без изменения состояния системы.

    python scripts/check_services.py
"""
from __future__ import annotations

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis.config import PLUGINS_DIR, Settings

results: list[tuple[str, bool, str]] = []


def check(name):
    def deco(fn):
        t = time.time()
        try:
            detail = fn()
            results.append((name, True, f"{detail} ({time.time() - t:.1f}s)"))
        except Exception as exc:
            traceback.print_exc()
            results.append((name, False, f"{type(exc).__name__}: {exc}"))
        return fn
    return deco


settings = Settings()


@check("Инструменты и плагины")
def _():
    from jarvis.tools.base import load_builtin_tools, load_plugins, registry
    load_builtin_tools()
    plugins = load_plugins(PLUGINS_DIR)
    return f"{len(registry.all())} инструментов, плагины: {plugins}"


apps = None


@check("Индекс приложений")
def _():
    global apps
    from jarvis.services.app_index import AppIndex
    apps = AppIndex(settings)
    apps.refresh()
    found = {q: (m.display, m.kind) if m else None for q in ("дискорд", "Steam", "телеграм", "Spotify", "firefox",
                                                              "блокнот", "хром")
             for m in [apps.resolve(q)]}
    return f"{len(apps.entries)} записей; {found}"


@check("YouTube: поиск")
def _():
    from jarvis.services import youtube
    r = youtube.search("музыка для учебы", limit=5)
    assert r, "пустой результат"
    assert all("watch?v=" in x.url for x in r), [x.url for x in r]
    return "; ".join(f"{x.title[:40]} [{x.channel}] {x.url[24:60]}" for x in r[:3])


@check("YouTube: последний ролик канала")
def _():
    from jarvis.services import youtube
    ch = youtube.find_channel("Wylsacom")
    assert ch, "канал не найден"
    vids = youtube.latest_videos(ch[0], 3)
    assert vids, "нет видео"
    return f"{ch[1]}: {vids[0].title} {vids[0].url}"


@check("Громкость (чтение)")
def _():
    from jarvis.services import audio
    current = audio.get_volume()
    assert audio.set_volume(current) == current
    return f"{current}%, mute={audio.is_muted()}"


@check("Медиа-сессии")
def _():
    from jarvis.services import media
    return [f"{s.app}: {s.label()} status={s.status}" for s in media.sessions()] or "нет сессий"


@check("VPN (только статус)")
def _():
    from jarvis.services.vpn import VpnController
    ctl = VpnController(settings, apps)
    cfg = ctl.detect()
    return f"тип={cfg.get('type')} app={cfg.get('app')} proxy={cfg.get('proxy')}; {ctl.status().detail}"


@check("Edge TTS (синтез в файл)")
def _():
    from jarvis.voice.tts import EdgeTTS
    path = EdgeTTS("ru-RU-DmitryNeural").synthesize("Проверка синтеза речи.")
    size = os.path.getsize(path)
    os.remove(path)
    return f"{size} байт"


@check("Микрофоны")
def _():
    from jarvis.voice.recorder import list_microphones
    mics = list_microphones()
    return f"{len(mics)} входов; первый: {mics[0] if mics else '-'}"


for name, ok, detail in results:
    print(f"{'OK ' if ok else 'ERR'} {name}: {detail}")
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
