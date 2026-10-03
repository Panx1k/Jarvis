"""Понимание команды: STT → нормализация → контекст → намерение → сущности → уверенность → инструмент.

STT отвечает только на вопрос «что сказал пользователь». Здесь решается «что он хотел сделать»:

  * из нескольких вариантов распознавания выбирается тот, что даёт понятную команду («запусти стем» /
    «запусти стим» → Steam);
  * текст нормализуется (паразиты, вежливые формы, правильные названия — см. normalizer);
  * учитывается предыдущая реплика: «теперь YouTube» после «открой Chrome», «а про Minecraft?» после новостей;
  * намерение (OPEN_APPLICATION, CLOSE_APPLICATION, SCREEN_READ…) и сущности берутся из разбора локальных
    правил, так что понимание и выполнение используют один и тот же разбор;
  * уверенность = качество распознавания × ясность команды. Высокая — выполнять, средняя — уточнить,
    низкая — попросить повторить. Это не подтверждение действий, а защита от неверно услышанной фразы.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from jarvis.nlu.normalizer import Normalized, TextNormalizer
from jarvis.utils.text import normalize

INTENTS = {
    "open_app": "OPEN_APPLICATION", "launch_game": "LAUNCH_GAME", "close_app": "CLOSE_APPLICATION",
    "kill_app": "CLOSE_APPLICATION", "open_site": "OPEN_URL", "open_url": "OPEN_URL", "search_web": "WEB_SEARCH",
    "search_youtube": "YOUTUBE_SEARCH", "play_youtube": "PLAY_MEDIA", "play_media": "PLAY_MEDIA",
    "youtube_latest": "PLAY_MEDIA", "open_result": "OPEN_RESULT", "vpn_on": "VPN_ON", "vpn_off": "VPN_OFF",
    "vpn_status": "VPN_STATUS", "screen_read": "SCREEN_READ", "uninstall_app": "UNINSTALL_APPLICATION",
    "steam_uninstall": "UNINSTALL_APPLICATION", "exclusion_add": "EXCLUSION_ADD",
    "exclusion_remove": "EXCLUSION_REMOVE", "exclusion_list": "EXCLUSION_LIST", "set_volume": "VOLUME_SET",
    "change_volume": "VOLUME_CHANGE", "mute": "VOLUME_MUTE", "unmute": "VOLUME_UNMUTE", "get_volume": "VOLUME_GET",
    "pause_media": "MEDIA_PAUSE", "resume_media": "MEDIA_RESUME", "next_track": "MEDIA_NEXT",
    "previous_track": "MEDIA_PREVIOUS", "news_get": "NEWS", "news_more": "NEWS_MORE", "news_details": "NEWS_DETAILS",
    "news_source": "NEWS_SOURCE", "get_time": "TIME", "get_date": "DATE", "get_weather": "WEATHER",
    "window_minimize": "WINDOW_MINIMIZE", "window_maximize": "WINDOW_MAXIMIZE", "window_show": "WINDOW_SHOW",
    "show_desktop": "SHOW_DESKTOP", "scroll": "SCROLL", "mouse_click": "MOUSE_CLICK", "press_key": "PRESS_KEY",
    "type_text": "TYPE_TEXT", "lock_screen": "LOCK_SCREEN", "shutdown_computer": "SHUTDOWN",
    "restart_computer": "RESTART", "sleep_computer": "SLEEP", "take_screenshot": "SCREENSHOT",
    "open_folder": "OPEN_FOLDER", "run_preset": "RUN_PRESET", "set_confirmation_mode": "CONFIRMATION_MODE",
    "sound_settings": "SOUND_SETTINGS", "steam_install": "INSTALL_GAME", "steam_switch_account": "SWITCH_ACCOUNT",
}
BROWSERS = {"chrome", "google chrome", "edge", "microsoft edge", "firefox", "opera", "yandex", "браузер"}
ENTITY_KEYS = {"app": "application", "game": "game", "site": "site", "url": "url", "query": "query",
               "browser": "browser", "city": "city", "percent": "percent", "delta": "delta", "folder": "folder",
               "channel": "channel", "name": "name", "mode": "mode", "keys": "keys", "text": "text",
               "account": "account", "index": "index", "category": "category", "kind": "kind"}
OPEN_TOOLS = {"open_app", "open_site", "open_url", "launch_game", "search_web", "search_youtube", "play_youtube",
              "window_show", "window_maximize"}
SAFE_INTENTS = {"TIME", "DATE", "WEATHER", "VOLUME_GET", "VPN_STATUS", "EXCLUSION_LIST", "NEWS", "NEWS_MORE",
                "NEWS_DETAILS", "NEWS_SOURCE", "SCREEN_READ", "MEDIA_PAUSE", "MEDIA_RESUME"}
SHORT_OK = {"да", "нет", "ага", "угу", "стоп", "пауза", "дальше", "отмена", "хватит", "громче", "тише", "следующий",
            "назад", "окей", "ок", "yes", "no", "stop", "next", "pause"}
CONTEXT_WINDOW = 180.0


@dataclass
class Hypothesis:
    """Один вариант распознавания и то, как он понят."""
    stt_text: str
    stt_confidence: float
    normalized: Normalized
    text: str
    plan: object | None
    context: str | None = None
    score: float = 0.0


@dataclass
class Understanding:
    raw: str
    normalized: str
    intent: str
    entities: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    level: str = "high"
    tool: str | None = None
    args: dict = field(default_factory=dict)
    plan: object | None = None
    stt_confidence: float | None = None
    alternatives: list[str] = field(default_factory=list)
    context: str | None = None
    corrections: list[tuple[str, str]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    clarify: str | None = None
    candidates: list[str] = field(default_factory=list)
    suggestion: str | None = None
    audio: str | None = None

    @property
    def text(self) -> str:
        """Что передать дальше «мозгу»: нормализованная (и дополненная контекстом) команда."""
        return self.normalized

    def trace(self) -> str:
        """Диагностика для режима разработчика: от звука до инструмента."""
        lines = []
        if self.audio:
            lines.append(f"RAW AUDIO: {self.audio}")
        conf = f" ({self.stt_confidence:.2f})" if self.stt_confidence is not None else ""
        lines.append(f"STT: «{self.raw}»{conf}")
        if len(self.alternatives) > 1:
            lines.append("Варианты STT: " + " | ".join(f"«{a}»" for a in self.alternatives[1:4]))
        lines.append(f"NORMALIZED: «{self.normalized}»")
        if self.corrections:
            lines.append("Исправлено: " + ", ".join(f"{a} → {b}" for a, b in self.corrections))
        if self.context:
            lines.append(f"CONTEXT: {self.context}")
        lines.append(f"INTENT: {self.intent}")
        if self.entities:
            lines.append("ENTITIES: " + ", ".join(f"{k}={v}" for k, v in self.entities.items()))
        lines.append(f"CONFIDENCE: {self.confidence:.2f} ({self.level.upper()})")
        if self.clarify:
            lines.append(f"Уточнение: {self.clarify}")
        lines.append(f"TOOL: {self.tool or ('AI Brain' if self.intent in ('CHAT', 'UNKNOWN') else '—')}")
        return "\n".join(lines)


class SpeechUnderstanding:
    def __init__(self, rt, rules, lexicon):
        self.rt = rt
        self.rules = rules
        self.lexicon = lexicon
        self.normalizer = TextNormalizer(lexicon)

    def thresholds(self) -> tuple[float, float]:
        """(низкая, высокая) границы уверенности. Нижняя — настройка «STT Confidence Threshold»."""
        low = float(self.rt.settings.get("voice.input.stt_threshold", 0.45) or 0.45)
        return max(0.05, min(low, 0.9)), max(low + 0.15, 0.72)

    def understand(self, text: str, stt=None, typed: bool = False) -> Understanding:
        alternatives = list(getattr(stt, "alternatives", None) or []) or [text]
        if alternatives[0] != text:
            alternatives.insert(0, text)
        top_conf = getattr(stt, "confidence", None)
        hyps = []
        for i, alt in enumerate(alternatives[:4]):
            conf = 1.0 if typed else (top_conf if top_conf is not None else 0.85)
            if i > 0:
                conf = max(0.3, conf - 0.08 * i)
            hyps.append(self._hypothesis(alt, conf))
        best = max(hyps, key=lambda h: (h.score, -hyps.index(h)))
        return self._result(best, alternatives, None if typed else top_conf, stt)

    def _hypothesis(self, stt_text: str, stt_conf: float) -> Hypothesis:
        norm = self.normalizer.normalize(stt_text)
        text, note = self._with_context(norm)
        plan = self._parse(text) if text else None
        h = Hypothesis(stt_text, stt_conf, norm, text, plan, note)
        h.score = stt_conf * self._clarity(h)
        return h

    def _parse(self, text: str):
        try:
            return self.rules.parse(text)
        except Exception:
            return None

    def _clarity(self, h: Hypothesis) -> float:
        """Насколько ясно, что делать: уверенный разбор правил — 1, неуверенный — 0.7, нет разбора — 0.55."""
        n = h.normalized
        if n.ambiguous:
            return 0.5
        if h.plan is None:
            words = re.findall(r"\w+", h.text)
            if not words:
                return 0.2
            if len(words) >= 2 or normalize(words[0]) in SHORT_OK:
                return 0.55
            return 0.5 if len(words[0]) >= 4 else 0.3
        base = 0.7 if getattr(h.plan, "weak", False) else 1.0
        if n.entity and n.entity_score < 1.0:
            base *= 0.55 + 0.45 * n.entity_score
        return base

    def _last_turn(self):
        history = list(getattr(self.rt.dialog, "history", []))
        if not history:
            return None
        turn = history[-1]
        if time.time() - getattr(turn, "at", 0) > CONTEXT_WINDOW:
            return None
        return turn

    def _with_context(self, norm: Normalized) -> tuple[str, str | None]:
        """Короткая реплика, продолжающая предыдущую: дополнить её до полной команды."""
        text = norm.text
        n = normalize(text).strip(" ?.!")
        turn = self._last_turn()
        if not n or turn is None:
            return text, None
        actions = list(turn.actions or [])
        last = actions[-1] if actions else None
        news = any(a.startswith("news_") for a in actions)

        m = re.match(r"^(?:а\s+)?(?:про|о|об|по\s+поводу|насчет|по)\s+(?P<x>.+)$", n)
        if m and news:
            return f"новости про {self._orig(text, m, 'x')}", f"продолжение разговора о новостях ({turn.user})"
        if m and last == "steam_game_info":
            return f"сколько я наиграл в {self._orig(text, m, 'x')}", "продолжение вопроса об игре"
        m = re.match(r"^(?:а\s+)?(?:в|во)\s+(?P<x>[^\s]+(?:\s+[^\s]+)?)$", n)
        if m and last == "get_weather":
            return f"погода в {self._orig(text, m, 'x')}", "продолжение вопроса о погоде"

        m = re.match(r"^(?:а\s+)?(?:теперь|потом|затем|еще|ещё|и|также|а\s+теперь|давай)\s+(?P<x>.+)$", n)
        bare = m.group("x") if m else (n if len(n.split()) <= 3 else None)
        if bare and not self._has_verb(bare) and last in OPEN_TOOLS | {"close_app"}:
            hit = self.lexicon.match(bare, ("sites", "apps", "games"), threshold=0.84)
            if hit.best and not hit.ambiguous:
                verb = "закрой" if last == "close_app" else "открой"
                name = hit.best.name
                browser = getattr(self.rt.dialog, "last_app", None)
                if hit.best.term.kind == "sites" and verb == "открой" and browser and \
                        normalize(browser).startswith(("google chrome", "chrome", "microsoft edge", "firefox", "opera")):
                    return f"открой {name} в {browser}", f"продолжение: {verb} в уже открытом браузере ({browser})"
                return f"{verb} {name}", f"продолжение предыдущей команды ({turn.user})"
        return text, None

    @staticmethod
    def _orig(text: str, m: re.Match, group: str) -> str:
        """Фрагмент из исходного текста (с регистром) по совпадению в нормализованном."""
        start, end = m.span(group)
        stripped = text.strip(" ?.!")
        return stripped[start:end] if len(stripped) >= end else m.group(group)

    @staticmethod
    def _has_verb(text: str) -> bool:
        return bool(re.match(r"^(?:открой|запусти|закрой|включи|выключи|найди|покажи|удали|сверни|поставь|сделай|"
                             r"прочитай|добавь|убери|скажи|расскажи)\b", normalize(text)))

    def _result(self, h: Hypothesis, alternatives: list[str], stt_conf: float | None, stt) -> Understanding:
        plan = h.plan
        actions = list(getattr(plan, "actions", []) or [])
        first = next((a for a in actions if a.tool), None)
        tool = first.tool if first else None
        args = dict(first.args) if first else {}
        intent = INTENTS.get(tool, tool.upper() if tool else ("CHAT" if h.text else "UNKNOWN"))
        if tool == "open_app" and normalize(str(args.get("app", ""))) in BROWSERS:
            intent = "OPEN_BROWSER"
        if len(actions) > 1:
            intent += f" (+{len(actions) - 1})"
        entities = {ENTITY_KEYS.get(k, k): str(v) for k, v in args.items() if not str(k).startswith("_")}
        if h.normalized.entity and not any(v == h.normalized.entity for v in entities.values()):
            entities.setdefault("name", h.normalized.entity)
        clarity = self._clarity(h)
        confidence = round(h.stt_confidence * clarity, 2)
        low, high = self.thresholds()
        level = "high" if confidence >= high else "medium" if confidence >= low else "low"
        if stt is None and level == "low":
            level = "medium"
        u = Understanding(raw=h.stt_text, normalized=h.text, intent=intent, entities=entities,
                          confidence=confidence, level=level, tool=tool, args=args, plan=plan,
                          stt_confidence=stt_conf, alternatives=alternatives, context=h.context,
                          corrections=list(h.normalized.changes), removed=list(h.normalized.removed),
                          audio=getattr(stt, "audio_note", None))
        if h.normalized.ambiguous:
            names = list(dict.fromkeys(h.normalized.ambiguous))[:3]
            u.candidates = names
            u.clarify = "Уточните, пожалуйста: " + " или ".join(names) + "?"
            u.level = "medium"
        elif level == "medium" and tool and intent not in SAFE_INTENTS and h.normalized.entity \
                and h.normalized.entity_score < 0.95:
            u.suggestion = h.normalized.entity
            u.clarify = f"Вы имели в виду {h.normalized.entity}?"
        return u
