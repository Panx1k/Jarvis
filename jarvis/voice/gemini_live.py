"""Живой голосовой режим через Gemini Live API.

Microphone → Gemini Live (сам слушает, понимает и отвечает голосом) → инструменты JARVIS → голос → Speakers.
Отдельные STT/TTS не нужны: модель получает звук и возвращает звук (голос, например, «Charon»).

Как встроено:
  * слово-активатор «Jarvis» по-прежнему ловит офлайн-детектор (звук никуда не уходит до обращения);
  * после активатора открывается сессия, звук микрофона идёт в Gemini, ответ проигрывается потоком;
  * модели доступны все инструменты реестра; опасные — только после голосового подтверждения
    (инструмент confirm_action), как и в обычном режиме;
  * контекст диалога и журнал действий общие с обычным режимом;
  * после паузы (LIVE_IDLE_SECONDS) сессия закрывается и JARVIS снова ждёт «Jarvis».

Настройки (.env): GEMINI_API_KEY (тот же ключ), GEMINI_LIVE_MODEL (пусто — автоподбор),
GEMINI_LIVE_VOICE (Charon, Puck, Kore, Fenrir, Aoede…), LIVE_IDLE_SECONDS.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import queue
import re
import threading
import time
from typing import TYPE_CHECKING, Any

import numpy as np

from jarvis.brain.prompting import SYSTEM_PROMPT
from jarvis.config import env, env_bool
from jarvis.utils.secrets import redact

if TYPE_CHECKING:
    from jarvis.core.assistant import Assistant

log = logging.getLogger("jarvis.live")

IN_RATE = 16000
OUT_RATE = 24000
VOICES = ["Charon", "Puck", "Kore", "Fenrir", "Aoede", "Orus", "Zephyr", "Leda"]

LIVE_EXTRA = """
Ты говоришь с пользователем голосом в реальном времени. Отвечай очень коротко и естественно.
Пользователь говорит по-русски (иногда по-английски). Отвечай по-русски; по-английски — только если вся
фраза пользователя на английском. Если фраза похожа на испанский/итальянский или непонятна — это,
скорее всего, неверно расслышанная русская речь: пойми её как русскую или переспроси.
СНАЧАЛА вызови инструмент, ПОТОМ говори. Нельзя говорить «открываю/переключаю/запускаю/включаю/готово»
и «уже открыт/запущен», пока инструмент не вызван и не вернул ok=true: ты не видишь экран и не знаешь,
что открыто, — только инструменты знают. Если просят действие, для которого есть инструмент, — вызывай его,
даже если кажется, что всё уже сделано. Если результат инструмента — ошибка, так и скажи. Если пользователь
возражает («он не открыт», «не сработало») — не спорь, вызови инструмент ещё раз.
Для вопросов о Steam (что установлено, во что поиграть, сколько наиграно) используй steam_games и сведения
«На компьютере». Окна: window_minimize / window_maximize / window_show. Spotify: spotify_play и др.
Discord: discord_open, discord_send_message, звонки.
Если инструмент сообщил, что действие требует подтверждения, спроси пользователя, и если он согласен —
вызови confirm_action(confirm=true), если нет — confirm_action(confirm=false).
Понимание речи: обращение «Джарвис» (и искажения вроде «Джаред», «Жаро», «Jari»), слова-паразиты и мат смысла
не меняют. «Можешь открыть…?», «давай запустим…», «Telegram мне открой» — это просьба выполнить действие.
Названия могли распознаться с ошибкой («диск орд» — Discord, «стем» — Steam, «ксго»/«кэс» — Counter-Strike 2):
сопоставь их с играми и программами из сведений «На компьютере». Если подходят несколько — спроси, какую.
Если фраза оборвана, бессмысленна или явно обращена не к тебе (разговор с другими людьми, обрывок вроде
«квартире выключить», «ma», «art») — НЕ вызывай инструменты: коротко переспроси «Повторите, сэр?» или промолчи
одним словом. Никогда не выполняй действие по догадке, если не уверен, что тебя об этом попросили."""

VOCABULARY = ["Jarvis", "Джарвис", "CS2", "КС", "Counter-Strike 2", "Dota 2", "Steam", "Discord", "Telegram",
              "Spotify", "YouTube", "VPN", "Chrome"]

PREBUFFER = 0.25
BARGE_MIN = 1500.0
BARGE_RATIO = float(env("LIVE_BARGE_IN_RATIO", "1.8") or 1.8)


INFO_TOOLS = {"get_time", "get_date", "get_weather", "get_volume", "media_status", "vpn_status", "list_apps",
              "steam_accounts", "steam_games", "steam_game_info", "spotify_now_playing", "spotify_playlists",
              "discord_status", "search_files"}
_DOING_RE = re.compile(r"\b(?:открываю|переключаю|запускаю|включаю|выключаю|закрываю|отправляю|сворачиваю|"
                       r"разворачиваю|устанавливаю|удаляю|меняю|ставлю|перехожу|opening|switching|launching|"
                       r"turning|closing|sending)\b")
_DONE_RE = re.compile(r"\b(?:открыл\w*|открыт\w*|запустил\w*|запущен\w*|включил\w*|включен\w*|выключил\w*|"
                      r"выключен\w*|закрыл\w*|закрыт\w*|переключил\w*|отправил\w*|отправлен\w*|свернул\w*|"
                      r"развернул\w*|сменил\w*|готово|сделано|opened|launched|done|switched)\b")

NUDGE = ("[Система JARVIS] В последнем ответе ты сообщил о действии («{reply}»), но не вызвал инструмент, "
         "который его выполняет, — на компьютере ничего не произошло. Если пользователь просил действие — вызови "
         "нужный инструмент прямо сейчас и скажи результат. Если действие не требовалось — коротко поправься.")


def unbacked_claim(reply: str, actions: list[str]) -> bool:
    """Ответ говорит о сделанном/делаемом действии, а действующего инструмента не было.
    «Переключаю аккаунт» после одного steam_accounts, «Discord уже открыт» без вызова — выдумка."""
    t = reply.lower().replace("ё", "е")
    acted = any(a not in INFO_TOOLS for a in actions)
    if acted:
        return False
    if _DOING_RE.search(t):
        return True
    return not actions and bool(_DONE_RE.search(t) or re.search(r"\bуже\s+(?:открыт|запущен|включ|закрыт|работает)", t))


def pick_live_model(model_names: list[str]) -> str | None:
    """Самая новая обычная live-модель (без translate/transcribe/robotics/extended-thinking)."""
    def version(m: str) -> float:
        v = re.search(r"gemini-(\d+(?:\.\d+)?)", m)
        return float(v.group(1)) if v else 0.0

    names = [m.split("/")[-1] for m in model_names]
    good = [m for m in names if "live" in m and not re.search(r"translate|transcribe|robotics|thinking", m)]
    audio = [m for m in names if "native-audio" in m and "latest" in m]
    ordered = sorted(good, key=version, reverse=True) + audio
    return ordered[0] if ordered else None


class AudioPlayer:
    """Проигрывание потока PCM 24 кГц в отдельном потоке (приём из сети не блокируется звуковой картой)."""

    def __init__(self, on_level=None):
        self.on_level = on_level
        self._lv_q: queue.Queue = queue.Queue()
        self._lv_thread: threading.Thread | None = None
        self._lv_next = 0.0
        self._q: queue.Queue[bytes | None] = queue.Queue()
        self.playing_until = 0.0
        self.audible_since = 0.0
        self.end_at = 0.0
        self._busy = False
        self.latency = 0.1
        self._thread = threading.Thread(target=self._run, name="live-audio", daemon=True)
        self._stop = threading.Event()
        self._thread.start()

    def _run(self) -> None:
        import sounddevice as sd

        with sd.RawOutputStream(samplerate=OUT_RATE, channels=1, dtype="int16", latency="high") as out:
            self.latency = float(out.latency or 0.1)
            starving = True
            while not self._stop.is_set():
                try:
                    chunk = self._q.get(timeout=0.05)
                except queue.Empty:
                    starving = True
                    continue
                if chunk is None:
                    break
                self._busy = True
                try:
                    if starving:
                        chunk = self._gather(chunk)
                        if chunk is None:
                            break
                        starving = False
                        if time.monotonic() > self.audible_since + 1.0:
                            self.audible_since = time.monotonic() + self.latency
                    if self.on_level:
                        self._emit_levels(chunk)
                    out.write(chunk)
                    self.end_at = time.monotonic() + self.latency
                finally:
                    self._busy = False

    def _emit_levels(self, chunk: bytes) -> None:
        """Громкость ответа кусками по ~60 мс, с учётом задержки звуковой карты (ядро пульсирует в такт речи)."""
        if self._lv_thread is None:
            self._lv_thread = threading.Thread(target=self._level_loop, name="live-levels", daemon=True)
            self._lv_thread.start()
        samples = np.frombuffer(chunk, dtype=np.int16)
        step = int(OUT_RATE * 0.06)
        start = max(time.monotonic(), self._lv_next) + 0.0
        for i in range(0, len(samples), step):
            part = samples[i:i + step].astype(np.float32)
            level = float(np.sqrt(np.mean(part * part))) / 6000.0 if len(part) else 0.0
            self._lv_q.put((start + self.latency + i / OUT_RATE, min(1.0, level)))
        self._lv_next = start + len(samples) / OUT_RATE

    def _level_loop(self) -> None:
        """Один поток отдаёт уровни громкости в момент, когда соответствующий звук звучит из колонок."""
        while not self._stop.is_set():
            try:
                due, level = self._lv_q.get(timeout=0.5)
            except queue.Empty:
                continue
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(min(delay, 1.0))
            if self._drop_until and time.monotonic() < self._drop_until:
                level = 0.0
            try:
                self.on_level(level)
            except Exception:
                pass

    def _gather(self, first: bytes) -> bytes | None:
        """Подкопить PREBUFFER секунд звука (или подождать не дольше PREBUFFER), затем играть одним куском."""
        buf = [first]
        size = len(first)
        deadline = time.monotonic() + PREBUFFER
        while size < PREBUFFER * OUT_RATE * 2 and time.monotonic() < deadline:
            try:
                chunk = self._q.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                break
            if chunk is None:
                return None
            buf.append(chunk)
            size += len(chunk)
        return b"".join(buf)

    def add(self, pcm: bytes) -> None:
        self._q.put(pcm)
        now = time.monotonic()
        start = self.playing_until if self.playing_until > now else now + PREBUFFER
        self.playing_until = start + len(pcm) / 2 / OUT_RATE

    def clear(self) -> None:
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self.playing_until = time.monotonic() - self.latency - 0.3
        self.end_at = time.monotonic()
        while True:
            try:
                self._lv_q.get_nowait()
            except queue.Empty:
                break
        self._lv_next = 0.0

    _drop_until = 0.0

    def interrupt(self) -> None:
        """Пользователь перебил: замолчать сразу и не играть хвост ответа, который ещё идёт из сети,
        пока сервер не подтвердит перебивание (interrupted / turn_complete) — не дольше 3 с."""
        self.clear()
        self._drop_until = time.monotonic() + 3.0

    def resume(self) -> None:
        self._drop_until = 0.0

    @property
    def dropping(self) -> bool:
        return time.monotonic() < self._drop_until

    @property
    def speaking(self) -> bool:
        """Звучит ли ответ. По факту: есть звук в очереди/записи или колонки ещё доигрывают буфер (+0.12 с
        на отзвук). Раньше бралась оценка с запасом в полсекунды — и глотались первые слова пользователя,
        если он начинал говорить сразу после ответа."""
        return self._busy or not self._q.empty() or time.monotonic() < self.end_at + 0.12

    def close(self) -> None:
        self._stop.set()
        self._q.put(None)


class NoiseGate:
    """Шумовой гейт для живого режима: пока человек молчит, в Gemini уходит тишина вместо фона (вентилятор,
    клавиатура, тихие разговоры рядом), и модель не реагирует на шум. Речь пропускается целиком: открытие —
    после двух громких блоков подряд, с 0.3 с звука до начала речи (первое слово не теряется), закрытие —
    только после hangover секунд тишины (паузы между словами не режутся)."""

    def __init__(self, min_threshold: float = 350.0, factor: float = 2.7, hangover: float = 0.8,
                 preroll: float = 0.3, start_blocks: int = 2):
        self.min_threshold, self.factor, self.hangover = min_threshold, factor, hangover
        self.start_blocks = start_blocks
        self.pre: collections.deque[bytes] = collections.deque(maxlen=max(1, int(preroll / 0.03)))
        self.open = False
        self._run = 0
        self._quiet = 0.0

    def threshold(self, noise: float) -> float:
        return max(self.min_threshold * 0.6, noise * self.factor * 0.8)

    def process(self, chunk: bytes, level: float, noise: float) -> list[bytes]:
        thr = self.threshold(noise)
        silence = b"\x00" * len(chunk)
        if self.open:
            self._quiet = 0.0 if level >= thr * 0.6 else self._quiet + len(chunk) / 2 / IN_RATE
            if self._quiet >= self.hangover:
                self.open, self._run = False, 0
                self.pre.clear()
                return [silence]
            return [chunk]
        self.pre.append(chunk)
        self._run = self._run + 1 if level > thr else 0
        if self._run >= self.start_blocks:
            self.open, self._quiet = True, 0.0
            out = list(self.pre)
            self.pre.clear()
            return out
        return [silence]


class GeminiLive:
    def __init__(self, assistant: "Assistant"):
        from jarvis.brain.key_manager import load_keys

        self.a = assistant
        keys = load_keys(prefix="GEMINI")
        self.key = keys[0][1] if keys else None
        self.voice = env("GEMINI_LIVE_VOICE", "Charon") or "Charon"
        self.model = env("GEMINI_LIVE_MODEL") or None
        self.idle_seconds = float(env("LIVE_IDLE_SECONDS", "30") or 30)
        self.barge_in = env_bool("LIVE_BARGE_IN", True)
        self.error: str | None = None if self.key else "Gemini API key is not configured."
        self.active = False
        self._lock = threading.Lock()
        self._client = None
        self._thread: threading.Thread | None = None
        self._t0 = time.monotonic()

    @property
    def available(self) -> bool:
        return self.key is not None

    def prepare_async(self) -> None:
        """Заранее (в фоне при запуске) загрузить SDK, выбрать модель и собрать описания инструментов —
        чтобы после «Jarvis» сразу подключаться (иначе это +5 с к первому ответу)."""
        def run():
            try:
                self.resolve_model()
                self._tools_cache = self._tools()
                from jarvis.core.knowledge import summary
                summary()
            except Exception as exc:
                log.debug("live prepare: %s", exc)

        threading.Thread(target=run, name="live-prepare", daemon=True).start()

    def _get_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.key, http_options={"api_version": "v1beta"})
        return self._client

    def resolve_model(self) -> str | None:
        if self.model:
            return self.model
        from jarvis.config import ROOT

        cache = ROOT / "models" / "gemini_live_model.txt"
        try:
            names = [m.name for m in self._get_client().models.list()
                     if "bidiGenerateContent" in (getattr(m, "supported_actions", None) or [])]
            self.model = pick_live_model(names)
            log.info("Gemini Live: модель %s, голос %s", self.model, self.voice)
            if self.model:
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(self.model, encoding="utf-8")
                except OSError:
                    pass
        except Exception as exc:
            if cache.exists():
                self.model = cache.read_text(encoding="utf-8").strip() or None
                log.info("Gemini Live: список моделей недоступен (%s) — беру %s из прошлого запуска",
                         type(exc).__name__, self.model)
            else:
                self.error = f"Gemini Live недоступен: {redact(str(exc))[:160]}"
                log.warning(self.error)
        return self.model

    def _tools(self):
        from google.genai import types

        decls = []
        for t in self.a.rt.registry.all():
            schema = t.openai_schema()["function"]
            decls.append(types.FunctionDeclaration(name=schema["name"], description=schema["description"],
                                                   parameters_json_schema=schema["parameters"]))
        decls.append(types.FunctionDeclaration(
            name="confirm_action", description="Подтвердить (true) или отменить (false) действие, которое "
                                               "ожидает подтверждения пользователя.",
            parameters_json_schema={"type": "object", "properties": {"confirm": {"type": "boolean"}},
                                    "required": ["confirm"]}))
        return [types.Tool(function_declarations=decls)]

    def _config(self):
        from google.genai import types

        from jarvis.brain.prompting import history_pairs

        from jarvis.core.knowledge import summary

        history = "\n".join(f"Пользователь: {u}\nJARVIS: {r}" for u, r in history_pairs(self.a.rt, 6))
        context = self.a.dialog.snapshot()
        known = summary()
        if known:
            context += f"\nНа компьютере: {known}"
        instruction = (SYSTEM_PROMPT + LIVE_EXTRA + f"\n\n[Текущий контекст]\n{context}"
                       + (f"\n\n[Недавний разговор]\n{history}" if history else ""))
        cfg = self._voice_cfg()
        languages = {"ru": ["ru-RU"], "en": ["en-US"]}.get(cfg.language, ["ru-RU", "en-US"])
        try:
            vocabulary = list(dict.fromkeys(VOCABULARY + self.a.lexicon.vocabulary(80)))
        except Exception:
            vocabulary = VOCABULARY
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice))),
            system_instruction=instruction,
            tools=getattr(self, "_tools_cache", None) or self._tools(),
            input_audio_transcription=types.AudioTranscriptionConfig(language_codes=languages,
                                                                     custom_vocabulary=vocabulary),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(automatic_activity_detection=self._activity(cfg)),
        )

    def _voice_cfg(self):
        getter = getattr(self.a, "voice_cfg", None)
        if getter is not None:
            return getter()
        from jarvis.voice.audio import VoiceInputSettings

        return VoiceInputSettings()

    @staticmethod
    def _activity(cfg):
        """VAD на стороне Gemini по настройкам голосового ввода: пауза конца фразы, чувствительность начала речи.
        Конец речи — «низкая» чувствительность: фраза не обрывается на короткой паузе между словами."""
        from google.genai import types

        start = (types.StartSensitivity.START_SENSITIVITY_HIGH if cfg.sensitivity >= 7
                 else types.StartSensitivity.START_SENSITIVITY_LOW)
        return types.AutomaticActivityDetection(
            silence_duration_ms=int(max(500, min(2500, cfg.silence_timeout * 1000 - 200))),
            prefix_padding_ms=300, start_of_speech_sensitivity=start,
            end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW)

    _NO_AUDIO = object()

    def begin(self) -> None:
        """Начать подключение заранее — как только прозвучал «Jarvis», пока человек договаривает команду.
        Фразу целиком сессия получит потом (run_session); если её так и не будет — тихо закроется."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._t0 = time.monotonic()
            self._first_q: queue.Queue = queue.Queue()
            self._result = None
            self.active = True
            self._thread = threading.Thread(target=self._thread_main, name="gemini-live", daemon=True)
            self._thread.start()

    def _thread_main(self) -> None:
        try:
            if not self.resolve_model():
                self._result = False
                return
            for attempt in range(2):
                self._started = False
                try:
                    self._result = asyncio.run(self._session())
                    self.error = None
                    return
                except Exception as exc:
                    self.error = redact(f"{type(exc).__name__}: {exc}")[:200]
                    if attempt == 0 and not self._started and isinstance(exc, (ConnectionError, OSError)):
                        log.info("Gemini Live: соединение сброшено (%s) — переподключаюсь", type(exc).__name__)
                        continue
                    log.warning("Gemini Live: %s", self.error)
                    self._result = False
                    return
        finally:
            self.active = False

    def stop(self) -> None:
        """Закончить живой разговор сейчас (из другого потока): «стоп, Jarvis», кнопка, Esc."""
        state, loop, session = getattr(self, "_state", None), getattr(self, "_loop", None), getattr(self, "_session_obj",
                                                                                                    None)
        if state is not None:
            state["closing"] = True
        if getattr(self, "_player", None) is not None:
            self._player.clear()
        if loop is not None and session is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(session.close(), loop)
            except RuntimeError:
                pass
        q = getattr(self, "_first_q", None)
        if q is not None:
            q.put(self._NO_AUDIO)

    def run_session(self, first_pcm: bytes | None = None) -> bool:
        """Провести живой разговор (блокирует вызывающий поток до конца сессии). False — не удалось подключиться."""
        self.begin()
        self._first_q.put(first_pcm if first_pcm else self._NO_AUDIO)
        self._thread.join()
        return bool(self._result)

    async def _session(self) -> bool:
        from google.genai import types

        a = self.a
        self._turn_calls, self._turn_override = [], None
        player = AudioPlayer(on_level=getattr(a.listener, "on_output_level", None))
        state = {"last_activity": time.monotonic(), "user_text": "", "reply_text": "", "actions": [],
                 "closed": False}
        try:
            config = self._config()
            from google import genai

            client = genai.Client(api_key=self.key, http_options={"api_version": "v1beta"})
            async with client.aio.live.connect(model=self.model, config=config) as session:
                self._session_obj, self._loop, self._state, self._player = (session, asyncio.get_running_loop(),
                                                                           state, player)
                log.info("Gemini Live: сессия открыта через %.2f с (%s)", time.monotonic() - self._t0, self.model)
                try:
                    first_pcm = await asyncio.to_thread(self._first_q.get, True, 8.0)
                except queue.Empty:
                    log.info("Gemini Live: команда не прозвучала — закрываю сессию")
                    return True
                self._started = True
                if first_pcm is self._NO_AUDIO:
                    first_pcm = None
                a.listener.on_state("listening")
                a.listener.on_status("Живой режим: говорите")
                if first_pcm:
                    for i in range(0, len(first_pcm), 3200):
                        await session.send_realtime_input(audio=types.Blob(data=first_pcm[i:i + 3200],
                                                                           mime_type=f"audio/pcm;rate={IN_RATE}"))
                    await session.send_realtime_input(audio_stream_end=True)
                    log.info("Gemini Live: фраза отправлена через %.2f с", time.monotonic() - self._t0)
                sender = asyncio.create_task(self._send_mic(session, player, state))
                try:
                    await self._receive(session, player, state)
                finally:
                    state["closed"] = True
                    sender.cancel()
            return True
        finally:
            self._session_obj = self._loop = self._state = self._player = None
            while player.speaking and not state.get("closing"):
                await asyncio.sleep(0.05)
            player.close()
            self._flush_turn(state)
            a.listener.on_state("idle")
            a.listener.on_status("Жду «Jarvis»")
            log.info("Gemini Live: сессия закрыта")

    async def _send_mic(self, session, player: AudioPlayer, state: dict) -> None:
        """Микрофон → Gemini. Пока звучит ответ, микрофон не отправляется (чтобы модель не слышала своё эхо),
        но если человек заговорил заметно громче эха — ответ обрывается и речь уходит в модель (перебивание)."""
        from google.genai import types

        from jarvis.voice.audio import rms

        src = self.a._audio_source_factory()
        loop = asyncio.get_running_loop()
        noise = 300.0
        echo = 0.0
        echo_blocks = 0
        loud = 0
        preroll: collections.deque[bytes] = collections.deque(maxlen=20)
        blob = lambda c: types.Blob(data=c, mime_type=f"audio/pcm;rate={IN_RATE}")
        cfg = self._voice_cfg()
        gate = NoiseGate(cfg.noise_threshold, cfg.factor, hangover=max(0.6, cfg.silence_timeout)) \
            if cfg.noise_suppression != "off" else None
        with src:
            while not state["closed"]:
                chunk = await loop.run_in_executor(None, src.read, 0.3)
                if chunk is None:
                    continue
                if self.a.speaker.speaking:
                    continue
                level = rms(chunk)
                if player.speaking:
                    if not self.barge_in:
                        continue
                    preroll.append(chunk)
                    if time.monotonic() < player.audible_since:
                        continue
                    threshold = max(BARGE_MIN, echo * BARGE_RATIO)
                    if echo_blocks >= 13 and level > threshold:
                        loud += 1
                    else:
                        loud = 0
                        echo = max(level, echo * 0.998)
                        echo_blocks += 1
                    if loud >= 3:
                        log.info("Gemini Live: перебивание (уровень %.0f, эхо %.0f)", level, echo)
                        player.interrupt()
                        self.a.listener.on_state("listening")
                        for c in preroll:
                            await session.send_realtime_input(audio=blob(c))
                        preroll.clear()
                        loud = 0
                        state["last_activity"] = time.monotonic()
                    continue
                preroll.clear()
                echo, echo_blocks, loud = 0.0, 0, 0
                self.a.listener.on_level(min(1.0, level / 3000.0))
                if level < noise * 2:
                    noise = 0.9 * noise + 0.1 * level
                else:
                    noise *= 1.005
                if level > max(500.0, noise * 3):
                    state["last_activity"] = time.monotonic()
                for out in (gate.process(chunk, level, noise) if gate else [chunk]):
                    await session.send_realtime_input(audio=blob(out))
                waiting = state.get("waiting_since")
                if waiting and time.monotonic() - waiting < 60:
                    continue
                if time.monotonic() - state["last_activity"] > self.idle_seconds and not player.speaking \
                        and not self.a.speaker.speaking:
                    state["closing"] = True
                    await session.close()
                    return

    async def _receive(self, session, player: AudioPlayer, state: dict) -> None:
        try:
            await self._receive_loop(session, player, state)
        except Exception:
            if state.get("closing"):
                return
            raise

    async def _receive_loop(self, session, player: AudioPlayer, state: dict) -> None:
        from google.genai import types

        a = self.a
        while not state["closed"] and not state.get("closing"):
            async for msg in session.receive():
                sc = msg.server_content
                if sc is not None and sc.input_transcription and sc.input_transcription.text:
                    state["user_text"] += sc.input_transcription.text
                    state["last_activity"] = time.monotonic()
                    a.listener.on_transcript(state["user_text"].strip())
                    from jarvis.voice.wake import is_stop_command

                    if is_stop_command(state["user_text"]):
                        log.info("Gemini Live: «%s» — заканчиваю разговор", state["user_text"].strip())
                        player.clear()
                        state["user_text"], state["reply_text"] = "", ""
                        state["closing"] = True
                        threading.Thread(target=a.stop_conversation, name="live-stop", daemon=True).start()
                        await session.close()
                        return
                if msg.data and player.dropping:
                    pass
                elif msg.data:
                    self._handle_audio(msg.data, state, player)
                elif state.get("held"):
                    self._handle_audio(None, state, player)
                if sc is not None:
                    if sc.output_transcription and sc.output_transcription.text:
                        state["reply_text"] += sc.output_transcription.text
                        a.listener.on_status("🔊 " + state["reply_text"].strip()[-80:])
                    if sc.interrupted:
                        player.clear()
                        player.resume()
                        if state.get("turn_mode") == "sample":
                            a.speaker.stop()
                        state["turn_mode"] = None
                    if sc.turn_complete:
                        player.resume()
                        if state.get("turn_audio") or state["reply_text"].strip():
                            state.pop("waiting_since", None)
                        if state.get("held"):
                            state["hold_until"] = 0.0
                            self._handle_audio(None, state, player)
                    if sc.turn_complete and (state["reply_text"].strip() or state.get("turn_audio")):
                        by_sample = state.get("turn_mode") == "sample"
                        if by_sample:
                            state["reply_text"] = state.get("sample_text", "") or state["reply_text"]
                        reply, actions = state["reply_text"].strip(), list(state["actions"])
                        asked = bool(state["user_text"].strip())
                        self._flush_turn(state)
                        state["turn_mode"], state["turn_audio"] = None, False
                        if asked and not by_sample and unbacked_claim(reply, actions) and not state.get("nudged"):
                            state["nudged"] = True
                            log.info("Gemini Live: ответ о действии без инструмента («%s») — прошу выполнить",
                                     reply[:80])
                            await session.send_client_content(
                                turns=types.Content(role="user", parts=[types.Part(text=NUDGE.format(reply=reply))]),
                                turn_complete=True)
                        else:
                            state["nudged"] = False
                        state["last_activity"] = time.monotonic()
                        a.listener.on_state("listening")
                if msg.tool_call:
                    if state.get("turn_mode") == "gemini":
                        state["turn_mode"] = None
                    log.info("Gemini Live: вызов инструмента через %.2f с", time.monotonic() - self._t0)
                    a.listener.on_state("executing")
                    responses = []
                    state["waiting_since"] = time.monotonic()
                    self._turn_user = state["user_text"].strip()
                    for fc in msg.tool_call.function_calls:
                        result = await asyncio.to_thread(self._run_tool, fc.name, dict(fc.args or {}))
                        state["actions"].append(fc.name)
                        responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response=result))
                    await session.send_tool_response(function_responses=responses)
                    state["waiting_since"] = time.monotonic()
                    state["last_activity"] = time.monotonic()
            if state["closed"]:
                break

    def _handle_audio(self, data: bytes | None, state: dict, player: AudioPlayer) -> None:
        """Звук ответа Gemini. В начале ответа решаем: запись JARVIS (голос Gemini в этом ответе не играет)
        или голос Gemini. Если фраза пользователя ещё не распознана и инструментов не было — начало ответа
        придерживается до 0.4 с, чтобы решить правильно («спасибо» → «Всегда к вашим услугам, сэр»)."""
        a = self.a
        state["turn_audio"] = True
        state["last_activity"] = time.monotonic()
        if state.get("turn_mode") is None:
            if data is not None:
                state.setdefault("held", []).append(data)
            if not state["user_text"].strip() and not getattr(self, "_turn_calls", None):
                state.setdefault("hold_until", time.monotonic() + 0.4)
                if time.monotonic() < state["hold_until"]:
                    return
            held = state.pop("held", [])
            state.pop("hold_until", None)
            decision = self._turn_sample(state)
            if decision is not None:
                state["turn_mode"] = "sample"
                state["sample_text"] = decision.sample.text
                threading.Thread(target=self._play_sample, args=(decision,), name="live-sample", daemon=True).start()
                return
            state["turn_mode"] = "gemini"
            a.listener.on_voice_output("live", {"intent": "", "file": ""})
            a.listener.on_state("speaking")
            for chunk in held:
                player.add(chunk)
            return
        if state["turn_mode"] == "gemini" and data is not None:
            if not player.speaking:
                a.listener.on_state("speaking")
            player.add(data)

    def _turn_sample(self, state: dict):
        """Запись JARVIS для текущего ответа — по фразе пользователя и результатам инструментов этого хода.
        None — подходящей нет (или в ответе конкретные данные): говорит Gemini."""
        a = self.a
        if not getattr(a, "samples", None) or not a.voice_replies:
            return None
        from jarvis.voice.intents import ResponseIntent, classify
        from jarvis.voice.lang import detect_lang
        from jarvis.voice.wake import find_wake

        user = state["user_text"].strip()
        calls = list(getattr(self, "_turn_calls", []))
        override = getattr(self, "_turn_override", None)
        if user and detect_lang(user) == "en" and not calls:
            return None
        if override:
            ri = ResponseIntent(override)
        elif not calls:
            words = re.findall(r"\w+", user)
            found, idx = find_wake(user, max_position=1)
            if found and len(words) <= idx + 1:
                ri = ResponseIntent("WAKE")
            elif not user or len(words) > 4:
                return None
            else:
                ri = classify([], "", "", user, True)
        else:
            ri = classify(calls, "", "", user, True)
        if ri.dynamic or ri.intent == "CHAT":
            return None
        decision = a.samples.find_best_sample(ri, "", "ru")
        return decision if decision.kind == "sample" else None

    def _play_sample(self, decision) -> None:
        a = self.a
        a._debug_voice(decision)
        a.listener.on_state("speaking")
        a.listener.on_voice_output("sample", decision.info())
        log.info("Gemini Live: вместо голоса Gemini — запись %s (%s, %.2f)", decision.sample.name, decision.intent,
                 decision.confidence)
        a.speaker.play(decision.sample.path, on_level=a.listener.on_output_level)
        a.listener.on_state("listening")

    def _run_tool(self, name: str, args: dict) -> dict[str, Any]:
        from jarvis.voice.responder import ToolCall

        a = self.a
        if not hasattr(self, "_turn_calls"):
            self._turn_calls = []
        if name == "confirm_action":
            if not a.pending:
                return {"ok": False, "message": "Нет действий, ожидающих подтверждения."}
            pending, a.pending = a.pending, None
            a.listener.on_confirm(None)
            if args.get("confirm"):
                r = a._run_tool(pending.tool, pending.args, confirmed=True, action_id=pending.action_id)
                self._turn_calls.append(ToolCall(pending.tool, pending.args, r))
                return {"ok": r.ok, "message": r.message}
            a._cancel_pending(pending)
            self._turn_override = "CANCELLED"
            return {"ok": True, "message": "Отменено."}
        debug = getattr(a, "debug_live", None)
        if debug is not None:
            debug(getattr(self, "_turn_user", ""), name, args)
        r = a._execute(name, args)
        self._turn_calls.append(ToolCall(name, args, r))
        if r.data.get("pending"):
            return {"ok": False, "needs_confirmation": True,
                    "message": f"Требуется подтверждение пользователя: {r.message}"}
        payload = {"ok": r.ok, "message": r.message}
        data = {k: v for k, v in (r.data or {}).items() if k != "pending"}
        if data:
            payload["data"] = json.loads(json.dumps(data, ensure_ascii=False, default=str))
        return payload

    def _flush_turn(self, state: dict) -> None:
        """Записать завершённый обмен репликами в чат и историю (общая с обычным режимом)."""
        user, reply = state["user_text"].strip(), state["reply_text"].strip()
        if user or reply:
            if user:
                self.a.listener.on_message("user", user)
            if reply:
                self.a.listener.on_message("assistant", reply)
            self.a.dialog.add_turn(user or "(голос)", reply or "", list(state["actions"]))
            log.info("диалог [live] «%s» → «%s» %s", user, reply[:200], state["actions"] or "")
        state["user_text"], state["reply_text"], state["actions"] = "", "", []
        self._turn_calls, self._turn_override = [], None
