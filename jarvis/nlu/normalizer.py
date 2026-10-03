"""Нормализация текста после распознавания речи — без изменения смысла.

«Ну Джарвис, можешь пожалуйста запустить дискорд?» → «запусти Discord»
«Блин, давай откроем стим» → «открой Steam»
«Открой-ка мне диск орд» → «открой мне Discord»

Что делает:
  * убирает обращение («Джарвис» и его искажения) и слова-паразиты («ну», «блин», «пожалуйста», «короче»);
  * вежливые и разговорные формы превращает в команду: «можешь открыть» / «давай откроем» / «хочу, чтобы ты
    открыл» → «открой»;
  * название после глагола команды сверяет со словарём и пишет правильно («хром» → Chrome, «стем» → Steam).
    Если название похоже сразу на несколько — не выбирает, а отмечает неоднозначность;
  * текст для печати и сообщений («напиши …») не трогает.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from jarvis.utils.text import clean_spaces, normalize, similarity

WAKE_FORMS = {"джарвис", "джервис", "джарвиз", "жарвис", "джарвес", "джервиз", "джарвиса", "джарвису", "джарус",
              "jarvis", "jervis", "jarvus", "jarus", "jarvi", "джарви", "жарвиз"}
LEADING_FILLERS = {"ну", "а", "слушай", "слушайте", "эй", "так", "короче", "ладно", "смотри", "вот", "типа", "итак",
                   "значит", "это", "окей", "ok", "hey", "алло", "эх", "ой", "о"}
ANYWHERE_FILLERS = {"пожалуйста", "плиз", "плз", "please", "блин", "бля", "блядь", "блять", "сука", "нахуй", "нахер",
                    "ёпт", "епт", "ээ", "эээ", "э", "эм", "мм", "ммм", "хм", "ну-ка", "давай-ка", "уж", "-ка"}
POLITE_PHRASES = ["будь добр", "будь добра", "будьте добры", "если можно", "если не сложно", "если тебе не сложно",
                  "будь так добр", "будь любезен"]
MODALS = re.compile(r"^(?:(?:а\s+)?(?:ты\s+|вы\s+)?(?:не\s+)?(?:можешь|можете|сможешь|сможете|мог\s+бы|могла\s+бы|"
                    r"могли\s+бы)(?:\s+ли)?(?:\s+ты|\s+вы)?|(?:я\s+)?(?:хочу|хотел\s+бы|хотела\s+бы|хотелось\s+бы|"
                    r"прошу)(?:,?\s+чтобы\s+(?:ты|вы))?|(?:мне\s+)?(?:надо|нужно|необходимо)(?:,?\s+чтобы\s+(?:ты|вы))?|"
                    r"нужно\s+чтобы\s+ты|попробуй|постарайся)\s+")
LETS = re.compile(r"^(?:давай|давайте|го|погнали)\s+")

IMPERATIVE = {
    "открыть": "открой", "закрыть": "закрой", "показать": "покажи", "найти": "найди", "сказать": "скажи",
    "рассказать": "расскажи", "написать": "напиши", "поставить": "поставь", "отправить": "отправь",
    "добавить": "добавь", "убавить": "убавь", "прибавить": "прибавь", "остановить": "останови",
    "установить": "установи", "поискать": "поищи", "искать": "ищи", "дать": "дай", "помочь": "помоги",
    "убрать": "убери", "прочесть": "прочитай", "посмотреть": "посмотри", "глянуть": "глянь", "перевести": "переведи",
    "выйти": "выйди", "зайти": "зайди", "перейти": "перейди", "переслать": "перешли", "скинуть": "скинь",
    "назвать": "назови", "свернуть": "сверни", "развернуть": "разверни", "вернуть": "верни",
    "заблокировать": "заблокируй", "активировать": "активируй", "сыграть": "сыграй", "включить": "включи",
    "выключить": "выключи", "запустить": "запусти", "удалить": "удали", "снести": "снеси", "прочитать": "прочитай",
    "поменять": "поменяй", "сменить": "смени", "сделать": "сделай", "скачать": "скачай", "проверить": "проверь",
    "открыл": "открой", "закрыл": "закрой", "нашёл": "найди", "нашел": "найди", "поставил": "поставь",
    "добавить": "добавь", "добавил": "добавь", "закрыть": "закрой", "запустил": "запусти", "включил": "включи",
    "выключил": "выключи", "удалил": "удали",
}
FIRST_PLURAL = {"откроем": "открой", "закроем": "закрой", "найдём": "найди", "найдем": "найди", "поставим": "поставь",
                "глянем": "глянь", "посмотрим": "посмотри", "послушаем": "включи", "сыграем": "сыграй",
                "поиграем": "запусти", "сделаем": "сделай", "скачаем": "скачай", "поменяем": "поменяй",
                "вернём": "верни", "вернем": "верни", "свернём": "сверни", "свернем": "сверни", "уберём": "убери",
                "уберем": "убери", "добавим": "добавь", "отправим": "отправь", "напишем": "напиши"}

VERB_KINDS = {
    "open": ({"открой", "запусти", "запускай", "открывай", "врубай", "вруби", "стартани", "покажи", "зайди",
              "перейди"}, ("apps", "games", "sites"), 0.82),
    "close": ({"закрой", "закрывай", "заверши", "выруби", "убей", "сверни", "разверни"}, ("apps", "games"), 0.82),
    "toggle": ({"включи", "выключи", "отключи", "подключи"}, ("apps", "games", "terms"), 0.9),
    "remove": ({"удали", "снеси", "деинсталлируй"}, ("apps", "games"), 0.82),
    "exclude": ({"добавь", "убери", "внеси", "занеси", "вычеркни"}, ("apps", "games"), 0.86),
}
DICTATION = {"напиши", "напечатай", "введи", "набери", "скажи", "отправь", "перешли", "переведи", "запомни"}
SLOT_PREFIX = re.compile(r"^(?:(?:мне|нам)\s+)?(?:(?:приложение|программу|прогу|игру|игрушку|сайт)\s+)?", re.I)
NO_CANON = {"браузер", "браузера", "мой браузер", "музыку", "музыка", "видео", "звук", "компьютер", "комп", "экран",
            "окно", "вкладку", "все", "всё", "его", "её", "ее", "это"}


@dataclass
class Normalized:
    raw: str
    text: str
    changes: list[tuple[str, str]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    wake: bool = False
    entity: str | None = None
    entity_kind: str | None = None
    entity_score: float = 0.0
    ambiguous: list[str] = field(default_factory=list)
    verb_kind: str | None = None


def is_wake_word(word: str) -> bool:
    w = normalize(word).strip(" ,.!?…:;—-")
    if w in WAKE_FORMS:
        return True
    return len(w) >= 5 and max(similarity(w, v) for v in ("джарвис", "jarvis")) >= 0.8


def to_imperative(word: str, plural: bool = False) -> str | None:
    w = normalize(word)
    if plural and w in FIRST_PLURAL:
        return FIRST_PLURAL[w]
    if w in IMPERATIVE:
        return IMPERATIVE[w]
    if plural and len(w) > 4:
        if w.endswith("им"):
            return w[:-1]
        if w.endswith("ем") and w[-3] in "аеоуяю":
            return w[:-1] + "й"
        return None
    if w.endswith(("ил", "ила", "или")) and len(w) > 4:
        w = w[: w.rindex("л")] + "ть"
    if w.endswith("ить") and len(w) > 5:
        return w[:-2]
    if w.endswith("уть") and len(w) > 5:
        return w[:-3] + "и"
    if w.endswith(("ать", "ять")) and len(w) > 5:
        return w[:-2] + "й"
    return None


class TextNormalizer:
    def __init__(self, lexicon=None):
        self.lexicon = lexicon

    def normalize(self, raw: str) -> Normalized:
        out = Normalized(raw=raw, text="")
        text = clean_spaces(raw or "")
        text = re.sub(r"(?i)\b([а-яё]+)-ка\b", r"\1", text)
        text = re.sub(r"[?!…]+", " ", text)
        text = re.sub(r"\s*[,;]\s*", ", ", text).strip(" ,.")
        words = text.split()
        words, out.wake = self._strip_wake(words)
        words = self._strip_fillers(words, out)
        text = " ".join(words).strip(" ,.")
        text = self._polite_to_command(text, out)
        text = re.sub(r"\s*,\s*$", "", text)
        text = re.sub(r"^,\s*", "", text)
        text = self._dedupe(text)
        text = self._verb_first(text, out)
        text = self._canonicalize(text, out)
        out.text = clean_spaces(text).strip(" ,.")
        return out

    @staticmethod
    def _strip_wake(words: list[str]) -> tuple[list[str], bool]:
        found = False
        kept = []
        for i, w in enumerate(words):
            near_edge = i < 4 or i >= len(words) - 2
            if near_edge and is_wake_word(w):
                found = True
                continue
            kept.append(w)
        return kept, found

    @staticmethod
    def _strip_fillers(words: list[str], out: Normalized) -> list[str]:
        if words and normalize(words[0]).strip(",.") in DICTATION:
            return words
        kept: list[str] = []
        leading = True
        for i, w in enumerate(words):
            n = normalize(w).strip(" ,.:;—")
            if n in ANYWHERE_FILLERS or (leading and n in LEADING_FILLERS):
                out.removed.append(n)
                if w.endswith(",") and kept and not kept[-1].endswith(","):
                    pass
                continue
            if kept and normalize(kept[-1]).strip(",") in DICTATION:
                kept.extend(words[i:])
                break
            leading = False
            kept.append(w)
        text = " ".join(kept)
        for phrase in POLITE_PHRASES:
            pattern = re.compile(rf"(?i)(?:^|,\s*|\s)({re.escape(phrase)})(?=[\s,]|$),?")
            if pattern.search(text):
                out.removed.append(phrase)
                text = pattern.sub(" ", text)
        return clean_spaces(text).split()

    @staticmethod
    def _polite_to_command(text: str, out: Normalized) -> str:
        first, _, tail = text.partition(" ")
        word = normalize(first).strip(",")
        if word in IMPERATIVE and tail and word != IMPERATIVE[word]:
            out.changes.append((first, IMPERATIVE[word]))
            text = f"{IMPERATIVE[word]} {tail}"
        for _ in range(3):
            n = normalize(text)
            m = MODALS.match(n) or LETS.match(n)
            if not m:
                break
            plural = bool(LETS.match(n))
            rest = text[m.end():].lstrip(" ,")
            first, _, tail = rest.partition(" ")
            imp = to_imperative(first.strip(","), plural=plural)
            if imp:
                out.changes.append((text[: m.end()] + first, imp))
                text = (imp + " " + tail).strip()
            elif plural and normalize(first) in {v for kind in VERB_KINDS.values() for v in kind[0]} | DICTATION:
                out.removed.append(n[: m.end()].strip())
                text = rest
            elif not plural:
                out.removed.append(n[: m.end()].strip())
                text = rest
            else:
                break
        return text

    @staticmethod
    def _dedupe(text: str) -> str:
        """«сверни, сверни доту» → «сверни доту» (повтор при запинке)."""
        words = text.split()
        kept: list[str] = []
        for w in words:
            if kept and normalize(kept[-1]).strip(",") == normalize(w).strip(","):
                kept[-1] = w
                continue
            kept.append(w)
        return " ".join(kept)

    @staticmethod
    def _verb_first(text: str, out: Normalized) -> str:
        """«Telegram мне открой», «доту сверни» → «открой Telegram», «сверни доту» (глагол в конце фразы)."""
        words = text.split()
        if len(words) < 2 or len(words) > 5:
            return text
        verbs = {v for kind in VERB_KINDS.values() for v in kind[0]}
        first = normalize(words[0]).strip(",")
        last = normalize(words[-1]).strip(",.")
        if first in verbs or first in DICTATION or last not in verbs:
            return text
        obj = [w for w in words[:-1] if normalize(w).strip(",") not in ("мне", "нам")]
        if not obj:
            return text
        result = " ".join([words[-1].strip(",.")] + obj).strip(" ,")
        out.changes.append((text, result))
        return result

    def _canonicalize(self, text: str, out: Normalized) -> str:
        if self.lexicon is None or not text:
            return text
        verb, _, rest = text.partition(" ")
        v = normalize(verb).strip(",")
        kind = next((k for k, (verbs, _, _) in VERB_KINDS.items() if v in verbs), None)
        if kind is None or not rest:
            return text
        out.verb_kind = kind
        _, kinds, threshold = VERB_KINDS[kind]
        prefix = SLOT_PREFIX.match(rest)
        head, slot = rest[: prefix.end()], rest[prefix.end():]
        parts = re.split(r"(\s+(?:в|во|на|через|из)\s+)", slot, maxsplit=1)
        target = parts[0].strip(" ,.")
        if not target or normalize(target) in NO_CANON or len(target.split()) > 4:
            return text
        m = self.lexicon.match(target, kinds, threshold=threshold)
        if m.best is None:
            return text
        if m.ambiguous:
            out.ambiguous = [m.best.name] + [c.name for c in m.others]
            out.entity_score = m.best.score
            return text
        out.entity, out.entity_kind, out.entity_score = m.best.name, m.best.term.kind, m.best.score
        tail = "".join(parts[1:])
        if len(parts) == 3:
            tail = self._canon_tail(parts[1], parts[2], out)
        if normalize(target) != normalize(m.best.name):
            out.changes.append((target, m.best.name))
        return f"{verb} {head}{m.best.name}{tail}"

    def _canon_tail(self, prep: str, tail: str, out: Normalized) -> str:
        """«открой ютуб в хроме» — второе название (где открыть) тоже приводим к словарному."""
        target = tail.strip(" ,.")
        m = self.lexicon.match(target, ("apps", "sites", "games"), threshold=0.88) if target else None
        if m and m.best and not m.ambiguous and normalize(target) not in NO_CANON and len(target.split()) <= 3:
            if normalize(target) != normalize(m.best.name):
                out.changes.append((target, m.best.name))
            return f"{prep}{m.best.name}"
        return prep + tail
