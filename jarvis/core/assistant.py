"""Оркестратор: принимает текст/голос → «мозг» → инструменты → ответ текстом и голосом.

Голосовой цикл: WAITING → «Jarvis» → LISTENING → команда → THINKING → EXECUTING → SPEAKING → WAITING.
Microphone → Wake Word (VoiceLoop) → STT → AI Brain → Tools → TTS → Speakers.

Не зависит от интерфейса: события отдаются через AssistantListener (GUI, консоль, тесты).
Все команды выполняются последовательно в одном рабочем потоке.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from jarvis.brain.hybrid import HybridBrain
from jarvis.config import PLUGINS_DIR, Settings, env, env_bool
from jarvis.core.context import DialogContext
from jarvis.services.app_index import AppIndex
from jarvis.tools.base import ToolRegistry, ToolResult, load_builtin_tools, load_plugins, registry
from jarvis.utils.text import clean_spaces, normalize
from jarvis.voice import responder
from jarvis.voice.lang import detect_lang
from jarvis.voice.wake import WAKE_VARIANTS

log = logging.getLogger("jarvis.assistant")

YES_RE = re.compile(r"^(?:да|ага|угу|конечно|подтверждаю|подтвердить|подтверди|выполняй|давай|делай|точно|уверен|"
                    r"yes|yeah|yep|sure|confirm|do it|ok|ок|окей|так точно|верно|согласен|разрешаю)\b")
NO_RE = re.compile(r"^(?:нет|не надо|не нужно|не делай|отмена|отмени|отставить|стоп|no|nope|cancel|stop|не)\b")

STATES = ("idle", "listening", "thinking", "executing", "speaking", "error")


class AssistantListener:
    """Интерфейс событий ассистента. Методы вызываются из рабочих потоков."""

    def on_state(self, state: str) -> None: ...
    def on_message(self, role: str, text: str) -> None: ...
    def on_action(self, action_id: str, text: str, status: str, detail: str = "") -> None: ...
    def on_confirm(self, question: str | None) -> None: ...
    def on_level(self, level: float) -> None: ...
    def on_status(self, text: str) -> None: ...
    def on_transcript(self, text: str) -> None: ...
    def on_spoken(self, text: str) -> None: ...
    def on_wake(self) -> None: ...
    def on_tool(self, name: str, status: str, message: str = "") -> None: ...
    def on_output_level(self, level: float) -> None: ...
    def on_voice_output(self, kind: str, info: dict) -> None: ...
    def on_debug(self, text: str) -> None: ...
    def on_news(self, category: str, info: dict) -> None: ...
    def on_restart(self) -> None: ...
    def on_theme(self, color: str) -> None: ...


@dataclass
class Runtime:
    """То, что получают инструменты в параметре ctx."""
    settings: Settings
    dialog: DialogContext
    registry: ToolRegistry
    apps: AppIndex
    restart_requested: bool = False


@dataclass
class PendingAction:
    tool: str
    args: dict
    question: str
    created: float
    action_id: str = ""


class Assistant:
    def __init__(self, listener: AssistantListener | None = None, *, enable_voice: bool = True,
                 audio_source_factory: Callable | None = None):
        self.listener = listener or AssistantListener()
        self.settings = Settings()
        self.dialog = DialogContext()
        load_builtin_tools()
        self.plugins = load_plugins(PLUGINS_DIR)
        self.apps = AppIndex(self.settings)
        self.apps.refresh_async()
        self.rt = Runtime(self.settings, self.dialog, registry, self.apps)
        self.brain = HybridBrain(self.rt)

        self.stt = None
        self.recorder = None
        self.voice_loop = None
        self.live = None
        self.live_enabled = False
        self.voice_error: str | None = None
        self.lang = "ru"
        self.followup_seconds = float(env("FOLLOWUP_SECONDS", "8") or 8)
        from jarvis.voice.tts import Speaker, make_tts
        self.speaker = Speaker(make_tts() if enable_voice else None)
        self._audio_source_factory = audio_source_factory
        from jarvis.services import news as news_service
        news_service.get().on_update = lambda cat, info: self.listener.on_news(cat, info)
        from jarvis.voice.samples import SampleManager
        self.samples = SampleManager(self.settings) if enable_voice else None
        if enable_voice:
            self._init_voice_input()
            self.speaker.prewarm_async([(responder.ACK["ru"], "ru"), (responder.ACK["en"], "en"),
                                        (responder.DONE["ru"], "ru"), (responder.NOT_HEARD["ru"], "ru")])

        self.voice_replies = True
        self.pending: PendingAction | None = None
        self.busy = False
        self.listening = False
        self._calls: list[responder.ToolCall] = []
        self._listen_stop = threading.Event()
        self._jobs: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._worker = threading.Thread(target=self._work, name="assistant", daemon=True)
        self._worker.start()
        wake = list(dict.fromkeys(WAKE_VARIANTS + list(self.settings.get("wake_words", []))))
        self._wake_re = re.compile(r"^(?:(?:эй|hey|ok|okay|окей|ок)[\s,]+)?(?:" +
                                   "|".join(re.escape(normalize(w).strip(",")) for w in wake) +
                                   r")(?:[\s,.!?]+|$)", re.I)

    def _init_voice_input(self) -> None:
        try:
            from jarvis.voice.recorder import Recorder
            from jarvis.voice.stt import make_stt

            self.stt = make_stt(self.settings.get("stt_language", "ru-RU"))
            if self._audio_source_factory is None:
                self.recorder = Recorder(env("MIC_DEVICE"), remembered=self.settings.get("voice.mic"))
                self._audio_source_factory = self.recorder.source
        except Exception as exc:
            log.exception("Голосовой ввод недоступен")
            self.voice_error = str(exc)
            return
        self.voice_loop = self._make_voice_loop()
        self.live = None
        if self.voice_loop:
            from jarvis.voice.gemini_live import GeminiLive

            live = GeminiLive(self)
            self.live = live if live.available else None
            if self.live:
                self.live.prepare_async()
            self.live_enabled = bool(self.live) and bool(self.settings.get("voice.live_enabled",
                                                                           env_bool("GEMINI_LIVE", True)))

    @property
    def live_mode(self) -> bool:
        return bool(self.live) and getattr(self, "live_enabled", False) and self.voice_replies

    def set_live_mode(self, enabled: bool) -> bool:
        """Живой голос Gemini (модель сама слушает и отвечает голосом) вместо STT → мозг → TTS."""
        if enabled and not self.live:
            self.listener.on_message("system", "Живой режим недоступен: нужен GEMINI_API_KEY в .env.")
            return False
        self.live_enabled = enabled
        self.settings.set("voice.live_enabled", enabled)
        return True

    def _live_session(self, first_pcm: bytes | None) -> bool:
        """Живой разговор с Gemini. False — не удалось подключиться (тогда фраза обработается как обычно)."""
        self.listening = True
        try:
            ok = self.live.run_session(first_pcm)
        finally:
            self.listening = False
        if not ok:
            log.warning("Живой режим недоступен (%s) — обычная обработка", self.live.error)
            self.listener.on_status("Живой режим недоступен — отвечаю обычным способом")
        return ok

    def _make_voice_loop(self):
        from jarvis.voice.loop import VoiceLoop
        from jarvis.voice.wake import STTWakeDetector, VoskWakeDetector

        detector = VoskWakeDetector(env("VOSK_MODEL_PATH", "models/vosk-model-small-ru-0.22"),
                                    env("VOSK_MODEL_PATH_EN", "models/vosk-model-small-en-us-0.15"))
        if not detector.available():
            log.warning("Нет офлайн-модели Vosk — активатор будет распознаваться онлайн")
            detector = STTWakeDetector(self.stt)
        self.wake_detector_name = detector.name
        return VoiceLoop(
            self._audio_source_factory, detector,
            on_utterance=lambda pcm, res, followup: self._jobs.put(
                lambda: self._voice_utterance(pcm, res, followup)),
            on_wake=self._on_wake,
            on_level=self.listener.on_level,
            on_followup_end=self._on_followup_end,
            on_error=lambda msg: self.listener.on_message("system", msg),
            on_dead_mic=self._on_dead_mic if self.recorder else None,
            is_blocked=self._voice_blocked,
        )

    def info(self) -> dict[str, Any]:
        return {
            "brain": self.brain.describe(),
            "stt": getattr(self.stt, "name", "недоступно"),
            "mic": getattr(self.recorder, "device_name", "нет"),
            "tts": self.speaker.name,
            "wake": getattr(self, "wake_detector_name", "недоступно"),
            "live": (f"Gemini Live ({self.live.voice})" if self.live else None),
            "tools": len(self.rt.registry.all()),
            "samples": self.samples.stats() if self.samples else None,
            "plugins": self.plugins,
            "notices": self.brain.startup_notices(),
        }

    def microphones(self) -> list[tuple[int, str]]:
        from jarvis.voice.recorder import microphones
        return microphones()

    def set_microphone(self, index: int, name: str) -> None:
        """Ручной выбор микрофона: запоминается и сразу применяется к постоянному прослушиванию."""
        if not self.recorder:
            return
        self.recorder.set_device(index, name)
        self.settings.set("voice.mic", name)
        if self.voice_loop:
            self.voice_loop.restart.set()

    def _on_dead_mic(self) -> bool:
        """Выбранный микрофон выдаёт цифровую тишину (выключен/отключён) — переключиться на живой."""
        old = self.recorder.device_name
        if self.recorder.preferred:
            return False
        if self.recorder.repick():
            self.settings.set("voice.mic", self.recorder.device_name)
            self.listener.on_message("system", f"Микрофон «{old.strip()}» молчит — переключился на "
                                               f"«{self.recorder.device_name.strip()}».")
            return True
        log.warning("Микрофон «%s» молчит, другого живого микрофона нет", old)
        return False

    def key_status(self) -> list[dict]:
        """API Keys: [{"number": 1, "state": "ACTIVE", …}] — без самих ключей."""
        return self.brain.key_status()

    def verify_brain(self) -> None:
        """Фоновая проверка ключа/соединения AI Brain; о проблеме сообщается в чат (без секретов)."""
        self.brain.verify_async(lambda msg: self.listener.on_message("system", msg))

    @property
    def wake_enabled(self) -> bool:
        return bool(self.voice_loop and self.voice_loop.running)

    def submit_text(self, text: str) -> None:
        text = clean_spaces(text)
        if text:
            self.speaker.stop()
            self._jobs.put(lambda: self._handle(text))

    def listen(self, hold: Callable[[], bool] | None = None) -> None:
        """Push-to-talk / кнопка микрофона.

        Короткое нажатие — слушать до паузы; повторное нажатие — закончить фразу.
        hold — удержание: пока функция возвращает True, запись идёт; отпустили — фраза закончена.
        """
        if self.live and self.live.active:
            return
        if self.listening:
            self._listen_stop.set()
            return
        self.speaker.stop()
        if self.voice_loop:
            self.voice_loop.cancel_followup()
        if self.live_mode:
            self._jobs.put(lambda: self._live_session(None) or self._listen_job(hold))
            return
        self._jobs.put(lambda: self._listen_job(hold))

    def run_tool(self, name: str, args: dict | None = None, label: str | None = None) -> None:
        """Выполнить инструмент напрямую (кнопки быстрых действий интерфейса) — тем же путём, что и голосовые
        команды: подтверждение опасных действий, журнал, короткий голосовой ответ."""
        args = dict(args or {})

        def job():
            self._calls = []
            self.listener.on_state("thinking")
            r = self._execute(name, args)
            self._finish(label or name, r.message, [name], True, "rules")

        self._jobs.put(job)

    def confirm(self, yes: bool) -> None:
        self._jobs.put(lambda: self._handle("да" if yes else "нет", echo=False))

    def stop_speaking(self) -> None:
        self.speaker.stop()

    def set_voice_replies(self, enabled: bool) -> None:
        self.voice_replies = enabled
        if not enabled:
            self.speaker.stop()

    def set_wake_word(self, enabled: bool) -> bool:
        """Постоянное ожидание «Jarvis»."""
        if not self.voice_loop:
            self.listener.on_message("system", f"Голосовой режим недоступен: {self.voice_error or 'нет микрофона'}")
            return False
        if enabled:
            self.voice_loop.start()
            self.listener.on_status("Жду «Jarvis»")
        else:
            self.voice_loop.stop()
            self.listener.on_status("Постоянное прослушивание выключено")
        return True

    def shutdown(self) -> None:
        if self.voice_loop:
            self.voice_loop.stop()
        self.speaker.stop()
        self._listen_stop.set()
        self._jobs.put(None)

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                break
            self.busy = True
            try:
                job()
            except Exception as exc:
                log.exception("Ошибка обработки")
                self.listener.on_state("error")
                self.listener.on_message("system", f"Внутренняя ошибка: {exc}")
            finally:
                self.busy = False
                if not self.listening and not (self.voice_loop and self.voice_loop.in_followup):
                    self.listener.on_state("idle")

    def _voice_blocked(self) -> bool:
        """Голосовой цикл не слушает, пока ассистент занят или говорит (и чуть после — эхо)."""
        return (self.busy or self.listening or self.speaker.speaking or not self._jobs.empty()
                or time.monotonic() - self.speaker.last_spoken_at < 0.35)

    def _on_wake(self) -> None:
        self.listener.on_wake()
        self.listener.on_state("listening")
        self.listener.on_status("Слушаю…")
        if self.live_mode and self.voice_loop and not self.voice_loop.in_followup:
            self.live.begin()

    def _on_followup_end(self) -> None:
        if not self.busy:
            self.listener.on_state("idle")
            self.listener.on_status("Жду «Jarvis»" if self.wake_enabled else "Готов к командам")

    def _transcribe(self, pcm: bytes, fallback_text: str = "") -> str | None:
        """Основной STT. При отсутствии сети — текст офлайн-модели (если есть). None — сбой."""
        from jarvis.voice.stt import STTError

        self.listener.on_state("thinking")
        self.listener.on_status("Распознаю речь…")
        try:
            return self.stt.transcribe(pcm).strip()
        except STTError as exc:
            log.warning("STT: %s", exc)
            return fallback_text or None

    def _listen_job(self, hold: Callable[[], bool] | None = None) -> None:
        if not self.recorder or not self.stt:
            self.listener.on_message("system", f"Голосовой ввод недоступен: {self.voice_error or 'нет микрофона'}")
            return
        from jarvis.voice.recorder import MicrophoneError

        self._listen_stop.clear()
        self.listening = True
        self.listener.on_state("listening")
        self.listener.on_status("Говорите…" if hold else "Слушаю…")
        try:
            pcm = self.recorder.record_phrase(stop=self._listen_stop, on_level=self.listener.on_level, holding=hold)
        except MicrophoneError as exc:
            self.listener.on_message("system", str(exc))
            return
        finally:
            self.listening = False
        if not pcm:
            self.listener.on_status("Речь не услышана")
            return
        text = self._transcribe(pcm)
        if text is None:
            self._say_only(responder.STT_DOWN[self.lang])
            return
        if not text:
            self._say_only(responder.NOT_HEARD[self.lang])
            return
        self.listener.on_transcript(text)
        self._handle(text, voice=True)

    def _voice_utterance(self, pcm: bytes, wake, followup: bool) -> None:
        """Фраза из голосового цикла: «Jarvis, …» или продолжение диалога без активатора."""
        if self.live_mode and not followup:
            from jarvis.voice.wake import is_stop_command

            if any(is_stop_command(part) for part in (wake.text or "").split(" | ")):
                if self.live.active:
                    self.live.stop()
                self.stop_conversation()
                return
            wake_only = self._wake_only(pcm, wake)
            if wake_only:
                self._ack()
            if self._live_session(None if wake_only else pcm):
                return
            if wake_only:
                self._awaiting_command = True
                self._open_followup(status="Слушаю команду…")
                return
        offline = wake.text.split(" | ")[0] if wake.text else ""
        text = self._transcribe(pcm, offline if wake.lang == "ru" else "")
        if text is None:
            self._say_only(responder.STT_DOWN[self.lang])
            return
        awaiting, self._awaiting_command = getattr(self, "_awaiting_command", False), False
        if not text:
            if followup and awaiting:
                self._say_only(responder.NOT_HEARD[self.lang])
                self._awaiting_command = True
                self._open_followup(status="Слушаю команду…")
            return
        if followup and not awaiting and not self._plausible_followup(text):
            log.info("Окно продолжения: пропускаю фоновый обрывок «%s»", text)
            self._open_followup()
            return
        command = self._strip_wake(text)
        if not followup and command == text.strip():
            from jarvis.voice.wake import find_wake

            found, idx = find_wake(text, max_position=1)
            if found:
                words = text.split()
                command = " ".join(words[idx + 1:]).strip(" ,.!?")
            else:
                log.info("Активатор слышала только офлайн-модель: %r / %r", wake.text, text)
        self.listener.on_transcript(text)
        if not command:
            self.lang = detect_lang(text, self.lang) if wake.lang == "ru" else wake.lang
            self.listener.on_message("user", text)
            self._ack()
            self._awaiting_command = True
            self._open_followup(status="Слушаю команду…")
            return
        self._handle(command, voice=True, echo_text=text)
        self._open_followup()

    SHORT_COMMANDS = {"да", "нет", "ага", "угу", "пауза", "стоп", "дальше", "продолжи", "продолжай", "громче", "тише",
                      "следующий", "следующая", "предыдущий", "назад", "отмена", "отмени", "хватит", "подтверждаю",
                      "yes", "no", "ok", "окей", "pause", "stop", "next", "cancel", "louder", "quieter", "play"}

    def _plausible_followup(self, text: str) -> bool:
        """Фраза без «Jarvis» после ответа: ≥ 2 слов или известная короткая команда."""
        words = re.findall(r"[\w'-]+", normalize(text))
        if len(words) >= 2:
            return True
        return bool(words) and (words[0] in self.SHORT_COMMANDS or words[0].isdigit())

    def _open_followup(self, status: str = "Можно продолжать без «Jarvis»") -> None:
        if self.voice_loop and self.voice_loop.running:
            self.voice_loop.followup(self.followup_seconds)
            self.listener.on_state("listening")
            self.listener.on_status(status)

    @staticmethod
    def _wake_only(pcm: bytes, wake) -> bool:
        """Короткая фраза, в которой офлайн-модель услышала только «Jarvis» (без команды)."""
        from jarvis.voice.wake import find_wake

        if len(pcm) / 2 / 16000 > 1.5 or not getattr(wake, "text", ""):
            return False
        for part in wake.text.split(" | "):
            words = [w for w in re.split(r"[\s,.!?]+", normalize(part)) if w]
            found, idx = find_wake(part, max_position=1)
            if not found or len(words) > idx + 1:
                return False
        return True

    def _ack(self) -> None:
        """Ответ на «Jarvis» без команды: «Да, сэр» — записью JARVIS, если есть, иначе синтез."""
        from jarvis.voice.intents import ResponseIntent

        spoken = responder.ACK[self.lang]
        if self.samples and self.voice_replies:
            decision = self.samples.find_best_sample(ResponseIntent("WAKE"), "", self.lang)
            if decision.kind == "sample":
                self.listener.on_message("assistant", decision.sample.text)
                self._voice(decision.sample.text, decision)
                return
        self._say_only(spoken, ResponseIntent("WAKE"))

    def greet(self) -> None:
        """Приветствие при запуске — только записью JARVIS («Я перезагрузился, сэр», утром «Доброе утро»).
        Подходящей записи нет — молча (синтезом при каждом запуске не приветствуем)."""
        if not self.samples or not self.voice_replies or not env_bool("SAMPLES_GREETING", True):
            return
        import datetime as dt

        from jarvis.voice.intents import ResponseIntent

        from jarvis.voice.samples import Decision

        wanted = (self.settings.get("voice_startup.sample", "") or "").lower()
        chosen = next((s for s in self.samples.samples if wanted and s.name.lower() == wanted), None)
        if chosen is not None and self.samples.config().enabled:
            decision = Decision("sample", chosen, 1.0, "STARTUP", 1, "settings", "startup sample")
        else:
            intent = "GREETING_MORNING" if 5 <= dt.datetime.now().hour < 12 else "STARTUP"
            decision = self.samples.find_best_sample(ResponseIntent(intent), "", "ru")
            if decision.kind != "sample" and intent != "STARTUP":
                decision = self.samples.find_best_sample(ResponseIntent("STARTUP"), "", "ru")
        if decision.kind == "sample":
            self._jobs.put(lambda: (self.listener.on_message("assistant", decision.sample.text),
                                    self._voice(decision.sample.text, decision)))

    def _say_only(self, spoken: str, intent=None) -> None:
        """Короткая голосовая реплика без записи в историю диалога."""
        self.listener.on_message("assistant", spoken)
        self._speak(spoken, intent)

    def _speak(self, spoken: str, intent=None) -> None:
        """Озвучить ответ: подходящая запись из голосовой библиотеки — или обычный TTS (Sample Matcher)."""
        if not (self.voice_replies and spoken):
            return
        from jarvis.voice.samples import Decision

        decision = (self.samples.find_best_sample(intent, spoken, self.lang) if self.samples
                    else Decision("tts", intent=getattr(intent, "intent", None), reason="no library"))
        self._voice(spoken, decision)

    def _voice(self, spoken: str, decision) -> None:
        self._debug_voice(decision)
        self.listener.on_state("speaking")
        self.listener.on_spoken(spoken)
        info = decision.info()
        if decision.kind in ("sample", "hybrid"):
            self.listener.on_voice_output(decision.kind, info)
            log.info("голос: %s %s (%s, уверенность %.2f)", decision.kind, decision.sample.name, decision.intent,
                     decision.confidence)
            played = self.speaker.play(decision.sample.path, on_level=self.listener.on_output_level)
            if played and decision.kind == "sample":
                return
            if not played and not self.samples.config().fallback_tts:
                return
            if not played:
                self.listener.on_voice_output("tts", dict(info, kind="tts", reason="sample playback failed"))
        elif decision.kind == "none":
            self.listener.on_voice_output("none", info)
            return
        else:
            self.listener.on_voice_output("tts", info)
        self.speaker.say(spoken, self.lang)

    def _debug_voice(self, decision) -> None:
        """Техническая информация о выборе записи — только в режиме разработчика."""
        if not (self.settings.get("ui.debug", False) or env_bool("JARVIS_DEBUG", False)):
            return
        i = decision.info()
        lines = [f"User intent: {i['intent']}", f"Sample candidates: {i['candidates']}"]
        if decision.sample is not None:
            lines += [f"Selected: {i['file']}", f"Confidence: {i['confidence']:.2f} ({i['via']})",
                      f"Playback: {'SAMPLE' if i['kind'] == 'sample' else 'HYBRID'}"]
        else:
            lines += ["Sample: NONE", f"Fallback: {'TTS' if i['kind'] == 'tts' else 'NONE'} ({i['reason']})"]
            if i["confidence"]:
                lines.append(f"Best score: {i['confidence']:.2f}")
        self.listener.on_debug("\n".join(lines))

    def _reply(self, text: str, handled: bool = True, source: str = "rules", user_text: str = "") -> None:
        from jarvis.voice.intents import classify

        if source == "error":
            spoken = responder.AI_DOWN[self.lang]
            from jarvis.utils import net

            if "gemini" in self.brain.describe().lower() and net.find_local_proxy(self.settings) is None:
                spoken = ("Сэр, облачный мозг недоступен: Gemini из России работает только через VPN. "
                          "Подключите Happ — меня можно пустить через него, не включая VPN для всего компьютера.")
                text = spoken
            self.listener.on_message("assistant", text)
            self._speak(spoken, classify([], text, spoken, user_text, handled, source))
            return
        pending = any(c.result.data.get("pending") or c.result.followup for c in self._calls)
        if source == "llm" and not pending:
            news = any(c.tool in responder.NEWS_TOOLS for c in self._calls)
            spoken = responder.spoken_news(text) if news else responder.compose(text, [], self.lang, handled)
            self.listener.on_message("assistant", text)
            self._speak(spoken, classify(self._calls, text, spoken, user_text, handled, source))
            return
        spoken = responder.compose(text, self._calls, self.lang, handled)
        self.listener.on_message("assistant", spoken if self.lang == "en" else text)
        self._speak(spoken, classify(self._calls, text, spoken, user_text, handled, source))

    def _strip_wake(self, text: str) -> str:
        m = self._wake_re.match(normalize(text))
        return text[m.end():].strip(" ,.!?") if m else text.strip()

    def stop_conversation(self, announce: bool = True) -> None:
        """«Стоп, Jarvis»: замолчать, закончить разговор (живой тоже) и снова ждать «Jarvis»."""
        self.speaker.stop()
        self._listen_stop.set()
        self._awaiting_command = False
        if self.pending:
            pending, self.pending = self.pending, None
            self.listener.on_confirm(None)
            self._cancel_pending(pending)
        if self.voice_loop:
            self.voice_loop.cancel_followup()
        if self.live and self.live.active:
            self.live.stop()
        self.listener.on_status("Жду «Jarvis»" if self.wake_enabled else "Готов к командам")
        if announce:
            from jarvis.voice.intents import ResponseIntent

            self._say_only("Хорошо, сэр.", ResponseIntent("CANCELLED"))
        self.listener.on_state("idle")

    def _handle(self, text: str, echo: bool = True, voice: bool = False, echo_text: str | None = None) -> None:
        if echo:
            self.listener.on_message("user", echo_text or text)
        from jarvis.voice.wake import is_stop_command

        if is_stop_command(text) and not self.pending:
            self.stop_conversation()
            return
        self.listener.on_state("thinking")
        command = self._strip_wake(text)
        self._calls = []
        if not command:
            self._ack()
            return
        self.lang = detect_lang(command, self.lang)
        n = normalize(command)

        if self.pending:
            pending, self.pending = self.pending, None
            self.listener.on_confirm(None)
            if time.time() - pending.created > 120:
                pass
            elif YES_RE.match(n):
                r = self._run_tool(pending.tool, pending.args, confirmed=True, action_id=pending.action_id)
                self._finish(command, r.message, [pending.tool])
                return
            elif NO_RE.match(n):
                self._cancel_pending(pending)
                self.dialog.add_turn(command, responder.CANCELLED["ru"], [])
                from jarvis.voice.intents import ResponseIntent
                self._say_only(responder.CANCELLED[self.lang], ResponseIntent("CANCELLED"))
                return
            self._cancel_pending(pending)

        reply = self.brain.respond(command, self._execute)
        self._finish(command, reply.text, reply.actions, reply.handled, reply.source)

    def _finish(self, user_text: str, reply: str, actions: list[str], handled: bool = True,
                source: str = "rules") -> None:
        log.info("диалог [%s] «%s» → «%s» %s", source, user_text, reply[:300], actions or "")
        if source != "error":
            self.dialog.add_turn(user_text, reply, actions)
        self._reply(reply, handled, source, user_text)

    def _execute(self, name: str, args: dict) -> ToolResult:
        """Вызывается «мозгом». Опасные действия откладываются до подтверждения."""
        tool = self.rt.registry.get(name)
        if tool is None:
            return ToolResult(False, f"Неизвестный инструмент: {name}")
        if tool.is_dangerous(args):
            question = tool.confirm_text(args)
            self._ask_confirmation(name, args, question)
            r = ToolResult(False, f"{question} Скажите «да» или «нет».", {"pending": True})
            self._calls.append(responder.ToolCall(name, args, r))
            return r
        return self._run_tool(name, args)

    def _ask_confirmation(self, name: str, args: dict, question: str) -> None:
        aid = uuid.uuid4().hex
        tool = self.rt.registry.get(name)
        self.pending = PendingAction(name, args, question, time.time(), aid)
        self.listener.on_action(aid, tool.announce_text(args) if tool else name, "confirm", "ожидает подтверждения")
        self.listener.on_confirm(question)

    def _cancel_pending(self, pending: PendingAction) -> None:
        tool = self.rt.registry.get(pending.tool)
        text = tool.announce_text(pending.args) if tool else pending.tool
        self.listener.on_action(pending.action_id, text, "error", "отменено")

    def _run_tool(self, name: str, args: dict, confirmed: bool = False, action_id: str | None = None) -> ToolResult:
        tool = self.rt.registry.get(name)
        announce = tool.announce_text(args) if tool else name
        aid = action_id or uuid.uuid4().hex
        self.listener.on_state("executing")
        self.listener.on_action(aid, announce, "running")
        self.listener.on_tool(name, "running")
        self.listener.on_status(announce + "…")
        log.info("tool %s %s%s", name, args, " (подтверждено)" if confirmed else "")
        r = self.rt.registry.call(name, args, self.rt)
        self.dialog.last_tool = name
        self.listener.on_action(aid, announce, "ok" if r.ok else "error", r.message)
        self.listener.on_tool(name, "ok" if r.ok else "error", r.message)
        if r.ok and r.data.get("theme"):
            self.listener.on_theme(r.data["theme"])
        if self.rt.restart_requested:
            self.rt.restart_requested = False
            threading.Timer(7.0, self.listener.on_restart).start()
        if r.followup:
            f_tool, f_args = r.followup
            self._ask_confirmation(f_tool, f_args, r.message)
            r.data["pending"] = True
        self._calls.append(responder.ToolCall(name, args, r))
        return r


def voice_enabled_by_default() -> bool:
    return env_bool("WAKE_WORD", True)
