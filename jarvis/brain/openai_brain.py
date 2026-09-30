"""AI Brain на OpenAI API (Chat Completions + function calling).

Цепочка: текст команды → OpenAI → tool calls → выполнение существующих инструментов JARVIS →
результаты (включая ошибки) обратно в OpenAI → итоговый короткий ответ → TTS.

Настройка только через переменные окружения (.env):
    OPENAI_API_KEY_1, OPENAI_API_KEY_2, … — пул ключей пользователя (количество не ограничено);
    OPENAI_API_KEY      — один ключ (совместимость; используется, если нумерованных нет)
    OPENAI_MODEL        — имя модели (обязательно; в коде не зашито)
    OPENAI_TEMPERATURE  — необязательно; если модель не поддерживает параметр, он отключается автоматически
    OPENAI_BASE_URL     — необязательно (совместимый прокси/шлюз)
    OPENAI_MAX_RETRIES  — повторы на одном ключе при временных ошибках (по умолчанию 2)

Политика ошибок (см. _call):
    rate limit / исчерпанная квота → ключ во временный cooldown, запрос уходит на следующий ключ;
    ошибка сервера 5xx / таймаут   → повтор с экспоненциальной паузой, затем следующий ключ;
    ошибка аутентификации          → ключ помечается INVALID, следующий ключ;
    сеть недоступна                → повтор, затем ещё один ключ; дальше — прекращаем (сеть общая);
    некорректный запрос (400)      → ключ не меняется: запрос исправляется (temperature) или ошибка;
    нет прав / нет модели          → ключ не меняется: ротация бессмысленна.
Каждый ключ пробуется не больше одного раза за запрос — бесконечного круга Key 1 → 2 → 3 → 1 нет.
"""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Callable

from jarvis.brain.base import BrainReply, BrainUnavailable, Executor
from jarvis.brain.key_manager import KeyManager, KeySlot
from jarvis.brain.prompting import PENDING_NOTE, SYSTEM_PROMPT, clean_reply, history_pairs, turn_prompt
from jarvis.config import env
from jarvis.utils.secrets import redact

log = logging.getLogger("jarvis.openai")

KEY_MISSING = "OpenAI API key is not configured."
MODEL_MISSING = "OpenAI model is not configured (OPENAI_MODEL)."
ALL_KEYS_DOWN = "Все настроенные API keys временно недоступны."
NETWORK_DOWN = "нет соединения с OpenAI API"


DISCOVERY_TOOLS = {"search_files", "list_apps", "steam_accounts", "media_status", "vpn_status", "get_volume",
                   "get_time", "get_date", "get_weather", "search_web", "run_command",
                   "news_get", "news_more", "news_details", "steam_games", "steam_game_info"}


class ModelUnavailable(Exception):
    """Конкретная модель сейчас недоступна (перегружена/снята/нет доступа/лимит) — пробовать следующую модель."""

    def __init__(self, kind: str, cooldown: float | None = None):
        super().__init__(kind)
        self.cooldown = cooldown


def _model_limit_cooldown(exc: Exception) -> float:
    """Пауза модели после 429: retryDelay из ответа сервера; дневной лимит — час; иначе минута."""
    import re

    text = str(exc)
    m = re.search(r"retry[_ ]?delay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", text, re.I) or \
        re.search(r"retry in (\d+(?:\.\d+)?)\s*s", text, re.I)
    if m:
        return float(m.group(1)) + 1
    if re.search(r"per ?day|PerDay|daily", text, re.I):
        return 3600.0
    return _retry_after(exc) or 60.0


def classify(exc: Exception) -> str:
    """Тип ошибки API: quota | rate_limit | auth | server | network | bad_request | permission | not_found | other."""
    import openai

    if isinstance(exc, openai.RateLimitError):
        body = exc.body if isinstance(exc.body, dict) else {}
        err = body.get("error", body) if isinstance(body, dict) else {}
        marker = f"{err.get('type', '')} {err.get('code', '')} {exc}".lower() if isinstance(err, dict) else str(exc)
        return "quota" if ("quota" in marker or "credit" in marker or "billing" in marker) else "rate_limit"
    if isinstance(exc, openai.AuthenticationError):
        return "auth"
    if isinstance(exc, openai.PermissionDeniedError):
        return "permission"
    if isinstance(exc, openai.NotFoundError):
        return "not_found"
    if isinstance(exc, openai.BadRequestError):
        return "bad_request"
    if isinstance(exc, openai.APITimeoutError):
        return "server"
    if isinstance(exc, openai.APIConnectionError):
        return "network"
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code >= 500 or exc.status_code in (408, 409):
            return "server"
        if exc.status_code == 422:
            return "bad_request"
    return "other"


