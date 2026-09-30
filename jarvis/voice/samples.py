"""Голосовые записи JARVIS (Voice Sample System).

    ответ JARVIS → намерение (intents.classify) → SampleManager.find_best_sample
        подходящая запись  → играет запись (без повторения тем же текстом через TTS)
        нет / слабое совпадение → обычный TTS (существующий Speaker)

* Библиотека: Samples/ (как положил пользователь, файлы не перемещаются) и assets/voice_samples/<категория>/.
  Индекс со смыслом записей — assets/voice_samples/index.json (намерения с весами, фразы, условия).
  Записи вне индекса подключаются автоматически, но звучат только при точном совпадении фразы.
* Уверенность: совпадение по смыслу текста ответа с фразами записи и/или совпадение намерения
  (своё — полный вес, более общее — с понижающим множителем). Порог — VOICE_SAMPLE_THRESHOLD (0.80);
  0.60–0.79 — только если совпало намерение. Ответы с конкретными данными (время, число, трек, причина
  ошибки) — только синтез, либо «запись + синтез» (если включено).
* Ротация: из равноценных записей берётся давно не звучавшая — JARVIS не звучит как одна пластинка.
* Файлы только проигрываются: по ним ничего не синтезируется и голос не копируется.
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
import wave
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from jarvis.config import ROOT, env, env_bool
from jarvis.voice.intents import ACTION_INTENTS, ResponseIntent

log = logging.getLogger("jarvis.samples")

LIBRARY_DIR = ROOT / "assets" / "voice_samples"
INDEX_FILE = LIBRARY_DIR / "index.json"
CATEGORIES = ["greetings", "confirmations", "acknowledgements", "actions", "status", "errors", "questions", "time",
              "system", "misc"]
AUDIO_EXT = {".wav", ".mp3"}
MAX_AUTO_SECONDS = 12.0
PARENT_FACTOR = 0.85


def phrase_key(text: str) -> str:
    """Смысловой ключ фразы: слова в нижнем регистре, без «сэр» и знаков."""
    words = re.findall(r"[\w]+", (text or "").lower().replace("ё", "е"))
    return " ".join(w for w in words if w not in ("сэр", "sir"))


def text_similarity(a: str, b: str) -> float:
    ka, kb = phrase_key(a), phrase_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    ratio = SequenceMatcher(None, ka, kb).ratio()
    wa, wb = set(ka.split()), set(kb.split())
    jacc = len(wa & wb) / len(wa | wb)
    return max(ratio, jacc) * 0.97


@dataclass
class Sample:
    file: str
    path: Path
    category: str = "misc"
    text: str = ""
    intents: dict[str, float] = field(default_factory=dict)
    phrases: list[str] = field(default_factory=list)
    hours: tuple[int, int] | None = None
    prefix: bool = False
    auto: bool = True
    duration: float = 0.0
    fmt: str = "wav"
    last_used: float = 0.0

    @property
    def name(self) -> str:
        return self.path.name

    def available_now(self, now: float | None = None) -> bool:
        if not self.hours:
            return True
        hour = time.localtime(now).tm_hour
        start, end = self.hours
        return start <= hour < end

    def text_score(self, text: str) -> float:
        if not text or len(phrase_key(text).split()) > 8:
            return 0.0
        return max((text_similarity(text, p) for p in self.phrases + [self.text]), default=0.0)


@dataclass
class Candidate:
    sample: Sample
    score: float
    via: str
    text_score: float = 0.0
    intent_score: float = 0.0


@dataclass
class Decision:
    kind: str
    sample: Sample | None = None
    confidence: float = 0.0
    intent: str | None = None
    candidates: int = 0
    via: str = ""
    reason: str = ""

    def info(self) -> dict:
        return {"kind": self.kind, "intent": self.intent or "NONE", "file": self.sample.name if self.sample else "",
                "text": self.sample.text if self.sample else "", "confidence": round(self.confidence, 2),
                "candidates": self.candidates, "via": self.via, "reason": self.reason}


@dataclass
class SampleConfig:
    enabled: bool = True
    threshold: float = 0.80
    low: float = 0.60
    variety: bool = True
    hybrid: bool = False
    fallback_tts: bool = True
    news_intro: bool = True


class SampleManager:
    """Библиотека записей: сканирование при запуске, индекс, выбор лучшей записи, проигрывание."""

    def __init__(self, settings=None, roots: list[Path] | None = None, index_file: Path | None = None):
        self.settings = settings
        default_dir = ROOT / (env("SAMPLES_DIR", "Samples") or "Samples")
        self.roots = roots if roots is not None else [default_dir, LIBRARY_DIR]
        self.index_file = index_file if index_file is not None else INDEX_FILE
        self.samples: list[Sample] = []
        self.broken: list[str] = []
        self._lock = threading.Lock()
        self.reload_library()

    def config(self) -> SampleConfig:
        s = (self.settings.get("voice.samples", {}) if self.settings is not None else {}) or {}
        threshold = s.get("threshold")
        if threshold is None:
            threshold = float(env("VOICE_SAMPLE_THRESHOLD", "0.80") or 0.8)
        return SampleConfig(
            enabled=bool(s.get("enabled", env_bool("SAMPLES", True))),
            threshold=max(0.0, min(1.0, float(threshold))),
            low=max(0.0, min(1.0, float(s.get("low", env("VOICE_SAMPLE_MIN", "0.60") or 0.6)))),
            variety=bool(s.get("variety", True)),
            hybrid=bool(s.get("hybrid", False)),
            fallback_tts=bool(s.get("fallback_tts", True)),
            news_intro=bool(s.get("news_intro", True)))

    def scan_library(self) -> dict[str, Path]:
        """Все аудиофайлы в папках библиотеки: имя файла → путь (без повторного чтения при каждом ответе)."""
        files: dict[str, Path] = {}
        for root in self.roots:
            if root == LIBRARY_DIR:
                for cat in CATEGORIES:
                    try:
                        (root / cat).mkdir(parents=True, exist_ok=True)
                    except OSError:
                        pass
            if not root.is_dir():
                continue
            for p in root.rglob("*"):
                if p.suffix.lower() in AUDIO_EXT and p.is_file():
                    files.setdefault(p.name.lower(), p)
        return files

    def load_metadata(self) -> list[dict]:
        try:
            return json.loads(self.index_file.read_text(encoding="utf-8")).get("samples", [])
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            log.warning("Индекс записей не прочитан (%s) — записи без смысловых тегов", exc)
            return []

    @staticmethod
    def _probe(path: Path) -> tuple[float, str]:
        """Длительность и формат; повреждённый файл — исключение."""
        if path.suffix.lower() == ".wav":
            with wave.open(str(path)) as w:
                if w.getsampwidth() != 2 or w.getnframes() == 0:
                    raise ValueError("нужен 16-битный непустой WAV")
                return w.getnframes() / w.getframerate(), "wav"
        import miniaudio

        info = miniaudio.get_file_info(str(path))
        return info.duration, path.suffix.lower().lstrip(".")

    def reload_library(self) -> None:
        files = self.scan_library()
        meta = {m["file"].lower(): m for m in self.load_metadata() if m.get("file")}
        samples, broken = [], []
        for key, path in sorted(files.items()):
            m = meta.get(key, {})
            if m.get("skip"):
                continue
            try:
                duration, fmt = self._probe(path)
            except Exception as exc:
                broken.append(path.name)
                log.debug("Запись пропущена: %s (%s)", path.name, exc)
                continue
            category = m.get("category") or (path.parent.name if path.parent.parent == LIBRARY_DIR else "misc")
            text = m.get("text") or path.stem
            explicit = bool(m.get("intents"))
            samples.append(Sample(
                file=path.name, path=path, category=category, text=text,
                intents={k.upper(): float(v) for k, v in (m.get("intents") or {}).items()},
                phrases=list(m.get("phrases") or [path.stem]),
                hours=tuple(m["hours"]) if m.get("hours") else None, prefix=bool(m.get("prefix")),
                auto=bool(m.get("auto", explicit)) and (explicit or duration <= MAX_AUTO_SECONDS),
                duration=round(duration, 2), fmt=fmt))
        with self._lock:
            self.samples, self.broken = samples, broken
        if broken:
            log.warning("Голосовые записи: пропущено повреждённых файлов — %d", len(broken))
        log.info("VOICE LIBRARY: %d samples, с намерениями: %d — READY", len(samples),
                 sum(1 for s in samples if s.intents))

    def stats(self) -> dict:
        return {"samples": len(self.samples), "tagged": sum(1 for s in self.samples if s.intents),
                "broken": len(self.broken), "ready": bool(self.samples)}

    def get_samples_by_intent(self, intent: str) -> list[Sample]:
        return [s for s in self.samples if intent in s.intents]

    def get_sample_confidence(self, sample: Sample, ri: ResponseIntent | None, text: str) -> Candidate:
        t = sample.text_score(text)
        i, via = 0.0, ""
        if ri is not None and sample.auto:
            if ri.intent in sample.intents:
                i, via = sample.intents[ri.intent], "intent"
            else:
                for anc, depth in ri.ancestors():
                    if anc in sample.intents and depth <= 2:
                        score = sample.intents[anc] * PARENT_FACTOR ** depth
                        if score > i:
                            i, via = score, f"parent:{anc}"
        if t >= i:
            return Candidate(sample, t, "text", t, i)
        return Candidate(sample, i, via, t, i)

    def find_best_sample(self, ri: ResponseIntent | None, text: str = "", lang: str = "ru") -> Decision:
        cfg = self.config()
        intent = ri.intent if ri else None
        fallback = "tts" if cfg.fallback_tts else "none"
        if not cfg.enabled:
            return Decision(fallback, intent=intent, reason="voice samples off")
        if lang != "ru" or not self.samples:
            return Decision(fallback, intent=intent, reason="no library" if not self.samples else "language")
        dynamic = ri.dynamic if ri is not None else True
        cands = []
        for s in self.samples:
            if not s.available_now():
                continue
            c = self.get_sample_confidence(s, ri, text)
            if c.score > 0.3:
                cands.append(c)
        usable = []
        for c in cands:
            if c.text_score >= cfg.threshold:
                usable.append(c)
            elif not dynamic and c.intent_score >= cfg.threshold:
                usable.append(c)
            elif not dynamic and c.intent_score >= cfg.low and c.via:
                usable.append(c)
        if usable:
            best = self._rotate(usable, cfg.variety)
            return Decision("sample", best.sample, best.score, intent, len(usable), best.via, "match")
        if ri is not None and ri.intent == "NEWS" and cfg.news_intro:
            intro = [c for c in cands if c.sample.prefix and c.sample.intents.get("ACKNOWLEDGEMENT", 0) >= 0.9]
            if intro:
                best = self._rotate(intro, cfg.variety)
                return Decision("hybrid", best.sample, best.score, intent, len(intro), best.via, "news intro")
        if dynamic and cfg.hybrid and ri is not None and ri.ok and ri.intent in ACTION_INTENTS | {"GAME_LAUNCHING"}:
            prefixes = [c for c in cands if c.sample.prefix and c.intent_score >= cfg.low]
            if prefixes:
                best = self._rotate(prefixes, cfg.variety)
                return Decision("hybrid", best.sample, best.score, intent, len(prefixes), best.via,
                                "sample + dynamic TTS")
        top = max((c.score for c in cands), default=0.0)
        return Decision(fallback, confidence=top, intent=intent, candidates=len(cands),
                        reason="dynamic response" if dynamic else ("low confidence" if cands else "no sample"))

    def _rotate(self, cands: list[Candidate], variety: bool) -> Candidate:
        """Лучшая группа (близкие по уверенности), из неё — давно не звучавшая запись."""
        best = max(c.score for c in cands)
        margin = 0.06 if best >= 0.85 else 0.15
        group = [c for c in cands if c.score >= best - margin]
        if not variety:
            chosen = max(group, key=lambda c: (c.score, -len(c.sample.name)))
        else:
            oldest = min(c.sample.last_used for c in group)
            fresh = [c for c in group if c.sample.last_used == oldest]
            chosen = random.choice(fresh)
        SampleManager._uses += 1
        chosen.sample.last_used = SampleManager._uses
        return chosen

    _uses = 0

    def play_sample(self, sample: Sample, stop: threading.Event, on_level=None) -> None:
        play_audio(sample.path, stop, on_level)

    EVENT_INTENTS = {"ack": "WAKE", "startup": "STARTUP", "morning": "GREETING_MORNING", "done": "TASK_COMPLETED",
                     "shutdown": "SHUTDOWN"}

    def has(self, event: str) -> bool:
        return bool(self.config().enabled and self.get_samples_by_intent(self.EVENT_INTENTS.get(event, event)))

    def pick(self, event: str) -> Sample | None:
        d = self.find_best_sample(ResponseIntent(self.EVENT_INTENTS.get(event, event)))
        return d.sample if d.kind == "sample" else None


def load_audio(path: Path) -> tuple[np.ndarray, int]:
    """PCM int16 (кадры × каналы) и частота. WAV — стандартной библиотекой, mp3 — miniaudio."""
    if path.suffix.lower() == ".wav":
        with wave.open(str(path)) as w:
            rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if width != 2:
            raise ValueError(f"поддерживаются только 16-битные WAV ({path.name})")
        data = np.frombuffer(raw, dtype=np.int16)
    else:
        import miniaudio

        dec = miniaudio.decode_file(str(path), output_format=miniaudio.SampleFormat.SIGNED16)
        rate, channels = dec.sample_rate, dec.nchannels
        data = np.frombuffer(dec.samples, dtype=np.int16)
    if channels > 1:
        data = data.reshape(-1, channels)
    return data, rate


load_wav = load_audio


def play_audio(path: Path, stop: threading.Event, on_level=None) -> None:
    """Проиграть запись синхронно; остановить по stop. on_level(0..1) — громкость звучащего фрагмента
    каждые ~50 мс (ядро пульсирует в такт записи)."""
    import sounddevice as sd

    data, rate = load_audio(path)
    duration = len(data) / rate
    mono = data.mean(axis=1) if data.ndim > 1 else data.astype(np.float32)
    sd.play(data, rate)
    start = time.monotonic()
    end = start + duration + 0.1
    try:
        while time.monotonic() < end:
            if stop.is_set():
                break
            if on_level is not None:
                pos = int((time.monotonic() - start) * rate)
                part = mono[pos:pos + int(rate * 0.05)].astype(np.float32)
                level = float(np.sqrt(np.mean(part * part))) / 6000.0 if len(part) else 0.0
                try:
                    on_level(min(1.0, level))
                except Exception:
                    pass
            time.sleep(0.04)
    finally:
        sd.stop()
        if on_level is not None:
            try:
                on_level(0.0)
            except Exception:
                pass


play_wav = play_audio


SampleBank = SampleManager
