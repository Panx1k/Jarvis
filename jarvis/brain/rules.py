"""Локальный разбор русских команд (без интернета, мгновенно).

Понимает контекст: «найди там …» (на активном сайте), «включи первый результат», цепочки
«открой ютуб и найди там музыку». Неуверенные разборы помечаются weak — при наличии LLM
их лучше отдать Claude.
"""
from __future__ import annotations

import re
import time
from typing import Callable

from jarvis.brain.base import Action, BrainReply, Executor, Plan
from jarvis.tools._common import default_browser_key, find_site, looks_like_url
from jarvis.tools.files import _drive, match_folder
from jarvis.utils.text import ORDINAL_RE, clean_spaces, normalize, ordinal_to_int, words_to_digits

FILL = r"(?:\s+(?:мне|нам|пожалуйста|плиз|быстренько|быстро|сейчас|скорее))*"
V_OPEN = r"(?:открой|откройте|открыть|открывай|покажи|перейди\s+(?:на|в)|зайди\s+(?:на|в))"
V_LAUNCH = r"(?:запусти|запустить|запускай|включи|включить|врубай|вруби|стартани)"
V_CLOSE = r"(?:закрой|закрыть|закройте|заверши|завершить|выключи|выруби|вырубай|останови|убей|убери)"
V_SEARCH = r"(?:найди|найти|поищи|поищем|ищи|искать|загугли|погугли|отыщи)"
V_PLAY = r"(?:включи|включай|поставь|поставить|воспроизведи|запусти|играй|сыграй|проиграй|вруби|врубай|давай)"
YT = r"(?:ютуб\w*|ютюб\w*|youtube|you\s?tube)"
VPN = r"(?:впн|vpn|в\s?п\s?н|вэпээн|випиэн|ви\s?пи\s?эн)"
MEDIA_WORDS = r"(?:музык|песн|песен|трек|клип|видео|видос|ролик|фильм|сериал|мульт|плейлист|альбом|подкаст|" \
              r"стрим|лофи|lofi|саундтрек|микс|mix|сборник|концерт|летсплей|обзор)"
COMMAND_VERBS = r"(?:открой|запусти|включи|найди|поставь|закрой|выключи|сделай|поищи|перейди|нажми|напиши|" \
                r"напечатай|заблокируй|убавь|прибавь)"

_SPLIT_RE = re.compile(
    r"\s*,?\s+(?:а\s+потом|и\s+потом|затем|после\s+этого|потом|и\s+ещ[её])\s+"
    r"|\s*,?\s+и\s+(?=" + COMMAND_VERBS + r"\b)"
    r"|\s*,\s*(?=" + COMMAND_VERBS + r"\b)"
)
_BARE_VERB_RE = re.compile(r"(?<=\s)(?:открой|запусти|найди|поищи|включи|поставь|закрой|выключи|сделай|"
                           r"заблокируй|убавь|прибавь)\b")
_FILLER_RE = re.compile(
    r"^(?:пожалуйста|будь\s+добр\w*|слушай|эй|ну|а|так|можешь|можете|сможешь|не\s+мог\w*\s+бы\s+ты|"
    r"давай-ка|ты\s+можешь|я\s+хочу\s+чтобы\s+ты|хочу\s+чтобы\s+ты)[,\s]+")

HELP_TEXT = ("Я умею: открывать и закрывать приложения («открой Discord», «закрой Chrome»), сайты («открой YouTube»), "
             "искать в интернете и на YouTube («найди на YouTube музыку для учёбы», «включи первый результат»), "
             "включать музыку и видео, ставить на паузу и продолжать, менять громкость («громкость 30 процентов»), "
             "включать и выключать VPN, открывать папки («открой загрузки»), печатать текст и нажимать клавиши, "
             "блокировать и выключать компьютер (с подтверждением), говорить время и погоду.")
NOT_UNDERSTOOD = ("Не понял команду. Попробуйте, например: «открой YouTube», «включи музыку», "
                  "«громкость 40 процентов». Для свободных вопросов подключите Claude (ANTHROPIC_API_KEY в .env).")


