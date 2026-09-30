"""Синтез тестовой речи (Edge TTS → PCM16 16 кГц) с кэшем на диске."""
from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import threading
import time
from pathlib import Path

CACHE = Path(__file__).parent / ".audio_cache"


def speech(text: str, voice: str = "ru-RU-DmitryNeural") -> bytes:
    import edge_tts
    import miniaudio

    CACHE.mkdir(exist_ok=True)
    key = hashlib.sha1(f"{voice}|{text}".encode()).hexdigest()[:16]
    raw = CACHE / f"{key}.pcm"
    if raw.exists():
        return raw.read_bytes()
    mp3 = os.path.join(tempfile.gettempdir(), f"vf_{key}.mp3")
    spoken = text if text.rstrip()[-1:] in ".!?" else text + "."
    pcm = None
    for attempt in range(4):
        try:
            asyncio.run(edge_tts.Communicate(spoken, voice).save(mp3))
            decoded = miniaudio.decode_file(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
                                            sample_rate=16000)
            pcm = decoded.samples.tobytes()
            os.remove(mp3)
            break
        except Exception as exc:
            print(f"[fixture] Edge не отдал «{text}» ({type(exc).__name__}), попытка {attempt + 1}")
            time.sleep(1 + attempt)
    if pcm is None:
        pcm = sapi_speech(text, "en" if voice.startswith("en") else "ru")
    raw.write_bytes(pcm)
    return pcm


def sapi_speech(text: str, lang: str) -> bytes:
    """Запасной синтез голосом Windows (Irina / Zira) сразу в 16 кГц."""
    import wave

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    path = os.path.join(tempfile.gettempdir(), "vf_sapi.wav")
    v = win32com.client.Dispatch("SAPI.SpVoice")
    want = "Russian" if lang == "ru" else "English"
    for cand in v.GetVoices():
        if want in cand.GetDescription():
            v.Voice = cand
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 18
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format = fmt
    stream.Open(path, 3)
    v.AudioOutputStream = stream
    v.Speak(text)
    stream.Close()
    with wave.open(path, "rb") as w:
        assert w.getframerate() == 16000
        return w.readframes(w.getnframes())


class RecordingTTS:
    """TTS-заглушка: запоминает, что ассистент сказал бы вслух."""
    name = "recording"

    def __init__(self):
        self.said: list[tuple[str, str]] = []
        self.event = threading.Event()

    def speak(self, text, stop, lang="ru"):
        self.said.append((text, lang))
        self.event.set()
        time.sleep(0.05)

    def prewarm(self, phrases):
        pass
