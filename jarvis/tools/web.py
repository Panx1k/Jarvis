"""Браузер и веб-поиск."""
from __future__ import annotations

from urllib.parse import quote_plus

from jarvis.tools._common import find_site, looks_like_url, open_in_browser, site_search_url
from jarvis.tools.base import ToolResult, tool


@tool("open_url", "Открыть ссылку (URL или домен) в браузере по умолчанию.",
      params={"url": {"type": "string", "description": "Адрес, например https://habr.com или habr.com"}},
      required=["url"], announce="Открываю {url}", category="web")
def open_url(ctx, url: str) -> ToolResult:
    url = url.strip()
    open_in_browser(url)
    ctx.dialog.last_url = url
    ctx.dialog.active_site = None
    if "youtube.com" in url or "youtu.be" in url:
        ctx.dialog.active_site = "youtube"
    return ToolResult(True, f"Открываю {url.removeprefix('https://').removeprefix('http://').rstrip('/')}.")


@tool("open_site", "Открыть известный сайт по названию: youtube, google, яндекс, вк, github, gmail, википедия, "
      "twitch, chatgpt, карты, кинопоиск и др. Для произвольного адреса используй open_url.",
      params={"site": {"type": "string", "description": "Название сайта, как его назвал пользователь"}},
      required=["site"], announce="Открываю сайт {site}", category="web")
def open_site(ctx, site: str) -> ToolResult:
    found = find_site(ctx.settings, site)
    if not found:
        if looks_like_url(site):
            return open_url(ctx, site)
        return ToolResult(False, f"Не знаю сайт «{site}». Назовите адрес, например «открой habr.com».")
    key, cfg = found
    open_in_browser(cfg["url"])
    ctx.dialog.active_site = key
    ctx.dialog.last_url = cfg["url"]
    pretty = {"youtube": "YouTube", "google": "Google", "vk": "ВКонтакте", "github": "GitHub", "yandex": "Яндекс",
              "wikipedia": "Википедия", "twitch": "Twitch", "chatgpt": "ChatGPT", "maps": "Карты"}.get(key, site.strip())
    return ToolResult(True, f"Открываю {pretty}.", {"site": pretty})


@tool("search_web", "Открыть в браузере результаты поиска в интернете. Если указан site (youtube, яндекс, вк, "
      "википедия...) — искать на этом сайте. Для поиска видео/музыки лучше search_youtube.",
      params={"query": {"type": "string", "description": "Поисковый запрос"},
              "site": {"type": "string", "description": "Необязательно: сайт, на котором искать"}},
      required=["query"], announce="Ищу в интернете: {query}", category="web")
def search_web(ctx, query: str, site: str | None = None) -> ToolResult:
    query = query.strip()
    if site:
        found = find_site(ctx.settings, site)
        if found:
            key, _ = found
            if key == "youtube":
                from jarvis.tools.youtube import search_youtube
                return search_youtube(ctx, query)
            url = site_search_url(ctx.settings, key, query)
            if url:
                open_in_browser(url)
                ctx.dialog.active_site = key
                ctx.dialog.set_results("web", query, [])
                return ToolResult(True, f"Ищу «{query}» на сайте {site}.")
    url = ctx.settings.get("search_url", "https://www.google.com/search?q={query}").replace("{query}", quote_plus(query))
    open_in_browser(url)
    ctx.dialog.active_site = "google"
    ctx.dialog.set_results("web", query, [])
    return ToolResult(True, f"Ищу в интернете: {query}.")


@tool("open_result", "Открыть/включить результат из последнего поиска по номеру (1 — первый, -1 — последний). "
      "Используй для «включи первый результат», «открой второе видео».",
      params={"index": {"type": "integer", "description": "Номер результата, начиная с 1; -1 — последний"}},
      required=["index"], announce="Открываю результат №{index}", category="web")
def open_result(ctx, index: int) -> ToolResult:
    index = int(index)
    d = ctx.dialog
    if d.last_results:
        results = d.last_results
        if index == -1:
            index = len(results)
        if not 1 <= index <= len(results):
            return ToolResult(False, f"В последнем поиске только {len(results)} результатов.")
        r = results[index - 1]
        if r.source == "file":
            import os

            if not os.path.exists(r.url):
                return ToolResult(False, f"Файл больше не существует: {r.url}")
            os.startfile(r.url)
            return ToolResult(True, f"Открываю «{r.title}».", {"path": r.url})
        d.last_url = r.url
        if r.source == "youtube":
            from jarvis.tools.youtube import _open_video
            note = _open_video(ctx, r.url)
            d.active_site = "youtube"
            d.now_playing = r.label()
            return ToolResult(True, f"Включаю «{r.title}».{note}", {"url": r.url})
        open_in_browser(r.url)
        return ToolResult(True, f"Открываю «{r.title}».", {"url": r.url})
    if d.last_results_source == "web" and d.last_query:
        if index != 1:
            return ToolResult(False, "Для веб-поиска я могу сразу открыть только первый результат — "
                                     "остальные выберите во вкладке с результатами.")
        url = "https://duckduckgo.com/?q=" + quote_plus("\\" + d.last_query)
        open_in_browser(url)
        return ToolResult(True, f"Открываю первый результат по запросу «{d.last_query}».")
    return ToolResult(False, "Я пока ничего не искал. Скажите, например: «найди на YouTube музыку для учёбы».")