class _U:
    """Фраза: оригинал и нормализованная форма одинаковой длины (для извлечения фрагментов)."""

    def __init__(self, raw: str):
        raw = clean_spaces(raw).strip(" .!?…,")
        n = normalize(raw)
        while True:
            m = _FILLER_RE.match(n)
            if not m:
                break
            raw, n = raw[m.end():], n[m.end():]
        m = re.search(r"[,\s]+(?:пожалуйста|плиз)$", n)
        if m:
            raw, n = raw[:m.start()], n[:m.start()]
        self.raw, self.n = raw.strip(), n.strip()

    def grp(self, m: re.Match, name: str) -> str:
        if m.group(name) is None:
            return ""
        return self.raw[m.start(name):m.end(name)].strip(" ,.«»\"'")


class RuleParser:
    def __init__(self, runtime):
        self.rt = runtime
        self.handlers: list[Callable[[_U, dict], Plan | None]] = [
            self.h_small_talk, self.h_preset, self.h_news, self.h_power, self.h_vpn, self.h_volume, self.h_media, self.h_youtube_latest,
            self.h_result, self.h_tool_patterns, self.h_uninstall, self.h_there, self.h_youtube, self.h_music,
            self.h_search, self.h_question, self.h_close, self.h_type, self.h_key, self.h_folder, self.h_site_in,
            self.h_site, self.h_app,
            self.h_play_fallback, self.h_open_fallback,
        ]

    def split(self, text: str) -> list[str]:
        n = normalize(text)
        parts, prev = [], 0
        for m in _SPLIT_RE.finditer(n):
            parts.append(text[prev:m.start()])
            prev = m.end()
        parts.append(text[prev:])
        parts = [p for p in (x.strip() for x in parts) if p]
        out = []
        for part in parts:
            pn = normalize(part)
            if re.match(r"^(?:напиши|напечатай|введи|набери|впиши)\b", pn):
                out.append(part)
                continue
            prev = 0
            for m in _BARE_VERB_RE.finditer(pn):
                if m.start() > 0:
                    out.append(part[prev:m.start()].strip())
                    prev = m.start()
            out.append(part[prev:].strip())
        return [p for p in out if p]

    def parse(self, text: str) -> Plan | None:
        from jarvis.brain.english import translate
        from jarvis.voice.lang import detect_lang

        if detect_lang(text) == "en":
            translated = translate(text)
            if translated is None:
                return None
            text = translated
        state = {"site": self.rt.dialog.active_site, "results": bool(self.rt.dialog.last_results)
                 or bool(self.rt.dialog.last_results_source)}
        m = re.match(r"^(?:(?:джарвис|jarvis)[\s,]+)?(?:создай|сделай|добавь|запомни|сохрани)\s+(?:новый\s+)?"
                     r"(?:пресет|режим|сценарий)\s+(?P<name>[^:,—-]+?)\s*[:,—-]\s*(?P<steps>.+)$",
                     normalize(text).strip())
        if m:
            raw = text.strip()
            steps = raw[len(raw) - len(m.group("steps")):]
            return Plan([Action("create_preset", {"name": m.group("name").strip(), "steps": steps})])
        actions: list[Action] = []
        weak = False
        for part in self.split(text):
            plan = self.parse_one(part, state)
            if plan is None:
                return None
            actions.extend(plan.actions)
            weak = weak or plan.weak
        return Plan(actions, weak) if actions else None

    def parse_one(self, text: str, state: dict | None = None) -> Plan | None:
        state = state if state is not None else {"site": self.rt.dialog.active_site}
        u = _U(text)
        if not u.n:
            return None
        for handler in self.handlers:
            plan = handler(u, state)
            if plan:
                for a in plan.actions:
                    if a.tool in ("search_youtube", "play_youtube", "youtube_latest"):
                        state["site"], state["results"] = "youtube", True
                    elif a.tool == "open_site":
                        found = find_site(self.rt.settings, a.args.get("site", ""))
                        state["site"] = found[0] if found else state.get("site")
                    elif a.tool == "search_web":
                        state["results"] = True
                return plan
        return None

    @staticmethod
    def run(plan: Plan, execute: Executor) -> BrainReply:
        texts, names = [], []
        for a in plan.actions:
            if a.tool is None:
                texts.append(a.reply or "")
                continue
            r = execute(a.tool, a.args)
            names.append(a.tool)
            texts.append(r.message)
            if r.data.get("pending") or not r.ok:
                break
        return BrainReply(" ".join(t for t in texts if t), True, "rules", names)

    @staticmethod
    def _plan(tool: str | None, weak: bool = False, reply: str | None = None, **args) -> Plan:
        return Plan([Action(tool, {k: v for k, v in args.items() if v not in (None, "")}, reply)], weak)

    def h_small_talk(self, u: _U, st: dict) -> Plan | None:
        n = u.n
        if re.match(r"^(?:что ты умеешь|что умеешь|что ты можешь|помощь|справка|какие (?:есть )?команды)", n):
            return self._plan(None, reply=HELP_TEXT)
        if re.match(r"^(?:привет\w*|здравствуй\w*|добр\w+ (?:утро|день|вечер)|хай|салют)$", n):
            return self._plan(None, weak=True, reply="Здравствуйте. Чем могу помочь?")
        if re.match(r"^(?:спасибо|благодарю|спс|пасиб\w*)(?: большое| тебе)?$", n):
            return self._plan(None, weak=True, reply="Всегда к вашим услугам.")
        if re.match(r"^(?:замолчи|помолчи|хватит говорить|заткнись|тихо|отмена|ничего|забудь|неважно)$", n):
            return self._plan(None, reply="Хорошо.")
        if re.match(r"^(?:ты тут|ты здесь|ты меня слышишь|слышишь)$", n):
            return self._plan(None, reply="Да, я здесь.")
        return None

    def h_power(self, u: _U, st: dict) -> Plan | None:
        n = words_to_digits(u.n)
        delay = None
        m = re.search(r"через\s+(\d+)\s*(секунд\w*|сек|минут\w*|мин)?", n)
        if m:
            delay = int(m.group(1)) * (60 if (m.group(2) or "").startswith("мин") else 1)
        pc = r"(?:компьютер|комп|пк|систему|ноутбук|ноут)"
        if re.match(rf"^(?:выключи|выруби|отключи|заверши\s+работу)\s+{pc}", n) or n == "выключение компьютера":
            return self._plan("shutdown_computer", delay=delay)
        if re.match(rf"^(?:перезагрузи|перезапусти|ребутни)\s+{pc}", n) or n == "перезагрузка":
            return self._plan("restart_computer", delay=delay)
        if re.match(rf"^(?:(?:переведи|отправь|уведи)\s+)?(?:{pc}\s+)?(?:в\s+)?(?:спящий режим|режим сна|сон)$", n) \
                or re.match(rf"^усыпи(?:\s+{pc})?$", n):
            return self._plan("sleep_computer")
        return None

    def h_vpn(self, u: _U, st: dict) -> Plan | None:
        n = u.n
        if re.match(rf"^(?:включи|подключи|вруби|врубай|запусти|активируй|поставь|подключись\s+к)\w*{FILL}\s+{VPN}\b", n) \
                or re.match(rf"^{VPN}\s+(?:включи|вкл|on)", n):
            return self._plan("vpn_on")
        if re.match(rf"^(?:выключи|отключи|выруби|отруби|останови|деактивируй|отключись\s+от)\w*{FILL}\s+{VPN}\b", n) \
                or re.match(rf"^{VPN}\s+(?:выключи|выкл|off)", n):
            return self._plan("vpn_off")
        return None

    def h_volume(self, u: _U, st: dict) -> Plan | None:
        n = words_to_digits(u.n)
        vol = r"(?:громкост\w*|звук\w*|уровень громкости)"
        if re.search(rf"(?:какая|сколько)\s+(?:сейчас\s+)?(?:\w+\s+)?громкост", n):
            return self._plan("get_volume")
        if re.search(rf"{vol}.*(?:максимум|максимальн\w*|на полную|сто процентов)|^(?:на\s+)?полную громкость", n):
            return self._plan("set_volume", percent=100)
        if re.search(rf"{vol}.*(?:на\s+)?(?:половину|наполовину|пол)$", n):
            return self._plan("set_volume", percent=50)
        if re.search(rf"{vol}.*(?:минимум|минимальн\w*)", n):
            return self._plan("set_volume", percent=5)
        m = re.search(rf"{vol}\D{{0,25}}?(\d{{1,3}})\s*(?:%|процент\w*)?", n)
        if m and not re.search(r"(?:громче|тише|прибав|убав|увелич|уменьш)", n):
            return self._plan("set_volume", percent=min(100, int(m.group(1))))
        step = re.search(r"на\s+(\d{1,3})", n)
        delta = int(step.group(1)) if step else 10
        if re.match(rf"^(?:сделай\s+)?(?:по|ещ[её]\s+)?громче|^(?:прибавь|увеличь|подними)\s+{vol}|^(?:прибавь|погромче)", n):
            return self._plan("change_volume", delta=delta)
        if re.match(rf"^(?:сделай\s+)?(?:по|ещ[её]\s+)?тише\b|^(?:убавь|уменьши|понизь|снизь)\s+{vol}|^(?:убавь|потише)", n):
            return self._plan("change_volume", delta=-delta)
        if re.match(rf"^(?:выключи|отключи|убери|выруби|вырубай)\s+звук$|^без звука$|^замь?ют\w*$|^mute$|^заглуши(?:\s+звук)?$", n):
            return self._plan("mute")
        if re.match(r"^(?:включи|верни)\s+звук(?:\s+обратно)?$|^(?:размьют\w*|unmute)$", n):
            return self._plan("unmute")
        return None

    def h_media(self, u: _U, st: dict) -> Plan | None:
        n = u.n
        obj = r"(?:видео|видос\w*|ролик\w*|музык\w*|песн\w*|трек\w*|воспроизведение|плеер|ютуб\w*|фильм|сериал|это|все)"
        if re.match(rf"^(?:поставь\s+)?(?:{obj}\s+)?на\s+паузу$|^пауза$|^паузу$", n) \
                or re.match(rf"^(?:останови|приостанови|стопни|стоп|выключи|выруби|вырубай|тормозни|заморозь)(?:\s+{obj})?$", n):
            return self._plan("pause_media")
        if re.match(rf"^(?:продолж\w*|возобнов\w*)(?:\s+(?:{obj}|играть|смотреть|воспроизведение))?$", n) \
                or re.match(rf"^сними\s+(?:{obj}\s+)?(?:с\s+паузы|паузу)$", n) \
                or re.match(rf"^(?:включи|запусти)\s+(?:{obj}\s+)?(?:обратно|снова|дальше|опять)$", n) \
                or re.match(r"^(?:включи|запусти)\s+(?:видео|видос|ролик)$", n) \
                or re.match(r"^(?:играй|играть)\s+дальше$|^дальше\s+играй$|^плей$|^play$", n):
            return self._plan("resume_media")
        track = r"(?:трек\w*|песн\w*|видео|ролик\w*|композици\w*|видос\w*)"
        if re.match(rf"^(?:(?:включи|поставь|давай)\s+)?(?:следующ\w+|некст)(?:\s+{track})?$", n) \
                or re.match(rf"^(?:переключи|скипни|пропусти)(?:\s+{track})?$|^дальше$", n):
            return self._plan("next_track")
        if re.match(rf"^(?:(?:включи|поставь|верни|давай)\s+)?(?:предыдущ\w+|прошл\w+)(?:\s+{track})$", n) \
                or re.match(rf"^(?:включи|верни)\s+предыдущ\w+$|^назад$", n):
            return self._plan("previous_track")
        return None

    def h_youtube_latest(self, u: _U, st: dict) -> Plan | None:
        m = re.search(rf"(?:последн\w*|нов\w*|свеж\w*|недавн\w*)\s+(?:ролик\w*|видео|видос\w*|выпуск\w*|стрим\w*|"
                      rf"видеоролик\w*)\s+(?:на\s+{YT}\s+)?(?:от|у|с\s+канала|канала|на\s+канале|из\s+канала)\s+"
                      rf"(?P<ch>.+?)(?:\s+(?:на|в|с)\s+{YT})?$", u.n)
        if m:
            return self._plan("youtube_latest", channel=u.grp(m, "ch"))
        return None

    def h_result(self, u: _U, st: dict) -> Plan | None:
        n = u.n
        noun = r"(?:результат\w*|ролик\w*|видео|видос\w*|вариант\w*|ссылк\w*|трек\w*|песн\w*|клип\w*|сайт\w*|" \
               r"из\s+(?:них|списка|результатов)|в\s+списке|по\s+счету|на\s+экране)"
        m = re.match(rf"^(?:{V_PLAY}|{V_OPEN}|выбери|жми|давай){FILL}\s+(?:номер\s+|№\s*)?(?P<ord>{ORDINAL_RE})"
                     rf"(?:\s+{noun})*$", n) or re.match(rf"^(?P<ord>{ORDINAL_RE})(?:\s+{noun})*$", n)
        if m:
            idx = ordinal_to_int(m.group("ord"))
            if idx is not None and (st.get("results") or idx != -1):
                return self._plan("open_result", index=idx)
        if st.get("results") and re.match(rf"^(?:{V_PLAY}|{V_OPEN})\s+(?:его|это|этот|эту|ее)$", n):
            return self._plan("open_result", index=1)
        return None

    def h_tool_patterns(self, u: _U, st: dict) -> Plan | None:
        for t in self.rt.registry.all():
            for pattern in t.patterns:
                m = pattern.search(u.n)
                if m:
                    args = {k: u.raw[m.start(k):m.end(k)].strip() for k, v in m.groupdict().items() if v}
                    return Plan([Action(t.name, args)])
        return None

    def _site_action(self, site: str | None, verb_kind: str, query: str) -> Plan:
        if site == "youtube":
            return self._plan("play_youtube" if verb_kind == "play" else "search_youtube", query=query)
        if site and self.rt.settings.get(f"sites.{site}.search"):
            return self._plan("search_web", query=query, site=site)
        if verb_kind == "play":
            return self._plan("play_media", query=query)
        return self._plan("search_web", query=query)

    @staticmethod
    def _verb_kind(verb: str) -> str:
        v = normalize(verb)
        if re.match(V_SEARCH, v):
            return "search"
        if re.match(r"(?:включ|постав|воспроизвед|запуст|игра|сыгра|проигра|вруб|давай)", v):
            return "play"
        return "open"

    def h_there(self, u: _U, st: dict) -> Plan | None:
        where = r"(?:там|тут|здесь|на\s+н[её]м|на\s+этом\s+сайте|на\s+сайте)"
        m = re.match(rf"^(?P<verb>{V_SEARCH}|{V_PLAY}|{V_OPEN}){FILL}\s+{where}{FILL}\s+(?P<q>.+)$", u.n) \
            or re.match(rf"^(?P<verb>{V_SEARCH}|{V_PLAY}|{V_OPEN}){FILL}\s+(?P<q>.+?)\s+{where}$", u.n)
        if not m:
            return None
        return self._site_action(st.get("site"), self._verb_kind(m.group("verb")), u.grp(m, "q"))

    def h_youtube(self, u: _U, st: dict) -> Plan | None:
        n = u.n
        loc = re.search(rf"\b(?:на|в|по|через)\s+{YT}\b", n)
        m = re.match(rf"^(?P<verb>{V_SEARCH}|{V_PLAY}|{V_OPEN}|посмотр\w*){FILL}\s+", n)
        if not loc or not m:
            return None
        q_raw = (u.raw[m.end():loc.start()] + " " + u.raw[loc.end():]) if loc.start() >= m.end() else u.raw[m.end():]
        q_raw = clean_spaces(re.sub(r"(?i)^(?:мне|нам)\s+", "", q_raw.strip()))
        kind = self._verb_kind(m.group("verb"))
        if not q_raw:
            return self._plan("open_site", site="youtube")
        if kind == "play":
            return self._plan("play_youtube", query=q_raw)
        return self._plan("search_youtube", query=q_raw)

    def h_music(self, u: _U, st: dict) -> Plan | None:
        m = re.match(rf"^(?:{V_PLAY}){FILL}\s+(?P<tail>.+)$", u.n)
        if not m:
            return None
        tail = u.grp(m, "tail")
        t = normalize(tail)
        t = re.sub(r"^(?:какую[- ]нибудь|что[- ]нибудь|немного|любую)\s+", "", t)
        if re.match(r"^(?:музык\w*|песн\w*|песенк\w*|музон\w*|что-нибудь послушать)$", t):
            return self._plan("play_media")
        if re.search(MEDIA_WORDS, t):
            return self._plan("play_media", query=tail)
        return None

    def h_preset(self, u: _U, st: dict) -> Plan | None:
        """«Режим работа», «запусти работу», «включи игровой режим» — только если такой пресет есть."""
        from jarvis.tools.presets import find

        settings = getattr(self.rt, "settings", None)
        if settings is None or not (settings.get("presets", {}) or {}):
            return None
        n = u.n.strip(" .!?")
        candidates = []
        m = re.match(r"^(?:(?:включи|запусти|активируй|вруби|давай)\s+)?(?:режим|пресет|сценарий)\s+(?P<name>.+)$", n)
        if m:
            candidates.append(m.group("name"))
        m = re.match(r"^(?:(?:включи|запусти|активируй|вруби|давай)\s+)?(?P<name>.+?)\s+(?:режим|пресет|сценарий)$", n)
        if m:
            candidates.append(m.group("name"))
        m = re.match(r"^(?:включи|запусти|активируй|вруби|давай)\s+(?P<name>.+)$", n)
        if m:
            candidates.append(m.group("name"))
        candidates.append(n)
        for name in candidates:
            found = find(settings, name)
            if found:
                return self._plan("run_preset", name=found[0])
        return None

    def h_news(self, u: _U, st: dict) -> Plan | None:
        """Новости: JARVIS сам получает и пересказывает (браузер — только «открой источник/статью»).
        Запрос новостей — weak: при доступном AI Brain он перескажет своими словами; без него — правилами."""
        n = u.n
        from jarvis.services import news as news_service

        m = news_service.get()
        recent = bool(m.current) and time.time() - m.updated_at < 1800 if m.updated_at else bool(m.current)
        if recent:
            mo = re.match(r"^(?:открой|покажи)\s+(?:мне\s+)?(?:источник|статью|эту новость|новость|сайт(?: новости)?|"
                          r"оригинал)(?:\s+(?P<i>\S+?)(?:\s+новост\w*)?)?(?:\s+в браузере)?$", n)
            if mo:
                i = mo.group("i")
                return self._plan("news_open", index=i if i and ordinal_to_int(i) else None)
            if re.search(r"\b(?:откуда (?:эта |это |такая )?(?:информаци\w*|новость|данные|известно)|кто (?:это )?"
                         r"(?:сообщил|написал)|какой источник|что за источник|источник)\b", n):
                return self._plan("news_source", weak=True)
            mm = re.match(r"^(?:расскажи\s+)?(?:подробнее|поподробнее|детальнее|больше)(?:\s+(?:про|о|об)\s+"
                          r"(?P<i>\S+)(?:\s+новост\w*)?)?$", n)
            if mm:
                return self._plan("news_details", weak=True, index=mm.group("i"))
            if re.match(r"^(?:ещ[её]|дальше|другие|следующие)(?:\s+новост\w*)?$|^что ещ[её](?: нового)?$", n):
                return self._plan("news_more", weak=True)
        if re.search(r"\bновост\w*|\bчто нового\b|\bчто (?:произошло|случилось) (?:сегодня|за день|в мире)", n) \
                and not re.match(r"^(?:открой|зайди|найди на ютубе|включи)", n):
            return self._plan("news_get", weak=True, query=u.raw)
        return None

    def h_search(self, u: _U, st: dict) -> Plan | None:
        m = re.match(rf"^(?:{V_SEARCH}){FILL}(?:\s+(?P<where>в\s+интернете|в\s+инете|в\s+сети|в\s+гугле|в\s+google|"
                     rf"в\s+яндексе|в\s+браузере|информацию(?:\s+(?:о|об|про))?))?\s+(?P<q>.+)$", u.n)
        if not m:
            return None
        q = u.grp(m, "q")
        where = m.group("where") or ""
        if "яндекс" in where:
            return self._plan("search_web", query=q, site="яндекс")
        if not where and re.search(MEDIA_WORDS, normalize(q)):
            return self._plan("search_youtube", query=q)
        return self._plan("search_web", query=q)

    def h_question(self, u: _U, st: dict) -> Plan | None:
        if re.match(r"^(?:что такое|кто так(?:ой|ая|ие)|что значит|что означает|как (?:сделать|приготовить|называется)|"
                    r"почему|зачем|сколько|где находится|когда|какой|какая|какие|расскажи)\b", u.n):
            return self._plan("search_web", weak=True, query=u.raw)
        return None

    def _app_name(self, name: str) -> str:
        if normalize(name) in ("браузер", "браузера", "мой браузер", "веб браузер"):
            key = default_browser_key()
            if key:
                return self.rt.settings.get(f"apps.{key}.start_name") or key
        return name

    def h_close(self, u: _U, st: dict) -> Plan | None:
        m = re.match(rf"^(?:{V_CLOSE}){FILL}\s+(?P<a>.+)$", u.n)
        if not m:
            return None
        a = normalize(m.group("a"))
        if re.match(r"^(?:эту\s+|текущую\s+)?вкладк\w*$", a):
            return self._plan("press_key", keys="ctrl+w")
        if re.match(r"^(?:это\s+|текущее\s+|активное\s+)?окно$", a):
            return self._plan("press_key", keys="alt+f4")
        name = self._app_name(u.grp(m, "a"))
        match = self.rt.apps.resolve(name)
        return self._plan("close_app", weak=match is None, app=name)

    def h_type(self, u: _U, st: dict) -> Plan | None:
        m = re.match(r"^(?:напечатай|напиши|введи|набери|впиши)(?:\s+текст)?\s*[:,\-]?\s+(?P<t>.+)$", u.n)
        if not m:
            return None
        weak = bool(re.search(r"\b(?:в|во)\s+(?:телеграм\w*|дискорд\w*|вк|вконтакте|чат\w*|сообщени\w*)", u.n)) \
            or u.n.startswith("напиши сообщение")
        return self._plan("type_text", weak=weak, text=u.grp(m, "t"))

    def h_key(self, u: _U, st: dict) -> Plan | None:
        m = re.match(r"^(?:нажми|нажать|жми|кликни)(?:\s+на)?(?:\s+(?:клавиш\w*|кнопк\w*|сочетание))?\s+(?P<k>.+?)"
                     r"(?:\s+(?P<times>\d+|два|три|пять)\s+раз\w*)?$", words_to_digits(u.n))
        if not m:
            return None
        times = {"два": 2, "три": 3, "пять": 5}.get(m.group("times") or "", None) or \
            (int(m.group("times")) if (m.group("times") or "").isdigit() else 1)
        return self._plan("press_key", keys=m.group("k"), times=times if times > 1 else None)

    def h_folder(self, u: _U, st: dict) -> Plan | None:
        m = re.match(rf"^(?:{V_OPEN}|запусти){FILL}\s+(?:мне\s+)?(?P<f>.+)$", u.n)
        if not m:
            return None
        f = u.grp(m, "f")
        if _drive(f) or match_folder(self.rt.settings, f):
            return self._plan("open_folder", folder=f)
        m2 = re.match(r"^(?:папку|директорию|каталог)\s+(?P<p>.+)$", normalize(f))
        if m2:
            return self._plan("open_folder", folder=f[m2.start("p"):])
        return None

    def h_uninstall(self, u: _U, st: dict) -> Plan | None:
        """«Удали Steam», «снеси доту», «удали программу Zoom с компьютера». Файлы, пресеты и сообщения — не сюда."""
        m = re.match(r"^(?:удали|снеси|деинсталлируй|удалить)\s+(?:мне\s+)?(?:с\s+(?:компьютера|компа|пк)\s+)?"
                     r"(?:программу\s+|приложение\s+|игру\s+|прогу\s+)?(?P<a>.+?)(?:\s+с\s+(?:компьютера|компа|пк))?$",
                     u.n)
        if not m:
            return None
        a = m.group("a")
        if re.match(r"^(?:файл|папк|сообщени|пресет|режим|сценари|истори|вс[её]\b|это|его|е[её]\b|текст|слов|букв|"
                    r"вкладк|строк|последн|скриншот|снимок)", a):
            return None
        from jarvis.nlu import lexicon

        known = lexicon.get(self.rt.settings).match(a, ("apps", "games"), threshold=0.84)
        strong = known.best is not None and not known.ambiguous
        return self._plan("uninstall_app", weak=not strong, app=u.grp(m, "a"))

    def h_site_in(self, u: _U, st: dict) -> Plan | None:
        """«Открой YouTube в Chrome» — сайт в названном браузере."""
        from jarvis.tools._common import browser_exe

        m = re.match(rf"^(?:{V_OPEN}|{V_LAUNCH}){FILL}\s+(?:сайт\s+)?(?P<s>.+?)\s+(?:в|во|через)\s+(?P<b>[\w\s-]+)$",
                     u.n)
        if not m or not find_site(self.rt.settings, u.grp(m, "s")):
            return None
        b = u.grp(m, "b")
        if not browser_exe(b) and not re.match(r"^(?:хром|chrome|google chrome|гугл хром|эдж|edge|microsoft edge|"
                                               r"firefox|фаерфокс|мозилл|опер|opera|яндекс браузер)", normalize(b)):
            return None
        return self._plan("open_site", site=u.grp(m, "s"), browser=b)

    def h_site(self, u: _U, st: dict) -> Plan | None:
        m = re.match(rf"^(?:{V_OPEN}|{V_LAUNCH}){FILL}\s+(?:сайт\s+)?(?P<s>.+)$", u.n)
        if not m:
            return None
        s = u.grp(m, "s")
        if find_site(self.rt.settings, s):
            return self._plan("open_site", site=s)
        if looks_like_url(s.replace(" точка ", ".")):
            return self._plan("open_url", url=s.replace(" точка ", "."))
        return None

    def h_app(self, u: _U, st: dict) -> Plan | None:
        m = re.match(rf"^(?:{V_OPEN}|{V_LAUNCH}){FILL}\s+(?:приложение\s+|программу\s+|игру\s+)?(?P<a>.+)$", u.n)
        if not m:
            return None
        name = self._app_name(u.grp(m, "a"))
        match = self.rt.apps.resolve(name)
        if match is not None and match.kind != "missing":
            return self._plan("open_app", app=name)
        try:
            from jarvis.tools.steam import find_game
            if find_game(name):
                return self._plan("launch_game", game=name)
        except Exception:
            pass
        if match is not None:
            return self._plan("open_app", app=name)
        return None

    def h_play_fallback(self, u: _U, st: dict) -> Plan | None:
        m = re.match(r"^(?P<verb>поставь|включи|воспроизведи|врубай|вруби|сыграй|проиграй)\s+(?P<q>.+)$", u.n)
        if not m:
            return None
        strong = m.group("verb") in ("поставь", "воспроизведи", "сыграй", "проиграй")
        return self._plan("play_media", weak=not strong, query=u.grp(m, "q"))

    def h_open_fallback(self, u: _U, st: dict) -> Plan | None:
        m = re.match(rf"^(?:{V_OPEN}|запусти){FILL}\s+(?P<a>.+)$", u.n)
        if not m:
            return None
        target = u.grp(m, "a")
        if re.search(MEDIA_WORDS, normalize(target)):
            query = re.sub(r"(?i)^(?:какое|какую|какой)[- ]нибудь\s+|^что[- ]нибудь\s+", "", target)
            return self._plan("play_media", query=query)
        return self._plan("open_app", weak=True, app=target)
