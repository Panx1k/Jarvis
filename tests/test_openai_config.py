"""Конфигурация OpenAI и безопасность — без обращения к API."""
from __future__ import annotations

import logging
import os
import re

import pytest

from jarvis.brain.hybrid import HybridBrain
from jarvis.brain.openai_brain import KEY_MISSING, MODEL_MISSING, OpenAIBrain
from jarvis.config import Settings
from jarvis.core.assistant import Runtime
from jarvis.core.context import DialogContext
from jarvis.tools.base import load_builtin_tools, registry
from jarvis.utils.secrets import RedactingFilter, redact

load_builtin_tools()


def rt():
    return Runtime(Settings(), DialogContext(), registry, apps=None)


def clear_keys(monkeypatch):
    for name in list(os.environ):
        if name.startswith("OPENAI_API_KEY"):
            monkeypatch.delenv(name, raising=False)


def test_missing_key_message(monkeypatch):
    clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_MODEL", "some-model")
    b = OpenAIBrain(rt())
    assert not b.available and b.error == KEY_MISSING == "OpenAI API key is not configured."
    monkeypatch.setenv("OPENAI_API_KEY", "your_api_key_here")
    assert OpenAIBrain(rt()).error == KEY_MISSING


def test_missing_model_message(monkeypatch):
    clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key-123456")
    monkeypatch.setenv("OPENAI_MODEL", "")
    assert OpenAIBrain(rt()).error == MODEL_MISSING


def test_startup_notice_and_fallback(monkeypatch):
    clear_keys(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("BRAIN_MODE", "llm")
    h = HybridBrain(rt())
    assert h.startup_notices()[0].startswith("OpenAI API key is not configured.")
    from jarvis.tools.base import ToolResult
    reply = h.respond("который час", lambda n, a: ToolResult(True, "Сейчас 12:00."))
    assert reply.text == "Сейчас 12:00."


def test_key_is_redacted_everywhere(monkeypatch):
    secret = "sk-proj-AbCdEf0123456789SECRET"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    assert secret not in redact(f"Incorrect API key provided: {secret}")
    assert "sk-ab" not in redact("Incorrect API key provided: sk-ab***********xyZ9")
    assert redact("task-manager-2024 desk-lamp") == "task-manager-2024 desk-lamp"
    record = logging.LogRecord("x", logging.WARNING, __file__, 1, "auth failed %s", (secret,), None)
    RedactingFilter().filter(record)
    assert secret not in record.getMessage()


def test_openai_schemas_valid():
    schemas = registry.openai_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert len(names) == len(set(names)) >= 40
    for s in schemas:
        assert s["type"] == "function"
        f = s["function"]
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", f["name"])
        assert f["parameters"]["type"] == "object"
        assert set(f["parameters"]["required"]) <= set(f["parameters"]["properties"])
    for needed in ("open_app", "close_app", "open_site", "search_web", "search_youtube", "play_youtube",
                   "pause_media", "resume_media", "set_volume", "vpn_on", "vpn_off", "open_folder", "search_files",
                   "type_text", "press_key", "take_screenshot"):
        assert needed in names


def test_no_key_in_source_tree():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pattern = re.compile(r"\bsk-[A-Za-z0-9]{20,}")
    for folder in ("jarvis", "plugins", "scripts"):
        for dirpath, _, files in os.walk(os.path.join(root, folder)):
            for f in files:
                if f.endswith(".py"):
                    with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                        assert not pattern.search(fh.read()), f
    with open(os.path.join(root, ".gitignore"), encoding="utf-8") as fh:
        rules = fh.read().split()
        assert ".env" in rules or "/.env" in rules


def test_search_files_and_open_by_number(tmp_path):
    from jarvis.tools.files import search_files

    (tmp_path / "sub").mkdir()
    (tmp_path / "отчёт_2026.pdf").write_text("x")
    (tmp_path / "sub" / "Отчет квартал.pdf").write_text("x")
    (tmp_path / "notes.txt").write_text("x")
    r = rt()
    res = search_files(r, "отчет", folder=str(tmp_path))
    assert res.ok and len(res.data["results"]) == 2
    assert r.dialog.last_results_source == "files" and r.dialog.last_results[0].source == "file"
    res = search_files(r, "*.txt", folder=str(tmp_path))
    assert res.ok and res.data["results"][0].endswith("notes.txt")
    assert not search_files(r, "несуществующий_файл_xyz", folder=str(tmp_path)).ok
