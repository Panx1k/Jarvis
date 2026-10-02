"""Steam (библиотека), Spotify (через подменённый клиент API), Discord, окна, защита живого режима от выдумок."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from jarvis.tools.base import load_builtin_tools, registry
from jarvis.utils import vdf
from jarvis.voice.gemini_live import unbacked_claim

load_builtin_tools()


def test_vdf_parser():
    d = vdf.loads('"AppState"\n{\n\t"appid"\t\t"730"\n\t"name"\t"Counter-Strike 2" // comment\n'
                  '\t"UserConfig" { "language" "russian" }\n}')
    assert vdf.get(d, "appstate", "AppID") == "730"
    assert vdf.get(d, "AppState", "userconfig", "language") == "russian"
    assert vdf.get(d, "AppState", "missing", default="x") == "x"


@pytest.fixture
def fake_steam(tmp_path, monkeypatch):
    from jarvis.tools import steam

    apps = tmp_path / "steamapps"
    (apps / "common" / "Counter-Strike Global Offensive").mkdir(parents=True)
    (apps / "common" / "dota 2 beta").mkdir(parents=True)
    (apps / "appmanifest_730.acf").write_text(
        '"AppState" { "appid" "730" "name" "Counter-Strike 2" "installdir" "Counter-Strike Global Offensive" '
        '"SizeOnDisk" "10737418240" "LastPlayed" "1700000000" }', encoding="utf-8")
    (apps / "appmanifest_570.acf").write_text(
        '"AppState" { "appid" "570" "name" "Dota 2" "installdir" "dota 2 beta" "SizeOnDisk" "0" }', encoding="utf-8")
    cfg = tmp_path / "userdata" / "42" / "config"
    cfg.mkdir(parents=True)
    (cfg / "localconfig.vdf").write_text(
        '"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" { '
        '"730" { "LastPlayed" "1800000000" "Playtime" "600" "Playtime2wks" "90" } } } } } }', encoding="utf-8")
    monkeypatch.setattr(steam, "steam_dir", lambda: tmp_path)
    monkeypatch.setattr(steam, "current_login", lambda: "me")
    monkeypatch.setattr(steam, "read_accounts",
                        lambda vdf=None: [steam.SteamAccount(str(steam.STEAM_ID64_BASE + 42), "me", "Me", True)])
    return steam


def test_steam_library_and_games_tool(fake_steam):
    games = fake_steam.library()
    assert [g.name for g in games] == ["Counter-Strike 2", "Dota 2"]
    cs = games[0]
    assert cs.playtime_h == 10 and cs.recent_h == 1.5 and cs.size_gb == 10 and cs.last_played == 1800000000
    r = registry.call("steam_games", {}, NS())
    assert r.ok and "Counter-Strike 2 — 10 ч" in r.message and "Dota 2" in r.message
    info = registry.call("steam_game_info", {"game": "кс"}, NS())
    assert info.ok and "10 ч" in info.message
    assert "Counter-Strike 2 (10 ч" in fake_steam.library_summary()


def test_steam_uninstall_needs_confirmation(monkeypatch):
    from jarvis.tools import steam

    assert registry.get("steam_uninstall").is_dangerous({"game": "Dota 2"})
    monkeypatch.setattr(steam, "running_steam_games", lambda: [])
    assert not registry.get("steam_install").is_dangerous({"game": "Rust"})
    monkeypatch.setattr(steam, "running_steam_games", lambda: ["Counter-Strike 2"])
    assert registry.get("steam_install").is_dangerous({"game": "Rust"})


class FakeSpotify:
    connected = True

    def __init__(self):
        self.calls = []

    def search(self, query, kind="track", limit=5):
        self.calls.append(("search", query, kind))
        return [{"name": "Numb", "uri": "spotify:track:1", "id": "1", "artists": [{"name": "Linkin Park"}]}]

    def my_playlists(self):
        return [{"name": "Для учёбы", "uri": "spotify:playlist:study"}]

    def player(self, method, path, params=None, body=None):
        self.calls.append((method, path, params, body))

    def play(self, body):
        self.calls.append(("PUT", "/play", None, body))
        uri = (body.get("uris") or [body.get("context_uri")])[0]
        return {"is_playing": True, "item": {"name": "Numb", "uri": uri, "artists": [{"name": "Linkin Park"}]}}

    def current(self):
        return {"is_playing": True, "item": {"name": "Numb", "id": "1", "uri": "spotify:track:1",
                                             "artists": [{"name": "Linkin Park"}], "album": {"name": "Meteora"}}}

    def api(self, method, path, params=None, body=None):
        self.calls.append((method, path, params))
        if path == "/me/tracks" and method == "GET":
            return {"items": [{"track": {"uri": f"spotify:track:{i}"}} for i in range(3)]}


@pytest.fixture
def spotify(monkeypatch):
    from jarvis.services import spotify as sp

    fake = FakeSpotify()
    monkeypatch.setattr(sp, "get", lambda: fake)
    return fake


def ctx():
    return NS(dialog=NS(now_playing=None))


def test_spotify_play_track(spotify):
    r = registry.call("spotify_play", {"query": "numb linkin park"}, ctx())
    assert r.ok and "Numb — Linkin Park" in r.message
    assert ("PUT", "/play", None, {"uris": ["spotify:track:1"]}) in spotify.calls


def test_spotify_play_own_playlist(spotify):
    r = registry.call("spotify_play", {"query": "для учебы", "kind": "playlist"}, ctx())
    assert r.ok and ("PUT", "/play", None, {"context_uri": "spotify:playlist:study"}) in spotify.calls


def test_spotify_other_tools(spotify):
    assert "Meteora" in registry.call("spotify_now_playing", {}, ctx()).message
    assert registry.call("spotify_like", {}, ctx()).ok and ("PUT", "/me/tracks", {"ids": "1"}) in spotify.calls
    assert registry.call("spotify_queue", {"query": "numb"}, ctx()).ok
    assert ("POST", "/queue", {"uri": "spotify:track:1"}, None) in spotify.calls
    registry.call("spotify_volume", {"percent": 150}, ctx())
    assert ("PUT", "/volume", {"volume_percent": 100}, None) in spotify.calls
    assert registry.call("spotify_play_liked", {}, ctx()).ok


def test_spotify_not_connected_is_honest(monkeypatch):
    from jarvis.services import spotify as sp
    from jarvis.tools import spotify as tools

    monkeypatch.setattr(sp, "get", lambda: NS(connected=False))
    opened = []
    monkeypatch.setattr(tools.os, "startfile", lambda uri: opened.append(uri))
    r = registry.call("spotify_play", {"query": "numb"}, ctx())
    assert not r.ok and "не подключён" in r.message and opened == ["spotify:search:numb"]


def test_spotify_token_encrypted_roundtrip():
    from jarvis.services.spotify import _protect, _unprotect

    blob = _protect(b'{"refresh_token": "abc"}')
    assert b"abc" not in blob and _unprotect(blob) == b'{"refresh_token": "abc"}'


def test_discord_message_needs_confirmation():
    t = registry.get("discord_send_message")
    assert t.is_dangerous({"to": "Вася", "text": "привет"})
    assert "Вася" in t.confirm_text({"to": "Вася", "text": "привет"})


def test_window_rules():
    from jarvis.brain.rules import RuleParser
    from jarvis.config import Settings
    from jarvis.core.assistant import Runtime
    from jarvis.core.context import DialogContext

    p = RuleParser(Runtime(Settings(), DialogContext(), registry, apps=NS(resolve=lambda n: None)))

    def tool_of(text):
        plan = p.parse(text)
        return [(a.tool, a.args) for a in plan.actions] if plan else None

    assert tool_of("сверни дискорд") == [("window_minimize", {"app": "дискорд"})]
    assert tool_of("сверни все окна") == [("show_desktop", {})]
    assert tool_of("открой стим на весь экран") == [("window_maximize", {"app": "стим"})]


@pytest.mark.parametrize("reply,actions,expected", [
    ("Переключаю Steam на аккаунт MainAcc, сэр.", ["steam_accounts"], True),
    ("Discord уже открыт, сэр.", [], True),
    ("Steam запущен, сэр.", [], True),
    ("Переключаю Steam на аккаунт MainAcc.", ["steam_switch_account"], False),
    ("Discord запущен, открыт канал general.", ["discord_status"], False),
    ("Сегодня в Москве 12 градусов, сэр.", ["get_weather"], False),
    ("Да, я прекрасно вас слышу, сэр.", [], False),
])
def test_unbacked_claim(reply, actions, expected):
    assert unbacked_claim(reply, actions) is expected


def test_spotify_play_verifies_and_wakes_device(monkeypatch):
    """Spotify отвечает 204, но не играет (устройство «активно» лишь на бумаге) → передать воспроизведение
    на компьютер и включить снова; если так и не заиграло — честная ошибка, а не «включаю»."""
    from jarvis.services import spotify as sp

    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    client = sp.Spotify.__new__(sp.Spotify)
    log = []
    state = {"playing": False}

    def api(method, path, params=None, body=None, _retry=True):
        log.append((method, path))
        if (method, path) == ("PUT", "/me/player"):
            state["woken"] = True
        if (method, path) == ("PUT", "/me/player/play") and state.get("woken"):
            state["playing"] = True
        if (method, path) == ("GET", "/me/player"):
            return {"is_playing": state["playing"], "item": {"uri": "spotify:track:1"} if state["playing"] else None}
        return None

    client.api = api
    client.device_id = lambda launch=True: "dev"
    client.restart_app = lambda: log.append("restart")
    st = client.play({"uris": ["spotify:track:1"]})
    assert st["is_playing"] and ("PUT", "/me/player") in log

    state.clear()
    state["playing"] = False
    client.api = lambda method, path, **kw: {"is_playing": False} if path == "/me/player" and method == "GET" else None
    with pytest.raises(sp.SpotifyError):
        client.play({"uris": ["spotify:track:1"]})


class FakeDiscordUI:
    """Кнопки Discord: имя → нажата ли. click меняет состояние, как настоящая кнопка."""

    def __init__(self, **pressed):
        from jarvis.services.discord_ui import Button

        self.Button = Button
        self.state_map = {"Заглушить": False, "Откл. звук": False, "Отключиться": None,
                          "Продемонстрируйте свой экран": None, **pressed}
        self.clicks = []

    def buttons(self, root=None):
        return [self.Button(n, n, p) for n, p in self.state_map.items()]

    def click(self, b):
        self.clicks.append(b.name)
        if b.name == "Продемонстрируйте свой экран":
            self.state_map.update({"Экраны": None, "Экран 1": None, "Прямой эфир": None})
        elif b.name == "Прямой эфир":
            for k in ("Экраны", "Экран 1", "Прямой эфир", "Продемонстрируйте свой экран"):
                self.state_map.pop(k, None)
            self.state_map["Прекратить стрим"] = None
        elif isinstance(self.state_map.get(b.name), bool):
            self.state_map[b.name] = not self.state_map[b.name]
        return True


def _discord(monkeypatch, fake):
    from jarvis.services import discord_ui
    from jarvis.tools import discord

    real = discord_ui.DiscordUI
    for name in ("set_toggle", "share_screen", "state", "_find_fresh"):
        monkeypatch.setattr(fake, name, getattr(real, name).__get__(fake), raising=False)
    monkeypatch.setattr(fake, "find", real.find, raising=False)
    monkeypatch.setattr(discord, "_ui", lambda: fake)
    monkeypatch.setattr(discord_ui.time, "sleep", lambda s: None)
    return discord


def test_discord_mute_uses_real_state(monkeypatch):
    fake = FakeDiscordUI()
    d = _discord(monkeypatch, fake)
    r = d.discord_toggle_mute(NS(), "выключи")
    assert r.ok and r.data["muted"] is True and fake.clicks == ["Заглушить"]
    r = d.discord_toggle_mute(NS(), "выключи")
    assert r.ok and r.data["muted"] is True and fake.clicks == ["Заглушить"]
    r = d.discord_toggle_deafen(NS(), "выключи")
    assert r.data["deafened"] is True


def test_discord_screen_share_flow(monkeypatch):
    fake = FakeDiscordUI()
    d = _discord(monkeypatch, fake)
    r = d.discord_screen_share(NS(), "включи")
    assert r.ok and r.data["result"] == "started"
    assert fake.clicks[:1] == ["Продемонстрируйте свой экран"] and "Прямой эфир" in fake.clicks
    r = d.discord_screen_share(NS(), "выключи")
    assert r.ok and r.data["result"] == "stopped" and fake.clicks[-1] == "Прекратить стрим"


THEME_CHECK = r'''
from PySide6.QtGui import QColor
from jarvis.ui import theme
base = "#brand { color: #00d8ff; background: rgba(0, 216, 255, 0.2); } #err { color: #ff4f65; }"
theme.set_accent("#ff3b3b")
out = theme.qss(base)
assert out.startswith("#brand {") and "#err {" in out, out
assert "#00d8ff" not in out and "rgba(0, 216, 255" not in out, out
assert "#ff4f65" in out, out
assert theme.shift(QColor(0, 200, 255)).red() > 200
theme.set_accent(theme.BASE.name())
assert theme.qss(base) == base
print("ok")
'''


def test_theme_recolors_colors_not_selectors():
    """В отдельном процессе: Qt после COM-библиотек инструментов (как в тестах) падает, в приложении порядок иной."""
    import subprocess
    import sys

    from jarvis.config import ROOT

    r = subprocess.run([sys.executable, "-c", THEME_CHECK], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-800:]


def test_proxy_auto_uses_local_vpn_proxy(monkeypatch):
    import os

    from jarvis.utils import net

    monkeypatch.setattr(net, "_user_set", False)
    monkeypatch.setattr(net, "_applied", None)
    monkeypatch.setattr(net, "_registry_proxy", lambda: None)
    monkeypatch.setattr(net, "_open", lambda addr, timeout=0.3: addr == "127.0.0.1:2080")
    monkeypatch.setenv("JARVIS_PROXY", "auto")
    for v in net._VARS:
        monkeypatch.delenv(v, raising=False)
    assert net.apply_proxy() == "http://127.0.0.1:2080" and os.environ["HTTPS_PROXY"] == "http://127.0.0.1:2080"
    monkeypatch.setattr(net, "_open", lambda addr, timeout=0.3: False)
    assert net.apply_proxy() is None and "HTTPS_PROXY" not in os.environ


def test_proxy_user_setting_ignored_while_vpn_down(monkeypatch):
    import os

    from jarvis.utils import net

    monkeypatch.setattr(net, "_user_set", True)
    monkeypatch.setattr(net, "_user_proxy", "http://127.0.0.1:10809")
    monkeypatch.setattr(net, "_applied", "http://127.0.0.1:10809")
    monkeypatch.setenv("JARVIS_PROXY", "auto")
    monkeypatch.setattr(net, "_open", lambda addr, timeout=0.3: False)
    gen = net.generation
    assert net.apply_proxy() is None and "HTTPS_PROXY" not in os.environ and net.generation == gen + 1
    monkeypatch.setattr(net, "_open", lambda addr, timeout=0.3: True)
    assert net.apply_proxy() == "http://127.0.0.1:10809" and os.environ["HTTPS_PROXY"] == "http://127.0.0.1:10809"


def test_brain_client_recreated_when_proxy_changes(monkeypatch):
    from jarvis.brain.openai_brain import OpenAIBrain
    from jarvis.utils import net

    made = []
    brain = OpenAIBrain.__new__(OpenAIBrain)
    brain._clients = {}
    brain._client_factory = lambda slot: made.append(slot.number) or object()
    slot = NS(number=1)
    a = brain._client(slot)
    assert brain._client(slot) is a and made == [1]
    monkeypatch.setattr(net, "generation", net.generation + 1)
    assert brain._client(slot) is not a and made == [1, 1] and len(brain._clients) == 1
