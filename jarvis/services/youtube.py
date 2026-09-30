"""Поиск по YouTube без API-ключа: разбор ytInitialData со страниц youtube.com.

Работает через системный прокси (переменные окружения HTTP(S)_PROXY учитываются requests).
Все функции устойчивы к сбоям сети: при ошибке возвращают пустой список.
"""
from __future__ import annotations

import json
import logging
import math
import re
import xml.etree.ElementTree as ET
from typing import Any, Iterator
from urllib.parse import quote_plus

import requests

from jarvis.core.context import SearchResult
from jarvis.utils.text import similarity

log = logging.getLogger("jarvis.youtube")

BASE = "https://www.youtube.com"
_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
})
_session.cookies.update({"CONSENT": "YES+cb", "SOCS": "CAI"})
_DATA_RE = re.compile(r"var ytInitialData\s*=\s*(\{.*?\});\s*</script>", re.S)
_DATA_RE2 = re.compile(r"window\[\"ytInitialData\"\]\s*=\s*(\{.*?\});", re.S)


def search_url(query: str) -> str:
    return f"{BASE}/results?search_query={quote_plus(query)}"


def watch_url(video_id: str) -> str:
    return f"{BASE}/watch?v={video_id}"


def _get(url: str, params: dict | None = None, attempts: int = 3) -> str | None:
    last = None
    for _ in range(attempts):
        try:
            r = _session.get(url, params=params, timeout=(4, 8))
            if r.status_code == 200:
                return r.text
            last = f"HTTP {r.status_code}"
        except requests.RequestException as exc:
            last = type(exc).__name__
    log.warning("YouTube недоступен (%s): %s", url, last)
    return None


def _initial_data(html: str | None) -> dict | None:
    if not html:
        return None
    m = _DATA_RE.search(html) or _DATA_RE2.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _walk(obj: Any, key: str) -> Iterator[dict]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and isinstance(v, dict):
                yield v
            else:
                yield from _walk(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, key)


def _text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return node["simpleText"]
    if "runs" in node:
        return "".join(r.get("text", "") for r in node["runs"])
    return node.get("content", "")


def _from_video_renderer(v: dict) -> SearchResult | None:
    vid = v.get("videoId")
    if not vid:
        return None
    return SearchResult(title=_text(v.get("title")), url=watch_url(vid), source="youtube",
                        channel=_text(v.get("ownerText")) or _text(v.get("longBylineText")),
                        duration=_text(v.get("lengthText")) or ("LIVE" if v.get("badges") else ""))


def _from_lockup(lv: dict) -> SearchResult | None:
    cid = lv.get("contentId")
    if not cid:
        return None
    ctype = lv.get("contentType", "")
    meta = (lv.get("metadata") or {}).get("lockupMetadataViewModel") or {}
    title = _text(meta.get("title"))
    channel = ""
    for row in _walk(meta, "contentMetadataViewModel"):
        for r in row.get("metadataRows", []):
            parts = r.get("metadataParts") or []
            if parts:
                channel = _text(parts[0].get("text"))
                break
        break
    if "PLAYLIST" in ctype:
        watch = next(_walk(lv, "watchEndpoint"), None)
        if watch and watch.get("videoId"):
            url = f"{watch_url(watch['videoId'])}&list={watch.get('playlistId') or cid}"
        elif cid.startswith("RD") and len(cid) == 13:
            url = f"{watch_url(cid[2:])}&list={cid}"
        else:
            url = f"{BASE}/playlist?list={cid}"
    else:
        url = watch_url(cid)
    return SearchResult(title=title or cid, url=url, source="youtube", channel=channel)


def _collect(data: dict, limit: int) -> list[SearchResult]:
    results: list[SearchResult] = []
    seen = set()
    def visit(obj: Any):
        if len(results) >= limit:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "videoRenderer" and isinstance(v, dict):
                    r = _from_video_renderer(v)
                elif k == "lockupViewModel" and isinstance(v, dict):
                    r = _from_lockup(v)
                else:
                    visit(v)
                    continue
                if r and r.url not in seen:
                    seen.add(r.url)
                    results.append(r)
        elif isinstance(obj, list):
            for v in obj:
                visit(v)
    visit(data)
    return results[:limit]


def search(query: str, limit: int = 10) -> list[SearchResult]:
    data = _initial_data(_get(f"{BASE}/results", {"search_query": query}))
    if not data:
        return []
    return _collect(data, limit)


def find_channel(name: str) -> tuple[str, str] | None:
    """Возвращает (channel_id, title) наиболее подходящего канала."""
    data = _initial_data(_get(f"{BASE}/results", {"search_query": name, "sp": "EgIQAg=="}))
    candidates: list[tuple[str, str, int]] = []
    if data:
        for ch in _walk(data, "channelRenderer"):
            if ch.get("channelId"):
                subs = 0
                for key in ("subscriberCountText", "videoCountText"):
                    subs = max(subs, _parse_count(_text(ch.get(key))))
                candidates.append((ch["channelId"], _text(ch.get("title")), subs))
    if not candidates:
        return None
    def score(item):
        pos, (_, title, subs) = item
        return math.log10(subs + 10) + 1.5 * similarity(name, title) - 0.15 * pos

    best = max(enumerate(candidates), key=score)[1]
    return best[0], best[1]


def _parse_count(text: str) -> int:
    """«1,23 млн подписчиков» → 1230000, «345 тыс. подписчиков» → 345000."""
    if "подпис" not in text.lower() and "subscriber" not in text.lower():
        return 0
    m = re.search(r"([\d\s.,]+)\s*(тыс|млн|млрд|k|m|b)?", text.lower().replace("\xa0", " "))
    if not m:
        return 0
    try:
        value = float(m.group(1).replace(" ", "").replace(",", "."))
    except ValueError:
        return 0
    mult = {"тыс": 1e3, "k": 1e3, "млн": 1e6, "m": 1e6, "млрд": 1e9, "b": 1e9}.get(m.group(2) or "", 1)
    return int(value * mult)


def latest_videos(channel_id: str, limit: int = 5) -> list[SearchResult]:
    data = _initial_data(_get(f"{BASE}/channel/{channel_id}/videos"))
    if data:
        results = _collect(data, limit)
        if results:
            return results
    xml = _get(f"{BASE}/feeds/videos.xml", {"channel_id": channel_id}, attempts=2)
    if not xml:
        return []
    try:
        ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
        root = ET.fromstring(xml)
        author = root.findtext("a:title", default="", namespaces=ns)
        out = []
        for entry in root.findall("a:entry", ns)[:limit]:
            vid = entry.findtext("yt:videoId", namespaces=ns)
            out.append(SearchResult(entry.findtext("a:title", default="", namespaces=ns), watch_url(vid),
                                    "youtube", channel=author))
        return out
    except ET.ParseError:
        return []
