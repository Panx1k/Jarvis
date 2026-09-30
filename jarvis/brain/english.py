"""Английские команды для локального разбора.

Не отдельный «мозг»: типовые английские фразы переводятся в русские формулировки, которые понимает
существующий RuleParser («open YouTube» → «открой YouTube»). Названия приложений, сайтов и запросы
остаются как есть. Всё, что не распознано, уходит в Claude (он понимает английский сам).
"""
from __future__ import annotations

import re

ORD = {"first": "первый", "second": "второй", "third": "третий", "fourth": "четвёртый", "fifth": "пятый",
       "sixth": "шестой", "seventh": "седьмой", "eighth": "восьмой", "ninth": "девятый", "tenth": "десятый",
       "last": "последний", "1st": "1", "2nd": "2", "3rd": "3"}
ORD_RE = r"(?P<n>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last|\d+(?:st|nd|rd|th)?)"
FOLDERS = {"downloads": "загрузки", "my downloads": "загрузки", "documents": "документы", "my documents": "документы",
           "desktop": "рабочий стол", "the desktop": "рабочий стол", "pictures": "изображения",
           "my pictures": "изображения", "recycle bin": "корзину", "the recycle bin": "корзину", "trash": "корзину",
           "explorer": "проводник", "file explorer": "проводник", "this pc": "этот компьютер",
           "my computer": "этот компьютер"}
MEDIA = r"(?:music|song|songs|video|videos|playlist|track|mix|lofi|lo-fi|podcast|trailer|clip)"
VERBS = r"(?:open|launch|start|run|play|search|find|close|quit|pause|resume|set|turn|mute|unmute|go|type|press)"

RULES: list[tuple[str, str]] = [
    (r"^(?:what can you do|help)$", "что ты умеешь"),
    (r"^(?:what(?:'s| is) the time|what time is it|time)$", "который час"),
    (r"^(?:what(?:'s| is) the date|what(?:'s| is) today(?:'s date)?|what day is (?:it|today))$", "какое сегодня число"),
    (r"^(?:what(?:'s| is)|how(?:'s| is)) the weather(?: like)?(?: in (?P<c>.+?))?(?: today)?$", "какая погода {c_in}"),
    (r"^(?:play|put on|open|start) (?:the )?" + ORD_RE + r"(?: (?:one|result|video|link|track|song))?$",
     "включи {n_ru} результат"),
    (r"^(?:play|put on|open|show)(?: me)? (?:the )?(?:latest|newest|last|new) (?:video|episode|upload)s? "
     r"(?:from|by|of) (?P<ch>.+?)(?: on youtube)?$", "включи последний ролик от {ch}"),
    (r"^(?:search|look|find)(?: for)? (?P<q>.+?) (?:on|in) youtube$", "найди на ютубе {q}"),
    (r"^(?:search|look|find)(?: youtube)? for (?P<q>.+?) there$", "найди там {q}"),
    (r"^(?:search|look|find)(?: for)? (?P<q>.+?) there$", "найди там {q}"),
    (r"^(?:search|look|find) there for (?P<q>.+)$", "найди там {q}"),
    (r"^(?:play|put on) (?P<q>.+?) on youtube$", "включи на ютубе {q}"),
    (r"^(?:play|put on) (?:some )?music$", "включи музыку"),
    (r"^(?:pause|stop)(?: (?:the )?(?:video|music|playback|song|it|this))?$", "пауза"),
    (r"^(?:resume|continue|unpause|keep playing)(?: (?:the )?(?:video|music|playback|song|it))?$", "продолжи"),
    (r"^(?:next|skip)(?: (?:track|song|video|one))?$", "следующий трек"),
    (r"^(?:previous|go back)(?: (?:track|song|video))?$", "предыдущий трек"),
    (r"^(?:set )?(?:the )?volume (?:to |at )?(?P<v>\d{1,3})(?: ?%| percent)?$", "громкость {v}"),
    (r"^(?:turn (?:it|the volume) up|volume up|louder)$", "громче"),
    (r"^(?:turn (?:it|the volume) down|volume down|quieter)$", "тише"),
    (r"^(?:mute|mute (?:the )?(?:sound|audio))$", "выключи звук"),
    (r"^(?:unmute|unmute (?:the )?(?:sound|audio))$", "включи звук"),
    (r"^what(?:'s| is) the volume$", "какая громкость"),
    (r"^(?:turn|switch) on (?:the )?vpn$|^(?:enable|connect)(?: to)? (?:the )?vpn$|^vpn on$", "включи впн"),
    (r"^(?:turn|switch) off (?:the )?vpn$|^(?:disable|disconnect)(?: from)? (?:the )?vpn$|^vpn off$", "выключи впн"),
    (r"^(?:lock) (?:the )?(?:computer|screen|pc)$", "заблокируй компьютер"),
    (r"^(?:shut ?down|turn off) (?:the )?(?:computer|pc)$", "выключи компьютер"),
    (r"^(?:restart|reboot) (?:the )?(?:computer|pc)$", "перезагрузи компьютер"),
    (r"^(?:take a )?screenshot$", "сделай скриншот"),
    (r"^close (?:the |this )?tab$", "закрой вкладку"),
    (r"^close (?:the |this )?window$", "закрой окно"),
    (r"^(?:close|quit|exit) (?:the )?(?P<x>.+)$", "закрой {x_app}"),
    (r"^type (?P<t>.+)$", "напиши {t}"),
    (r"^press (?P<k>.+)$", "нажми {k}"),
    (r"^(?:search|google|look up|find)(?: for)? (?P<q>.+)$", "найди {q_where}"),
    (r"^(?:play|put on) (?P<q>.+)$", "поставь {q}"),
    (r"^(?:open|launch|start|run|go to) (?:the )?(?:my )?(?P<x>.+?)(?: app)?$", "открой {x_target}"),
]
_COMPILED = [(re.compile(p, re.I), t) for p, t in RULES]
_SPLIT = re.compile(r"\s*,?\s+(?:and then|then|and)\s+(?=" + VERBS + r"\b)", re.I)


def _translate_one(text: str) -> str | None:
    t = text.strip().strip(".!?")
    t = re.sub(r"^(?:please|can you|could you|would you|jarvis,?)\s+", "", t, flags=re.I)
    t = re.sub(r"\s+please$", "", t, flags=re.I)
    for rx, template in _COMPILED:
        m = rx.match(t)
        if not m:
            continue
        g = {k: (v or "") for k, v in m.groupdict().items()}
        values = dict(g)
        if "n" in g:
            values["n_ru"] = ORD.get(g["n"].lower(), re.sub(r"\D", "", g["n"]))
        if "c" in g:
            values["c_in"] = f"в {g['c']}" if g["c"] else ""
        if "x" in g:
            x = g["x"].strip()
            values["x_target"] = FOLDERS.get(x.lower(), x)
            values["x_app"] = "браузер" if x.lower() in ("browser", "the browser") else x
        if "q" in g:
            q = g["q"]
            values["q_where"] = f"на ютубе {q}" if re.search(MEDIA, q, re.I) else f"в интернете {q}"
        return template.format(**values).strip()
    return None


def translate(text: str) -> str | None:
    """Английская команда (в т. ч. цепочка «open YouTube and search there for lofi») → русская. None — не знаю."""
    parts = _SPLIT.split(text.strip())
    out = []
    for part in parts:
        tr = _translate_one(part)
        if tr is None:
            return None
        out.append(tr)
    return " а потом ".join(out)
