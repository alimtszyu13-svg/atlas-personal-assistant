"""
Экраны приложения на телефоне: дела, заметки, тренировка карточек.

Работают с теми же данными, что и голос: notes.py (atlas_data.json) и core/study.py (memory.db),
поэтому всё, что сделано на экране, видно и Atlas на компьютере (через общую память в облаке).

    POST /api/home                       → дела, заметки, колоды, серия занятий
    POST /api/todo/add {task}            /api/todo/done {i}      /api/todo/delete {i}
    POST /api/note/add {text}            /api/note/delete {i}
    POST /api/study/start {deck}         /api/study/answer {text}    /api/study/stop
    POST /api/transcribe (аудио)         → {text} — ответ на карточку голосом, без мозга
    POST /api/reminder/delete {id}
    POST /api/push/key → {key}   /api/push/subscribe {sub, name}   /api/push/test → {sent}

Номера i — с единицы, как в голосовых командах («отметь второе дело»).
"""
import threading

_lock = threading.Lock()          # экран и голос не правят один файл одновременно


def _notes():
    import notes
    return notes


def _study():
    from core import study
    return study


def _int(data, key="i") -> int:
    try:
        return int(data.get(key))
    except (TypeError, ValueError):
        return 0


def home(data=None) -> dict:
    n = _notes()._load()
    out = {"todos": [{"task": t.get("task", ""), "done": bool(t.get("done"))} for t in n.get("todos", [])],
           "notes": [str(x) for x in n.get("notes", [])], "decks": [], "streak": 0, "study": None, "reminders": []}
    try:
        import reminders
        out["reminders"] = reminders.upcoming()
    except Exception as e:
        print(f"[телефон] напоминания недоступны: {e}")
    try:
        st = _study().stats()
        out["decks"], out["streak"] = st.get("decks", []), st.get("streak", 0)
        out["week_accuracy"] = st.get("week_accuracy")
        out["study"] = _card_state()
    except Exception as e:                      # карточек может не быть — экраны всё равно открываются
        print(f"[телефон] карточки недоступны: {e}")
    return out


def _text(data, key, limit=500) -> str:
    return str((data or {}).get(key) or "").strip()[:limit]


def todo_add(data) -> dict:
    task = _text(data, "task")
    if task:
        with _lock:
            _notes().add_todo(task)
    return home()


def todo_done(data) -> dict:
    with _lock:
        _notes().complete_todo(_int(data))
    return home()


def todo_delete(data) -> dict:
    with _lock:
        _notes().delete_todo(_int(data))
    return home()


def note_add(data) -> dict:
    text = _text(data, "text", 2000)
    if text:
        with _lock:
            _notes().add_note(text)
    return home()


def note_delete(data) -> dict:
    with _lock:
        _notes().delete_note(_int(data))
    return home()


# ---------------------------------------------------------------------------
# Карточки
# ---------------------------------------------------------------------------
def _card_state():
    """Текущая карточка тренировки или None."""
    s = _study()
    if not s.active():
        return None
    ses = s._session
    card = s._card(ses["current"])
    if not card:
        return None
    return {"front": card["front"], "deck": card["deck"], "n": ses["asked"] + 1,
            "total": max(ses.get("limit") or 0, ses["asked"] + 1 + len(ses["queue"])),
            "correct": ses["correct"]}


def _split(said: str, state):
    """Ответ тренера = отзыв о прошлой карточке + вопрос следующей. Экрану нужен только отзыв."""
    if state:
        q = _study()._question(_study()._card(_study()._session["current"]))
        if q and said.endswith(q):
            return said[: -len(q)].strip()
    return said.strip()


def study_start(data) -> dict:
    said = _study().start(_text(data, "deck", 80), _int(data, "count") or 10)
    state = _card_state()
    return {"said": said, "feedback": "" if state else said, "card": state, "done": state is None}


def study_answer(data) -> dict:
    s = _study()
    if not s.active():
        return {"said": "", "feedback": "Тренировка уже закончилась.", "card": None, "done": True}
    before = s._card(s._session["current"])
    said = s.answer(_text(data, "text"))
    state = _card_state()
    return {"said": said, "feedback": _split(said, state), "card": state, "done": state is None,
            "answer": before["back"] if before else ""}


def study_stop(data=None) -> dict:
    said = _study().stop()
    return {"said": said, "feedback": said, "card": None, "done": True}


def reminder_delete(data) -> dict:
    import reminders
    reminders.delete(_text(data, "id", 40))
    return home()


# ---------------------------------------------------------------------------
# Уведомления
# ---------------------------------------------------------------------------
def push_key(data=None) -> dict:
    from core import push
    if not push.available():
        return {"key": "", "error": "Уведомления недоступны: на сервере нет библиотеки cryptography."}
    return {"key": push.public_key()}


def push_subscribe(data) -> dict:
    from core import push
    ok = push.subscribe((data or {}).get("sub"), _text(data, "name", 60))
    return {"ok": ok}


def push_test(data=None) -> dict:
    from core import push
    n = push.notify("Atlas", "Уведомления работают. Так будут приходить напоминания.", tag="atlas-test")
    return {"sent": n}


ROUTES = {
    "/api/home": home,
    "/api/todo/add": todo_add, "/api/todo/done": todo_done, "/api/todo/delete": todo_delete,
    "/api/note/add": note_add, "/api/note/delete": note_delete,
    "/api/study/start": study_start, "/api/study/answer": study_answer, "/api/study/stop": study_stop,
    "/api/reminder/delete": reminder_delete,
    "/api/push/key": push_key, "/api/push/subscribe": push_subscribe, "/api/push/test": push_test,
}
