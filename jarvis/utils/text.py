"""Работа с русским текстом: нормализация, числительные, транслитерация, нечёткое сравнение."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

_UNITS = {
    "ноль": 0, "нуль": 0, "один": 1, "одна": 1, "одну": 1, "одного": 1, "два": 2, "две": 2, "три": 3,
    "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
    "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
}
_TENS = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
}
_HUNDREDS = {"сто": 100}

_ORDINALS = [
    (r"перв", 1), (r"втор", 2), (r"трет", 3), (r"четв[её]рт", 4), (r"пят(?!ьдес)", 5), (r"шест(?!ьдес)", 6),
    (r"седьм", 7), (r"восьм", 8), (r"девят(?!на|ьс)", 9), (r"десят", 10), (r"последн", -1),
]
ORDINAL_RE = r"(?:перв\w*|втор\w*|трет\w*|четв[её]рт\w*|пят(?:ый|ое|ую|ая|ого|ой)|шест(?:ой|ое|ую|ая|ого)|" \
             r"седьм\w*|восьм\w*|девят(?:ый|ое|ую|ая|ого|ой)|десят(?:ый|ое|ую|ая|ого|ой)|последн\w*|\d+\s*-?\s*(?:й|ый|ой|ий|е|ое|ю|ую)?)"


def normalize(text: str) -> str:
    """Нижний регистр, ё→е. Сохраняет длину строки (важно для извлечения фрагментов из оригинала)."""
    return text.lower().replace("ё", "е")


_INVISIBLE = re.compile(r"[﻿​‌‍⁠­]")


def clean_spaces(text: str) -> str:
    """Убирает невидимые символы (BOM, zero-width) и схлопывает пробелы."""
    return re.sub(r"\s+", " ", _INVISIBLE.sub("", text)).strip()


def words_to_digits(text: str) -> str:
    """«громкость тридцать пять процентов» → «громкость 35 процентов»."""
    tokens = text.split(" ")
    out: list[str] = []
    acc: int | None = None
    for tok in tokens:
        low = normalize(tok).strip(",.")
        if low in _HUNDREDS:
            acc = (acc or 0) + _HUNDREDS[low]
        elif low in _TENS:
            acc = (acc or 0) + _TENS[low]
        elif low in _UNITS and (acc is None or acc % 10 == 0):
            acc = (acc or 0) + _UNITS[low]
        else:
            if acc is not None:
                out.append(str(acc))
                acc = None
            out.append(tok)
    if acc is not None:
        out.append(str(acc))
    return " ".join(out)


def ordinal_to_int(word: str) -> int | None:
    """«первый» → 1, «третье» → 3, «последний» → -1, «2-й» → 2."""
    w = normalize(word).strip()
    m = re.match(r"(\d+)", w)
    if m:
        return int(m.group(1))
    for stem, value in _ORDINALS:
        if re.match(stem, w):
            return value
    return None


_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}


def translit(text: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in normalize(text))


def simplify(text: str) -> str:
    """Для сравнения имён: только буквы/цифры, без регистра."""
    return re.sub(r"[^a-zа-я0-9]+", "", normalize(text))


def similarity(a: str, b: str) -> float:
    """Нечёткое сходство с учётом транслитерации (дискорд ≈ discord)."""
    a1, b1 = simplify(a), simplify(b)
    if not a1 or not b1:
        return 0.0
    best = SequenceMatcher(None, a1, b1).ratio()
    a2, b2 = simplify(translit(a)), simplify(translit(b))
    best = max(best, SequenceMatcher(None, a2, b2).ratio())
    return best


def strip_markdown(text: str) -> str:
    """Убирает разметку перед озвучкой."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#>]+", "", text)
    return clean_spaces(text)
