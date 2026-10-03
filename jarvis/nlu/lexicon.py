"""Словарь известных названий: приложения, игры, сайты, термины.

Источники: config/lexicon.json (его можно дополнять), приложения и сайты из настроек, установленные игры Steam.
match() — нечёткий поиск с учётом падежей («дискорда»), транслитерации («discord» ≈ «дискорд»), опечаток
распознавания («стем», «дискордд») и слов, разбитых на части («диск орд»). Если два разных названия подходят
одинаково — возвращаются оба, и выбирать между ними должен не словарь, а контекст или пользователь.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.config import CONFIG_DIR
from jarvis.utils.text import normalize, similarity, simplify, translit

log = logging.getLogger("jarvis.nlu")

LEXICON_FILE = CONFIG_DIR / "lexicon.json"
KINDS = ("apps", "games", "sites", "terms")
ENDINGS = ("ами", "ями", "ого", "ему", "ом", "ой", "ей", "ую", "юю", "ах", "ях", "а", "у", "е", "ы", "и", "ю", "я")


@dataclass(frozen=True)
class Term:
    name: str
    kind: str
    aliases: tuple[str, ...]


@dataclass
class Candidate:
    term: Term
    score: float
    alias: str
    span: str = ""

    @property
    def name(self) -> str:
        return self.term.name


@dataclass
class Match:
    """Результат поиска: лучший кандидат и остальные близкие (если есть неоднозначность)."""
    best: Candidate | None
    others: list[Candidate] = field(default_factory=list)

    @property
    def ambiguous(self) -> bool:
        return bool(self.others)

    @property
    def score(self) -> float:
        return self.best.score if self.best else 0.0


def stems(word: str) -> list[str]:
    """«дискорда» → [«дискорда», «дискорд»]: слово и оно же без падежного окончания."""
    out = [word]
    for end in ENDINGS:
        if len(word) - len(end) >= 3 and word.endswith(end):
            out.append(word[: -len(end)])
            break
    return out


def _key(text: str) -> str:
    return simplify(normalize(text))


class Lexicon:
    def __init__(self, settings=None, path: Path = LEXICON_FILE, extra: dict[str, dict[str, list[str]]] | None = None):
        self.settings = settings
        self.path = path
        self.extra = extra or {}
        self._terms: list[Term] = []
        self._index: dict[str, list[tuple[Term, str]]] = {}
        self._built_at = 0.0
        self._lock = threading.Lock()

    def terms(self) -> list[Term]:
        self._ensure()
        return list(self._terms)

    def names(self, kinds: tuple[str, ...] = KINDS) -> list[str]:
        return [t.name for t in self.terms() if t.kind in kinds]

    def invalidate(self) -> None:
        self._built_at = 0.0

    def _ensure(self) -> None:
        with self._lock:
            if self._terms and time.monotonic() - self._built_at < 600:
                return
            self._build()

    def _sources(self) -> dict[str, dict[str, list[str]]]:
        data: dict[str, dict[str, list[str]]] = {k: {} for k in KINDS}

        def add(kind: str, name: str, aliases) -> None:
            bucket = data.setdefault(kind, {})
            bucket.setdefault(name, [])
            bucket[name].extend(a for a in aliases if a)

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig")) if self.path.exists() else {}
        except (OSError, ValueError) as exc:
            log.warning("Словарь %s не прочитан: %s", self.path.name, exc)
            raw = {}
        for kind in KINDS:
            for name, aliases in (raw.get(kind) or {}).items():
                add(kind, name, aliases)
        if self.settings is not None:
            for key, cfg in (self.settings.get("apps", {}) or {}).items():
                add("apps", cfg.get("start_name") or key, [key] + list(cfg.get("aliases", [])))
            for key, cfg in (self.settings.get("sites", {}) or {}).items():
                if key.endswith("_web") or key == "steam_store":
                    continue
                add("sites", key, list(cfg.get("aliases", [])))
        try:
            from jarvis.tools.steam import GAME_ALIASES, library

            installed = {g.name for g in library()}
            for alias, target in GAME_ALIASES.items():
                name = next((n for n in installed if normalize(n).startswith(target)), None)
                if name:
                    add("games", name, [alias])
            for name in installed:
                add("games", name, [name])
        except Exception:
            pass
        for kind, items in self.extra.items():
            for name, aliases in items.items():
                add(kind, name, aliases)
        return data

    def _build(self) -> None:
        merged: dict[str, Term] = {}
        for kind, items in self._sources().items():
            for name, aliases in items.items():
                canon = self._canonical(merged, name, aliases)
                prev = merged.get(canon)
                all_aliases = list(dict.fromkeys(([*prev.aliases] if prev else []) + [name] + list(aliases)))
                merged[canon] = Term(prev.name if prev else name, prev.kind if prev else kind, tuple(all_aliases))
        self._terms = list(merged.values())
        self._index = {}
        for term in self._terms:
            for alias in term.aliases:
                self._index.setdefault(_key(alias), []).append((term, alias))
        self._built_at = time.monotonic()

    @staticmethod
    def _canonical(merged: dict[str, Term], name: str, aliases) -> str:
        """Одно и то же название из разных источников — один термин: «Google Chrome» из настроек и «Chrome» из
        словаря (где «google chrome» — псевдоним). Разные программы с общим псевдонимом НЕ склеиваются —
        это настоящая неоднозначность, и о ней надо спросить."""
        key = _key(name)
        alias_keys = {_key(a) for a in aliases}
        for canon, term in merged.items():
            if key == _key(term.name) or key in {_key(a) for a in term.aliases} or _key(term.name) in alias_keys:
                return canon
        return key or name

    def exact(self, phrase: str) -> Term | None:
        self._ensure()
        hits = {t.name: t for t, _ in self._index.get(_key(phrase), [])}
        if len(hits) == 1:
            return next(iter(hits.values()))
        for stem in stems(normalize(phrase).strip())[1:]:
            hits = {t.name: t for t, _ in self._index.get(_key(stem), [])}
            if len(hits) == 1:
                return next(iter(hits.values()))
        return None

    def match(self, phrase: str, kinds: tuple[str, ...] = KINDS, threshold: float = 0.84,
              margin: float = 0.04) -> Match:
        """Лучшее совпадение для фразы. threshold — минимальное сходство; margin — насколько лучший должен
        опережать следующего, чтобы считаться однозначным."""
        self._ensure()
        phrase = normalize(phrase).strip(" ,.!?«»\"'")
        if not phrase:
            return Match(None)
        variants = {phrase, phrase.replace(" ", "")}
        words = phrase.split()
        if len(words) == 1:
            variants.update(stems(words[0]))
        else:
            variants.add(" ".join(words[:-1] + [stems(words[-1])[-1]]))
        best_by_term: dict[str, Candidate] = {}
        for v in variants:
            key = _key(v)
            if not key:
                continue
            for term, alias in self._index.get(key, []):
                if term.kind in kinds:
                    self._keep(best_by_term, Candidate(term, 1.0, alias, phrase))
        if not best_by_term:
            for term in self._terms:
                if term.kind not in kinds:
                    continue
                for alias in term.aliases:
                    a = _key(alias)
                    if len(a) < 3:
                        continue
                    score = max(self._fuzzy(v, alias) for v in variants)
                    if score >= threshold - 0.1:
                        self._keep(best_by_term, Candidate(term, round(score, 3), alias, phrase))
        ranked = sorted(best_by_term.values(), key=lambda c: c.score, reverse=True)
        ranked = [c for c in ranked if c.score >= threshold]
        if not ranked:
            return Match(None)
        best = ranked[0]
        close = [c for c in ranked[1:] if best.score - c.score < margin]
        return Match(best, close)

    @staticmethod
    def _keep(acc: dict[str, Candidate], cand: Candidate) -> None:
        prev = acc.get(cand.term.name)
        if prev is None or cand.score > prev.score:
            acc[cand.term.name] = cand

    @staticmethod
    def _fuzzy(text: str, alias: str) -> float:
        """Сходство с поправкой на длину: у коротких слов одна ошибка значит больше («стол» ≠ «стим»)."""
        a, b = _key(text), _key(alias)
        if not a or not b:
            return 0.0
        score = similarity(text, alias)
        ta, tb = simplify(translit(text)), simplify(translit(alias))
        short, long_ = sorted((len(ta), len(tb)))
        if ta and tb and (ta.startswith(tb) or tb.startswith(ta)) and short >= 5 and short / long_ >= 0.75:
            score = max(score, 0.86 + 0.1 * short / long_)
        if min(len(a), len(b)) <= 4:
            score -= 0.06
        return max(0.0, min(1.0, score))

    def find_mentions(self, text: str, kinds: tuple[str, ...] = KINDS, threshold: float = 0.9) -> list[Candidate]:
        """Названия, упомянутые в тексте (для подсказок распознаванию и отладки). Только уверенные совпадения."""
        words = re.findall(r"[\w.+-]+", normalize(text))
        found: dict[str, Candidate] = {}
        i = 0
        while i < len(words):
            hit = None
            for n in (3, 2, 1):
                chunk = " ".join(words[i:i + n])
                if len(words[i:i + n]) < n or len(_key(chunk)) < 2:
                    continue
                m = self.match(chunk, kinds, threshold=threshold, margin=0.0)
                if m.best and not m.ambiguous and (m.best.score >= 1.0 or (n == 1 and len(_key(chunk)) >= 4)):
                    hit = (n, m.best)
                    break
            if hit:
                n, cand = hit
                cand.span = " ".join(words[i:i + n])
                found.setdefault(cand.name, cand)
                i += n
            else:
                i += 1
        return list(found.values())

    def vocabulary(self, limit: int = 120) -> list[str]:
        """Названия для подсказки распознаванию речи (Gemini Live, Google): сначала игры и приложения."""
        order = {"games": 0, "apps": 1, "sites": 2, "terms": 3}
        names = [t.name for t in sorted(self.terms(), key=lambda t: order.get(t.kind, 9))]
        return list(dict.fromkeys(["Jarvis", "Джарвис"] + names))[:limit]


_shared: Lexicon | None = None


def get(settings=None) -> Lexicon:
    global _shared
    if _shared is None:
        _shared = Lexicon(settings)
    elif settings is not None and _shared.settings is None:
        _shared.settings = settings
        _shared.invalidate()
    return _shared
