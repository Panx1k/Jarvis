"""Память голосом: «запомни, что …», «что ты обо мне знаешь?», «что ты помнишь про работу?», «забудь про …»."""
from __future__ import annotations

from jarvis.core import memory
from jarvis.tools.base import ToolResult, tool


@tool("memory_remember", "Запомнить факт о пользователе надолго (предпочтения, имена, адреса почты знакомых, "
      "планы). Только когда пользователь сам просит запомнить или явно сообщает о себе важное. Пароли и номера "
      "карт не запоминаются.",
      params={"fact": {"type": "string", "description": "Факт одной фразой, от третьего лица или как сказал "
                                                         "пользователь"}}, required=["fact"],
      announce="Запоминаю", category="memory",
      patterns=[r"^(?:запомни|запиши себе|запомнишь)(?:\s*[,:]\s*|\s+)(?:что\s+|это\s*[,:]?\s*)?(?P<fact>(?!пресет|"
                r"режим|сценарий).+)$"])
def memory_remember(ctx, fact: str) -> ToolResult:
    ok, message = memory.get().add(fact)
    return ToolResult(ok, message, {"fact": fact} if ok else {})


@tool("memory_recall", "Что JARVIS помнит о пользователе (всё или по теме query).",
      params={"query": {"type": "string", "description": "Тема (необязательно)"}},
      announce="Вспоминаю", category="memory",
      patterns=[r"^что\s+ты\s+(?:обо\s+мне|про\s+меня|о\s+мне)\s+(?:знаешь|помнишь)$",
                r"^что\s+ты\s+(?:помнишь|знаешь)\s+(?:про|о|об)\s+(?P<query>.+)$",
                r"^что\s+ты\s+(?:запомнил|помнишь)$"])
def memory_recall(ctx, query: str = "") -> ToolResult:
    facts = memory.get().search(query, limit=10) if query else memory.get().search("", limit=15)
    if not facts:
        return ToolResult(True, "Ничего такого не помню." if query else
                          "Пока ничего не запоминал. Скажите, например: «запомни, что я люблю рок».", {"facts": []})
    return ToolResult(True, "Помню: " + "; ".join(f["text"] for f in facts) + ".", {"facts": [f["text"] for f in facts]})


@tool("memory_forget", "Забыть факты по теме (query). query=«всё» — очистить всю память (с подтверждением).",
      params={"query": {"type": "string"}}, required=["query"],
      dangerous=lambda a: (a.get("query") or "").strip().lower() in ("всё", "все", "all", "всю память"),
      confirm="Стереть всё, что я о вас помню?", announce="Забываю", category="memory",
      patterns=[r"^забудь(?:\s*[,:]\s*|\s+)(?:что\s+|про\s+|о\s+|об\s+)?(?P<query>.{3,})$",
                r"^(?:очисти|сотри)\s+(?:свою\s+)?(?P<query>всю\s+память)$"])
def memory_forget(ctx, query: str) -> ToolResult:
    if query.strip().lower() in ("всё", "все", "all", "всю память"):
        n = memory.get().clear()
        return ToolResult(True, f"Память очищена ({n} записей).")
    removed = memory.get().forget(query)
    if not removed:
        return ToolResult(False, "Такого я не помню.")
    return ToolResult(True, "Забыл: " + "; ".join(removed) + ".", {"removed": removed})
