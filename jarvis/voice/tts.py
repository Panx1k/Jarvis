"""Синтез речи. Провайдеры взаимозаменяемы: реализуйте TTSEngine.speak(text, stop, lang) и укажите
класс в .env: TTS_ENGINE=mypackage.module:MyTTS — остальная система не меняется.

* EdgeTTS — нейронные голоса Microsoft (естественное звучание, нужен интернет). По умолчанию мужские:
  ru-RU-DmitryNeural и британский en-GB-RyanNeural, с лёгким понижением тона — «голос ИИ-ассистента».
  Короткие фразы кэшируются на диске: «Да, сэр. Слушаю.» звучит без задержки.
* SapiTTS — голоса Windows (офлайн).
* FallbackTTS — основной движок, при ошибке — запасной.
"""
from __future__ import annotations

import asyncio
import ctypes
import hashlib
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid

from jarvis.config import ROOT, env
from jarvis.utils.text import strip_markdown

log = logging.getLogger("jarvis.tts")

CACHE_DIR = ROOT / "models" / "tts_cache"


class TTSEngine:
    name = "base"

    def speak(self, text: str, stop: threading.Event, lang: str = "ru") -> None:
        """Проговорить текст синхронно; прервать, если выставлен stop."""
        raise NotImplementedError

    def prewarm(self, phrases: list[tuple[str, str]]) -> None:
        """Заранее подготовить частые фразы (необязательно)."""


def _mci(command: str) -> str:
    buf = ctypes.create_unicode_buffer(256)
    err = ctypes.windll.winmm.mciSendStringW(command, buf, 255, 0)
    if err:
        msg = ctypes.create_unicode_buffer(256)
        ctypes.windll.winmm.mciGetErrorStringW(err, msg, 255)
        raise RuntimeError(f"MCI: {msg.value} ({command})")
    return buf.value


def play_audio_file(path: str, stop: threading.Event) -> None:
    """Проигрывание mp3/wav встроенными средствами Windows (WinMM MCI)."""
    alias = "jv" + uuid.uuid4().hex[:8]
    _mci(f'open "{path}" type mpegvideo alias {alias}')
    try:
        _mci(f"play {alias}")
        time.sleep(0.05)
        while True:
            if stop.is_set():
                _mci(f"stop {alias}")
                break
            if _mci(f"status {alias} mode") != "playing":
                break
            time.sleep(0.03)
    finally:
        try:
            _mci(f"close {alias}")
        except RuntimeError:
            pass


