"""Обновление JARVIS голосом: «есть обновления?», «обновись»."""
from __future__ import annotations

from jarvis.tools.base import ToolResult, tool


@tool("jarvis_update_check", "Проверить, вышла ли новая версия JARVIS на GitHub.",
      announce="Проверяю обновления", category="system",
      patterns=[r"^(?:есть|проверь|проверить|посмотри)\s+(?:ли\s+)?(?:новые\s+)?обновлени\w*(?:\s+(?:для\s+)?(?:себя|"
                r"джарвис\w*|jarvis))?$", r"^(?:ты|джарвис)\s+(?:обновлён|последней версии)\??$"])
def jarvis_update_check(ctx) -> ToolResult:
    from jarvis.services import updater

    info = updater.check()
    if info.error:
        return ToolResult(False, info.error)
    if not info.available:
        return ToolResult(True, "У меня последняя версия, сэр.", {"available": False})
    what = f": {info.message}" if info.message else ""
    return ToolResult(True, f"Доступно обновление{what}. Скажите «обновись», и я обновлюсь.",
                      {"available": True, "message": info.message})


@tool("jarvis_update", "Скачать и установить новую версию JARVIS с GitHub и перезапуститься. Настройки, ключи и "
      "модели пользователя не трогаются.",
      dangerous=True, confirm="Обновить JARVIS до новой версии? Я перезапущусь.",
      announce="Обновляюсь", category="system",
      patterns=[r"^(?:обновись|обнови себя|обновить джарвис\w*|обнови джарвис\w*|установи обновлени\w*|"
                r"обновись до последней версии)$"])
def jarvis_update(ctx) -> ToolResult:
    from jarvis.services import updater

    ok, text = updater.apply()
    if not ok:
        return ToolResult(False, text)
    ctx.restart_requested = True
    return ToolResult(True, f"{text} Перезапускаюсь, сэр.")
