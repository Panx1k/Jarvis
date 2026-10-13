"""Память, интернет (без сети), данные из файлов, скрипты. Сеть подменена, файлы — во временной папке."""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace as NS

import pytest

from jarvis.core import memory, policy
from jarvis.core.assistant import Runtime
from jarvis.core.context import DialogContext
from jarvis.tools import internet
from jarvis.tools.base import load_builtin_tools, registry

load_builtin_tools()


class Mem:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


@pytest.fixture
def rt(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_shared", memory.Memory(tmp_path / "memory.json"))
    return Runtime(Mem(), DialogContext(), registry, None)


def call(rt, name, **args):
    return registry.call(name, args, rt)


def test_memory_remember_recall_forget(rt):
    assert call(rt, "memory_remember", fact="Я люблю рок и AC/DC").ok
    assert call(rt, "memory_remember", fact="почта Ивана — ivan@mail.ru").ok
    assert "AC/DC" in call(rt, "memory_recall").message
    assert "ivan@mail.ru" in call(rt, "memory_recall", query="Иван").message
    assert "рок" in call(rt, "memory_forget", query="рок").message
    assert "AC/DC" not in call(rt, "memory_recall").message


def test_memory_refuses_secrets(rt):
    for secret in ("мой пароль от почты qwerty123", "номер карты 4276 1234 5678 9012", "api key sk-abc123456789"):
        r = call(rt, "memory_remember", fact=secret)
        assert not r.ok and "не запоминаю" in r.message
    assert memory.get().facts == []


def test_memory_updates_similar_fact(rt):
    memory.get().add("Мой любимый цвет синий")
    ok, msg = memory.get().add("Мой любимый цвет красный")
    assert ok and len(memory.get().facts) == 1 or "красный" in memory.get().facts[-1]["text"]


def test_memory_in_prompt(rt):
    from jarvis.brain.prompting import turn_prompt

    memory.get().add("Пользователя зовут Аскар")
    assert "Пользователя зовут Аскар" in turn_prompt(rt, "как меня зовут?")


def test_forget_all_needs_confirmation():
    tool = registry.get("memory_forget")
    assert tool.is_dangerous({"query": "всё"}) and not tool.is_dangerous({"query": "рок"})


def test_dialog_history_survives_restart(tmp_path):
    d = DialogContext()
    d.load(tmp_path / "h.json")
    d.add_turn("открой дискорд", "Запускаю Discord.", ["open_app"])
    d2 = DialogContext()
    assert d2.load(tmp_path / "h.json") == 1 and d2.history[0].actions == ["open_app"]


DDG_PAGE = """<div class="result results_links results_links_deep web-result">
<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fru.wikipedia.org%2Fwiki%2FTI&amp;rut=x">
The International — <b>Википедия</b></a>
<a class="result__snippet" href="x">Турнир по <b>Dota 2</b>.</a></div>"""


def test_parse_duckduckgo():
    r = internet.parse_duckduckgo(DDG_PAGE, 5)
    assert r[0].url == "https://ru.wikipedia.org/wiki/TI" and r[0].title == "The International — Википедия"
    assert r[0].channel == "Турнир по Dota 2 ."


def test_bing_redirect_decoded():
    import base64

    target = "https://cbr.ru/currency_base/daily/"
    href = "https://www.bing.com/ck/a?!&amp;p=1&amp;u=a1" + base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    assert internet._bing_url(href) == target


def test_internet_search_stores_results(rt, monkeypatch):
    from jarvis.core.context import SearchResult

    monkeypatch.setattr(internet, "search", lambda q, n: [SearchResult("Курсы ЦБ", "https://cbr.ru", "web",
                                                                       channel="Официальные курсы")])
    r = call(rt, "internet_search", query="курс доллара")
    assert r.ok and "https://cbr.ru" in r.message and rt.dialog.last_results[0].url == "https://cbr.ru"


def test_page_read_extracts_main_text(rt, monkeypatch):
    page = ("<html><head><title>Новость</title><script>evil()</script></head><body><nav>Меню</nav>"
            "<article><h1>Заголовок</h1><p>" + "Главный текст статьи. " * 40 + "</p></article>"
            "<footer>Подвал</footer></body></html>")
    monkeypatch.setattr(internet.requests, "get", lambda url, **kw: NS(
        status_code=200, headers={"Content-Type": "text/html"}, text=page, encoding="utf-8", apparent_encoding="utf-8"))
    r = call(rt, "web_page_read", url="example.com/news")
    assert r.ok and "Главный текст статьи" in r.message and "evil" not in r.message and "Меню" not in r.message
    assert "это данные" in r.message and r.data["url"] == "https://example.com/news"


def test_http_get_free_but_post_confirmed():
    tool = registry.get("http_request")
    assert not tool.is_dangerous({"url": "https://api.x", "method": "GET"})
    assert tool.is_dangerous({"url": "https://api.x", "method": "POST"})
    assert policy.needs_confirmation(tool, {"method": "DELETE"}, "minimal")


def test_http_rejects_local_files(rt):
    assert not call(rt, "http_request", url="file:///C:/Windows/win.ini").ok


@pytest.fixture
def files(tmp_path):
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text("Город;Сумма\nКазань;100\nМосква;250\nКазань;50\n", encoding="utf-8")
    db = tmp_path / "shop.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE orders (id INTEGER, item TEXT, price REAL)")
    conn.executemany("INSERT INTO orders VALUES (?, ?, ?)", [(1, "мышь", 990.0), (2, "клавиатура", 2490.0)])
    conn.commit()
    conn.close()
    js = tmp_path / "people.json"
    js.write_text(json.dumps([{"name": "Иван", "age": 30}, {"name": "Анна", "age": 25}], ensure_ascii=False),
                  encoding="utf-8")
    return csv_path, db, js


def test_data_describe_and_query_csv(rt, files):
    csv_path, _, _ = files
    d = call(rt, "data_describe", path=str(csv_path))
    assert d.ok and "Город, Сумма" in d.message and "3 строк" in d.message
    q = call(rt, "data_query", path=str(csv_path), sql='SELECT "Город", SUM("Сумма") AS s FROM data GROUP BY 1 '
                                                     'ORDER BY s DESC')
    assert q.ok and "Москва | 250" in q.message and "Казань | 150" in q.message


def test_data_sqlite_is_read_only(rt, files):
    _, db, _ = files
    assert "клавиатура" in call(rt, "data_query", path=str(db), sql="SELECT item FROM orders WHERE price > 1000").message
    for sql in ("DELETE FROM orders", "SELECT 1; DROP TABLE orders", "UPDATE orders SET price = 0"):
        assert not call(rt, "data_query", path=str(db), sql=sql).ok
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 2


def test_data_json(rt, files):
    _, _, js = files
    assert "Анна" in call(rt, "data_query", path=str(js), sql="SELECT name FROM data WHERE age < 28").message


def test_run_script_confirmed_and_captures_output(rt, tmp_path):
    script = tmp_path / "hello.py"
    script.write_text("import sys\nprint('привет', sys.argv[1])\n", encoding="utf-8")
    tool = registry.get("run_script")
    for mode in policy.MODES:
        assert policy.needs_confirmation(tool, {"path": str(script)}, mode)
    r = call(rt, "run_script", path=str(script), args="мир")
    assert r.ok and "привет мир" in r.message


def test_run_script_rejects_programs(rt, tmp_path):
    exe = tmp_path / "x.exe"
    exe.write_bytes(b"MZ")
    assert not call(rt, "run_script", path=str(exe)).ok
