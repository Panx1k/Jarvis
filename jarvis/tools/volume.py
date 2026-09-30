"""Громкость системы."""
from __future__ import annotations

from jarvis.services import audio
from jarvis.tools.base import ToolResult, tool


@tool("set_volume", "Установить общую громкость системы в процентах (0–100).",
      params={"percent": {"type": "integer", "description": "Громкость 0–100"}}, required=["percent"],
      announce="Устанавливаю громкость {percent}%", category="volume")
def set_volume(ctx, percent: int) -> ToolResult:
    value = audio.set_volume(int(percent))
    return ToolResult(True, f"Громкость {value} процентов.", {"volume": value})


@tool("change_volume", "Сделать громче или тише на указанное число процентов (отрицательное — тише).",
      params={"delta": {"type": "integer", "description": "Изменение громкости, например 10 или -10"}},
      required=["delta"], announce=lambda a: "Делаю громче" if int(a.get("delta", 0)) > 0 else "Делаю тише",
      category="volume")
def change_volume(ctx, delta: int) -> ToolResult:
    value = audio.change_volume(int(delta))
    return ToolResult(True, f"{'Громче' if int(delta) > 0 else 'Тише'}: {value} процентов.", {"volume": value})


@tool("mute", "Выключить звук (mute).", announce="Выключаю звук", category="volume")
def mute(ctx) -> ToolResult:
    audio.set_mute(True)
    return ToolResult(True, "Звук выключен.")


@tool("unmute", "Включить звук обратно (unmute).", announce="Включаю звук", category="volume")
def unmute(ctx) -> ToolResult:
    audio.set_mute(False)
    return ToolResult(True, f"Звук включён, громкость {audio.get_volume()} процентов.")


@tool("get_volume", "Узнать текущую громкость.", announce="Проверяю громкость", category="volume")
def get_volume(ctx) -> ToolResult:
    muted = " (звук выключен)" if audio.is_muted() else ""
    value = audio.get_volume()
    return ToolResult(True, f"Громкость {value} процентов{muted}.", {"volume": value})
