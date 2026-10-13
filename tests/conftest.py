"""Тесты не трогают личные данные: история разговора не сохраняется, память JARVIS — во временной папке."""
from __future__ import annotations

import os

import pytest

os.environ["JARVIS_NO_PERSIST"] = "1"


@pytest.fixture(autouse=True, scope="session")
def _isolated_memory(tmp_path_factory):
    from jarvis.core import memory

    memory._shared = memory.Memory(tmp_path_factory.mktemp("memory") / "memory.json")
    yield
    memory._shared = None
