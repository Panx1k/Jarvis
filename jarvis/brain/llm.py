"""AI Brain на Claude (Anthropic API, tool use).

Claude получает список инструментов из реестра, текущий контекст диалога и историю, сам выбирает
инструменты и параметры. Опасные действия не выполняются сразу — ассистент спрашивает подтверждение.
"""
from __future__ import annotations

import logging

from jarvis.brain.base import BrainReply, BrainUnavailable, Executor
from jarvis.brain.prompting import PENDING_NOTE, SYSTEM_PROMPT, history_pairs, turn_prompt
from jarvis.config import env, env_bool

log = logging.getLogger("jarvis.llm")


class LLMBrain:
    def __init__(self, runtime):
        self.rt = runtime
        self.model = env("JARVIS_MODEL", "claude-opus-5")
        self.effort = env("JARVIS_EFFORT", "low")
        self.use_fallbacks = env_bool("LLM_FALLBACKS", True)
        self.web_search = env_bool("LLM_WEB_SEARCH", True)
        self.client = None
        self.error: str | None = None
        key = env("ANTHROPIC_API_KEY")
        if key:
            try:
                import anthropic

                self._anthropic = anthropic
                self.client = anthropic.Anthropic(api_key=key, timeout=60.0, max_retries=1)
            except Exception as exc:
                self.error = f"SDK anthropic не загружен: {exc}"
        else:
            self.error = "ANTHROPIC_API_KEY не задан"

    @property
    def available(self) -> bool:
        return self.client is not None

    def describe(self) -> str:
        return f"Claude ({self.model})" if self.available else "локальные правила"

    def _tools(self) -> list[dict]:
        tools = self.rt.registry.anthropic_schemas()
        if self.web_search:
            tools.append({"type": "web_search_20260209", "name": "web_search", "max_uses": 3})
        return tools

    def _create(self, messages: list) -> object:
        a = self._anthropic
        kwargs = dict(
            model=self.model,
            max_tokens=8000,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=self._tools(),
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
        )
        try:
            if self.use_fallbacks:
                try:
                    return self.client.beta.messages.create(
                        **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
                except (TypeError, a.BadRequestError) as exc:
                    log.warning("Серверный fallback недоступен (%s) — отключаю", exc)
                    self.use_fallbacks = False
            try:
                return self.client.messages.create(**kwargs)
            except a.BadRequestError as exc:
                if self.web_search and "web_search" in str(exc):
                    log.warning("web_search недоступен: %s", exc)
                    self.web_search = False
                    kwargs["tools"] = self._tools()
                    return self.client.messages.create(**kwargs)
                raise
        except a.AuthenticationError as exc:
            raise BrainUnavailable("неверный ANTHROPIC_API_KEY") from exc
        except a.PermissionDeniedError as exc:
            raise BrainUnavailable("нет доступа к модели (проверьте ключ и регион/VPN)") from exc
        except a.NotFoundError as exc:
            raise BrainUnavailable(f"модель {self.model} не найдена") from exc
        except a.RateLimitError as exc:
            raise BrainUnavailable("превышен лимит запросов") from exc
        except a.APIConnectionError as exc:
            raise BrainUnavailable("нет соединения с Anthropic API") from exc
        except a.APIStatusError as exc:
            raise BrainUnavailable(f"ошибка API {exc.status_code}") from exc

    def _history(self) -> list[dict]:
        msgs: list[dict] = []
        for user, reply in history_pairs(self.rt):
            msgs.append({"role": "user", "content": user})
            msgs.append({"role": "assistant", "content": reply})
        return msgs

    def respond(self, text: str, execute: Executor) -> BrainReply:
        if not self.available:
            raise BrainUnavailable(self.error or "LLM не настроен")
        messages = self._history() + [{"role": "user", "content": turn_prompt(self.rt, text)}]
        actions: list[str] = []
        final = ""
        for _ in range(8):
            resp = self._create(messages)
            if resp.stop_reason == "refusal":
                return BrainReply("Извините, с этим я помочь не могу.", True, "llm", actions)
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            tool_uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
            texts = [b.text for b in resp.content if getattr(b, "type", "") == "text" and b.text.strip()]
            if not tool_uses:
                final = " ".join(texts).strip()
                break
            messages.append({"role": "assistant", "content": resp.content})
            results, pending_msg = [], None
            for tu in tool_uses:
                args = dict(tu.input) if isinstance(tu.input, dict) else {}
                r = execute(tu.name, args)
                actions.append(tu.name)
                if r.data.get("pending"):
                    pending_msg = r.message
                    content = PENDING_NOTE
                else:
                    content = r.for_llm()
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": content,
                                "is_error": not r.ok and not r.data.get("pending")})
            messages.append({"role": "user", "content": results})
            if pending_msg:
                final = pending_msg
                break
        else:
            final = final or "Готово."
        return BrainReply(final or "Готово.", True, "llm", actions)
