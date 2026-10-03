"""Понимание речи: нормализация, нечёткие названия, намерения и сущности, контекст, уверенность, уточнения.

Фразы — те, что люди говорят на самом деле: с «Джарвис», паразитами, вежливыми формами, другим порядком слов
и ошибками распознавания. Инструменты не выполняются — проверяется только, как команда понята.
"""
from __future__ import annotations

import time

import pytest

from jarvis.brain.rules import RuleParser
from jarvis.config import Settings
from jarvis.core.assistant import Runtime
from jarvis.core.context import DialogContext
from jarvis.nlu.lexicon import Lexicon
from jarvis.nlu.normalizer import TextNormalizer
from jarvis.nlu.understanding import SpeechUnderstanding
from jarvis.services.app_index import AppEntry, AppIndex
from jarvis.tools.base import load_builtin_tools, registry
from jarvis.voice.stt import STTResult

load_builtin_tools()

INSTALLED = ["Discord", "Steam", "Google Chrome", "Telegram Desktop", "Firefox", "Zoom Workplace", "Spotify"]


@pytest.fixture(scope="module")
def settings():
    return Settings()


@pytest.fixture(scope="module")
def lexicon(settings):
    return Lexicon(settings)


@pytest.fixture
def nlu(settings, lexicon):
    apps = AppIndex(settings)
    apps.entries = [AppEntry(n, f"C:\\fake\\{n}.lnk", "lnk") for n in INSTALLED]
    apps.ready.set()
    rt = Runtime(settings, DialogContext(), registry, apps)
    return SpeechUnderstanding(rt, RuleParser(rt), lexicon)


@pytest.mark.parametrize("raw, text", [
    ("Джарвис открой мне хром", "открой мне Chrome"),
    ("Ну Джарвис, можешь пожалуйста запустить Discord?", "запусти Discord"),
    ("Блин, Джарвис, запусти хром", "запусти Chrome"),
    ("Ну давай откроем стим", "открой Steam"),
    ("Открой-ка мне браузер", "Открой мне браузер"),
    ("я хочу чтобы ты открыл телегу", "открой Telegram"),
    ("не мог бы ты включить впн", "включи VPN"),
    ("Jarvis, блядь, Telegram мне открой", "открой Telegram"),
    ("открой диск орд", "открой Discord"),
    ("запусти стем", "запусти Steam"),
    ("открой дискордд", "открой Discord"),
    ("сверни, сверни доту", "сверни Dota 2"),
    ("напиши пожалуйста привет как дела", "напиши пожалуйста привет как дела"),
])
def test_normalizer(lexicon, raw, text):
    assert TextNormalizer(lexicon).normalize(raw).text == text


def test_normalizer_keeps_ordinary_words(lexicon):
    n = TextNormalizer(lexicon)
    for raw in ("открой дискотеку", "поставь будильник", "открой стол", "включи музыку"):
        assert n.normalize(raw).text.lower() == raw


@pytest.mark.parametrize("word, name", [
    ("Discord", "Discord"), ("дискорд", "Discord"), ("дискор", "Discord"), ("дискордд", "Discord"),
    ("дискорда", "Discord"), ("Steam", "Steam"), ("стим", "Steam"), ("стимм", "Steam"), ("стиме", "Steam"),
    ("хром", "Chrome"), ("ютуб", "YouTube"), ("кс", "Counter-Strike 2"), ("доту", "Dota 2"),
])
def test_fuzzy_names(lexicon, word, name):
    m = lexicon.match(word, ("apps", "games", "sites"), threshold=0.82)
    assert m.best is not None and m.best.name == name and not m.ambiguous


def test_fuzzy_rejects_unrelated_words(lexicon):
    for word in ("стол", "радио", "хоккей", "кино", "тест"):
        assert lexicon.match(word, ("apps", "games", "sites"), threshold=0.82).best is None, word


def test_two_equal_candidates_are_not_guessed(settings):
    lx = Lexicon(settings, extra={"apps": {"Mail.ru Агент": ["агент"], "Агент Smith": ["агент"]}})
    m = lx.match("агент", ("apps",))
    assert m.ambiguous and {m.best.name, *(c.name for c in m.others)} == {"Mail.ru Агент", "Агент Smith"}


