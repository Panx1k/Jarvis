"""Экран, удаление программ, исключения, режимы подтверждений, звуки. Ничего не удаляется и не закрывается:
реестр программ, процессы и снимок экрана подменены."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from jarvis.core import policy
from jarvis.core.assistant import Runtime
from jarvis.core.context import DialogContext
from jarvis.services import installed
from jarvis.services.installed import InstalledApp
from jarvis.tools import apps as apps_mod
from jarvis.tools import screen
from jarvis.tools.base import load_builtin_tools, registry

load_builtin_tools()


class MemSettings:
    def __init__(self, **data):
        self.d = dict(data)

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


PROGRAMS = [InstalledApp("Steam", "Valve Corporation", r'"C:\Steam\uninstall.exe"', "Steam"),
            InstalledApp("Zoom Workplace", "Zoom", "MsiExec.exe /I{11111111-2222-3333-4444-555555555555}", "z"),
            InstalledApp("Discord", "Discord Inc.", r'"C:\Discord\Update.exe" --uninstall', "d"),
            InstalledApp("NVIDIA Graphics Driver 560.94", "NVIDIA", r"C:\nv\setup.exe -uninstall", "n"),
            InstalledApp("Telegram Desktop", "Telegram FZ-LLC", r"C:\tg\unins000.exe", "t"),
            InstalledApp("Telegram Desktop Beta", "Telegram FZ-LLC", r"C:\tgb\unins000.exe", "tb")]


@pytest.fixture
def rt(monkeypatch):
    monkeypatch.setattr(installed, "installed", lambda: list(PROGRAMS))
    launched = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: launched.append(a[0]))
    from jarvis.tools import steam

    monkeypatch.setattr(steam, "find_game", lambda g: None)
    runtime = Runtime(MemSettings(), DialogContext(), registry, NS(resolve=lambda n: None,
                                                                    find_processes=lambda *a: []))
    runtime.launched = launched
    return runtime


def call(rt, name, **args):
    return registry.call(name, args, rt)


def test_exclusions_add_list_remove(rt):
    assert call(rt, "exclusion_list").message.startswith("Исключений нет")
    assert call(rt, "exclusion_add", app="дискорд").ok
    assert rt.settings.get("exclusions") == ["Discord"]
    assert "уже" in call(rt, "exclusion_add", app="Discord").message
    assert call(rt, "exclusion_list").message == "В исключениях: Discord."
    assert call(rt, "exclusion_remove", app="discord").ok and rt.settings.get("exclusions") == []


def test_excluded_app_is_not_closed_killed_or_uninstalled(rt):
    rt.settings.set("exclusions", ["Discord"])
    for tool in ("close_app", "kill_app", "uninstall_app"):
        r = call(rt, tool, app="дискорд")
        assert not r.ok and "в исключениях" in r.message and r.data["excluded"] == "Discord", tool
    assert rt.launched == []


def test_uninstall_asks_with_exact_name_and_opens_uninstaller(rt):
    tool = registry.get("uninstall_app")
    args = {"app": "зум"}
    assert tool.check(rt, args) is None and args["_target"] == "Zoom Workplace"
    assert policy.needs_confirmation(tool, args, "minimal")
    assert tool.confirm_text(args) == "Удалить Zoom Workplace (Zoom) с компьютера?"
    r = call(rt, "uninstall_app", **args)
    assert r.ok and rt.launched == ["MsiExec.exe /X{11111111-2222-3333-4444-555555555555}"]


def test_uninstall_ambiguous_asks_which(rt):
    r = call(rt, "uninstall_app", app="телеграм")
    assert not r.ok and r.data.get("clarify") and len(r.data["candidates"]) == 2 and not rt.launched


def test_uninstall_warns_about_drivers(rt):
    args = {"app": "nvidia graphics driver"}
    registry.get("uninstall_app").check(rt, args)
    assert "драйвер" in registry.get("uninstall_app").confirm_text(args)


def test_uninstall_unknown_offers_settings(rt):
    r = call(rt, "uninstall_app", app="Совсем Неизвестная Программа")
    assert not r.ok and r.followup == ("open_settings", {"page": "apps"})


def test_uninstall_steam_game_goes_to_steam(rt, monkeypatch):
    from jarvis.tools import steam

    monkeypatch.setattr(steam, "find_game", lambda g: ("570", "Dota 2"))
    opened = []
    monkeypatch.setattr("os.startfile", lambda url: opened.append(url))
    args = {"app": "доту"}
    registry.get("uninstall_app").check(rt, args)
    assert args["_kind"] == "steam"
    assert call(rt, "uninstall_app", **args).ok and opened == ["steam://uninstall/570"]


def test_msiexec_modify_becomes_remove():
    app = InstalledApp("X", "", "MsiExec.exe /I{ABCDEF01-2345-6789-ABCD-EF0123456789}", "x")
    assert installed.uninstall_command(app) == "MsiExec.exe /X{ABCDEF01-2345-6789-ABCD-EF0123456789}"


def test_confirmation_modes():
    get = registry.get
    close, shutdown, sleep, vpn = get("close_app"), get("shutdown_computer"), get("sleep_computer"), get("vpn_on")
    assert not policy.needs_confirmation(close, {}, "normal") and policy.needs_confirmation(close, {}, "strict")
    assert policy.needs_confirmation(vpn, {}, "strict") and not policy.needs_confirmation(vpn, {}, "normal")
    assert policy.needs_confirmation(sleep, {}, "normal") and not policy.needs_confirmation(sleep, {}, "minimal")
    for mode in policy.MODES:
        assert policy.needs_confirmation(shutdown, {}, mode)
        assert policy.needs_confirmation(get("uninstall_app"), {"app": "x"}, mode)
        assert policy.needs_confirmation(get("run_command"), {"command": "dir"}, mode)
        assert policy.needs_confirmation(get("discord_send_message"), {"to": "a", "text": "b"}, mode)


def test_set_confirmation_mode_by_voice(rt):
    tool = registry.get("set_confirmation_mode")
    assert not tool.is_dangerous({"mode": "строгий"}) and tool.is_dangerous({"mode": "минимальный"})
    assert call(rt, "set_confirmation_mode", mode="строгий").ok and rt.settings.get("confirm.mode") == "strict"
    assert not call(rt, "set_confirmation_mode", mode="непонятный").ok


def test_sound_settings(rt):
    assert call(rt, "sound_settings", kind="samples", enabled="выключи").ok
    assert rt.settings.get("voice.samples.enabled") is False
    call(rt, "sound_settings", kind="system", enabled=False)
    assert rt.settings.get("voice.system_sounds") is False


class FakeImage:
    size = (1920, 1080)

    def copy(self):
        return self

    def thumbnail(self, size):
        pass


@pytest.fixture
def screen_env(monkeypatch):
    monkeypatch.setattr(screen, "capture", lambda max_side=1600: FakeImage())
    monkeypatch.setattr(screen, "jpeg", lambda img, quality=80: b"jpeg")
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)
    return Runtime(MemSettings(), DialogContext(), registry, None)


def test_screen_describe_uses_vision(screen_env):
    asked = []
    screen_env.vision = lambda prompt, image: asked.append(prompt) or "Открыт Steam, страница библиотеки."
    r = call(screen_env, "screen_read", question="что у меня на экране?")
    assert r.ok and r.message == "Открыт Steam, страница библиотеки." and "что у меня на экране" in asked[0]


def test_screen_read_falls_back_to_offline_ocr(screen_env, monkeypatch):
    from jarvis.brain.base import BrainUnavailable

    def down(prompt, image):
        raise BrainUnavailable("нет VPN")

    screen_env.vision = down
    monkeypatch.setattr(screen, "ocr", lambda img, lang="ru": "Сохранить изменения? Да Нет")
    r = call(screen_env, "screen_read", mode="read")
    assert r.ok and r.message == "Сохранить изменения? Да Нет" and r.data["via"] == "ocr"


def test_screen_nothing_found(screen_env, monkeypatch):
    monkeypatch.setattr(screen, "ocr", lambda img, lang="ru": "")
    assert not call(screen_env, "screen_read", mode="read").ok


def test_real_ocr_reads_rendered_text():
    """Настоящий OCR Windows на картинке с текстом (без снимка экрана)."""
    pytest.importorskip("winrt.windows.media.ocr")
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 160), "white")
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except OSError:
        pytest.skip("нет шрифта Arial")
    ImageDraw.Draw(img).text((20, 40), "Привет, это проверка", fill="black", font=font)
    text = screen.ocr(img).lower()
    assert "привет" in text and "проверка" in text, text
