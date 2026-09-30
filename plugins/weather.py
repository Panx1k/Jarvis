"""Пример плагина: погода (Open-Meteo, без API-ключа).

Любой .py-файл в папке plugins/ загружается автоматически. Достаточно объявить функцию с @tool —
она станет доступна и Claude, и локальному разбору команд (через patterns).
"""
from __future__ import annotations

import requests

from jarvis.tools.base import ToolResult, tool

_CODES = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно", 45: "туман", 48: "туман",
    51: "морось", 53: "морось", 55: "сильная морось", 61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
    66: "ледяной дождь", 67: "ледяной дождь", 71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа",
    80: "ливень", 81: "ливень", 82: "сильный ливень", 85: "снегопад", 86: "сильный снегопад", 95: "гроза",
    96: "гроза с градом", 99: "гроза с градом",
}


def _city_base(city: str) -> str:
    """«в москве» → «москве» → попытка привести к именительному падежу для геокодера."""
    c = city.strip().strip("?.!").removeprefix("в ").removeprefix("во ").strip()
    return c


@tool("get_weather", "Узнать текущую погоду и прогноз на сегодня в городе.",
      params={"city": {"type": "string", "description": "Город (по умолчанию — домашний город из настроек)"}},
      announce=lambda a: f"Узнаю погоду{' в ' + a['city'] if a.get('city') else ''}", category="weather",
      patterns=[r"(?:какая|какой|что с|что по|скажи|покажи)?\s*(?:сейчас\s+)?(?:погод\w*|прогноз\w*)"
                r"(?:\s+(?:сейчас|сегодня|на сегодня))?(?:\s+(?:в|во)\s+(?P<city>[\w\- ]+?))?"
                r"(?:\s+(?:сейчас|сегодня))?\s*\??$"])
def get_weather(ctx, city: str | None = None) -> ToolResult:
    city = _city_base(city or ctx.settings.get("home_city", "Москва"))
    geo = None
    candidates = [city]
    if city[-1:].lower() in ("е", "и", "у"):
        stem = city[:-1]
        candidates += [stem + "ь", stem + "а", stem, stem + "я"]
    for candidate in candidates:
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": candidate, "count": 1, "language": "ru"}, timeout=10).json()
        if r.get("results"):
            geo = r["results"][0]
            break
    if not geo:
        return ToolResult(False, f"Не нашёл город «{city}».")
    w = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": geo["latitude"], "longitude": geo["longitude"], "timezone": "auto",
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max", "forecast_days": 1,
    }, timeout=10).json()
    cur, day = w["current"], w["daily"]
    desc = _CODES.get(cur.get("weather_code"), "")
    text = (f"В городе {geo['name']} сейчас {round(cur['temperature_2m'])}°, {desc}, ощущается как "
            f"{round(cur['apparent_temperature'])}°. Днём от {round(day['temperature_2m_min'][0])} до "
            f"{round(day['temperature_2m_max'][0])}°, вероятность осадков {day['precipitation_probability_max'][0]}%.")
    return ToolResult(True, text)
