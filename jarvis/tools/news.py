"""Новости: JARVIS сам получает, отбирает и пересказывает (News Brain — services/news.py).

NEWS REQUEST ≠ OPEN BROWSER: браузер открывает только news_open — по прямой просьбе «открой источник/статью».
"""
from __future__ import annotations

import re
import time

from jarvis.services import news as news_service
from jarvis.services.news import CATEGORIES, CATEGORY_NAMES, NewsQuery
from jarvis.tools.base import ToolResult, tool
from jarvis.utils.text import ordinal_to_int

ORD = ["Первое", "Второе", "Третье", "Четвёртое", "Пятое"]
OFFLINE = "Сэр, сейчас я не могу получить актуальные новости."
_FILLER = re.compile(r"\b(?:jarvis|джарвис|какие|какая|какой|что|за|нового|новое|новост\w*|расскажи|скажи|сегодня|"
                     r"сегодняшние|свежие|свежее|последние|главные|главное|про|о|об|в|во|на|мире|мне|есть|ещё|еще|"
                     r"неделю|недели|неделя|за|день|сутки|игров\w*|гейминг\w*|игр\w*|индустри\w*|технолог\w*|"
                     r"научн\w*|наук\w*|космос\w*|космическ\w*|спорт\w*|спортивн\w*|бизнес\w*|экономик\w*|"
                     r"экономическ\w*|политическ\w*|политик\w*|ии|ai|искусственн\w*|интеллект\w*|произошло|случилось|"
                     r"происходит|мир\w*|общие|важное|важные|топ|пожалуйста|сэр)\b", re.I)
PLATFORM_TOPICS = {"playstation": r"playstation|плейстейшн|ps ?5|пс ?5|плойк",
                   "xbox": r"xbox|иксбокс|икс бокс", "nintendo": r"nintendo|нинтендо|свитч|switch",
                   "steam": r"steam|стим\w*", "gta": r"\bgta\b|\bгта\b|grand theft auto", "esports": r"киберспорт\w*",
                   "hardware": r"видеокарт\w*|железо|железе"}


def _manager():
    return news_service.get()


def parse_request(category: str | None, topic: str | None, period: str | None, text: str = "") -> NewsQuery:
    """Категория, тема и период по параметрам модели или по самой фразе («новости PlayStation за неделю»)."""
    raw = " ".join(x for x in (topic, text) if x)
    low = raw.lower().replace("ё", "е")
    cat = (category or "").lower().strip()
    if cat not in CATEGORIES:
        cat = news_service.NewsManager.detect_category(low) if low else "general"
    t = ""
    for key, pattern in PLATFORM_TOPICS.items():
        if re.search(pattern, low):
            t, cat = key, ("gaming" if key != "hardware" or cat == "general" else cat)
            break
    if not t:
        rest = _FILLER.sub(" ", low)
        rest = re.sub(r"[^\w\s-]", " ", rest)
        t = " ".join(rest.split())
    per = (period or "").lower()
    if per not in ("today", "week", "latest"):
        per = "week" if re.search(r"недел", low) else ("latest" if re.search(r"что нового|свеж|последн", low)
                                                      else "today")
    return NewsQuery(cat, t, per)


def _digest(items, query: NewsQuery, stale_from: float | None = None, more: bool = False) -> str:
    """Короткий пересказ подборки для голоса: номер, суть, при необходимости — давность и источник."""
    if query.topic:
        intro = f"Вот свежее по теме {query.topic}."
    elif query.category == "gaming":
        intro = "Вот главное в игровой индустрии" + (" за неделю." if query.period == "week" else " за сегодня.")
    elif query.category == "general":
        intro = "Вот главное" + (" за неделю." if query.period == "week" else " за сегодня.")
    else:
        intro = f"Вот главные новости: {CATEGORY_NAMES.get(query.category, query.category)}."
    if more:
        intro = "Вот ещё."
    parts = [intro]
    now = time.time()
    for i, it in enumerate(items):
        age = it.age_text(now)
        dated = "" if now - it.published_at < 20 * 3600 else f" ({age})"
        attributed = f"По данным {it.source}: " if query.category == "politics" else ""
        summary = it.summary.strip() if it.summary and len(it.summary) < 170 else ""
        if summary and summary[-1] not in ".!?…":
            summary += "."
        summary = f" {summary}" if summary else ""
        parts.append(f"{ORD[i] if i < len(ORD) else str(i + 1) + '-е'} — {attributed}{it.title.rstrip('.')}.{summary}{dated}")
    parts.append("Если хотите, могу рассказать подробнее о любой из них.")
    text = " ".join(parts)
    if stale_from:
        when = time.strftime("%d.%m %H:%M", time.localtime(stale_from))
        text = f"{OFFLINE} У меня есть последние сохранённые данные от {when}. " + text
    return text


