"""News Brain: получение, свежесть, темы, дубликаты, приоритет Telegram, диалог, офлайн (без сети — подмена загрузки)."""
from __future__ import annotations

import time
from email.utils import formatdate
from types import SimpleNamespace as NS

import pytest

from jarvis.services import news as news_service
from jarvis.services.news import NewsManager, NewsQuery, deduplicate, parse_rss, parse_telegram
from jarvis.tools.base import load_builtin_tools, registry

load_builtin_tools()
NOW = time.time()


def rss(*items):
    body = "".join(f"<item><title>{t}</title><link>https://example.com/{i}</link><description>{d}</description>"
                   f"<pubDate>{formatdate(NOW - age * 3600)}</pubDate></item>" for i, (t, d, age) in enumerate(items))
    return f'<?xml version="1.0" encoding="utf-8"?><rss><channel>{body}</channel></rss>'


def tg(channel, *posts):
    out = ""
    for i, (text, age) in enumerate(posts):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(NOW - age * 3600))
        out += (f'<div class="tgme_widget_message" data-post="{channel}/{100 + i}">'
                f'<div class="tgme_widget_message_text js-message_text" dir="auto">{text}</div>'
                f'<a class="tgme_widget_message_date"><time datetime="{stamp}"></time></a></div>')
    return f"<html><body>{out}</body></html>"


FEEDS = {
    "https://www.interfax.ru/rss.asp": rss(("Сочи ограничил приём самолётов", "Росавиация ввела ограничения.", 1),
                                           ("Старая новость про выборы", "Это было давно.", 80)),
    "https://tass.ru/rss/v2.xml": rss(("Аэропорт Сочи ограничил приём самолётов", "Сообщила Росавиация.", 2),
                                      ("Министр примет участие во встрече G20", "Встреча пройдёт в США.", 3)),
    "https://t.me/s/igromania": tg("igromania", ("Sony запустила предзаказы God of War для PlayStation 5<br>Релиз в "
                                                 "феврале.", 1), ("Вышел патч для Cyberpunk 2077 в Steam", 2)),
    "https://t.me/s/stopgameru": tg("stopgameru", ("Rockstar показала новый трейлер GTA 6<br>Фанаты в восторге.", 5)),
    "https://blog.playstation.com/feed/": rss(("PlayStation Plus: игры октября", "Список игр подписки.", 4)),
    "https://store.steampowered.com/feeds/news/": rss(("Steam Next Fest starts", "Hundreds of demos.", 3)),
    "https://rss.stopgame.ru/rss_news.xml": rss(("Sony запустила предзаказы God of War для PlayStation 5",
                                                 "Предзаказы открыты.", 1)),
}


@pytest.fixture
def mgr(monkeypatch, tmp_path):
    monkeypatch.setattr(news_service, "CACHE_FILE", tmp_path / "news_cache.json")
    calls = []

    def fetch(url):
        calls.append(url)
        if url in FEEDS:
            return FEEDS[url]
        raise OSError("нет такого источника в тесте")

    m = NewsManager(fetch=fetch)
    m.calls = calls
    monkeypatch.setattr(news_service, "_instance", m)
    return m


def test_parsers_and_dedup():
    items = parse_rss(FEEDS["https://www.interfax.ru/rss.asp"], {"name": "Интерфакс", "priority": 0.9}, "general")
    assert items[0].title == "Сочи ограничил приём самолётов" and items[0].url.startswith("https://")
    posts = parse_telegram(FEEDS["https://t.me/s/igromania"], {"name": "Игромания (TG)", "priority": 1.0}, "gaming")
    assert posts[0].telegram and posts[0].url == "https://t.me/igromania/100"
    assert "PlayStation 5" in posts[0].title and posts[0].summary.startswith("Релиз")
    both = items + parse_rss(FEEDS["https://tass.ru/rss/v2.xml"], {"name": "ТАСС", "priority": 0.85}, "general")
    merged = deduplicate(both)
    sochi = [s for s in merged if "Сочи" in s.title]
    assert len(sochi) == 1 and sochi[0].source == "Интерфакс" and sochi[0].also == ["ТАСС"]


