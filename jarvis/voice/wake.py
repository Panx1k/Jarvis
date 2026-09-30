"""Детектор слова-активатора «Jarvis».

VoskWakeDetector — офлайн (звук не покидает компьютер): русская и английская малые модели Vosk
распознают фразу свободно, а «Jarvis» ищется среди первых слов с учётом типичных искажений
(джарвис / джервис / jarvis …). Режим грамматики из одного слова не используется —
он даёт ложные срабатывания («Дарвин» → «джарвис»).

STTWakeDetector — запасной вариант без моделей: вся фраза отправляется в основной STT.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from jarvis.config import ROOT
from jarvis.utils.text import normalize, similarity

log = logging.getLogger("jarvis.wake")

WAKE_VARIANTS = ["джарвис", "джервис", "джарвиз", "жарвис", "джарвес", "джервиз", "jarvis", "jarvis's", "jervis",
                 "jarvus"]
LEADING = {"эй", "hey", "ok", "okay", "окей", "ок", "слушай", "а", "ну"}


@dataclass
class WakeResult:
    detected: bool
    text: str = ""
    lang: str = "ru"


STOP_WORDS = r"(?:стоп|стой|хватит|отбой|замолчи|помолчи|тихо|прекрати|перестань слушать|не слушай|stop|enough)"


def is_stop_command(text: str) -> bool:
    """«Стоп, Jarvis», «Jarvis, стоп», «хватит», «отбой» — замолчать и закончить разговор (снова ждать «Jarvis»)."""
    words = [w for w in re.split(r"[\s,.!?…]+", normalize(text or "")) if w]
    if not words or len(words) > 4:
        return False
    rest = [w for w in words if not (w in WAKE_VARIANTS or (len(w) >= 5 and max(similarity(w, v) for v in
                                                                                    ("джарвис", "jarvis")) >= 0.8))
            and w not in ("пожалуйста", "всё", "все", "уже")]
    return bool(rest) and bool(re.fullmatch(STOP_WORDS, " ".join(rest)))


def find_wake(text: str, max_position: int = 2) -> tuple[bool, int]:
    """Есть ли «Jarvis» среди первых слов фразы. Возвращает (найдено, индекс слова)."""
    words = [w for w in re.split(r"[\s,.!?]+", normalize(text)) if w]
    pos = 0
    for i, w in enumerate(words):
        if w in LEADING and pos == 0:
            continue
        if w in WAKE_VARIANTS or (len(w) >= 5 and max(similarity(w, v) for v in ("джарвис", "jarvis")) >= 0.8):
            return True, i
        pos += 1
        if pos > max_position:
            break
    return False, -1


class WakeWordDetector:
    name = "base"

    def available(self) -> bool:
        return True

    def start(self) -> None: ...

    def feed(self, chunk: bytes) -> bool:
        """Добавить звук текущей фразы. True — активатор уже услышан (ранняя реакция интерфейса)."""
        return False

    def finish(self, pcm: bytes) -> WakeResult:
        raise NotImplementedError


class VoskWakeDetector(WakeWordDetector):
    def __init__(self, ru_model: str, en_model: str | None = None):
        self.paths = [p for p in (ru_model, en_model) if p]
        self.models = []
        self._recs = []
        self._early = False
        self.name = "Vosk (офлайн)"

    @staticmethod
    def _resolve(p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else ROOT / path

    def available(self) -> bool:
        return self._resolve(self.paths[0]).is_dir() if self.paths else False

    def _load(self):
        if not self.models:
            import vosk

            vosk.SetLogLevel(-1)
            for p in self.paths:
                path = self._resolve(p)
                if path.is_dir():
                    lang = "en" if "-en" in path.name else "ru"
                    self.models.append((lang, vosk.Model(str(path))))
            if not self.models:
                raise RuntimeError("модели Vosk не найдены")
        return self.models

    def start(self) -> None:
        import vosk

        self._recs = [(lang, vosk.KaldiRecognizer(model, 16000), []) for lang, model in self._load()]
        self._early = False

    def feed(self, chunk: bytes) -> bool:
        for _, rec, done in self._recs:
            if rec.AcceptWaveform(chunk):
                seg = json.loads(rec.Result()).get("text", "")
                if seg:
                    done.append(seg)
                current = " ".join(done)
            else:
                current = " ".join(done + [json.loads(rec.PartialResult()).get("partial", "")])
            if not self._early and current.strip() and find_wake(current)[0]:
                self._early = True
        return self._early

    def finish(self, pcm: bytes) -> WakeResult:
        if not self._recs:
            self.start()
            self.feed(pcm)
        texts = []
        for lang, rec, done in self._recs:
            text = " ".join(done + [json.loads(rec.FinalResult()).get("text", "")]).strip()
            texts.append((lang, text))
        self._recs = []
        for lang, text in texts:
            if find_wake(text)[0]:
                return WakeResult(True, text, lang)
        return WakeResult(False, " | ".join(t for _, t in texts))


class STTWakeDetector(WakeWordDetector):
    """Без офлайн-моделей: каждая фраза уходит в основной STT (менее приватно)."""

    def __init__(self, stt):
        self.stt = stt
        self.name = f"{stt.name} (онлайн)"

    def finish(self, pcm: bytes) -> WakeResult:
        try:
            text = self.stt.transcribe(pcm)
        except Exception as exc:
            log.warning("wake stt: %s", exc)
            return WakeResult(False)
        from jarvis.voice.lang import detect_lang
        return WakeResult(find_wake(text)[0], text, detect_lang(text))