class EdgeTTS(TTSEngine):
    """voices: язык → цепочка голосов. Сервис для ru-RU-DmitryNeural заметно чаще отвечает NoAudioReceived
    (особенно с rate/pitch), поэтому следующим идёт мужской мультиязычный голос, говорящий по-русски."""

    def __init__(self, voices: dict[str, list[str]] | None = None, rate: str = "+0%", pitch: str = "+0Hz"):
        import edge_tts

        self.voices = voices or {"ru": ["ru-RU-DmitryNeural", "en-US-AndrewMultilingualNeural"],
                                 "en": ["en-GB-RyanNeural", "en-US-AndrewMultilingualNeural"]}
        self.rate, self.pitch = rate, pitch
        self.name = f"Edge ({self.voices.get('ru', ['?'])[0]})"

    def voices_for(self, lang: str) -> list[str]:
        return self.voices.get(lang) or self.voices.get("ru") or next(iter(self.voices.values()))

    def _cache_path(self, text: str, voice: str):
        key = hashlib.sha1(f"{voice}|{self.rate}|{self.pitch}|{text}".encode()).hexdigest()[:20]
        return CACHE_DIR / f"{key}.mp3"

    def synthesize(self, text: str, lang: str = "ru") -> str:
        """Возвращает путь к mp3. Короткие фразы берутся из кэша; временный файл удаляет вызывающий."""
        import edge_tts

        text = text.strip()
        if text and text[-1] not in ".!?…":
            text += "."
        path = os.path.join(tempfile.gettempdir(), f"jarvis_tts_{uuid.uuid4().hex[:8]}.mp3")
        voices = self.voices_for(lang)
        if len(text) <= 80:
            for voice in voices:
                cached = self._cache_path(text, voice)
                if cached.exists():
                    shutil.copyfile(cached, path)
                    return path
        if len(voices) == 1:
            base = text.rstrip(".!?…")
            variants = list(dict.fromkeys([text, (text.replace(",", " —") if "," in text else base + "!"), base + "…"]))
            n = max(1, int(env("TTS_EDGE_PARALLEL", "6") or 6))
            attempts = [(voices[0], variants[i % len(variants)]) for i in range(n)]
        else:
            attempts = [(v, text) for v in voices]
        chain = [v for v, _ in attempts]

        async def one(voice: str, out: str, spoken: str) -> str:
            await asyncio.wait_for(edge_tts.Communicate(spoken, voice, rate=self.rate, pitch=self.pitch).save(out), 15)
            if not os.path.exists(out) or os.path.getsize(out) == 0:
                raise RuntimeError("пустой ответ Edge TTS")
            return voice

        async def race() -> str:
            """Основной и резервный голос запрашиваются параллельно: отказ основного не добавляет задержку.
            Предпочтение — основному голосу, если он ответил."""
            outs = [path] + [f"{path}.{i}.mp3" for i in range(1, len(chain))]
            tasks = [asyncio.create_task(one(v, o, s)) for (v, s), o in zip(attempts, outs)]
            winner, errors = None, []
            pending = set(tasks)
            ready_backup: int | None = None
            while pending and winner is None:
                timeout = 0.7 if ready_backup is not None else None
                done, pending = await asyncio.wait(pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    break
                for task in done:
                    i = tasks.index(task)
                    if task.exception() is not None:
                        errors.append(task.exception())
                        log.info("Edge: голос %s не ответил (%s)", chain[i], type(task.exception()).__name__)
                    elif i == 0:
                        winner = chain[0]
                    elif ready_backup is None or i < ready_backup:
                        ready_backup = i
                if tasks[0].done() and tasks[0].exception() is not None and ready_backup is not None:
                    break
            if winner is None and ready_backup is not None:
                winner = chain[ready_backup]
                shutil.move(outs[ready_backup], path)
            for t in tasks:
                t.cancel()
            for o in outs[1:]:
                try:
                    os.remove(o)
                except OSError:
                    pass
            if winner is None:
                raise RuntimeError(f"Edge TTS: {errors[-1] if errors else 'нет голосов'}")
            return winner

        try:
            voice = asyncio.run(race())
        except RuntimeError:
            voice = asyncio.run(race())
        cached = self._cache_path(text, voice) if len(text) <= 80 else None
        if cached:
            try:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, cached)
            except OSError:
                pass
        return path

    def speak(self, text: str, stop: threading.Event, lang: str = "ru") -> None:
        path = self.synthesize(text, lang)
        try:
            if not stop.is_set():
                play_audio_file(path, stop)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def prewarm(self, phrases: list[tuple[str, str]]) -> None:
        for text, lang in phrases:
            try:
                os.remove(self.synthesize(text, lang))
            except Exception as exc:
                log.debug("prewarm %s: %s", text, exc)


class SapiTTS(TTSEngine):
    def __init__(self):
        self.name = "Windows SAPI"
        self._local = threading.local()

    def _voice(self, lang: str):
        cache = getattr(self._local, "voices", None)
        if cache is None:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            cache = self._local.voices = {"obj": win32com.client.Dispatch("SAPI.SpVoice")}
        v = cache["obj"]
        want = "Russian" if lang == "ru" else "English"
        if cache.get("lang") != lang:
            for candidate in v.GetVoices():
                if want.lower() in candidate.GetDescription().lower():
                    v.Voice = candidate
                    break
            v.Rate = 1
            cache["lang"] = lang
        return v

    def speak(self, text: str, stop: threading.Event, lang: str = "ru") -> None:
        v = self._voice(lang)
        v.Speak(text, 1)
        while not v.WaitUntilDone(50):
            if stop.is_set():
                v.Speak("", 3)
                break


