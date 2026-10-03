"""Когда спрашивать подтверждение («да/нет»).

Режимы (config/settings.json → confirm.mode, меняются в настройках или голосом):
  strict  — строгий: опасные действия + закрытие программ, VPN, смена аккаунта, печать текста и т. п.;
  normal  — обычный (по умолчанию): только опасные действия;
  minimal — минимальный: только необратимые (удаление, выключение, команды, отправка сообщений).
Необратимые действия подтверждаются в любом режиме.
"""
from __future__ import annotations

MODES = {"strict": "Строгий", "normal": "Обычный", "minimal": "Минимальный"}
MODE_HINTS = {"strict": "спрашивать чаще: опасные действия, закрытие программ, VPN, смена аккаунта",
              "normal": "спрашивать только про опасные действия",
              "minimal": "спрашивать только про необратимые: удаление, выключение, команды, сообщения"}
ALWAYS = {"shutdown_computer", "restart_computer", "run_command", "delete_to_recycle_bin", "discord_send_message",
          "uninstall_app", "steam_uninstall", "kill_app", "jarvis_update"}
STRICT_EXTRA = {"close_app", "vpn_on", "vpn_off", "steam_switch_account", "steam_install", "lock_screen",
                "discord_screen_share", "discord_disconnect", "type_text", "send_enter", "run_preset",
                "exclusion_remove", "discord_camera"}
MINIMAL_SKIP = {"sleep_computer", "steam_switch_account", "steam_install"}


def mode(settings) -> str:
    value = str(settings.get("confirm.mode", "normal") or "normal").lower()
    return value if value in MODES else "normal"


def needs_confirmation(tool, args: dict, current: str) -> bool:
    dangerous = tool.is_dangerous(args)
    if tool.name in ALWAYS or tool.irreversible:
        return dangerous or tool.name in ALWAYS
    if current == "strict":
        return dangerous or tool.name in STRICT_EXTRA
    if current == "minimal":
        return dangerous and tool.name not in MINIMAL_SKIP
    return dangerous


def parse_mode(text: str) -> str | None:
    t = (text or "").lower().replace("ё", "е")
    if t.startswith(("строг", "strict", "всегда", "чаще", "максим", "повыш")):
        return "strict"
    if t.startswith(("миним", "minimal", "реже", "только необрат", "пониж")):
        return "minimal"
    if t.startswith(("обычн", "normal", "стандарт", "по умолчанию", "нормальн", "средн")):
        return "normal"
    return None
