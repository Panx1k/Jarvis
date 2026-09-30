"""YouTube: поиск, включение видео, последний ролик канала."""
from __future__ import annotations

from jarvis.services import youtube as yt
from jarvis.tools._common import open_in_browser
from jarvis.tools.base import ToolResult, tool


BROWSERS = {"firefox.exe", "chrome.exe", "msedge.exe", "browser.exe", "opera.exe", "brave.exe", "vivaldi.exe"}


def _foreground_process() -> str:
    import ctypes
    from ctypes import wintypes

    import psutil

    hwnd = ctypes.windll.user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name().lower()
    except Exception:
        return ""


def ensure_playing(wait: float = 3.0) -> str:
    """Браузеры (особенно Firefox) блокируют автозапуск видео со звуком. Ждём воспроизведения; если его нет,
    а активно окно браузера — нажимаем «k» (Play на YouTube): нажатие клавиши снимает блокировку автозапуска.
    Возвращает "playing" | "started" | "blocked"."""
    import time

    from jarvis.services import media
    from jarvis.utils import winapi

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(0.7)
        if any(s.playing for s in media.sessions()):
            return "playing"
    if _foreground_process() not in BROWSERS:
        hwnd = winapi.find_window("YouTube", BROWSERS)
        if not hwnd or not winapi.focus_window(hwnd) or _foreground_process() not in BROWSERS:
            return "blocked"
    winapi.tap_key(0x4B)
    for _ in range(6):
        time.sleep(0.5)
        if any(s.playing for s in media.sessions()):
            return "started"
    return "blocked"


def _open_video(ctx, url: str) -> str:
    """Открывает видео и добивается воспроизведения. Возвращает приписку к ответу."""
    from jarvis.services import media

    if any(s.playing for s in media.sessions()):
        media.pause()
    open_in_browser(url)
    if not ctx.settings.get("youtube_autoplay_fix", True):
        return ""
    state = ensure_playing()
    if state == "blocked":
        return (" Браузер не дал запустить воспроизведение автоматически — нажмите Play "
                "или разрешите автовоспроизведение для youtube.com.")
    return ""


def _titles(results, n=3) -> str:
    return "; ".join(f"{i}. {r.title}" for i, r in enumerate(results[:n], 1))


@tool("search_youtube", "Найти видео/музыку на YouTube и показать страницу результатов. Результаты запоминаются, "
      "после этого можно включить любой через open_result.",
      params={"query": {"type": "string", "description": "Что искать"}},
      required=["query"], announce="Ищу на YouTube: {query}", category="youtube")
def search_youtube(ctx, query: str) -> ToolResult:
    query = query.strip()
    results = yt.search(query, limit=10)
    open_in_browser(yt.search_url(query))
    ctx.dialog.active_site = "youtube"
    ctx.dialog.set_results("youtube", query, results)
    if results:
        return ToolResult(True, f"Нашёл на YouTube «{query}». Первый результат: «{results[0].title}». "
                                f"Скажите «включи первый», чтобы запустить.",
                          {"results": _titles(results, 5)})
    return ToolResult(True, f"Открыл поиск на YouTube по запросу «{query}».")


@tool("play_youtube", "Найти на YouTube и сразу включить самое подходящее видео (первый результат).",
      params={"query": {"type": "string", "description": "Название видео, песни, исполнителя и т. п."}},
      required=["query"], announce="Включаю на YouTube: {query}", category="youtube")
def play_youtube(ctx, query: str) -> ToolResult:
    query = query.strip()
    results = yt.search(query, limit=10)
    ctx.dialog.active_site = "youtube"
    if not results:
        open_in_browser(yt.search_url(query))
        ctx.dialog.set_results("youtube", query, [])
        return ToolResult(False, f"Не удалось получить результаты YouTube (сеть или VPN?). "
                                 f"Открыл страницу поиска «{query}».")
    ctx.dialog.set_results("youtube", query, results)
    first = next((r for r in results if "watch?v=" in r.url), results[0])
    note = _open_video(ctx, first.url)
    ctx.dialog.now_playing = first.label()
    ctx.dialog.last_url = first.url
    who = f" — {first.channel}" if first.channel else ""
    return ToolResult(True, f"Включаю «{first.title}»{who}.{note}", {"url": first.url})


@tool("youtube_latest", "Включить последний (самый новый) ролик YouTube-канала/блогера.",
      params={"channel": {"type": "string", "description": "Название канала или имя блогера"}},
      required=["channel"], announce="Ищу последний ролик канала {channel}", category="youtube")
def youtube_latest(ctx, channel: str) -> ToolResult:
    channel = channel.strip()
    found = yt.find_channel(channel)
    if not found:
        return ToolResult(False, f"Не нашёл канал «{channel}» на YouTube.")
    channel_id, title = found
    videos = yt.latest_videos(channel_id, limit=5)
    ctx.dialog.active_site = "youtube"
    if not videos:
        url = f"{yt.BASE}/channel/{channel_id}/videos"
        open_in_browser(url)
        return ToolResult(False, f"Не удалось получить список роликов, открыл канал {title}.")
    ctx.dialog.set_results("youtube", f"канал {title}", videos)
    latest = videos[0]
    note = _open_video(ctx, latest.url)
    ctx.dialog.now_playing = f"{latest.title} — {title}"
    return ToolResult(True, f"Включаю последний ролик канала {title}: «{latest.title}».{note}", {"url": latest.url})
