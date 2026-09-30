"""Выбор «мозга»: LLM-провайдер (OpenAI или Claude) + локальные правила как запасной вариант.

LLM_PROVIDER: openai (по умолчанию) | anthropic
BRAIN_MODE:   llm (по умолчанию — все команды решает LLM, правила только при сбое API)
              | hybrid (уверенные локальные правила выполняются мгновенно, остальное — LLM)
              | rules (без облака)
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from jarvis.brain.base import BrainReply, BrainUnavailable, Executor
from jarvis.brain.rules import NOT_UNDERSTOOD, RuleParser
from jarvis.config import env

log = logging.getLogger("jarvis.brain")


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class FallbackLLM:
    """Основной облачный мозг (Gemini) и запасной (локальная модель): если основной недоступен
    (нет VPN/сети, исчерпан дневной лимит) — отвечает запасной, а уже затем локальные правила."""

    def __init__(self, primary, secondary):
        self.primary, self.secondary = primary, secondary

    @property
    def available(self) -> bool:
        return self.primary.available or self.secondary.available

    @property
    def error(self):
        return None if self.available else self.primary.error

    @property
    def keys(self):
        return self.primary.keys

    def describe(self) -> str:
        return f"{self.primary.describe()} → {self.secondary.label} запасной"

    def key_status(self) -> list[dict]:
        return self.primary.key_status()

    def check_connection(self):
        ok, msg = self.primary.check_connection()
        if ok:
            return ok, msg
        ok2, msg2 = self.secondary.check_connection()
        return ok2, (f"{msg}. Работает запасной мозг: {msg2}" if ok2 else msg)

    def respond(self, text, execute):
        if self.primary.available:
            try:
                return self.primary.respond(text, execute)
            except BrainUnavailable as exc:
                log.warning("%s недоступен (%s) — отвечает %s", self.primary.label, exc, self.secondary.label)
        return self.secondary.respond(text, execute)


def pick_gemini_models(model_ids: list[str], limit: int = 5) -> list[str]:
    """Кандидаты по убыванию версии: полные «flash» (умнее), затем «flash-lite» (быстрее, реже перегружены).
    Без image/tts/live/audio/preview — они не для текста или нестабильны."""
    import re

    def version(mid: str) -> tuple:
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", mid)
        return (float(m.group(1)) if m else 0.0, -len(mid))

    ids = list(dict.fromkeys(m.split("/")[-1] for m in model_ids))
    text = [m for m in ids if m.startswith("gemini-") and "flash" in m
            and not re.search(r"image|tts|live|audio|embedding|thinking|exp|8b|preview|omni", m)]
    full = sorted((m for m in text if "lite" not in m and re.search(r"\d", m)), key=version, reverse=True)
    lite = sorted((m for m in text if "lite" in m and re.search(r"\d", m)), key=version, reverse=True)
    return (full[:3] + lite[:2])[:limit]


def pick_gemini_model(model_ids: list[str]) -> str | None:
    models = pick_gemini_models(model_ids)
    return models[0] if models else None


def _auto_gemini_model() -> None:
    """GEMINI_MODEL не задан, а ключ есть — выбрать модель по списку, доступному ключу (названия у Google меняются)."""
    import os

    from jarvis.brain.key_manager import load_keys

    if env("GEMINI_MODEL"):
        return
    from jarvis.config import ROOT

    keys = load_keys(prefix="GEMINI")
    if not keys:
        return
    cache = ROOT / "models" / "gemini_models.txt"
    chosen = ""
    for attempt in range(2):
        try:
            import openai

            client = openai.OpenAI(api_key=keys[0][1], base_url=env("GEMINI_BASE_URL") or GEMINI_BASE_URL,
                                   timeout=15, max_retries=0)
            chosen = ",".join(pick_gemini_models([m.id for m in client.models.list()]))
            break
        except Exception as exc:
            from jarvis.utils.secrets import redact
            log.warning("Gemini: не удалось получить список моделей (%s)", redact(str(exc))[:200])
            if attempt == 0:
                import time
                time.sleep(1.5)
    if chosen:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(chosen, encoding="utf-8")
        except OSError:
            pass
    elif cache.exists():
        chosen = cache.read_text(encoding="utf-8").strip()
        log.info("Gemini: беру модели из прошлого запуска")
    if chosen:
        os.environ["GEMINI_MODEL"] = chosen
        log.info("Gemini: модели по порядку: %s", chosen)


def make_llm(runtime):
    provider = (env("LLM_PROVIDER", "openai") or "openai").lower()
    if provider in ("anthropic", "claude"):
        from jarvis.brain.llm import LLMBrain
        return LLMBrain(runtime)
    from jarvis.brain.openai_brain import OpenAIBrain
    local = OpenAIBrain(runtime, label="Локальная модель" if env("OPENAI_BASE_URL") else "OpenAI")
    if provider == "gemini":
        _auto_gemini_model()
        gemini = OpenAIBrain(runtime, prefix="GEMINI", default_base_url=GEMINI_BASE_URL, label="Gemini")
        if gemini.available:
            return FallbackLLM(gemini, local) if local.available else gemini
        log.warning("Gemini не настроен (%s) — работает %s", gemini.error, local.label)
        local.startup_note = f"{gemini.error} Пока работает локальная модель (впишите GEMINI_API_KEY в .env)."
    return local


class HybridBrain:
    def __init__(self, runtime):
        self.rules = RuleParser(runtime)
        self.llm = make_llm(runtime)
        self.mode = (env("BRAIN_MODE", "llm") or "llm").lower()

    def describe(self) -> str:
        if self.mode == "rules" or not self.llm.available:
            return "Локальные правила"
        return f"{self.llm.describe()} + правила" if self.mode == "hybrid" else self.llm.describe()

    def key_status(self) -> list[dict]:
        """Состояние API keys (только номер и статус) — для интерфейса."""
        getter = getattr(self.llm, "key_status", None)
        return getter() if getter else []

    def startup_notices(self) -> list[str]:
        """Проблемы конфигурации, о которых нужно сказать пользователю при запуске (без секретов)."""
        if self.mode == "rules":
            return []
        error = getattr(self.llm, "error", None)
        if not self.llm.available and error:
            return [f"{error} Работают только локальные команды. Укажите ключ в файле .env."]
        note = getattr(self.llm, "startup_note", None)
        return [note] if note else []

    def verify_async(self, report: Callable[[str], None]) -> None:
        """Фоновая проверка соединения с API — чтобы проблема с ключом была видна сразу при запуске."""
        check = getattr(self.llm, "check_connection", None)
        if self.mode == "rules" or not self.llm.available or check is None:
            return

        def run():
            ok, msg = check()
            if not ok:
                report(f"AI Brain недоступен: {msg}. Простые команды выполняются локально.")

        threading.Thread(target=run, name="llm-check", daemon=True).start()

    def respond(self, text: str, execute: Executor) -> BrainReply:
        use_llm = self.llm.available and self.mode != "rules"
        plan = None if self.mode == "llm" and use_llm else self.rules.parse(text)
        if plan and (not plan.weak or not use_llm):
            log.info("rules → %s", [(a.tool, a.args) for a in plan.actions])
            return self.rules.run(plan, execute)
        if use_llm:
            try:
                reply = self.llm.respond(text, execute)
                log.info("llm → %s", reply.actions)
                if reply.source == "llm_unverified":
                    plan = plan or self.rules.parse(text)
                    if plan:
                        return self.rules.run(plan, execute)
                    return BrainReply("Не понял, что нужно сделать. Скажите иначе, пожалуйста.", handled=False,
                                      source="rules")
                return reply
            except BrainUnavailable as exc:
                log.warning("LLM недоступен: %s", exc)
                plan = plan or self.rules.parse(text)
                if plan:
                    return self.rules.run(plan, execute)
                return BrainReply(f"AI Brain сейчас недоступен ({exc}). Простые команды я выполняю и без него.",
                                  handled=False, source="error")
        return BrainReply(NOT_UNDERSTOOD, handled=False)
