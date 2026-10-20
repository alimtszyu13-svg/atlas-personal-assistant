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
    POST /api/reminder/add {text, when}   — when: «через 20 минут», «18:30», «2026-10-21 09:00»
    POST /api/brief/text                  → {text} — сводка дня для карточки на главном экране
    POST /api/pc/info                     → {online, local} — компьютер на связи?
    POST /api/pc/cmd {cmd}                — кнопки пульта: громкость, музыка, блокировка (список PC_CMDS)
    POST /api/pc/open {what}              — «открой …» на компьютере
    POST /api/pc/screen                   → {url} — снимок экрана компьютера
    POST /api/files/search {q}            → {files: [{name, folder, ext, size, snippet, url}]} — файлы компьютера

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


# ---------------------------------------------------------------------------
# Утренняя сводка
# ---------------------------------------------------------------------------
def brief_get(data=None) -> dict:
    from core import briefing
    return briefing.settings()


def brief_set(data) -> dict:
    from core import briefing
    on = (data or {}).get("on")
    return briefing.set_settings(on=None if on is None else bool(on), at=_text(data, "time", 5) or None)


def brief_now(data=None) -> dict:
    from core import briefing
    return briefing.send_now(mark=False)


# ---------------------------------------------------------------------------
# Напоминания с экрана
# ---------------------------------------------------------------------------
def reminder_add(data) -> dict:
    import reminders
    text, when = _text(data, "text", 200), _text(data, "when", 40)
    if not when:
        return {"ok": False, "message": "Когда напомнить?", **home()}
    msg = reminders.set_reminder(text or "Напоминание", when)
    ok = msg.startswith("Reminder set")
    if not ok:
        msg = ("Не понял время. Например: «через 20 минут», «18:30» или «21.10 09:00»." if "understand" in msg
               else "Это время уже прошло." if "passed" in msg else msg)
    return {"ok": ok, "message": "Напомню." if ok else msg, **home()}


def brief_text(data=None) -> dict:
    from core import briefing
    return {"text": briefing.build()}


# ---------------------------------------------------------------------------
# Пульт компьютера и его файлы
# ---------------------------------------------------------------------------
def _srv():
    from phone import server
    return server


PC_CMDS = {
    "vol_down": "сделай тише", "mute": "выключи звук", "vol_up": "сделай громче",
    "prev": "предыдущий трек", "play": "пауза", "next": "следующий трек",
    "lock": "заблокируй компьютер", "unmute": "включи звук", "stop": "стоп",
}


def pc_info(data=None) -> dict:
    s = _srv()
    if not s.PC_HUB:
        return {"online": True, "local": True}
    try:
        from core import pc_link
        return {"online": bool(pc_link.online()), "local": False}
    except Exception:
        return {"online": False, "local": False}


def pc_cmd(data) -> dict:
    cmd = _text(data, "cmd", 20)
    if cmd not in PC_CMDS:
        return {"ok": False, "text": "Такой кнопки нет."}
    reply = _srv().handle_text(PC_CMDS[cmd], log=False)
    return {"ok": True, "text": _clean(reply)}


def pc_open(data) -> dict:
    what = _text(data, "what", 200)
    if not what:
        return {"ok": False, "text": "Что открыть?"}
    reply = _srv().handle_text(f"открой на компьютере {what}", log=False)
    return {"ok": True, "text": _clean(reply)}


def pc_screen(data=None) -> dict:
    s = _srv()
    if s.PC_HUB:                                   # облако: просим компьютер прислать снимок
        reply = s.handle_text("Сделай снимок экрана компьютера и пришли его мне на телефон", log=False)
        return {"ok": True, "text": _clean(reply)}
    import os
    import tempfile
    import time
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=False)
        img.thumbnail((1920, 1920))
        path = os.path.join(tempfile.gettempdir(), f"atlas_screen_{int(time.time())}.jpg")
        img.convert("RGB").save(path, "JPEG", quality=82)
    except Exception as e:
        return {"ok": False, "text": f"Не получилось снять экран: {e}"}
    fid = s.share_file(path, name="Экран компьютера.jpg", temp=True)
    return {"ok": True, "url": "/api/file/" + fid}


def files_search(data) -> dict:
    import os
    import time
    q = _text(data, "q", 200)
    if not q:
        return {"files": []}
    s = _srv()
    if s.PC_HUB:
        return {"files": [], "message": "Поиск файлов работает, когда компьютер включён и телефон подключён к нему. "
                                         "Или скажи Atlas: «пришли мне файл … на телефон»."}
    try:
        import file_search
        hits = file_search._hybrid(q, k=8)
    except Exception as e:
        return {"files": [], "message": f"Поиск недоступен: {e}"}
    out = []
    for path, text in hits:
        if not os.path.isfile(path):
            continue
        try:
            st = os.stat(path)
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = 0, 0
        folder = os.path.basename(os.path.dirname(path)) or os.path.dirname(path)
        snippet = " ".join((text or "").split())[:140]
        out.append({"name": os.path.basename(path), "folder": folder, "ext": os.path.splitext(path)[1].lower()[1:],
                    "size": size, "date": time.strftime("%d.%m.%Y", time.localtime(mtime)) if mtime else "",
                    "snippet": snippet, "url": "/api/file/" + s.share_file(path)})
    return {"files": out, "message": "" if out else "Ничего не нашёл. Попробуй другими словами."}


def _clean(reply) -> str:
    try:
        from core import emotions, filler
        return emotions.strip(filler.clean(str(reply or "")))
    except Exception:
        return str(reply or "")


ROUTES = {
    "/api/home": home,
    "/api/todo/add": todo_add, "/api/todo/done": todo_done, "/api/todo/delete": todo_delete,
    "/api/note/add": note_add, "/api/note/delete": note_delete,
    "/api/study/start": study_start, "/api/study/answer": study_answer, "/api/study/stop": study_stop,
    "/api/reminder/delete": reminder_delete,
    "/api/push/key": push_key, "/api/push/subscribe": push_subscribe, "/api/push/test": push_test,
    "/api/brief": brief_get, "/api/brief/set": brief_set, "/api/brief/now": brief_now,
    "/api/reminder/add": reminder_add, "/api/brief/text": brief_text,
    "/api/pc/info": pc_info, "/api/pc/cmd": pc_cmd, "/api/pc/open": pc_open, "/api/pc/screen": pc_screen,
    "/api/files/search": files_search,
}
