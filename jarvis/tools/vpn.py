"""VPN: включить / выключить / статус."""
from __future__ import annotations

from jarvis.services.vpn import VpnController
from jarvis.tools.base import ToolResult, tool


def _ctl(ctx) -> VpnController:
    if not hasattr(ctx, "_vpn"):
        ctx._vpn = VpnController(ctx.settings, ctx.apps)
    return ctx._vpn


@tool("vpn_on", "Включить VPN.", announce="Включаю VPN", category="vpn")
def vpn_on(ctx) -> ToolResult:
    ok, msg = _ctl(ctx).turn_on()
    return ToolResult(ok, msg)


@tool("vpn_off", "Выключить VPN.", announce="Выключаю VPN", category="vpn")
def vpn_off(ctx) -> ToolResult:
    ok, msg = _ctl(ctx).turn_off()
    return ToolResult(ok, msg)


@tool("vpn_status", "Проверить, включён ли VPN.", announce="Проверяю VPN", category="vpn",
      patterns=[r"(?:впн|vpn)\s+(?:включен|работает|подключен)", r"статус\s+(?:впн|vpn)",
                r"(?:включен|работает)\s+ли\s+(?:впн|vpn)"])
def vpn_status(ctx) -> ToolResult:
    st = _ctl(ctx).status()
    return ToolResult(True, st.detail, {"connected": st.connected})
