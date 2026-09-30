"""Проверка голосового конвейера без человека: SAPI → WAV 16 кГц → STT → разбор команды.

    python scripts/check_voice_pipeline.py "открой ютуб и найди там музыку для учебы"
"""
from __future__ import annotations

import os
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pythoncom
import win32com.client

from jarvis.brain.rules import RuleParser
from jarvis.config import Settings
from jarvis.core.assistant import Runtime
from jarvis.core.context import DialogContext
from jarvis.services.app_index import AppIndex
from jarvis.tools.base import load_builtin_tools, registry
from jarvis.voice.stt import make_stt

phrase = sys.argv[1] if len(sys.argv) > 1 else "открой ютуб и найди там музыку для учебы"
path = os.path.join(tempfile.gettempdir(), "jarvis_voice_test.wav")

pythoncom.CoInitialize()
voice = win32com.client.Dispatch("SAPI.SpVoice")
for v in voice.GetVoices():
    if "Russian" in v.GetDescription():
        voice.Voice = v
stream = win32com.client.Dispatch("SAPI.SpFileStream")
fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
fmt.Type = 22
stream.Format = fmt
stream.Open(path, 3)
voice.AudioOutputStream = stream
voice.Speak(phrase)
stream.Close()

with wave.open(path, "rb") as w:
    pcm = w.readframes(w.getnframes())
    rate = w.getframerate()
    print(f"WAV: {rate} Гц, {len(pcm)} байт")

stt = make_stt()
text = stt.transcribe(pcm, rate)
print(f"STT ({stt.name}): «{text}»")

settings = Settings()
apps = AppIndex(settings)
apps.refresh()
load_builtin_tools()
plan = RuleParser(Runtime(settings, DialogContext(), registry, apps)).parse(text)
print("План:", [(a.tool, a.args) for a in plan.actions] if plan else None)
os.remove(path)