def test_general_news_fresh_and_no_browser(mgr, monkeypatch):
    opened = []
    monkeypatch.setattr("os.startfile", lambda u: opened.append(u))
    r = registry.call("news_get", {"query": "Jarvis, какие сегодня новости?"}, NS())
    assert r.ok and r.message.startswith("Вот главное за сегодня.")
    assert "Первое —" in r.message and "Второе —" in r.message
    assert "Старая новость" not in r.message
    assert "подробнее" in r.message and not opened
    assert all(i["source"] for i in r.data["items"])


def test_gaming_prefers_telegram(mgr):
    r = registry.call("news_get", {"category": "gaming"}, NS())
    first = r.data["items"][0]
    assert "(Telegram)" in first["source"]
    assert any(u.startswith("https://t.me/s/") for u in mgr.calls)
    gow = [i for i in r.data["items"] if "God of War" in i["title"]]
    assert len(gow) == 1 and gow[0]["also"]


def test_topics_playstation_steam_gta(mgr):
    r = registry.call("news_get", {"query": "новости PlayStation"}, NS())
    titles = " ".join(i["title"] for i in r.data["items"])
    assert r.data["topic"] == "playstation" and "PlayStation" in titles and "GTA" not in titles
    r = registry.call("news_get", {"query": "новости стим"}, NS())
    assert r.data["topic"] == "steam" and all("steam" in (i["title"] + i["summary"]).lower() for i in r.data["items"])
    r = registry.call("news_get", {"query": "новости гта"}, NS())
    assert r.data["items"][0]["title"].startswith("Rockstar показала")


def test_follow_up_dialog(mgr, monkeypatch):
    opened = []
    monkeypatch.setattr("jarvis.tools._common.os.startfile", lambda u: opened.append(u))
    registry.call("news_get", {"query": "какие новости"}, NS())
    second = mgr.current[1]
    monkeypatch.setattr(mgr, "details", lambda it: f"Подробности: {it.summary}")
    d = registry.call("news_details", {"index": "вторую"}, NS())
    assert d.ok and d.message.startswith(second.title.rstrip("."))
    s = registry.call("news_source", {}, NS())
    assert s.ok and second.source in s.message and not opened
    o = registry.call("news_open", {}, NS())
    assert o.ok and opened == [second.url]


def test_more_news_and_end(mgr):
    news_service.PAGE, old = 2, news_service.PAGE
    try:
        registry.call("news_get", {"category": "gaming"}, NS())
        more = registry.call("news_more", {}, NS())
        assert more.ok and more.message.startswith("Вот ещё.")
        while not more.data.get("empty"):
            more = registry.call("news_more", {}, NS())
        assert "Другой информации нет" in more.message
    finally:
        news_service.PAGE = old


def test_offline_with_and_without_cache(mgr, monkeypatch):
    registry.call("news_get", {"category": "gaming"}, NS())
    monkeypatch.setattr(mgr, "_fetch", lambda url: (_ for _ in ()).throw(OSError("offline")))
    mgr._src_cache.clear()
    r = registry.call("news_get", {"category": "gaming"}, NS())
    assert r.message.startswith("Сэр, сейчас я не могу получить актуальные новости. У меня есть последние "
                                "сохранённые данные от")
    r = registry.call("news_get", {"category": "space"}, NS())
    assert not r.ok and r.message == "Сэр, сейчас я не могу получить актуальные новости."


def test_rules_route_news_without_browser(mgr):
    from jarvis.brain.rules import RuleParser
    from jarvis.config import Settings
    from jarvis.core.assistant import Runtime
    from jarvis.core.context import DialogContext

    p = RuleParser(Runtime(Settings(), DialogContext(), registry, apps=NS(resolve=lambda n: None)))

    def plan(text):
        pl = p.parse(text)
        return pl.actions[0].tool, pl.weak

    assert plan("Jarvis, какие сегодня новости?") == ("news_get", True)
    assert plan("что нового в AI") == ("news_get", True)
    assert plan("найди новости про gta") [0] == "news_get"
    registry.call("news_get", {"category": "general"}, NS())
    assert plan("расскажи подробнее про вторую")[0] == "news_details"
    assert plan("откуда эта информация")[0] == "news_source"
    assert plan("ещё новости")[0] == "news_more"
    assert plan("открой источник")[0] == "news_open"