class FallbackTTS(TTSEngine):
    def __init__(self, primary: TTSEngine, fallback: TTSEngine):
        self.primary, self.fallback = primary, fallback
        self.name = primary.name

    def speak(self, text: str, stop: threading.Event, lang: str = "ru") -> None:
        try:
            self.primary.speak(text, stop, lang)
        except Exception as exc:
            level = logging.DEBUG if type(exc).__name__ == "NotReady" else logging.WARNING
            log.log(level, "%s не сработал (%s) — говорю через %s", self.primary.name, exc, self.fallback.name)
            self.fallback.speak(text, stop, lang)

    def prewarm(self, phrases: list[tuple[str, str]]) -> None:
        self.primary.prewarm(phrases)


class Speaker:
    """Потокобезопасная обёртка: одна фраза за раз, мгновенная остановка."""

    def __init__(self, engine: TTSEngine | None):
        self.engine = engine
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.speaking = False
        self.last_spoken_at = 0.0

    @property
    def name(self) -> str:
        return self.engine.name if self.engine else "выключен"

    def say(self, text: str, lang: str = "ru") -> None:
        text = strip_markdown(text)
        if not self.engine or not text:
            return
        with self._lock:
            self._stop.clear()
            self.speaking = True
            try:
                self.engine.speak(text, self._stop, lang)
            except Exception:
                log.exception("Ошибка синтеза речи")
            finally:
                self.speaking = False
                self.last_spoken_at = time.monotonic()

    def play(self, path, on_level=None) -> bool:
        """Проиграть готовую запись (голосовая библиотека) в том же голосовом потоке, что и синтез.
        False — не получилось (тогда говорим синтезом). on_level — громкость для анимации ядра."""
        from jarvis.voice.samples import play_audio

        with self._lock:
            self._stop.clear()
            self.speaking = True
            try:
                play_audio(path, self._stop, on_level)
                return True
            except Exception as exc:
                log.warning("Не удалось проиграть запись %s: %s", getattr(path, "name", path), exc)
                return False
            finally:
                self.speaking = False
                self.last_spoken_at = time.monotonic()

    def stop(self) -> None:
        self._stop.set()

    def prewarm_async(self, phrases: list[tuple[str, str]]) -> None:
        if self.engine:
            threading.Thread(target=self.engine.prewarm, args=(phrases,), name="tts-prewarm", daemon=True).start()


def make_tts() -> TTSEngine | None:
    raw = env("TTS_ENGINE", "edge") or "edge"
    if ":" in raw:
        from jarvis.voice.stt import load_class
        return load_class(raw)()
    engine = raw.lower()
    if engine in ("none", "off", "0"):
        return None
    sapi = SapiTTS()
    if engine == "sapi":
        return sapi
    try:
        backup = env("TTS_VOICE_BACKUP", "none")
        extra = [] if backup.lower() in ("", "none", "off") else [backup]
        edge = EdgeTTS({"ru": [env("TTS_VOICE", "ru-RU-DmitryNeural")] + extra,
                        "en": [env("TTS_VOICE_EN", "en-GB-RyanNeural")] + extra},
                       rate=env("TTS_RATE", "+0%"), pitch=env("TTS_PITCH", "+0Hz"))
        online = FallbackTTS(edge, sapi)
    except ImportError:
        online = sapi
    if engine == "xtts":
        try:
            from jarvis.voice.xtts import XttsTTS
            return FallbackTTS(XttsTTS(), online)
        except ImportError as exc:
            log.warning("XTTS недоступен (%s) — использую Edge", exc)
    return online