def _items_data(items) -> list[dict]:
    """Для AI Brain: номер, заголовок, источник, давность, суть (без ссылок — браузер не нужен)."""
    now = time.time()
    return [{"n": i + 1, "title": it.title, "source": it.source, "also": it.also[:3], "published": it.age_text(now),
             "summary": it.summary} for i, it in enumerate(items)]


@tool("news_get", "Получить и пересказать свежие новости (сам, БЕЗ открытия браузера и без search_web). "
      "category: general (главное), politics, tech, ai, gaming (игры — Telegram-каналы в приоритете), science, space, "
      "business, sports. topic — конкретная тема: playstation, xbox, nintendo, steam, gta или название игры/компании. "
      "period: today (24 ч, по умолчанию), week (7 дней), latest (самое свежее). Перескажи 3–5 новостей своими "
      "словами, коротко; политику — нейтрально, с указанием, кто что сообщает.",
      params={"category": {"type": "string", "enum": CATEGORIES},
              "topic": {"type": "string", "description": "Конкретная тема (необязательно)"},
              "period": {"type": "string", "enum": ["today", "week", "latest"]}},
      announce="Собираю новости", category="news")
def news_get(ctx, category: str | None = None, topic: str | None = None, period: str | None = None,
             query: str | None = None) -> ToolResult:
    q = parse_request(category, topic, period, query or "")
    r = _manager().get_news(q)
    if r.get("offline") and not r["items"]:
        return ToolResult(False, OFFLINE, {"offline": True})
    if not r["items"]:
        what = f" по теме {q.topic}" if q.topic else ""
        return ToolResult(False, f"Свежих новостей{what} не нашёл.", {"empty": True})
    stale = r.get("fetched_at") if r.get("offline") else None
    data = {"category": q.category, "topic": q.topic, "period": q.period, "items": _items_data(r["items"]),
            "sources_ok": r.get("sources"), "stale_saved_at": time.strftime("%d.%m %H:%M", time.localtime(stale))
            if stale else None}
    return ToolResult(True, _digest(r["items"], q, stale), data)


@tool("news_more", "Следующая группа новостей из последней подборки («ещё новости», «что ещё»).",
      announce="Ищу ещё новости", category="news")
def news_more(ctx) -> ToolResult:
    m = _manager()
    if not m.current:
        return ToolResult(False, "Сначала спросите новости, сэр.")
    page = m.next_page()
    if not page:
        return ToolResult(True, "Другой информации нет, сэр. Это все свежие новости по теме.", {"empty": True})
    return ToolResult(True, _digest(page, m.query, more=True), {"items": _items_data(page)})


def _index(value) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    s = str(value).strip().lower()
    if s.isdigit():
        return int(s)
    n = ordinal_to_int(s.split()[0]) if s else None
    return n


@tool("news_details", "Подробнее об одной новости из последней подборки (номер из последнего рассказа: "
      "«про вторую» → 2; без номера — о той, о которой шла речь). Перескажи своими словами, не читай дословно.",
      params={"index": {"type": "integer", "description": "Номер новости в последней группе (1–5)"}},
      announce="Смотрю подробности", category="news")
def news_details(ctx, index=None) -> ToolResult:
    m = _manager()
    it = m.item(_index(index))
    if it is None:
        return ToolResult(False, "Не нашёл такую новость в последней подборке, сэр.")
    text = m.details(it)
    return ToolResult(True, f"{it.title.rstrip('.')}. {text}",
                      {"source": it.source, "published": it.age_text(), "also": it.also})


@tool("news_source", "Назвать источник новости (кто сообщил и когда). Сам сайт НЕ открывать.",
      params={"index": {"type": "integer", "description": "Номер новости; без номера — последняя обсуждаемая"}},
      announce="Смотрю источник", category="news")
def news_source(ctx, index=None) -> ToolResult:
    it = _manager().item(_index(index))
    if it is None:
        return ToolResult(False, "Сейчас нет новости, о которой шла речь.")
    also = f" Также об этом пишут: {', '.join(it.also[:3])}." if it.also else ""
    name = re.sub(r"\s*\(Telegram\)\s*$", "", it.source)
    who = f"Telegram-канал {name}" if it.telegram else name
    return ToolResult(True, f"Источник — {who}, {it.age_text()}.{also}",
                      {"source": it.source, "url": it.url, "also": it.also})


@tool("news_open", "Открыть источник новости в браузере — ТОЛЬКО по прямой просьбе пользователя («открой источник», "
      "«покажи статью», «открой эту новость в браузере»).",
      params={"index": {"type": "integer", "description": "Номер новости; без номера — последняя обсуждаемая"}},
      announce="Открываю источник", category="news")
def news_open(ctx, index=None) -> ToolResult:
    it = _manager().item(_index(index))
    if it is None or not it.url:
        return ToolResult(False, "Не знаю, какой источник открыть — сначала спросите новости.")
    from jarvis.tools._common import open_in_browser

    open_in_browser(it.url)
    return ToolResult(True, f"Открываю источник: {it.source}.", {"site": it.source})