@pytest.mark.parametrize("phrase, intent, entity", [
    ("Джарвис открой хром", "OPEN_BROWSER", "Chrome"),
    ("Jarvis, открой Chrome", "OPEN_BROWSER", "Chrome"),
    ("Джарвис, можешь открыть браузер?", "OPEN_BROWSER", None),
    ("Открой-ка мне браузер", "OPEN_BROWSER", None),
    ("Блин, Джарвис, запусти хром", "OPEN_BROWSER", "Chrome"),
    ("Джарвис, пожалуйста, запусти дискорд", "OPEN_APPLICATION", "Discord"),
    ("Ну Джарвис, можешь пожалуйста запустить Discord?", "OPEN_APPLICATION", "Discord"),
    ("Джарвис, можешь пожалуйста открыть мне дискорд?", "OPEN_APPLICATION", "Discord"),
    ("Открой Дискорд", "OPEN_APPLICATION", "Discord"),
    ("Открой Discord", "OPEN_APPLICATION", "Discord"),
    ("Запусти Стим", "OPEN_APPLICATION", "Steam"),
    ("Запусти Steam", "OPEN_APPLICATION", "Steam"),
    ("Ну давай откроем стим", "OPEN_APPLICATION", "Steam"),
    ("Открой мне браузер", "OPEN_BROWSER", None),
    ("Можешь закрыть Discord?", "CLOSE_APPLICATION", "Discord"),
    ("Закрой Discord", "CLOSE_APPLICATION", "Discord"),
    ("Включи VPN", "VPN_ON", None),
    ("Выключи VPN", "VPN_OFF", None),
    ("Джарвис что у меня сейчас на экране?", "SCREEN_READ", None),
    ("Что у меня сейчас на экране?", "SCREEN_READ", None),
    ("Прочитай что написано", "SCREEN_READ", None),
    ("Прочитай, что здесь написано", "SCREEN_READ", None),
    ("Удали Steam", "UNINSTALL_APPLICATION", "Steam"),
    ("Добавь Discord в исключения", "EXCLUSION_ADD", "Discord"),
    ("Какие у меня программы в исключениях?", "EXCLUSION_LIST", None),
    ("Открой YouTube в хроме", "OPEN_URL", "YouTube"),
    ("открой диск орд", "OPEN_APPLICATION", "Discord"),
    ("запусти стем", "OPEN_APPLICATION", "Steam"),
])
def test_intent_and_entities(nlu, phrase, intent, entity):
    u = nlu.understand(phrase, STTResult(phrase, 0.93, [phrase]))
    assert u.intent == intent, u.trace()
    if entity:
        assert entity in u.entities.values(), u.trace()
    assert u.level == "high", u.trace()
    assert not u.clarify


def test_url_with_browser_entities(nlu):
    u = nlu.understand("Открой YouTube в Chrome")
    assert u.tool == "open_site" and u.entities == {"site": "YouTube", "browser": "Chrome"}


def test_screen_read_mode(nlu):
    assert nlu.understand("Прочитай что написано").args.get("mode")
    assert not nlu.understand("что у меня на экране").args.get("mode")


def test_best_stt_alternative_wins(nlu):
    stt = STTResult("открой дискотеку", 0.9, ["открой дискотеку", "открой дискорд"])
    u = nlu.understand("открой дискотеку", stt)
    assert u.tool == "open_app" and u.entities.get("application") == "Discord", u.trace()


def test_low_confidence_asks_to_repeat(nlu):
    u = nlu.understand("ma", STTResult("ma", 0.5, ["ma"]))
    assert u.level == "low"
    u = nlu.understand("открой Discord", STTResult("открой Discord", 0.35, ["открой Discord"]))
    assert u.level == "low"


def test_typed_text_is_never_low(nlu):
    assert nlu.understand("ma", typed=True).level != "low"


def test_medium_fuzzy_name_is_clarified(nlu):
    u = nlu.understand("запусти стем", STTResult("запусти стем", 0.6, ["запусти стем"]))
    assert u.level == "medium" and u.clarify == "Вы имели в виду Steam?" and u.suggestion == "Steam"


def test_ambiguous_name_asks_which(settings):
    apps = AppIndex(settings)
    apps.entries = []
    apps.ready.set()
    rt = Runtime(settings, DialogContext(), registry, apps)
    lx = Lexicon(settings, extra={"apps": {"Mail.ru Агент": ["агент"], "Агент Smith": ["агент"]}})
    u = SpeechUnderstanding(rt, RuleParser(rt), lx).understand("открой агент")
    assert u.clarify and set(u.candidates) == {"Mail.ru Агент", "Агент Smith"}


def test_context_now_youtube_after_chrome(nlu):
    nlu.rt.dialog.add_turn("открой Chrome", "Запускаю Google Chrome.", ["open_app"])
    nlu.rt.dialog.last_app = "Google Chrome"
    u = nlu.understand("Теперь YouTube.")
    assert u.tool == "open_site" and u.entities.get("site") == "YouTube", u.trace()
    assert u.entities.get("browser") == "Google Chrome" and u.context


def test_context_news_followup(nlu):
    nlu.rt.dialog.add_turn("Какие новости по GTA?", "Первое — …", ["news_get"])
    u = nlu.understand("А про Minecraft?")
    assert u.tool == "news_get" and "Minecraft" in u.args.get("query", ""), u.trace()


def test_context_expires(nlu):
    nlu.rt.dialog.add_turn("открой Chrome", "Запускаю.", ["open_app"])
    nlu.rt.dialog.history[-1].at = time.time() - 600
    assert nlu.understand("теперь ютуб").context is None


def test_trace_lists_pipeline(nlu):
    stt = STTResult("открой дискорд пожалуйста", 0.96, ["открой дискорд пожалуйста"])
    stt.audio_note = "1.8 с, срез < 80 Гц"
    trace = nlu.understand("открой дискорд пожалуйста", stt).trace()
    for line in ("RAW AUDIO: 1.8 с", "STT: «открой дискорд пожалуйста» (0.96)", "NORMALIZED: «открой Discord»",
                 "INTENT: OPEN_APPLICATION", "ENTITIES: application=Discord", "CONFIDENCE:", "TOOL: open_app"):
        assert line in trace, trace
