"""Тесты локального разбора команд (без выполнения действий)."""
from __future__ import annotations

import pytest

from jarvis.brain.rules import RuleParser
from jarvis.config import PLUGINS_DIR, Settings
from jarvis.core.assistant import Runtime
from jarvis.core.context import DialogContext, SearchResult
from jarvis.services.app_index import AppMatch
from jarvis.tools.base import load_builtin_tools, load_plugins, registry

load_builtin_tools()
load_plugins(PLUGINS_DIR)


class FakeApps:
    known = {"discord": "Discord", "дискорд": "Discord", "steam": "Steam", "стим": "Steam", "telegram": "Telegram",
             "телеграм": "Telegram", "chrome": "Google Chrome", "хром": "Google Chrome", "spotify": "Spotify",
             "блокнот": "Блокнот", "firefox": "Firefox"}

    def resolve(self, name):
        key = name.lower().strip()
        if key in self.known:
            return AppMatch(key, self.known[key], "x", "startapp")
        return None


@pytest.fixture()
def parser():
    rt = Runtime(Settings(), DialogContext(), registry, FakeApps())
    return RuleParser(rt)


def one(parser, text):
    plan = parser.parse(text)
    assert plan is not None, f"не разобрано: {text}"
    return [(a.tool, a.args) for a in plan.actions]


@pytest.mark.parametrize("text,tool,args", [
    ("Открой YouTube", "open_site", {"site": "YouTube"}),
    ("открой ютуб", "open_site", {"site": "ютуб"}),
    ("Включи музыку", "play_media", {}),
    ("Найди мне музыку для учёбы", "search_youtube", {"query": "музыку для учёбы"}),
    ("Поставь на YouTube последний ролик от Wylsacom", "youtube_latest", {"channel": "Wylsacom"}),
    ("Открой Discord", "open_app", {"app": "Discord"}),
    ("Запусти Steam", "open_app", {"app": "Steam"}),
    ("Включи VPN", "vpn_on", {}),
    ("Выключи VPN", "vpn_off", {}),
    ("Включи впн", "vpn_on", {}),
    ("Сделай громкость 30 процентов", "set_volume", {"percent": 30}),
    ("громкость тридцать пять", "set_volume", {"percent": 35}),
    ("сделай погромче", "change_volume", {"delta": 10}),
    ("тише на 20", "change_volume", {"delta": -20}),
    ("выключи звук", "mute", {}),
    ("Поставь видео на паузу", "pause_media", {}),
    ("пауза", "pause_media", {}),
    ("продолжи", "resume_media", {}),
    ("сними с паузы", "resume_media", {}),
    ("следующий трек", "next_track", {}),
    ("Открой мои загрузки", "open_folder", {"folder": "мои загрузки"}),
    ("открой рабочий стол", "open_folder", {"folder": "рабочий стол"}),
    ("открой диск D", "open_folder", {"folder": "диск D"}),
    ("открой проводник", "open_folder", {"folder": "проводник"}),
    ("Закрой Chrome", "close_app", {"app": "Chrome"}),
    ("закрой вкладку", "press_key", {"keys": "ctrl+w"}),
    ("напиши привет, как дела", "type_text", {"text": "привет, как дела"}),
    ("нажми ctrl+c", "press_key", {"keys": "ctrl+c"}),
    ("который час", "get_time", {}),
    ("какое сегодня число", "get_date", {}),
    ("заблокируй компьютер", "lock_screen", {}),
    ("выключи компьютер", "shutdown_computer", {}),
    ("перезагрузи компьютер через 2 минуты", "restart_computer", {"delay": 120}),
    ("найди в интернете рецепт борща", "search_web", {"query": "рецепт борща"}),
    ("включи на ютубе imagine dragons believer", "play_youtube", {"query": "imagine dragons believer"}),
    ("поставь песню Believer", "play_media", {"query": "песню Believer"}),
    ("какая погода в Казани", "get_weather", {"city": "Казани"}),
    ("какая погода", "get_weather", {}),
    ("открой habr.com", "open_url", {"url": "habr.com"}),
    ("открой какое-нибудь видео по Dota", "play_media", {"query": "видео по Dota"}),
    ("запусти видео про котиков", "play_media", {"query": "видео про котиков"}),
    ("открой мои видео", "open_folder", {"folder": "мои видео"}),
    ("Джарвис, пожалуйста, открой Discord", None, None),
])
def test_single_commands(parser, text, tool, args):
    if tool is None:
        return
    actions = one(parser, text)
    assert actions[0] == (tool, args), actions


def test_context_there_youtube(parser):
    parser.rt.dialog.active_site = "youtube"
    assert one(parser, "Найди там музыку для учебы") == [("search_youtube", {"query": "музыку для учебы"})]


def test_context_first_result(parser):
    parser.rt.dialog.set_results("youtube", "lofi", [SearchResult("A", "https://youtube.com/watch?v=1", "youtube")])
    assert one(parser, "Включи первый результат") == [("open_result", {"index": 1})]
    assert one(parser, "открой второе видео") == [("open_result", {"index": 2})]
    assert one(parser, "включи последний") == [("open_result", {"index": -1})]


def test_chain(parser):
    actions = one(parser, "открой ютуб и найди там музыку для учебы")
    assert actions == [("open_site", {"site": "ютуб"}), ("search_youtube", {"query": "музыку для учебы"})]


def test_chain_then(parser):
    actions = one(parser, "запусти дискорд, а потом сделай громкость 20")
    assert actions == [("open_app", {"app": "дискорд"}), ("set_volume", {"percent": 20})]


def test_chain_without_commas_from_stt(parser):
    actions = one(parser, "открой YouTube найди музыку для учёбы и поставь громкость 30%")
    tools = [t for t, _ in actions]
    assert tools == ["open_site", "search_youtube", "set_volume"], actions
    assert actions[2][1] == {"percent": 30}
    assert one(parser, "напиши открой дверь") == [("type_text", {"text": "открой дверь"})]


@pytest.mark.parametrize("text,account", [
    ("Смени аккаунт в Стиме", None),
    ("Смени аккаунт", None),
    ("смени аккаунт на Смурф", "Смурф"),
    ("смени аккаунт в стиме на Смурф", "Смурф"),
    ("Зайди на аккаунт Смурф", "Смурф"),
    ("зайди на аккаунт MainAcc в стиме", "MainAcc"),
])
def test_steam_switch_commands(parser, text, account):
    actions = one(parser, text)
    assert actions[0][0] == "steam_switch_account", actions
    assert actions[0][1].get("account") == account


@pytest.mark.parametrize("text,tool", [
    ("отправь", "send_enter"),
    ("выключи микрофон", "discord_toggle_mute"),
    ("включи микрофон в дискорде", "discord_toggle_mute"),
    ("выключи звук в дискорде", "discord_toggle_deafen"),
    ("выключи звук", "mute"),
])
def test_new_short_commands(parser, text, tool):
    assert one(parser, text)[0][0] == tool


def test_unknown_goes_to_llm(parser):
    plan = parser.parse("расскажи анекдот про программистов")
    assert plan is None or plan.weak
