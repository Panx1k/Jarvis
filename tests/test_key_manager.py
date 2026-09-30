"""KeyManager и политика ротации ключей OpenAI.

Ошибки создаются настоящими классами исключений OpenAI SDK (с HTTP-ответами 429/401/500/400/404),
подменяется только транспорт — чтобы получить нужную ошибку по требованию. Логика провайдера
(OpenAIBrain._call) и KeyManager — настоящие.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace as NS

import httpx2
import openai
import pytest

from jarvis.brain.base import BrainUnavailable
from jarvis.brain.key_manager import KeyManager, KeyState, load_keys
from jarvis.brain.openai_brain import ALL_KEYS_DOWN, KEY_MISSING, NETWORK_DOWN, OpenAIBrain
from jarvis.config import Settings
from jarvis.core.assistant import Runtime
from jarvis.core.context import DialogContext
from jarvis.tools.base import load_builtin_tools, registry
from jarvis.utils.secrets import RedactingFilter, redact

load_builtin_tools()

SECRETS = {1: "sk-proj-TESTKEYONE0000000000000001", 2: "sk-proj-TESTKEYTWO0000000000000002",
           3: "plain-secret-key-number-three"}
URL = "https://api.openai.com/v1/chat/completions"
REQ = httpx2.Request("POST", URL)


def api_error(cls, code, body=None, headers=None, message=None):
    return cls(message or f"Error code: {code}", response=httpx2.Response(code, request=REQ, headers=headers or {}),
               body=body)


def rate_limit(retry_after=None):
    return api_error(openai.RateLimitError, 429, {"error": {"type": "requests", "code": "rate_limit_exceeded"}},
                     {"retry-after": str(retry_after)} if retry_after else None)


def quota():
    return api_error(openai.RateLimitError, 429,
                     {"error": {"type": "insufficient_quota", "code": "credit_balance_exhausted"}})


def auth_error(key_no):
    return api_error(openai.AuthenticationError, 401, None, None,
                     f"Incorrect API key provided: {SECRETS[key_no]}")


def server_error():
    return api_error(openai.InternalServerError, 500, {"error": {"message": "server"}})


def network_error():
    return openai.APIConnectionError(request=REQ)


OK = "ok"


def completion(text="Здравствуйте."):
    return NS(choices=[NS(message=NS(content=text, tool_calls=None, refusal=None), finish_reason="stop")])


class Transport:
    """Сценарий ответов по номеру ключа; журнал: какой ключ вызывался."""

    def __init__(self, script: dict[int, list] | None = None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls: list[int] = []

    def factory(self, slot):
        transport = self

        def create(**kwargs):
            transport.calls.append(slot.number)
            queue = transport.script.get(slot.number) or [OK]
            outcome = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(outcome, Exception):
                raise outcome
            return completion()

        return NS(chat=NS(completions=NS(create=create)),
                  models=NS(retrieve=lambda model: NS(id=model)))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def model(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "test-model")


def make(script=None, n=3, **km_kwargs):
    clock = Clock()
    km = KeyManager([(i, SECRETS[i]) for i in range(1, n + 1)], clock=clock, **km_kwargs)
    transport = Transport(script)
    sleeps: list[float] = []
    brain = OpenAIBrain(Runtime(Settings(), DialogContext(), registry, apps=None), key_manager=km,
                        client_factory=transport.factory, sleep=sleeps.append)
    return brain, km, transport, clock, sleeps


def ask(brain):
    return brain.respond("привет", lambda n, a: None)


def states(km):
    return {s["number"]: s["state"] for s in km.status()}


def test_1_success_via_key_1():
    brain, km, t, _, _ = make()
    assert ask(brain).text == "Здравствуйте."
    assert t.calls == [1]
    assert states(km)[1] == "ACTIVE" and km.status()[0]["successes"] == 1


def test_2_rate_limit_switches_to_key_2(caplog):
    brain, km, t, _, _ = make({1: [rate_limit(retry_after=5)]})
    with caplog.at_level(logging.INFO):
        assert ask(brain).text == "Здравствуйте."
    assert t.calls == [1, 2]
    assert states(km)[1] == "RATE_LIMITED" and states(km)[2] == "ACTIVE"
    assert "API key #1 rate limited, switching to key #2" in caplog.text


def test_2b_quota_goes_to_cooldown_and_switches(caplog):
    brain, km, t, _, _ = make({1: [quota()]})
    with caplog.at_level(logging.INFO):
        ask(brain)
    assert t.calls == [1, 2] and states(km)[1] == "COOLDOWN"
    assert "API key #1 quota exhausted, switching to key #2" in caplog.text


def test_3_invalid_key_disabled():
    brain, km, t, _, _ = make({1: [auth_error(1)]})
    ask(brain)
    assert t.calls == [1, 2] and states(km)[1] == "INVALID"
    t.calls.clear()
    for _ in range(4):
        ask(brain)
    assert 1 not in t.calls


def test_4_cooldown_and_recovery():
    brain, km, t, clock, _ = make({1: [rate_limit(), OK]}, n=2, rate_limit_cooldown=30)
    ask(brain)
    t.calls.clear()
    for _ in range(3):
        ask(brain)
    assert t.calls == [2, 2, 2]
    assert km.status()[0]["seconds_left"] == 30
    clock.t += 31
    assert states(km)[1] == "ACTIVE"
    t.calls.clear()
    ask(brain), ask(brain)
    assert 1 in t.calls


def test_4b_retry_after_is_honoured():
    brain, km, t, clock, _ = make({1: [rate_limit(retry_after=120), OK]}, n=2, rate_limit_cooldown=30)
    ask(brain)
    clock.t += 60
    assert states(km)[1] == "RATE_LIMITED"
    clock.t += 61
    assert states(km)[1] == "ACTIVE"


def test_5_retry_with_backoff_then_success():
    brain, km, t, _, sleeps = make({1: [server_error(), server_error(), OK]})
    assert ask(brain).text == "Здравствуйте."
    assert t.calls == [1, 1, 1] and len(sleeps) == 2
    assert sleeps[1] > sleeps[0] > 0
    assert states(km)[1] == "ACTIVE"


def test_5b_persistent_server_errors_fall_back():
    brain, km, t, _, sleeps = make({1: [server_error()]})
    ask(brain)
    assert t.calls == [1, 1, 1, 2]
    assert states(km)[1] == "COOLDOWN"


def test_5c_network_errors_are_bounded():
    brain, km, t, _, _ = make({1: [network_error()], 2: [network_error()], 3: [network_error()]})
    with pytest.raises(BrainUnavailable, match=NETWORK_DOWN):
        ask(brain)
    assert t.calls == [1, 1, 1, 2, 2, 2]
    assert set(states(km).values()) == {"ACTIVE"}


def test_6_all_keys_unavailable():
    brain, km, t, _, _ = make({1: [quota()], 2: [rate_limit()], 3: [auth_error(3)]})
    with pytest.raises(BrainUnavailable, match=ALL_KEYS_DOWN):
        ask(brain)
    assert t.calls == [1, 2, 3]
    t.calls.clear()
    with pytest.raises(BrainUnavailable, match="Все настроенные API keys временно недоступны."):
        ask(brain)
    assert t.calls == []
    assert states(km) == {1: "COOLDOWN", 2: "RATE_LIMITED", 3: "INVALID"}


def test_6b_no_endless_cycle_between_keys():
    brain, km, t, _, _ = make({1: [rate_limit()], 2: [rate_limit()], 3: [rate_limit()]})
    with pytest.raises(BrainUnavailable):
        ask(brain)
    assert sorted(t.calls) == [1, 2, 3]


def test_7_no_keys(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("OPENAI_API_KEY"):
            monkeypatch.delenv(name)
    assert load_keys() == []
    brain = OpenAIBrain(Runtime(Settings(), DialogContext(), registry, apps=None))
    assert not brain.available and brain.error == KEY_MISSING
    with pytest.raises(BrainUnavailable, match=KEY_MISSING):
        ask(brain)


def test_7b_key_discovery_from_env():
    env = {"OPENAI_API_KEY_1": "k-one-000", "OPENAI_API_KEY_2": "", "OPENAI_API_KEY_3": "your_api_key_here",
           "OPENAI_API_KEY_12": "k-twelve-0", "OPENAI_API_KEY_7": "k-one-000",
           "OPENAI_API_KEY": "k-legacy-00", "OTHER": "x"}
    assert load_keys(env) == [(1, "k-one-000"), (12, "k-twelve-0"), (13, "k-legacy-00")]
    assert load_keys({"OPENAI_API_KEY": "k-legacy-00", "OPENAI_API_KEY_2": "k-two-0000"}) == \
        [(1, "k-legacy-00"), (2, "k-two-0000")]


def test_invalid_request_does_not_rotate():
    brain, km, t, _, _ = make({1: [api_error(openai.BadRequestError, 400, {"error": {"message": "bad"}})]})
    with pytest.raises(BrainUnavailable, match="400"):
        ask(brain)
    assert t.calls == [1] and set(states(km).values()) == {"ACTIVE"}


def test_bad_temperature_is_fixed_not_rotated():
    brain, km, t, _, _ = make({1: [api_error(openai.BadRequestError, 400, None, None,
                                             "Unsupported parameter: 'temperature'"), OK]})
    brain.temperature = 0.3
    assert ask(brain).text == "Здравствуйте."
    assert t.calls == [1, 2] and brain.temperature is None
    assert states(km)[1] == "ACTIVE"


def test_model_or_permission_error_does_not_rotate():
    for err in (api_error(openai.NotFoundError, 404), api_error(openai.PermissionDeniedError, 403)):
        brain, km, t, _, _ = make({1: [err]})
        with pytest.raises(BrainUnavailable):
            ask(brain)
        assert t.calls == [1]


def test_round_robin_balancing():
    brain, km, t, _, _ = make()
    for _ in range(6):
        ask(brain)
    assert t.calls == [1, 2, 3, 1, 2, 3]


def test_text_tool_calls_from_small_models():
    brain, *_ = make()
    parse = lambda s: [(c.function.name, __import__("json").loads(c.function.arguments))
                       for c in brain._text_tool_calls(s)]
    assert parse("pause_media") == [("pause_media", {})]
    assert parse("open_app Photoshop") == [("open_app", {"app": "Photoshop"})]
    assert parse("Узнать громкость.system.get_volume()") == [("get_volume", {})]
    assert parse("Steam_switch_account → steam_switch_account") == [("steam_switch_account", {})]
    assert parse("Steam_switch_account") == [("steam_switch_account", {})]
    assert parse('Открываю Discord.system.open_app(app="Discord")') == [("open_app", {"app": "Discord"})]
    assert parse('set_volume({"percent": 30})') == [("set_volume", {"percent": 30})]
    assert parse('<tool_call>{"name": "vpn_on", "arguments": {}}</tool_call>') == [("vpn_on", {})]
    assert parse('[{"name": "open_site", "arguments": "{\\"site\\": \\"youtube\\"}"}]') == \
        [("open_site", {"site": "youtube"})]
    assert parse("Пауза.") == [] and parse("hack_the_planet") == [] and parse('{"name": "rm_rf"}') == []


def test_text_tool_call_is_executed():
    clock = Clock()
    km = KeyManager([(1, SECRETS[1])], clock=clock)
    replies = iter([NS(choices=[NS(message=NS(content="pause_media", tool_calls=None, refusal=None))]),
                    completion("Поставил на паузу.")])
    factory = lambda slot: NS(chat=NS(completions=NS(create=lambda **k: next(replies))))
    brain = OpenAIBrain(Runtime(Settings(), DialogContext(), registry, apps=None), key_manager=km,
                        client_factory=factory)
    executed = []
    from jarvis.tools.base import ToolResult
    brain.fast_actions = False
    reply = brain.respond("пауза", lambda n, a: executed.append(n) or ToolResult(True, "ok"))
    assert executed == ["pause_media"] and reply.text == "Поставил на паузу."


def test_fast_actions_skip_second_request():
    km = KeyManager([(1, SECRETS[1])], clock=Clock())
    requests = []
    replies = iter([NS(choices=[NS(message=NS(content=None, refusal=None, tool_calls=[
        NS(id="c1", type="function", function=NS(name="set_volume", arguments='{"percent": 30}'))]))])])

    def create(**kw):
        requests.append(kw)
        return next(replies)

    brain = OpenAIBrain(Runtime(Settings(), DialogContext(), registry, apps=None), key_manager=km,
                        client_factory=lambda s: NS(chat=NS(completions=NS(create=create))))
    from jarvis.tools.base import ToolResult
    reply = brain.respond("громкость 30", lambda n, a: ToolResult(True, "Громкость 30 процентов.", {"volume": 30}))
    assert len(requests) == 1 and reply.source == "llm_tools" and reply.actions == ["set_volume"]


def test_promised_action_gets_one_nudge():
    km = KeyManager([(1, SECRETS[1])], clock=Clock())
    calls = []

    def create(**kwargs):
        calls.append(kwargs["messages"][-1]["content"])
        return completion("Ищу музыку на YouTube…")

    brain = OpenAIBrain(Runtime(Settings(), DialogContext(), registry, apps=None), key_manager=km,
                        client_factory=lambda s: NS(chat=NS(completions=NS(create=create))))
    brain.respond("найди музыку", lambda n, a: None)
    assert len(calls) == 2
    assert OpenAIBrain._promises_action("Открываю Discord.") and not OpenAIBrain._promises_action("Париж.")


def test_status_text():
    brain, km, t, clock, _ = make({2: [rate_limit()]}, rate_limit_cooldown=30)
    ask(brain), ask(brain)
    text = km.status_text()
    assert text.splitlines()[0] == "API Keys:"
    assert "Key #1 — ACTIVE" in text and "Key #2 — RATE_LIMITED" in text and "Key #3 — ACTIVE" in text


def test_8_keys_never_in_logs(caplog):
    with caplog.at_level(logging.DEBUG):
        brain, km, t, _, _ = make({1: [auth_error(1)], 2: [quota()], 3: [server_error()]})
        with pytest.raises(BrainUnavailable) as exc_info:
            ask(brain)
        brain.check_connection()
        outputs = [caplog.text, km.status_text(), str(km.status()), repr(km.slots), brain.describe(),
                   str(exc_info.value), str(brain.key_status())]
    for secret in SECRETS.values():
        for out in outputs:
            assert secret not in out
    assert SECRETS[3] not in redact(f"token={SECRETS[3]}")
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "leak %s / %s", (SECRETS[1], SECRETS[3]), None)
    RedactingFilter().filter(record)
    assert all(s not in record.getMessage() for s in SECRETS.values())


def test_8b_slot_repr_hides_secret():
    km = KeyManager([(1, SECRETS[1])])
    slot = km.slots[0]
    assert SECRETS[1] not in repr(slot) and SECRETS[1] not in str(slot) and slot.state is KeyState.ACTIVE
