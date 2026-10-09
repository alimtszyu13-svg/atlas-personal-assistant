"""
Рабочие места: «сохрани это как Учёба» — и потом «открой Учёба» возвращает всё как было.

Снимок = открытые окна программ: сама программа (путь к exe), документ из заголовка окна
и, для браузера, адрес открытой страницы (по заголовку — из истории браузера за последний час).
Хранится в memory.db, таблица workspaces, только на этом компьютере.

    save("учёба")   → что запомнил
    open_("учёба")  → что открыл
    names()         → список
"""
import json
import os
import sqlite3
import subprocess
import threading
import time

from core import worklog

_lock = threading.Lock()
_IGNORE_EXE = {"explorer.exe", "python.exe", "pythonw.exe", "applicationframehost.exe", "textinputhost.exe",
               "shellexperiencehost.exe", "searchhost.exe", "systemsettings.exe", "lockapp.exe", "taskmgr.exe"}


def _connect(db: str = None):
    c = sqlite3.connect(db or worklog.DB, check_same_thread=False, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS workspaces (name TEXT PRIMARY KEY, saved REAL, items TEXT)")
    return c


def _key(name: str) -> str:
    return " ".join((name or "").lower().replace("ё", "е").split())


def windows() -> list:
    """Видимые окна программ: [{"exe": путь, "app": имя, "title": заголовок}] (Windows)."""
    import ctypes
    import ctypes.wintypes as wt
    import psutil
    u = ctypes.windll.user32
    out = []

    def cb(h, _):
        if not u.IsWindowVisible(h) or u.GetWindow(h, 4):          # 4 = GW_OWNER: всплывающие окна пропускаем
            return True
        n = u.GetWindowTextLengthW(h)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, buf, n + 1)
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        try:
            p = psutil.Process(pid.value)
            out.append({"exe": p.exe(), "app": p.name(), "title": buf.value})
        except Exception:
            pass
        return True
    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return out


def snapshot(wins: list, history=None) -> list:
    """Окна → что открыть потом: программы, документы, страницы браузера."""
    items, seen = [], set()
    recent = None
    for w in wins:
        exe, title = w.get("exe") or "", w.get("title") or ""
        name = os.path.basename(exe).lower()
        if not exe or name in _IGNORE_EXE or (title.strip().upper() == "ATLAS"):
            continue
        if worklog._PRIVATE.search(title):
            continue
        base = name[:-4] if name.endswith(".exe") else name
        if any(b in base for b in worklog._BROWSERS):
            if recent is None:
                now = time.time()
                recent = (history or worklog.browser_history)(now - 3600, now)
            page = title.rsplit(" - ", 1)[0].strip()
            url = next((r[3] for r in recent if r[2] and r[2].strip() == page), None)
            if url and ("url", url) not in seen:
                seen.add(("url", url))
                items.append({"kind": "url", "url": url, "label": page})
                continue
        doc = worklog.doc_of(title)
        if doc and ("doc", doc) not in seen:
            seen.add(("doc", doc))
            items.append({"kind": "doc", "doc": doc, "exe": exe, "label": doc})
        elif ("app", exe.lower()) not in seen and not doc:
            seen.add(("app", exe.lower()))
            items.append({"kind": "app", "exe": exe, "label": base.capitalize()})
    return items


def save(name: str, wins: list = None, history=None, db: str = None) -> str:
    if not _key(name):
        return "Say what to call this workspace, e.g. 'save this as Study'."
    items = snapshot(wins if wins is not None else windows(), history)
    if not items:
        return "Nothing to save: no program windows are open."
    with _lock:
        c = _connect(db)
        try:
            c.execute("INSERT OR REPLACE INTO workspaces (name, saved, items) VALUES (?, ?, ?)",
                      (_key(name), time.time(), json.dumps(items, ensure_ascii=False)))
            c.commit()
        finally:
            c.close()
    return f"Saved workspace '{name}': " + ", ".join(i["label"] for i in items[:8]) + \
        (f" and {len(items) - 8} more" if len(items) > 8 else "") + "."


def names(db: str = None) -> list:
    with _lock:
        c = _connect(db)
        try:
            return [r[0] for r in c.execute("SELECT name FROM workspaces ORDER BY saved DESC")]
        finally:
            c.close()


def _running_exes() -> set:
    try:
        import psutil
        return {(p.info.get("exe") or "").lower() for p in psutil.process_iter(["exe"])}
    except Exception:
        return set()


def open_(name: str, db: str = None, launcher=None, running: set = None) -> str:
    key = _key(name)
    with _lock:
        c = _connect(db)
        try:
            row = c.execute("SELECT items FROM workspaces WHERE name=?", (key,)).fetchone()
            if not row:                                    # «учебу» ≈ «учёба»: по началу слова
                row = next(((r[1],) for r in c.execute("SELECT name, items FROM workspaces")
                            if r[0].startswith(key[:4]) or key.startswith(r[0][:4])), None)
        finally:
            c.close()
    if not row:
        have = names(db)
        return f"No workspace '{name}'." + (f" Saved: {', '.join(have)}." if have else " Say 'save this as …' first.")
    items = json.loads(row[0])
    launch = launcher or _launch
    running = running if running is not None else _running_exes()
    done, missing = [], []
    for it in items:
        try:
            if it["kind"] == "app" and it["exe"].lower() in running:
                done.append(it["label"])                   # уже открыта — не дублируем
                continue
            if launch(it):
                done.append(it["label"])
            else:
                missing.append(it["label"])
        except Exception:
            missing.append(it["label"])
    text = f"Opened workspace '{name}': " + ", ".join(done[:8]) + "." if done else f"Couldn't open '{name}'."
    if missing:
        text += " Not found: " + ", ".join(missing[:5]) + "."
    return text


def _launch(it: dict) -> bool:
    if it["kind"] == "url":
        import webbrowser
        return bool(webbrowser.open(it["url"]))
    if it["kind"] == "doc":
        return bool(worklog._open_doc(it["doc"]))
    if os.path.exists(it["exe"]):
        subprocess.Popen([it["exe"]], close_fds=True)
        return True
    return False


def delete(name: str, db: str = None) -> bool:
    with _lock:
        c = _connect(db)
        try:
            n = c.execute("DELETE FROM workspaces WHERE name=?", (_key(name),)).rowcount
            c.commit()
            return n > 0
        finally:
            c.close()
