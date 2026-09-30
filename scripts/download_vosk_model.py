"""Скачивает офлайн-модель распознавания русской речи Vosk (~45 МБ) в models/.

    python scripts/download_vosk_model.py
"""
from __future__ import annotations

import io
import os
import sys
import zipfile

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jarvis.config import MODELS_DIR

MODELS = ["vosk-model-small-ru-0.22", "vosk-model-small-en-us-0.15"]


def download(name: str) -> None:
    target = MODELS_DIR / name
    if target.exists():
        print(f"Модель уже есть: {target}")
        return
    url = f"https://alphacephei.com/vosk/models/{name}.zip"
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Скачиваю {url} …")
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        z.extractall(MODELS_DIR)
    print(f"Готово: {target}")


def main() -> None:
    names = sys.argv[1:] or MODELS
    for name in names:
        download(name)


if __name__ == "__main__":
    main()
