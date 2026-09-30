"""Воспроизведение: музыка, пауза/продолжить, переключение треков."""
from __future__ import annotations

from jarvis.services import media
from jarvis.tools.base import ToolResult, tool


@tool("play_media", "Включить музыку или видео. С query — найти и включить на YouTube. Без query — "
      "продолжить поставленное на паузу, а если ничего нет — включить музыку по умолчанию.",
      params={"query": {"type": "string", "description": "Что включить (жанр, песня, исполнитель). Можно пусто."}},
      announce=lambda a: f"Включаю {a.get('query') or 'музыку'}", category="media")
def play_media(ctx, query: str | None = None) -> ToolResult:
    from jarvis.tools.youtube import play_youtube

    if query and query.strip():
        return play_youtube(ctx, query)
    paused = [s for s in media.sessions() if s.status == media.PAUSED]
    if paused:
        ok, s = media.resume()
        if ok:
            return ToolResult(True, f"Продолжаю воспроизведение{': ' + s.label() if s else ''}.")
    default = ctx.settings.get("music.default_query", "lofi hip hop radio")
    return play_youtube(ctx, default)


@tool("pause_media", "Поставить на паузу всё, что сейчас играет (видео в браузере, музыка, плееры).",
      announce="Ставлю на паузу", category="media",
      patterns=[r"^(?:поставь\s+)?(?:на\s+)?паузу$", r"^пауза$"])
def pause_media(ctx) -> ToolResult:
    ok, paused = media.pause()
    if not ok:
        return ToolResult(False, "Сейчас ничего не играет.")
    if paused:
        return ToolResult(True, "Поставил на паузу.", {"paused": [s.label() for s in paused]})
    return ToolResult(True, "Отправил команду паузы.")


@tool("resume_media", "Продолжить воспроизведение (снять с паузы).", announce="Продолжаю воспроизведение",
      category="media")
def resume_media(ctx) -> ToolResult:
    ok, s = media.resume()
    if not ok:
        return ToolResult(False, "Нечего продолжать — нет приостановленного воспроизведения.")
    return ToolResult(True, f"Продолжаю{': ' + s.label() if s else ''}.")


@tool("next_track", "Следующий трек/видео.", announce="Следующий трек", category="media")
def next_track(ctx) -> ToolResult:
    media.skip(True)
    return ToolResult(True, "Переключаю на следующий.")


@tool("previous_track", "Предыдущий трек/видео.", announce="Предыдущий трек", category="media")
def previous_track(ctx) -> ToolResult:
    media.skip(False)
    return ToolResult(True, "Возвращаю предыдущий.")


@tool("stop_media", "Остановить воспроизведение.", announce="Останавливаю воспроизведение", category="media")
def stop_media(ctx) -> ToolResult:
    media.stop()
    return ToolResult(True, "Остановил.")


@tool("media_status", "Узнать, что сейчас играет.", announce="Проверяю, что играет", category="media",
      patterns=[r"^что (?:сейчас )?(?:играет|звучит|за (?:песня|трек|музыка))"])
def media_status(ctx) -> ToolResult:
    items = media.sessions()
    playing = [s for s in items if s.playing]
    if playing:
        return ToolResult(True, f"Сейчас играет {playing[0].label()}.")
    if items:
        return ToolResult(True, f"На паузе: {items[0].label()}.")
    return ToolResult(True, "Сейчас ничего не играет.")
