"""Переключение аккаунтов Steam — на временной копии loginusers.vdf, без реального Steam."""
from __future__ import annotations

from types import SimpleNamespace as NS

from jarvis.tools import steam

VDF = '''"users"
{
\t"76561198000000001"
\t{
\t\t"AccountName"\t\t"main_login"
\t\t"PersonaName"\t\t"MainAcc"
\t\t"RememberPassword"\t\t"1"
\t\t"MostRecent"\t\t"1"
\t}
\t"76561198000000002"
\t{
\t\t"AccountName"\t\t"khan_login"
\t\t"PersonaName"\t\t"Смурф"
\t\t"RememberPassword"\t\t"1"
\t\t"MostRecent"\t\t"0"
\t}
}
'''


def setup(tmp_path, monkeypatch, current="main_login", running=False):
    (tmp_path / "config").mkdir()
    vdf = tmp_path / "config" / "loginusers.vdf"
    vdf.write_text(VDF, encoding="utf-8")
    state = {"auto": current, "started": [], "running": running}
    monkeypatch.setattr(steam, "steam_dir", lambda: tmp_path)
    monkeypatch.setattr(steam, "current_login", lambda: state["auto"])
    monkeypatch.setattr(steam, "set_auto_login", lambda acc: state.__setitem__("auto", acc))
    monkeypatch.setattr(steam, "steam_processes", lambda: [1] if state["running"] else [])

    def popen(args, **kw):
        state["started"].append(args)
        if args[-1] == "-shutdown":
            state["running"] = False
    monkeypatch.setattr(steam.subprocess, "Popen", popen)
    ctx = NS(dialog=NS(last_app=None))
    return vdf, state, ctx


def test_read_accounts(tmp_path, monkeypatch):
    vdf, _, _ = setup(tmp_path, monkeypatch)
    accounts = steam.read_accounts(vdf)
    assert [a.persona for a in accounts] == ["MainAcc", "Смурф"] and all(a.remember for a in accounts)


def test_switch_by_persona_restarts_steam(tmp_path, monkeypatch):
    vdf, state, ctx = setup(tmp_path, monkeypatch, running=True)
    r = steam.steam_switch_account(ctx, "Смурф")
    assert r.ok and state["auto"] == "khan_login"
    assert state["started"][0][-1] == "-shutdown" and state["started"][1][0].endswith("steam.exe")
    text = vdf.read_text(encoding="utf-8")
    assert '"MostRecent"\t\t"0"' in text.split("76561198000000002")[0]
    assert '"MostRecent"\t\t"1"' in text.split("76561198000000002")[1]
    assert text.count("{") == VDF.count("{")


def test_switch_without_name_goes_to_other(tmp_path, monkeypatch):
    _, state, ctx = setup(tmp_path, monkeypatch, current="khan_login")
    assert steam.steam_switch_account(ctx).ok and state["auto"] == "main_login"


def test_unknown_account(tmp_path, monkeypatch):
    _, state, ctx = setup(tmp_path, monkeypatch)
    r = steam.steam_switch_account(ctx, "Васян")
    assert not r.ok and "Смурф" in r.message and state["started"] == []


def test_does_not_kill_steam_that_refuses_to_close(tmp_path, monkeypatch):
    _, state, ctx = setup(tmp_path, monkeypatch, running=True)
    monkeypatch.setattr(steam.subprocess, "Popen", lambda args, **kw: None)
    monkeypatch.setattr(steam.time, "sleep", lambda s: None)
    t = iter(range(0, 1000, 5))
    monkeypatch.setattr(steam.time, "time", lambda: next(t))
    r = steam.steam_switch_account(ctx, "Смурф")
    assert not r.ok and state["auto"] == "main_login"


def test_close_game_found_by_install_folder(monkeypatch):
    """«Закрой Counter-Strike 2»: процесс называется cs2.exe — ищем по папке установки игры."""
    from types import SimpleNamespace as NS

    from jarvis.tools import apps, steam

    proc = NS(info={"name": "cs2.exe"})
    monkeypatch.setattr(steam, "game_processes", lambda q: ("Counter-Strike 2", [proc]))
    ctx = NS(apps=NS(resolve=lambda a: None, find_processes=lambda a, m: []))
    assert apps._find(ctx, "Counter-Strike 2") == ("Counter-Strike 2", [proc])
    monkeypatch.setattr(steam, "game_processes", lambda q: None)
    assert apps._find(ctx, "Photoshop") == ("Photoshop", [])


def _accounts_env(monkeypatch, active="main"):
    """Два аккаунта: MainAcc (по умолчанию) и Смурф (для CS2); Steam запущен на `active`."""
    from types import SimpleNamespace as NS

    from jarvis.tools import steam

    accs = [steam.SteamAccount(str(steam.STEAM_ID64_BASE + 1), "main", "MainAcc", True),
            steam.SteamAccount(str(steam.STEAM_ID64_BASE + 2), "smurf", "Смурф", True)]
    ids = {"main": 1, "smurf": 2}
    calls = []
    monkeypatch.setattr(steam, "read_accounts", lambda vdf=None: accs)
    monkeypatch.setattr(steam, "steam_processes", lambda: [object()])
    monkeypatch.setattr(steam, "active_id32", lambda: ids[active])
    monkeypatch.setattr(steam, "current_login", lambda: active)
    monkeypatch.setattr(steam, "find_game", lambda q: ("730", "Counter-Strike 2") if "к" in q or "cs" in q.lower()
                        else ("570", "Dota 2"))
    monkeypatch.setattr(steam, "running_game", lambda: None)
    monkeypatch.setattr(steam, "switch_to", lambda ctx, t: calls.append(("switch", t.persona)) or
                        steam.ToolResult(True, "ok"))
    monkeypatch.setattr(steam, "_launch_after_login", lambda appid, t: calls.append(("launch_after", appid)))
    monkeypatch.setattr("os.startfile", lambda uri: calls.append(("start", uri)))
    settings = {"steam.game_accounts": {"730": "Смурф"}, "steam.default_account": "MainAcc"}
    ctx = NS(settings=NS(get=lambda k, d=None: settings.get(k, d)), dialog=NS(last_app=None))
    return steam, ctx, calls


def test_cs_switches_to_its_account(monkeypatch):
    import time

    steam, ctx, calls = _accounts_env(monkeypatch, active="main")
    r = steam.launch_game(ctx, "кс")
    time.sleep(0.1)
    assert r.ok and "Смурф" in r.message
    assert ("switch", "Смурф") in calls and ("launch_after", "730") in calls
    assert not any(c[0] == "start" for c in calls)


def test_other_game_uses_default_account(monkeypatch):
    steam, ctx, calls = _accounts_env(monkeypatch, active="main")
    r = steam.launch_game(ctx, "дота")
    assert r.ok and calls == [("start", "steam://rungameid/570")]
    steam, ctx, calls = _accounts_env(monkeypatch, active="smurf")
    steam.launch_game(ctx, "дота")
    assert ("switch", "MainAcc") in calls


def test_no_switch_while_game_running(monkeypatch):
    steam, ctx, calls = _accounts_env(monkeypatch, active="main")
    monkeypatch.setattr(steam, "running_game", lambda: "Dota 2")
    r = steam.launch_game(ctx, "кс")
    assert not r.ok and "Dota 2" in r.message and not calls
