"""News Brain: JARVIS сам получает новости, отбирает главное и пересказывает (браузер не открывается).

    запрос → категория/тема/период → источники (RSS, публичные Telegram-каналы) → свежесть → тема →
    удаление дубликатов → ранжирование → 3–5 новостей → пересказ (AI Brain / голос)

* Источники — в конфигурации, не в коде: config/news_sources.json (категории) и config/gaming_sources.json
  (игровые: Telegram — в приоритете, затем официальные блоги и игровые СМИ).
* Telegram: публичные каналы читаются через их веб-версию t.me/s/<канал> (без входа и ключей).
* Свежесть: «сегодня» — 24 ч, «неделя» — 7 дней, «что нового» — самые свежие. Старая новость не выдаётся за
  новую: у каждой хранится время публикации, в ответе — «N ч назад» / «вчера».
* Каждая новость: title, source, url, published_at, category, summary (+ другие источники с той же новостью).
* Нет сети — честно: «не могу получить актуальные новости»; если есть сохранённые — с датой, когда они получены.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from email.utils import parsedate_to_datetime

import requests

from jarvis.config import ROOT

log = logging.getLogger("jarvis.news")

CONFIG_DIR = ROOT / "config"
CACHE_FILE = ROOT / "models" / "news_cache.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JARVIS-news/1.0"}
PAGE = 4

CATEGORIES = ["general", "politics", "tech", "ai", "gaming", "science", "space", "business", "sports"]
CATEGORY_NAMES = {"general": "главное", "politics": "политика", "tech": "технологии", "ai": "искусственный интеллект",
                  "gaming": "игры", "science": "наука", "space": "космос", "business": "экономика и бизнес",
                  "sports": "спорт"}
CATEGORY_WORDS = {
    "gaming": r"игр|гейм|gaming|game|playstation|плейстейшн|ps5|xbox|иксбокс|nintendo|нинтендо|switch|стим|steam|"
              r"гта|gta|киберспорт|esports|консол|видеоигр",
    "ai": r"\bии\b|\bai\b|искусственн\w* интеллект|нейросет|chatgpt|openai|\bgpt|gemini|claude|llm|машинн\w* обучен",
    "tech": r"технолог|тех\b|гаджет|смартфон|айти|\bit\b|компьютер|хабр",
    "science": r"наук|учён|исследован",
    "space": r"космос|космич|nasa|spacex|роскосмос|марс|луна|ракет",
    "business": r"бизнес|эконом|финанс|рынок|рубл|курс|биржа|акци",
    "sports": r"спорт|футбол|хоккей|теннис|матч|чемпионат",
    "politics": r"полит|выбор|президент|правительств|госдум|санкци|переговор|дипломат",
}


@dataclass
class NewsItem:
    title: str
    source: str
    url: str
    published_at: float
    category: str
    summary: str = ""
    priority: float = 0.5
    telegram: bool = False
    also: list[str] = field(default_factory=list)
    score: float = 0.0

    def age_text(self, now: float | None = None) -> str:
        age = (now or time.time()) - self.published_at
        if age < 3600:
            return f"{max(1, int(age // 60))} мин назад"
        if age < 86400:
            return f"{int(age // 3600)} ч назад"
        if age < 2 * 86400:
            return "вчера"
        return dt.datetime.fromtimestamp(self.published_at).strftime("%d.%m")


@dataclass
class NewsQuery:
    category: str = "general"
    topic: str = ""
    period: str = "today"

    def key(self) -> str:
        return f"{self.category}|{self.topic.lower()}|{self.period}"


_TAG = re.compile(r"<[^>]+>")


def clean_text(raw: str) -> str:
    raw = re.sub(r"<br\s*/?>", "\n", raw or "", flags=re.I)
    text = html.unescape(_TAG.sub(" ", raw))
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()


def first_sentences(text: str, limit: int = 240, count: int = 2) -> str:
    text = " ".join(text.split())
    parts = re.split(r"(?<=[.!?…])\s+", text)
    out = ""
    for p in parts[:count]:
        if out and len(out) + len(p) > limit:
            break
        out = f"{out} {p}".strip()
    return out[:limit].rstrip(" ,;:") if out else text[:limit]


def _parse_date(value: str) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_rss(xml_text: str, source: dict, category: str) -> list[NewsItem]:
    """RSS 2.0 и Atom → NewsItem (без сторонних библиотек)."""
    items = []
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return items
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        fields: dict[str, str] = {}
        for child in node:
            name = _local(child.tag)
            if name == "link" and child.get("href"):
                fields.setdefault("link", child.get("href"))
            elif child.text and name not in fields:
                fields[name] = child.text
        title = clean_text(fields.get("title", ""))
        when = _parse_date(fields.get("pubdate") or fields.get("published") or fields.get("updated")
                           or fields.get("date") or "")
        if not title or when is None:
            continue
        summary = clean_text(fields.get("description") or fields.get("summary") or fields.get("content") or "")
        if summary.startswith(title[:40]):
            summary = summary[len(title):].strip(" .")
        items.append(NewsItem(title=title, source=source["name"], url=(fields.get("link") or "").strip(),
                              published_at=when, category=category, summary=first_sentences(summary),
                              priority=float(source.get("priority", 0.5))))
    return items


_TG_POST = re.compile(r'data-post="([^"]+)"(.*?)(?=data-post="|\Z)', re.S)


def parse_telegram(page: str, source: dict, category: str) -> list[NewsItem]:
    """Публичная веб-версия канала t.me/s/<канал>: текст поста, время, ссылка."""
    items = []
    for post_id, body in _TG_POST.findall(page):
        m = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', body, re.S)
        when = re.search(r'<time datetime="([^"]+)"', body)
        if not m or not when:
            continue
        text = clean_text(m.group(1))
        if len(text) < 25 or re.search(r"#реклама|#ad\b|erid", text, re.I):
            continue
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        title = tidy_title(first_sentences(lines[0], 160, 1))
        rest = " ".join(lines[1:]) if len(lines) > 1 else text[len(title):]
        items.append(NewsItem(title=title, source=source["name"], url=f"https://t.me/{post_id}",
                              published_at=_parse_date(when.group(1)) or time.time(), category=category,
                              summary=first_sentences(rest), priority=float(source.get("priority", 0.9)),
                              telegram=True))
    return items


def has_word(word: str, text: str) -> bool:
    """Короткое слово (ии, ai, pc, gta) — только целиком («ии» не должно находиться в «Россия»), длинное — как
    начало слова (основа «нейросет» ловит «нейросети», «нейросетью»)."""
    w = word.lower().replace("ё", "е")
    t = text.lower().replace("ё", "е")
    if len(w) <= 3:
        return bool(re.search(rf"(?<![a-zа-я0-9]){re.escape(w)}(?![a-zа-я0-9])", t))
    return bool(re.search(rf"(?<![a-zа-я0-9]){re.escape(w)}", t))


_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️‍]+")


def tidy_title(title: str) -> str:
    """Заголовок для пересказа: без эмодзи и рубрик вида «ЧЕГО:» / «Главное:» в начале."""
    t = _EMOJI.sub("", title).strip()
    t = re.sub(r"^(?:[A-ZА-ЯЁ]{2,12}|Главное|Коротко|Слух|Инсайд)\s*[:!—-]\s*", "", t)
    return t.strip(" -—:") or title


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-zа-яё0-9]+", text.lower().replace("ё", "е"))
    return {w[:6] for w in words if len(w) > 3}


def same_story(a: NewsItem, b: NewsItem) -> bool:
    ta, tb = _tokens(a.title), _tokens(b.title)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / min(len(ta), len(tb))
    return overlap >= 0.6 or (len(ta & tb) >= 4 and overlap >= 0.45)


def deduplicate(items: list[NewsItem]) -> list[NewsItem]:
    """Одна история из нескольких источников → одна новость (представитель — источник приоритетнее)."""
    out: list[NewsItem] = []
    for it in sorted(items, key=lambda x: (-x.priority, -x.published_at)):
        dup = next((o for o in out if same_story(o, it)), None)
        if dup is None:
            out.append(it)
        elif it.source != dup.source and it.source not in dup.also:
            dup.also.append(it.source)
            if not dup.summary and it.summary:
                dup.summary = it.summary
    return out


class NewsManager:
    def __init__(self, fetch=None):
        self._fetch = fetch or self._http_get
        self._src_cache: dict = {}
        self._lock = threading.Lock()
        self.current: list[NewsItem] = []
        self.offset = 0
        self.query: NewsQuery | None = None
        self.focus: int | None = None
        self.updated_at: float = 0.0
        self.stale = False
        self.stats: dict[str, dict] = {}
        self.on_update = None

    @staticmethod
    def _load(name: str) -> dict:
        try:
            return json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Источники новостей %s не прочитаны: %s", name, exc)
            return {}

    def sources_for(self, category: str) -> list[dict]:
        if category == "gaming":
            return list(self._load("gaming_sources.json").get("sources", []))
        cats = self._load("news_sources.json").get("categories", {})
        return list(cats.get(category) or cats.get("general", []))

    def topic_words(self, topic: str) -> list[str]:
        """Слова для фильтра по теме: из словаря тем (playstation, gta…) или сама тема."""
        t = topic.lower().strip()
        if not t:
            return []
        topics = self._load("gaming_sources.json").get("topics", {})
        for key, words in topics.items():
            if t == key or any(w in t for w in words):
                return words + [key]
        return [w for w in re.findall(r"[a-zа-яё0-9]+", t) if len(w) > 2]

    @staticmethod
    def detect_category(text: str) -> str:
        t = (text or "").lower().replace("ё", "е")
        for cat in ("gaming", "ai", "space", "science", "business", "sports", "politics", "tech"):
            if re.search(CATEGORY_WORDS[cat], t):
                return cat
        return "general"

    @staticmethod
    def _http_get(url: str) -> str:
        r = requests.get(url, headers=UA, timeout=6)
        r.raise_for_status()
        if "charset" not in r.headers.get("content-type", "").lower():
            head = r.content[:2000].decode("ascii", "ignore").lower()
            m = re.search(r'(?:charset|encoding)=["\']?([\w-]+)', head)
            r.encoding = m.group(1) if m else (r.apparent_encoding or "utf-8")
        return r.text

    SOURCE_TTL = 300

    def _fetch_source(self, source: dict, category: str) -> list[NewsItem]:
        key = (source.get("channel") or source.get("url"), category, tuple(source.get("keywords", [])))
        cached = self._src_cache.get(key)
        if cached and time.time() - cached[0] < self.SOURCE_TTL:
            return [NewsItem(**asdict(i)) for i in cached[1]]
        items = self._fetch_source_now(source, category)
        self._src_cache[key] = (time.time(), items)
        return [NewsItem(**asdict(i)) for i in items]

    def _fetch_source_now(self, source: dict, category: str) -> list[NewsItem]:
        if source.get("type") == "telegram":
            page = self._fetch(f"https://t.me/s/{source['channel']}")
            items = parse_telegram(page, source, category)
        else:
            items = parse_rss(self._fetch(source["url"]), source, category)
        words = source.get("keywords", [])
        if words:
            items = [i for i in items if any(has_word(w, f"{i.title} {i.summary}") for w in words)]
        return items

    def fetch(self, query: NewsQuery) -> tuple[list[NewsItem], int, int]:
        """(новости, сколько источников ответило, сколько всего). Источники опрашиваются параллельно."""
        sources = self.sources_for(query.category)
        topic_words = self.topic_words(query.topic)
        if query.topic and query.category == "general":
            seen = {s.get("channel") or s.get("url") for s in sources}
            for extra in self.sources_for("gaming") + self.sources_for("tech"):
                key = extra.get("channel") or extra.get("url")
                if key not in seen:
                    seen.add(key)
                    sources.append(extra)
        if topic_words and query.category == "gaming":
            sources.sort(key=lambda s: 0 if set(s.get("topics", [])) & set(topic_words) else 1)
        items: list[NewsItem] = []
        ok = 0
        with ThreadPoolExecutor(max_workers=max(1, min(24, len(sources)))) as ex:
            futures = {ex.submit(self._fetch_source, s, query.category): s for s in sources}
            deadline = time.monotonic() + 10
            for fut, src in futures.items():
                try:
                    got = fut.result(timeout=max(0.1, deadline - time.monotonic()))
                    ok += 1
                    if topic_words and set(src.get("topics", [])) & set(topic_words):
                        for it in got:
                            it.priority += 0.3
                    items.extend(got)
                except Exception as exc:
                    log.debug("Новости: источник %s недоступен (%s)", src.get("name"), type(exc).__name__)
        return items, ok, len(sources)

    def select(self, items: list[NewsItem], query: NewsQuery, now: float | None = None) -> list[NewsItem]:
        """Свежесть → тема → дубликаты → ранжирование."""
        now = now or time.time()
        window = {"today": 24, "week": 24 * 7, "latest": 48}.get(query.period, 24) * 3600
        fresh = [i for i in items if 0 <= now - i.published_at <= window or -600 < now - i.published_at < 0]
        if len(fresh) < 3 and query.period != "week":
            fresh = [i for i in items if now - i.published_at <= 72 * 3600]
        words = self.topic_words(query.topic)
        if words:
            fresh = [i for i in fresh if any(has_word(w, f"{i.title} {i.summary}") for w in words)]
        stories = deduplicate(fresh)
        half_life = {"today": 8, "week": 48, "latest": 6}.get(query.period, 8) * 3600
        for s in stories:
            freshness = math.exp(-(now - s.published_at) / half_life)
            s.score = s.priority + freshness + 0.25 * min(3, len(s.also)) + (0.15 if s.telegram else 0)
        return sorted(stories, key=lambda s: -s.score)

    def get_news(self, query: NewsQuery) -> dict:
        items, ok, total = self.fetch(query)
        stale = False
        if ok == 0 or not items:
            cached = self._load_cache(query)
            if cached is None:
                return {"ok": False, "offline": True, "items": [], "query": query}
            items_all, fetched_at = cached
            stale = True
            stories = items_all
        else:
            stories = self.select(items, query)
            self._save_cache(query, stories)
            fetched_at = time.time()
        with self._lock:
            self.current, self.offset, self.query, self.focus = stories, 0, query, None
            self.page_start = 0
            self.updated_at, self.stale = fetched_at, stale
        page = self.next_page()
        self._report(query.category, len(stories), ok, stale)
        return {"ok": bool(page), "offline": stale, "items": page, "query": query, "sources": ok, "total_sources": total,
                "fetched_at": fetched_at, "count": len(stories)}

    page_start = 0

    def next_page(self) -> list[NewsItem]:
        with self._lock:
            page = self.current[self.offset:self.offset + PAGE]
            if page:
                self.page_start = self.offset
            self.offset += len(page)
        return page

    def item(self, index: int | None) -> NewsItem | None:
        """Новость по номеру в последней рассказанной группе («про вторую» — вторая из неё).
        Без номера — та, о которой шла речь (или первая)."""
        with self._lock:
            if not self.current:
                return None
            pos = (self.focus - 1) if index is None and self.focus else self.page_start + (index or 1) - 1
            if not 0 <= pos < len(self.current):
                return None
            self.focus = pos + 1
            return self.current[pos]

    def details(self, item: NewsItem) -> str:
        """Подробности: текст поста Telegram целиком или начало статьи (описание + первые абзацы)."""
        if item.telegram:
            try:
                post = self._fetch(item.url + "?embed=1")
                m = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', post, re.S)
                if m:
                    return first_sentences(clean_text(m.group(1)), 1200, 12)
            except Exception:
                pass
            return f"{item.title}. {item.summary}"
        try:
            page = self._fetch(item.url)
        except Exception:
            return f"{item.title}. {item.summary}"
        desc = re.search(r'<meta[^>]+(?:property="og:description"|name="description")[^>]+content="([^"]+)"', page)
        paras = [clean_text(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", page, re.S)]
        paras = [p for p in paras if len(p) > 60 and not re.search(r"cookie|подпис|©|реклам", p, re.I)]
        parts = []
        if desc:
            d = html.unescape(desc.group(1)).strip()
            if len(_tokens(d) - _tokens(item.title)) > 2:
                parts.append(d)
        for p in paras[:8]:
            if not any(len(_tokens(p) ^ _tokens(q)) < 3 for q in parts):
                parts.append(p)
        text = " ".join(parts)
        return first_sentences(text, 1400, 12) if text else item.summary or item.title

    def _save_cache(self, query: NewsQuery, stories: list[NewsItem]) -> None:
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
            data[query.key()] = {"fetched_at": time.time(), "items": [asdict(s) for s in stories[:20]]}
            CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except (OSError, ValueError) as exc:
            log.debug("news cache: %s", exc)

    def _load_cache(self, query: NewsQuery) -> tuple[list[NewsItem], float] | None:
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8")).get(query.key())
        except (OSError, ValueError):
            return None
        if not data:
            return None
        return [NewsItem(**i) for i in data["items"]], float(data["fetched_at"])

    def _report(self, category: str, count: int, sources: int, stale: bool, background: bool = False) -> None:
        self.stats[category] = {"count": count, "sources": sources, "updated": time.time(), "stale": stale}
        if self.on_update:
            try:
                self.on_update(category, {"count": count, "sources": sources, "stale": stale,
                                          "background": background})
            except Exception:
                pass

    def refresh_counts(self, categories=("general", "gaming", "ai")) -> None:
        """Фоновое обновление HUD: сколько свежих историй по категориям (без изменения текущей подборки)."""
        for cat in categories:
            q = NewsQuery(cat, "", "today")
            items, ok, _ = self.fetch(q)
            if ok:
                stories = self.select(items, q)
                self._save_cache(q, stories)
                self._report(cat, len([s for s in stories if time.time() - s.published_at <= 86400]), ok, False,
                             background=True)


_instance: NewsManager | None = None


def get() -> NewsManager:
    global _instance
    if _instance is None:
        _instance = NewsManager()
    return _instance
