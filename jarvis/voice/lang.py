"""Определение языка команды (ru/en).

Решает первое значимое слово: «включи на ютубе imagine dragons» — русская команда с английским
запросом, а «open YouTube» — английская. Обращения («Jarvis», «эй») пропускаются.
"""
from __future__ import annotations

import re

_SKIP = {"jarvis", "джарвис", "джервис", "hey", "эй", "ok", "okay", "окей", "ок", "please", "пожалуйста"}


def detect_lang(text: str, default: str = "ru") -> str:
    for word in re.findall(r"[a-zа-яё']+", text.lower()):
        if word in _SKIP:
            continue
        return "ru" if re.search(r"[а-яё]", word) else "en"
    return default
