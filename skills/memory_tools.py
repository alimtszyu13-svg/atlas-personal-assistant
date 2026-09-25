"""Поиск по прошлым разговорам."""
from core.skills import skill
from core import memory


@skill("memory", read_only=True,
       description="Searches summaries of past conversations with the user by meaning. "
                   "Use for 'what did we discuss about X', 'remind me what I said about Y'.",
       params={"query": "What to look for in past conversations"})
def recall_conversations(query: str) -> str:
    memory.consolidate(force=True)          # учесть и текущий, ещё не пересказанный разговор
    items = memory.recall(query, k=5)
    if not items:
        return "Nothing relevant in past conversations."
    return "\n".join(f"({memory._ago(e)}) {s}" for e, s in items)