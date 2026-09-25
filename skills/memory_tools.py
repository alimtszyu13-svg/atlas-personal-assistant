"""Память: поиск по прошлому и запись фактов в граф знаний."""
from core.skills import skill
from core import memory


@skill("memory", read_only=True,
       description="Searches what Atlas knows about the user: facts and past conversations. "
                   "Use for 'do you remember', 'what did I tell you about X', 'what did we discuss'.",
       params={"query": "What to look for"})
def recall_conversations(query: str) -> str:
    memory.consolidate(force=True)          # учесть и текущий, ещё не пересказанный разговор
    facts = memory.graph_facts(query or "me", limit=15)
    items = memory.recall(query, k=5, min_sim=memory.MEMORY_INTENT_MIN_SIM)
    out = []
    if facts:
        out.append("Facts: " + "; ".join(facts))
    out += [f"({memory._ago(e)}) {s}" for e, s in items]
    return "\n".join(out) or "Nothing relevant in memory."


@skill("memory",
       description="Stores or updates a durable fact as a relation. Call it right away when the "
                   "user states or CHANGES a fact ('my exam moved to December'). subject 'User' "
                   "means the user; relation is short snake_case (exam_date, weak_area, likes, "
                   "lives_in, goal, works_on). single=true replaces the previous value.",
       params={"subject": "Who or what the fact is about ('User' for the user)",
               "relation": "Short snake_case relation, e.g. exam_date",
               "value": "The value, e.g. 'December 2026'",
               "single": "True if only one value can be true at a time (dates, current city); "
                         "false for lists (likes)"})
def remember_fact(subject: str, relation: str, value: str, single: bool = True) -> str:
    memory.upsert_fact(subject, relation, value, single=single, conf=0.95)
    return f"Remembered: {subject} — {relation} → {value}"