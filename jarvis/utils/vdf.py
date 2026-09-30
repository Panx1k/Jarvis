"""Мини-парсер текстового формата Valve KeyValues (VDF): appmanifest_*.acf, localconfig.vdf, loginusers.vdf."""
from __future__ import annotations

import re

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|(//[^\n]*)')


def loads(text: str) -> dict:
    """VDF → вложенные dict (ключи в нижнем регистре; при повторе ключа побеждает последнее значение)."""
    root: dict = {}
    stack = [root]
    key: str | None = None
    for m in _TOKEN.finditer(text):
        string, brace, comment = m.groups()
        if comment is not None:
            continue
        if brace == "{":
            child: dict = {}
            if key is not None:
                stack[-1][key.lower()] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = string.replace('\\\\', '\\').replace('\\"', '"')
        else:
            stack[-1][key.lower()] = string.replace('\\\\', '\\').replace('\\"', '"')
            key = None
    return root


def get(d: dict, *path: str, default=None):
    """Безопасный доступ по пути ключей (без учёта регистра)."""
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p.lower())
        if d is None:
            return default
    return d
