"""
Память компьютера — инструменты Atlas (данные — core/worklog.py, только на этом компьютере).

«Что я делал вчера?», «где тот сайт про линейные уравнения?», «открой эссе, которое я писал
во вторник», «на чём я остановился?», «забудь, что я делал сегодня».
"""
from core import workspaces, worklog
from core.skills import skill

try:
    import tool_router
    tool_router.TRIGGERS["worklog"] = (
        "что я делал", "чем я занимался", "что я открывал", "над чем я работал", "на чём я остановился",
        "на чем я остановился", "продолжим", "продолжить работу", "где тот", "где та", "тот сайт", "тот файл",
        "открывал", "смотрел", "работал над", "читал", "верни", "забудь, что я делал",
        "what did i do", "what was i doing", "where was i", "that site", "that file", "i was working on",
        "рабочее место", "рабочие места", "сохрани это как", "сохрани как", "режим", "workspace",
    )
except Exception:
    pass


@skill("worklog", read_only=True,
       description="What the user did on THIS computer in a period: time per program, documents worked on, "
                   "websites visited. period: 'today', 'yesterday', 'week', a weekday ('wednesday'/'среда'), "
                   "'3 days' or 'YYYY-MM-DD'.",
       params={"period": "today | yesterday | week | weekday | 'N days' | YYYY-MM-DD"})
def what_did_i_do(period: str = "today") -> str:
    return worklog.summary(period)


@skill("worklog", read_only=True,
       description="Finds when and where the user saw or worked on something on this computer — a document, "
                   "a window, a website from browser history — by words from its name or topic. "
                   "Use for 'where is that site about…', 'when did I work on…', 'what was that article…'.",
       params={"query": "words from the document or page name / topic",
               "period": "optional: today | yesterday | week | weekday | YYYY-MM-DD"})
def find_past_activity(query: str, period: str = "") -> str:
    return worklog.search_text(query, period)


@skill("worklog",
       description="Opens again a document or website the user worked on before, found by words from its name or "
                   "topic in the computer's memory: 'open the essay I wrote on Tuesday', 'open that site about…'.",
       params={"query": "words from the document or page name / topic"})
def reopen_from_history(query: str) -> str:
    return worklog.reopen(query)


@skill("worklog",
       description="Continues where the user left off: reopens the document they worked on last before Atlas "
                   "started. Use for 'where was I', 'let's continue', 'на чём я остановился', 'продолжим'.")
def continue_last_work() -> str:
    w = worklog.last_work()
    if not w:
        return "No document from the last session found in the computer's memory."
    path = worklog._open_doc(w["doc"])
    if not path:
        return f"Last time you worked on {w['doc']} ({w['app']}, {w['when']}), but the file wasn't found."
    return f"Opened {w['doc']} — you worked on it {w['when']} in {w['app']}."


@skill("worklog",
       description="Deletes what the computer's memory recorded for a period (privacy): 'forget what I did today'.",
       params={"period": "today | yesterday | week | YYYY-MM-DD"})
def forget_activity(period: str = "today") -> str:
    n = worklog.forget(period)
    return f"Deleted {n} records from the computer's memory for that period."


@skill("worklog",
       description="Saves the windows open right now (programs, documents, browser pages) as a named workspace, "
                   "to bring it all back later: 'save this as Study', 'запомни это рабочее место как учёба'.",
       params={"name": "workspace name, e.g. 'учёба', 'Atlas', 'игры'"})
def save_workspace(name: str) -> str:
    return workspaces.save(name)


@skill("worklog",
       description="Opens a saved workspace: launches its programs and reopens its documents and browser pages. "
                   "Use for 'open workspace Study', 'режим учёба', 'открой рабочее место Atlas'.",
       params={"name": "the workspace name"})
def open_workspace(name: str) -> str:
    return workspaces.open_(name)


@skill("worklog", read_only=True, description="Lists the saved workspaces.")
def list_workspaces() -> str:
    n = workspaces.names()
    return ("Saved workspaces: " + ", ".join(n) + ".") if n else "No saved workspaces yet."
