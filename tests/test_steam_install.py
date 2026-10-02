"""Установка игры Steam без окна выбора диска: библиотека, где ставились прошлые игры (Steam подменён)."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace as NS

import pytest

from jarvis.tools import steam
from jarvis.tools.base import load_builtin_tools, registry


@pytest.fixture
def fake_steam(tmp_path, monkeypatch):
    main = tmp_path / "Steam"
    games = tmp_path / "D" / "SteamLibrary"
    (main / "steamapps").mkdir(parents=True)
    (games / "steamapps").mkdir(parents=True)
    (main / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
        % (str(main).replace("\\", "\\\\"), str(games).replace("\\", "\\\\")), encoding="utf-8")
    old = main / "steamapps" / "appmanifest_1.acf"
    new = games / "steamapps" / "appmanifest_2.acf"
    old.write_text('"AppState"\n{\n}\n', encoding="utf-8")
    new.write_text('"AppState"\n{\n}\n', encoding="utf-8")
    now = time.time()
    os.utime(old, (now - 1000, now - 1000))
    os.utime(new, (now, now))
    monkeypatch.setattr(steam, "steam_dir", lambda: main)
    return main, games


def test_preferred_library_is_where_last_game_went(fake_steam):
    main, games = fake_steam
    assert set(steam.library_folders()) == {main, games}
    assert steam.preferred_library() == games


def test_queue_install_writes_manifest(fake_steam):
    _, games = fake_steam
    path = steam.queue_install("730", "Counter-Strike: 2", games)
    text = path.read_text(encoding="utf-8")
    assert path.name == "appmanifest_730.acf"
    assert '"StateFlags"\t\t"1026"' in text and '"installdir"\t\t"Counter-Strike 2"' in text


def test_install_tool_queues_and_restarts(fake_steam, monkeypatch):
    _, games = fake_steam
    load_builtin_tools()
    restarted = []
    monkeypatch.setattr(steam, "find_game", lambda g: None)
    monkeypatch.setattr(steam, "store_search", lambda g: ("570", "Dota 2"))
    monkeypatch.setattr(steam, "restart_steam", lambda: restarted.append(1) or True)
    monkeypatch.setattr(steam, "running_steam_games", lambda: [])
    r = registry.get("steam_install").func(NS(), game="дота")
    assert r.ok and restarted and (games / "steamapps" / "appmanifest_570.acf").exists()
    assert r.data["library"] == str(games)


def test_steam_addons_are_not_games(monkeypatch, tmp_path):
    root = tmp_path / "Steam"

    def proc(name, exe):
        return NS(name=lambda: name, exe=lambda: exe)

    children = [proc("millennium.luavm64.exe", str(root / "millennium" / "bin" / "millennium.luavm64.exe")),
                proc("millennium.crashhandler64.exe", str(root / "millennium" / "bin" / "x.exe")),
                proc("helper.exe", str(root / "ext" / "helper.exe")),
                proc("steamwebhelper.exe", str(root / "bin" / "steamwebhelper.exe")),
                proc("cs2.exe", str(root / "steamapps" / "common" / "cs2" / "cs2.exe"))]
    monkeypatch.setattr(steam, "steam_dir", lambda: root)
    monkeypatch.setattr(steam, "steam_processes", lambda: [NS(children=lambda recursive: children)])
    assert steam.running_steam_games() == ["cs2.exe"]
    children.pop()
    assert steam.running_steam_games() == []
    load_builtin_tools()
    assert not registry.get("steam_install").is_dangerous({"game": "Rust"})
    assert not registry.get("steam_switch_account").is_dangerous({})


def test_install_pattern_does_not_steal_other_commands():
    load_builtin_tools()
    tool = registry.get("steam_install")
    import re

    matches = lambda s: any(re.search(p, s) for p in tool.patterns)
    assert matches("установи игру rust") and matches("скачай доту в стиме")
    assert not matches("поставь громкость 30") and not matches("установи обновление")