def _short_error(exc: Exception) -> str:
    """Понятное пользователю описание ошибки API — без трассировок и секретов."""
    import openai

    kind = classify(exc)
    if kind == "auth":
        return "неверный или отозванный API key"
    if kind == "permission":
        return "нет доступа к OpenAI API (регион/VPN или права ключа)"
    if kind == "not_found":
        return f"модель «{env('OPENAI_MODEL')}» не найдена или недоступна для ключа"
    if kind == "quota":
        return "закончилась квота OpenAI (проверьте баланс)"
    if kind == "rate_limit":
        return "превышен лимит запросов OpenAI"
    if isinstance(exc, openai.APITimeoutError):
        return "OpenAI API не ответил вовремя"
    if kind == "network":
        return NETWORK_DOWN
    if isinstance(exc, openai.APIStatusError):
        return f"ошибка OpenAI API {exc.status_code}"
    return type(exc).__name__


def _retry_after(exc: Exception) -> float | None:
    """Пауза, которую просит сервер (Retry-After / retry-after-ms)."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    try:
        if headers.get("retry-after-ms"):
            return float(headers["retry-after-ms"]) / 1000
        if headers.get("retry-after"):
            return float(headers["retry-after"])
    except (TypeError, ValueError):
        return None
    return None


class OpenAIBrain:
    """prefix — какие переменные окружения читать: OPENAI_* (OpenAI / Ollama) или GEMINI_* (Google Gemini
    через OpenAI-совместимый адрес). label — имя провайдера в интерфейсе."""

    def __init__(self, runtime, *, key_manager: KeyManager | None = None,
                 client_factory: Callable[[KeySlot], Any] | None = None,
                 sleep: Callable[[float], None] = time.sleep, prefix: str = "OPENAI",
                 default_base_url: str | None = None, label: str = "OpenAI"):
        self.rt = runtime
        self.prefix, self.label = prefix, label
        self.startup_note: str | None = None
        self.base_url = self._cfg("BASE_URL") or default_base_url
        self.models = [m.strip() for m in (self._cfg("MODEL") or "").split(",") if m.strip()]
        self.model = self.models[0] if self.models else ""
        self._preferred_model: str | None = None
        self._model_cooldown: dict[str, float] = {}
        self._no_reasoning: set[str] = set()
        self.fast_actions = (self._cfg("FAST_ACTIONS", "1") or "1") not in ("0", "false", "no")
        self.model_cooldown = float(self._cfg("MODEL_COOLDOWN", "300") or 300)
        self.max_rounds = int(self._cfg("MAX_TOOL_ROUNDS", "8") or 8)
        self.max_retries = int(self._cfg("MAX_RETRIES", "2") or 2)
        self.reasoning_effort = self._cfg("REASONING_EFFORT")
        self.backoff_base, self.backoff_cap = 0.5, 8.0
        self._sleep = sleep
        self.temperature: float | None = None
        raw_temp = self._cfg("TEMPERATURE")
        if raw_temp:
            try:
                self.temperature = float(raw_temp)
            except ValueError:
                log.warning("%s_TEMPERATURE=%r не число — параметр не используется", prefix, raw_temp)
        self.keys = key_manager if key_manager is not None else KeyManager.from_env(prefix=prefix)
        self._client_factory = client_factory or self._make_client
        self._clients: dict[int, Any] = {}
        self.config_error: str | None = None
        if not len(self.keys):
            self.config_error = KEY_MISSING if prefix == "OPENAI" else f"{label} API key is not configured."
        elif not self.model:
            self.config_error = MODEL_MISSING if prefix == "OPENAI" else f"{label} model is not configured ({prefix}_MODEL)."
        elif client_factory is None:
            try:
                import openai
            except Exception as exc:
                self.config_error = f"OpenAI SDK не загружен: {type(exc).__name__}"
        if self.config_error:
            log.warning("%s: %s", label, self.config_error)

    def _cfg(self, name: str, default: str | None = None) -> str | None:
        return env(f"{self.prefix}_{name}", default)

    def _make_client(self, slot: KeySlot):
        import os

        import openai

        if not env("OPENAI_BASE_URL"):
            os.environ.pop("OPENAI_BASE_URL", None)
        default_timeout = "45" if self.prefix == "OPENAI" else "8"
        return openai.OpenAI(api_key=slot.secret(), base_url=self.base_url,
                             timeout=float(self._cfg("TIMEOUT", default_timeout) or default_timeout), max_retries=0)

    def _client(self, slot: KeySlot):
        if slot.number not in self._clients:
            self._clients[slot.number] = self._client_factory(slot)
        return self._clients[slot.number]

    @property
    def available(self) -> bool:
        return self.config_error is None

    @property
    def error(self) -> str | None:
        return self.config_error

    def describe(self) -> str:
        if not self.available:
            return "локальные правила"
        return f"{self.label} ({self.model})" + (f" · ключей {len(self.keys)}" if len(self.keys) > 1 else "")

    def key_status(self) -> list[dict]:
        """Состояние ключей для интерфейса: только номер и статус."""
        return self.keys.status()

    def _backoff(self, attempt: int, exc: Exception) -> float:
        delay = min(self.backoff_cap, self.backoff_base * (2 ** attempt)) * (0.8 + 0.4 * random.random())
        return max(delay, min(_retry_after(exc) or 0, self.backoff_cap))

    def _switch_log(self, slot: KeySlot, what: str, tried: set[int]) -> None:
        nxt = self.keys.peek_next(tried)
        if nxt:
            log.warning("API key %s %s, switching to key %s", slot.label, what, nxt.label)
        else:
            log.warning("API key %s %s, no other API keys available", slot.label, what)

    def _call(self, fn: Callable[[Any], Any]):
        """Выполнить fn(client) с политикой повторов и переключения ключей. Число попыток конечно:
        каждый ключ — не более одного раза за запрос, на ключе — не более max_retries повторов."""
        tried: set[int] = set()
        network_failures = 0
        last_kind = None
        while True:
            slot = self.keys.acquire(exclude=tried)
            if slot is None:
                if last_kind == "network":
                    raise BrainUnavailable(NETWORK_DOWN)
                raise BrainUnavailable(ALL_KEYS_DOWN)
            tried.add(slot.number)
            client = self._client(slot)
            for attempt in range(self.max_retries + 1):
                try:
                    result = fn(client)
                    self.keys.report_success(slot)
                    return result
                except Exception as exc:
                    kind = classify(exc)
                    last_kind = kind
                    if len(self.models) > 1 and kind in ("quota", "rate_limit") and self.prefix != "OPENAI":
                        raise ModelUnavailable(kind, _model_limit_cooldown(exc)) from exc
                    if len(self.models) > 1 and (kind in ("not_found", "permission", "network")
                                                 or (kind == "server" and getattr(exc, "status_code", 0) == 503)):
                        raise ModelUnavailable(kind) from exc
                    if kind in ("server", "network") and attempt < self.max_retries:
                        delay = self._backoff(attempt, exc)
                        log.info("API key %s: временная ошибка (%s), повтор через %.1f с", slot.label, kind, delay)
                        self._sleep(delay)
                        continue
                    if kind == "quota":
                        self.keys.report_quota(slot)
                        self._switch_log(slot, "quota exhausted", tried)
                    elif kind == "rate_limit":
                        self.keys.report_rate_limit(slot, _retry_after(exc))
                        self._switch_log(slot, "rate limited", tried)
                    elif kind == "auth":
                        self.keys.report_invalid(slot)
                        self._switch_log(slot, "invalid (authentication failed)", tried)
                    elif kind == "server":
                        self.keys.report_server_errors(slot)
                        self._switch_log(slot, "server errors persist", tried)
                    elif kind == "network":
                        self.keys.report_error(slot, "network")
                        network_failures += 1
                        if network_failures >= 2:
                            log.warning("OpenAI: сеть недоступна, прекращаю попытки")
                            raise BrainUnavailable(NETWORK_DOWN) from None
                        self._switch_log(slot, "network error", tried)
                    else:
                        self.keys.report_error(slot, kind)
                        raise
                    break

    def _probe(self, client) -> None:
        """Минимальный запрос к модели (проверяет ключ, модель и баланс). При нескольких моделях —
        первая ответившая становится основной; недоступные уходят на паузу."""
        limit_key = "max_tokens" if self.base_url else "max_completion_tokens"
        last: Exception | None = None
        for model in self._model_order():
            kwargs = {"model": model, "messages": [{"role": "user", "content": "ping"}], limit_key: 16}
            if self.reasoning_effort and model not in self._no_reasoning:
                kwargs["reasoning_effort"] = self.reasoning_effort
            try:
                try:
                    client.chat.completions.create(**kwargs)
                except Exception as exc:
                    if classify(exc) == "bad_request" and "reasoning_effort" in kwargs:
                        self._no_reasoning.add(model)
                        kwargs.pop("reasoning_effort")
                        client.chat.completions.create(**kwargs)
                    else:
                        raise
                self._preferred_model = self.model = model
                return
            except Exception as exc:
                kind = classify(exc)
                if len(self.models) > 1 and kind in ("not_found", "permission", "network", "server", "bad_request",
                                                     "quota", "rate_limit"):
                    self._model_cooldown[model] = time.monotonic() + self.model_cooldown
                    log.info("%s: модель %s недоступна при проверке (%s)", self.label, model, kind)
                    last = exc
                    continue
                raise
        if last is not None:
            raise last

    def check_connection(self) -> tuple[bool, str]:
        """Проверяет каждый ключ: models.retrieve + минимальный запрос к модели (models.retrieve проходит даже
        при исчерпанной квоте). Состояния ключей обновляются, в сообщении — только номера."""
        if not self.available:
            return False, self.config_error or KEY_MISSING
        results = []
        model_error = None
        for slot in self.keys.slots:
            if slot not in self.keys.available():
                results.append(f"{slot.label} {slot.state.value}")
                continue
            client = self._client(slot)
            try:
                self._probe(client)
                self.keys.report_success(slot)
            except Exception as exc:
                kind = classify(exc)
                if kind == "quota":
                    self.keys.report_quota(slot)
                elif kind == "rate_limit":
                    self.keys.report_rate_limit(slot, _retry_after(exc))
                elif kind == "auth":
                    self.keys.report_invalid(slot)
                elif kind == "not_found":
                    model_error = _short_error(exc)
                else:
                    self.keys.report_error(slot, kind)
                log.warning("OpenAI check: API key %s — %s", slot.label, _short_error(exc))
            results.append(f"{slot.label} {slot.state.value}")
        summary = ", ".join(results)
        if model_error:
            return False, model_error
        if self.keys.available():
            return True, f"{self.label} доступен, модель {self.model} (ключи: {summary})."
        return False, f"{ALL_KEYS_DOWN} (ключи: {summary})"

    def _model_order(self) -> list[str]:
        """Сначала модель, ответившая последней, затем остальные по порядку; модели на паузе — в конце."""
        now = time.monotonic()
        order = sorted(self.models, key=lambda m: (m != self._preferred_model, self.models.index(m)))
        ready = [m for m in order if self._model_cooldown.get(m, 0) <= now]
        return ready or order

    def _create(self, messages: list[dict]):
        errors = []
        for model in self._model_order():
            try:
                result = self._create_with(messages, model)
                if model != self._preferred_model:
                    log.info("%s: работает модель %s", self.label, model)
                self._preferred_model = model
                self.model = model
                return result
            except ModelUnavailable as exc:
                errors.append(f"{model}: {exc}")
                self._model_cooldown[model] = time.monotonic() + (exc.cooldown or self.model_cooldown)
                log.warning("%s: модель %s недоступна (%s) — пробую следующую", self.label, model, exc)
        raise BrainUnavailable(f"модели {self.label} сейчас недоступны")

    def _create_with(self, messages: list[dict], model: str):
        import openai

        kwargs = dict(model=model, messages=messages, tools=self.rt.registry.openai_schemas(),
                      tool_choice="auto")
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        local = bool(self.base_url)
        limit = self._cfg("MAX_TOKENS", "300" if local else "")
        if limit:
            kwargs["max_tokens" if local else "max_completion_tokens"] = int(limit)
        if self.reasoning_effort and model not in self._no_reasoning:
            kwargs["reasoning_effort"] = self.reasoning_effort
        try:
            try:
                return self._call(lambda c: c.chat.completions.create(**kwargs))
            except openai.BadRequestError as exc:
                if "temperature" in str(exc) and "temperature" in kwargs:
                    log.warning("Модель %s не поддерживает temperature — параметр отключён", self.model)
                    self.temperature = None
                    kwargs.pop("temperature")
                    return self._call(lambda c: c.chat.completions.create(**kwargs))
                if "reasoning_effort" in kwargs and ("reasoning" in str(exc).lower() or "invalid argument" in
                                                     str(exc).lower()):
                    log.warning("Модель %s не принимает reasoning_effort — отправляю без него", model)
                    self._no_reasoning.add(model)
                    kwargs.pop("reasoning_effort")
                    return self._call(lambda c: c.chat.completions.create(**kwargs))
                if len(self.models) > 1:
                    raise ModelUnavailable("bad_request") from exc
                raise
        except ModelUnavailable:
            raise
        except openai.BadRequestError as exc:
            log.error("OpenAI 400: %s", redact(str(exc))[:500])
            raise BrainUnavailable("OpenAI отклонил запрос (400)") from None
        except openai.OpenAIError as exc:
            msg = _short_error(exc)
            log.warning("OpenAI: %s", redact(msg))
            raise BrainUnavailable(msg) from None

    def _messages(self, text: str) -> list[dict]:
        msgs: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for user, reply in history_pairs(self.rt):
            msgs.append({"role": "user", "content": user})
            msgs.append({"role": "assistant", "content": reply})
        msgs.append({"role": "user", "content": turn_prompt(self.rt, text)})
        return msgs

    @staticmethod
    def _tool_content(r) -> str:
        """Результат инструмента для модели: успех/ошибка + сообщение + данные (без трассировок)."""
        payload = {"ok": r.ok, "message": r.message}
        data = {k: v for k, v in (r.data or {}).items() if k != "pending"}
        if data:
            payload["data"] = data
        return redact(json.dumps(payload, ensure_ascii=False, default=str))[:4000]

    @staticmethod
    def _is_command(text: str) -> bool:
        """Пользователь просит действие (а не здоровается / спрашивает) — только тогда уместна подсказка
        «ты не вызвал инструмент»."""
        import re

        return bool(re.search(r"\b(?:открой|закрой|запусти|включи|выключи|поставь|найди|поищи|смени|переключи|"
                              r"зайди|войди|сделай|нажми|напиши|напечатай|убавь|прибавь|заблокируй|перезагрузи|"
                              r"отправь|отправить|отключи|заглуши|скачай|удали|покажи|перейди|вруби|выруби|"
                              r"open|close|launch|start|play|pause|stop|find|search|switch|turn|set|press|type)\b",
                              text.lower().replace("ё", "е")))

    @staticmethod
    def _asks_live_state(text: str) -> bool:
        """Вопрос о текущем состоянии компьютера — ответ возможен только через инструмент, не «из головы»."""
        import re

        t = text.lower().replace("ё", "е")
        return bool(re.search(r"(?:как\w*|сколько|что|кто|включен|работает)\b.*\b(?:громкост\w*|звук|погод\w*|"
                              r"играет|звучит|впн|vpn|аккаунт\w*)", t)
                    or re.search(r"\b(?:what(?:'s| is)|how)\b.*\b(?:volume|weather|playing|vpn)\b", t))

    @staticmethod
    def _claims_done(reply: str) -> bool:
        """Ответ утверждает, что что-то сделано/открыто/отправлено. Без вызова инструмента это выдумка."""
        import re

        t = reply.lower().replace("ё", "е")
        return bool(re.search(r"\b(?:отправлен\w*|отправил\w*|нажал\w*|напечатал\w*|открыл\w*|открыт\w*|запустил\w*|"
                              r"запущен\w*|включил\w*|включен\w*|выключил\w*|выключен\w*|закрыл\w*|закрыт\w*|"
                              r"переключил\w*|переключен\w*|установил\w*|установлен\w*|поставил\w*|выполнил\w*|"
                              r"выполнен\w*|сделал\w*|сделано|готово|sent|opened|launched|done|switched|muted)\b", t))

    def _needs_tool(self, user_text: str, reply: str) -> bool:
        return ((self._is_command(user_text) and self._promises_action(reply)) or self._asks_live_state(user_text)
                or self._claims_done(reply))

    @staticmethod
    def _promises_action(text: str) -> bool:
        """«Ищу музыку на YouTube…», «Открываю Discord.» — обещание действия, а не ответ на вопрос."""
        import re

        t = text.strip().lower().replace("ё", "е")
        if not t or len(t) > 200:
            return False
        promise = re.match(r"^(?:ищу|поиск|открываю|включаю|запускаю|ставлю|выполняю|переключаю|закрываю|"
                           r"сейчас (?:найду|открою|включу|запущу|поставлю|сделаю)|searching|opening|playing|"
                           r"launching|turning)\b", t) or t.endswith(("...", "…"))
        claim = re.search(r"\b(?:открыт|открыла?|запущен|включен|выключен|установлен|закрыт|поставлен|"
                          r"готово|сделано|выполнено|is (?:open|on|off|running)|opened|launched|done)\w*", t)
        return bool(promise or claim)

    def _text_tool_calls(self, content: str | None) -> list:
        """Небольшие локальные модели (Ollama) иногда пишут вызов текстом: «pause_media», «set_volume({"percent":
        30})», JSON {"name": …, "arguments": …} или <tool_call>…</tool_call>. Если ВЕСЬ ответ — такой вызов
        известного инструмента, превращаем его в настоящий tool call. Обычный текст не трогаем."""
        import re
        import uuid
        from types import SimpleNamespace as NS

        text = (content or "").strip().strip("`").strip()
        if not text or len(text) > 600:
            return []
        text = re.sub(r"^<tool_call>\s*|\s*</tool_call>$", "", text).strip()
        text = re.sub(r"^json\s*", "", text, flags=re.I)
        names = set(self.rt.registry.names())

        def call(name, args):
            return NS(id=f"call_{uuid.uuid4().hex[:12]}", type="function",
                      function=NS(name=name, arguments=json.dumps(args or {}, ensure_ascii=False)))

        embedded = re.findall(r"(?:system|functions|tools)\.([a-z_][a-z0-9_]*)\s*\(([^()]*)\)?", text)
        if embedded and all(n in names for n, _ in embedded):
            calls = []
            for n, raw in embedded:
                raw = raw.strip()
                try:
                    args = json.loads(raw) if raw.startswith("{") else dict(
                        re.findall(r"(\w+)\s*=\s*[\"']?([^,\"')]+)[\"']?", raw))
                except json.JSONDecodeError:
                    args = {}
                calls.append(call(n, args if isinstance(args, dict) else {}))
            return calls

        lower = {n.lower(): n for n in names}
        if text.lower().strip(".") in lower:
            return [call(lower[text.lower().strip(".")], {})]
        idents = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)
        rest = re.sub(r"[A-Za-z_][A-Za-z0-9_]*", "", text)
        if idents and all(i.lower() in lower for i in idents) and not re.search(r"[\w(){}\[\]]", rest):
            return [call(lower[n], {}) for n in dict.fromkeys(i.lower() for i in idents)]
        m = re.fullmatch(r"([a-z_][a-z0-9_]*)\s+([^{(\[].*)", text, re.S)
        if m and m.group(1) in names:
            tool = self.rt.registry.get(m.group(1))
            param = (tool.required or list(tool.params) or [None])[0]
            if param and (len(tool.required) <= 1):
                return [call(m.group(1), {param: m.group(2).strip().strip("\"'")})]
            return []
        m = re.fullmatch(r"([a-z_][a-z0-9_]*)\s*\((.*)\)", text, re.S)
        if m and m.group(1) in names:
            raw = m.group(2).strip()
            try:
                args = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                args = dict(re.findall(r"(\w+)\s*=\s*[\"']?([^,\"')]+)", raw))
            return [call(m.group(1), args if isinstance(args, dict) else {})]
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return []
        items = obj if isinstance(obj, list) else [obj]
        calls = []
        for item in items:
            if not isinstance(item, dict):
                return []
            name = item.get("name") or (item.get("function") or {}).get("name")
            args = item.get("arguments", item.get("parameters", {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if name not in names:
                return []
            calls.append(call(name, args if isinstance(args, dict) else {}))
        return calls

    def respond(self, text: str, execute: Executor) -> BrainReply:
        if not self.available:
            raise BrainUnavailable(self.config_error or KEY_MISSING)
        messages = self._messages(text)
        actions: list[str] = []
        final = ""
        nudged = False
        for _ in range(self.max_rounds):
            resp = self._create(messages)
            choice = resp.choices[0]
            msg = choice.message
            tool_calls = list(msg.tool_calls or [])
            if getattr(msg, "refusal", None):
                return BrainReply("Извините, с этим я помочь не могу.", True, "llm", actions)
            text_calls = False
            if not tool_calls:
                tool_calls = self._text_tool_calls(msg.content)
                text_calls = bool(tool_calls)
                if text_calls:
                    log.info("Вызов инструмента пришёл текстом — выполняю: %s", [t.function.name for t in tool_calls])
            if not tool_calls:
                final = clean_reply(msg.content)
                if not actions and not nudged and self._needs_tool(text, final):
                    nudged = True
                    log.info("Модель пообещала действие без вызова инструмента — прошу выполнить")
                    messages.append({"role": "assistant", "content": final})
                    messages.append({"role": "user", "content": "Ты не вызвал инструмент. Выполни это действие "
                                     "через подходящий инструмент, затем коротко ответь."})
                    continue
                if not actions and nudged and self._needs_tool(text, final):
                    log.warning("Модель сообщила о действии, не выполнив его — ответ помечен как непроверенный")
                    return BrainReply(final, False, "llm_unverified", actions)
                break
            if not text_calls and hasattr(msg, "model_dump"):
                assistant = msg.model_dump(exclude_none=True)
                assistant["role"] = "assistant"
                assistant.pop("refusal", None)
                assistant.pop("annotations", None)
                assistant.setdefault("content", None)
                for tc_dump in assistant.get("tool_calls", []):
                    tc_dump.setdefault("type", "function")
                    tc_dump.get("function", {}).setdefault("arguments", "{}")
            else:
                assistant = {
                    "role": "assistant",
                    "content": None if text_calls else (msg.content or None),
                    "tool_calls": [{"id": tc.id, "type": "function",
                                    "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                                   for tc in tool_calls],
                }
            messages.append(assistant)
            pending_msg = None
            round_results: list = []
            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("аргументы должны быть объектом")
                except (json.JSONDecodeError, ValueError) as exc:
                    content = json.dumps({"ok": False, "message": f"Некорректные аргументы JSON: {exc}"},
                                         ensure_ascii=False)
                else:
                    if pending_msg:
                        content = json.dumps({"ok": False, "message": "Пропущено: ждём подтверждения пользователя"},
                                             ensure_ascii=False)
                    else:
                        r = execute(name, args)
                        actions.append(name)
                        round_results.append((name, r))
                        if r.data.get("pending"):
                            pending_msg = r.message
                            content = json.dumps({"ok": False, "message": PENDING_NOTE}, ensure_ascii=False)
                        else:
                            content = self._tool_content(r)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
            if pending_msg:
                final = pending_msg
                break
            if self.fast_actions and round_results and len(round_results) == len(tool_calls) and all(
                    r.ok and not r.followup and name not in DISCOVERY_TOOLS for name, r in round_results):
                return BrainReply(" ".join(r.message for _, r in round_results), True, "llm_tools", actions)
        else:
            final = final or "Готово."
        return BrainReply(final or "Готово.", True, "llm", actions)