def test_news_voice_intro_and_full_digest(mgr):
    from jarvis.voice import responder
    from jarvis.voice.intents import classify
    from jarvis.voice.responder import ToolCall

    r = registry.call("news_get", {"category": "general"}, NS())
    calls = [ToolCall("news_get", {}, r)]
    spoken = responder.compose(r.message, calls)
    assert spoken == r.message
    ri = classify(calls, r.message, spoken, "какие новости")
    assert ri.intent == "NEWS" and ri.dynamic


def test_stop_command_detection():
    from jarvis.voice.wake import is_stop_command

    for text in ("стоп, Jarvis", "Jarvis, стоп", "Джарвис стоп.", "хватит", "отбой джарвис", "перестань слушать"):
        assert is_stop_command(text), text
    for text in ("стоп музыку", "поставь на стоп", "джарвис", "хватит играть музыку", "включи стоп-кадр"):
        assert not is_stop_command(text), text


def test_stop_command_ends_conversation():
    from jarvis.core.assistant import Assistant, AssistantListener

    said = []

    class L(AssistantListener):
        def on_message(self, role, text):
            said.append((role, text))

    a = Assistant(L(), enable_voice=False)
    stopped = []
    a.speaker = NS(stop=lambda: stopped.append(1), say=lambda *x, **k: None, speaking=False, last_spoken_at=0.0,
                   name="fake")
    a.voice_replies = False
    try:
        a._handle("стоп, Jarvis")
        assert stopped and ("assistant", "Хорошо, сэр.") in said
    finally:
        a.shutdown()


def test_updater_zip_keeps_user_files(tmp_path, monkeypatch):
    """Обновление архивом: код заменяется, .env / settings.json / изменённые конфиги пользователя — нет."""
    import io
    import zipfile

    from jarvis.services import updater

    (tmp_path / "jarvis").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "main.py").write_text("old", encoding="utf-8")
    (tmp_path / "jarvis" / "old_module.py").write_text("x", encoding="utf-8")
    (tmp_path / ".env").write_text("GEMINI_API_KEY=secret", encoding="utf-8")
    (tmp_path / "config" / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config" / "news_sources.json").write_text("mine", encoding="utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in {"jarvis-main/main.py": "new", "jarvis-main/jarvis/core.py": "code",
                           "jarvis-main/.env": "HACK", "jarvis-main/config/settings.json": "theirs",
                           "jarvis-main/config/news_sources.json": "theirs", "jarvis-main/config/gaming_sources.json":
                           "added", "jarvis-main/../evil.py": "x"}.items():
            z.writestr(name, data)
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    monkeypatch.setattr(updater, "STATE_FILE", tmp_path / "config" / "update_state.json")
    monkeypatch.setattr(updater, "repo", lambda: ("user/jarvis", "main"))
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: NS(content=buf.getvalue(), raise_for_status=lambda: None))
    text = updater.apply_zip(updater.UpdateInfo(True, "zip", "abc123", "", "msg"))
    assert (tmp_path / "main.py").read_text() == "new" and (tmp_path / "jarvis" / "core.py").exists()
    assert not (tmp_path / "jarvis" / "old_module.py").exists()
    assert (tmp_path / ".env").read_text() == "GEMINI_API_KEY=secret"
    assert (tmp_path / "config" / "settings.json").read_text() == "{}"
    assert (tmp_path / "config" / "news_sources.json").read_text() == "mine"
    assert (tmp_path / "config" / "gaming_sources.json").read_text() == "added"
    assert not (tmp_path.parent / "evil.py").exists()
    assert "abc123" in (tmp_path / "config" / "update_state.json").read_text()
    assert "Обновлено" in text
