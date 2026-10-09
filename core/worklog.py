"""
Память компьютера: что ты делал, в каких документах и на каких сайтах.

Atlas раз в 15 секунд смотрит на активное окно и записывает программу и заголовок окна
(в заголовке обычно имя документа: «эссе.docx - Word», «● main.py - Visual Studio Code»).
Сайты берутся из истории браузера (Chrome, Edge, Brave, Opera, Яндекс, Firefox) — только при вопросе.

Всё хранится только на этом компьютере (memory.db, таблица work_log) и не уходит в облако.
Заголовки окон инкогнито, менеджеров паролей и банков не записываются. Хранится 30 дней.
WORKLOG=off в .env — выключить совсем; «забудь, что я делал сегодня» — стереть.

    summary("yesterday")          → что делал: программы, документы, сайты
    search("эссе про климат")     → когда и где это было
    reopen("эссе про климат")     → открыть снова (файл или сайт)
    last_work()                   → на чём остановился в прошлый раз
"""
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "memory.db")
SAMPLE_S = 15
IDLE_S = 300
MERGE_GAP_S = 90            # то же окно с перерывом меньше — одна запись
KEEP_DAYS = 30
_lock = threading.Lock()
_started = {"on": False}
SESSION_START = time.time()

# Не записываем заголовок (только программу): приватные окна, пароли, банки
_PRIVATE = re.compile(r"inprivate|incognito|инкогнито|private brows|приватн|password|пароль|1password|keepass|"
                      r"bitwarden|lastpass|bank|банк|mbank|оптима|элсом|o!dengi|demir|kicb|visa|mastercard", re.I)
_SKIP_APPS = {"atlas", "python", "pythonw", "lockapp", "searchhost", "shellexperiencehost", "startmenuexperiencehost"}
_BROWSERS = {"chrome", "edge", "msedge", "firefox", "opera", "brave", "yandex", "browser", "vivaldi"}
_FILE = re.compile(r"([^\\/:*?\"<>|●•]+?\.(?:docx?|xlsx?|pptx?|pdf|txt|md|py|js|ts|html?|css|json|csv|"
                   r"ipynb|png|jpe?g|psd|fig|odt|rtf|java|cpp|c|cs|go|rs|kt|swift))\b", re.I)


def enabled() -> bool:
    return (os.getenv("WORKLOG") or "on").strip().lower() not in ("off", "0", "false", "no", "нет")


def _connect(db: str = None):
    c = sqlite3.connect(db or DB, check_same_thread=False, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS work_log (id INTEGER PRIMARY KEY, start REAL, end REAL, app TEXT, "
              "title TEXT, doc TEXT)")
    c.execute("CREATE INDEX IF NOT EXISTS work_log_start ON work_log(start)")
    return c


# =============================================================================
# Запись
# =============================================================================
def doc_of(title: str) -> str:
    """Имя документа из заголовка окна: «● эссе.docx - Word» → «эссе.docx»."""
    m = _FILE.search(title or "")
    return m.group(1).strip(" -—–●") if m else ""


def clean(app: str, title: str):
    """→ (app, title, doc) для записи или None, если окно не записываем."""
    app = (app or "").strip()
    if not app or app.lower() in _SKIP_APPS:
        return None
    title = re.sub(r"\s+", " ", title or "").strip()[:300]
    if _PRIVATE.search(title):
        title = ""
    return app, title, doc_of(title)


def record(app: str, title: str, now: float = None, db: str = None) -> None:
    """Одно наблюдение активного окна. То же окно подряд — продлеваем запись, не дублируем."""
    item = clean(app, title)
    if not item:
        return
    app, title, doc = item
    now = now or time.time()
    with _lock:
        c = _connect(db)
        try:
            last = c.execute("SELECT id, end, app, title FROM work_log ORDER BY id DESC LIMIT 1").fetchone()
            if last and last[2] == app and last[3] == title and now - last[1] <= MERGE_GAP_S:
                c.execute("UPDATE work_log SET end=? WHERE id=?", (now, last[0]))
            else:
                c.execute("INSERT INTO work_log (start, end, app, title, doc) VALUES (?, ?, ?, ?, ?)",
                          (now, now, app, title, doc))
            c.commit()
        finally:
            c.close()


def _foreground():
    """(программа, заголовок) активного окна Windows."""
    import ctypes
    import ctypes.wintypes as wt
    import psutil
    u = ctypes.windll.user32
    h = u.GetForegroundWindow()
    if not h:
        return "", ""
    n = u.GetWindowTextLengthW(h)
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(h, buf, n + 1)
    pid = wt.DWORD()
    u.GetWindowThreadProcessId(h, ctypes.byref(pid))
    try:
        exe = psutil.Process(pid.value).name()
    except Exception:
        return "", ""
    title = buf.value
    if exe.lower() in ("python.exe", "pythonw.exe"):
        return ("Atlas" if title.strip().upper() == "ATLAS" else "Python"), ""
    try:
        from core.day_report import _PRETTY
        app = _PRETTY.get(exe.lower())
    except Exception:
        app = None
    return app or (exe[:-4] if exe.lower().endswith(".exe") else exe), title


def _idle() -> float:
    try:
        from core.day_report import _idle_seconds
        return _idle_seconds()
    except Exception:
        return 0.0


def cleanup(db: str = None, keep_days: int = KEEP_DAYS) -> int:
    with _lock:
        c = _connect(db)
        try:
            n = c.execute("DELETE FROM work_log WHERE end < ?", (time.time() - keep_days * 86400,)).rowcount
            c.commit()
            return n
        finally:
            c.close()


def start() -> bool:
    if _started["on"] or not enabled():
        if not enabled():
            print("[память ПК] выключена (WORKLOG=off)")
        return False
    _started["on"] = True

    def loop():
        last_clean = 0.0
        while True:
            time.sleep(SAMPLE_S)
            try:
                if _idle() > IDLE_S:
                    continue
                app, title = _foreground()
                record(app, title)
                if time.time() - last_clean > 86400:
                    cleanup()
                    last_clean = time.time()
            except Exception as e:
                print(f"[память ПК] {e}")
                time.sleep(120)
    threading.Thread(target=loop, daemon=True, name="worklog").start()
    print("[память ПК] включена: программы, документы и заголовки окон — только на этом компьютере")
    return True


# =============================================================================
# Периоды
# =============================================================================
_WEEKDAYS = {"понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3, "пятниц": 4, "суббот": 5, "воскресен": 6,
             "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}


def period_range(period: str = "today", now: datetime = None):
    """«сегодня», «вчера», «неделя», «среда», «2026-10-07», «3 дня» → (начало, конец, подпись)."""
    now = now or datetime.now()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    p = (period or "today").strip().lower()
    if p in ("", "today", "сегодня"):
        return day0.timestamp(), now.timestamp(), "сегодня"
    if p in ("yesterday", "вчера"):
        d = day0 - timedelta(days=1)
        return d.timestamp(), day0.timestamp(), "вчера"
    if p in ("week", "неделя", "неделю", "за неделю", "this week"):
        return (day0 - timedelta(days=6)).timestamp(), now.timestamp(), "за неделю"
    m = re.match(r"(\d+)\s*(?:дн|day)", p)
    if m:
        return (day0 - timedelta(days=int(m.group(1)) - 1)).timestamp(), now.timestamp(), f"за {m.group(1)} дн."
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", p)
    if m:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d.timestamp(), (d + timedelta(days=1)).timestamp(), d.strftime("%d.%m")
    for word, wd in _WEEKDAYS.items():
        if word in p:
            back = (now.weekday() - wd) % 7 or 7
            d = day0 - timedelta(days=back)
            return d.timestamp(), (d + timedelta(days=1)).timestamp(), d.strftime("%d.%m")
    return (day0 - timedelta(days=6)).timestamp(), now.timestamp(), "за неделю"


def _rows(t0: float, t1: float, db: str = None) -> list:
    with _lock:
        c = _connect(db)
        try:
            return c.execute("SELECT start, end, app, title, doc FROM work_log WHERE end >= ? AND start <= ? "
                             "ORDER BY start", (t0, t1)).fetchall()
        finally:
            c.close()


def _mins(sec: float) -> str:
    m = int(round(sec / 60))
    return f"{m // 60} ч {m % 60} мин" if m >= 60 else f"{max(m, 1)} мин"


def _when(ts: float) -> str:
    d = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    day = "сегодня" if d.date() == today else "вчера" if d.date() == today - timedelta(days=1) else d.strftime("%d.%m")
    return f"{day} {d:%H:%M}"


# =============================================================================
# История браузеров (только чтение копии — сам браузер не трогаем)
# =============================================================================
def _browser_dbs() -> list:
    local, roaming = os.getenv("LOCALAPPDATA") or "", os.getenv("APPDATA") or ""
    found = []
    for name, base in (("Chrome", os.path.join(local, "Google", "Chrome", "User Data")),
                       ("Edge", os.path.join(local, "Microsoft", "Edge", "User Data")),
                       ("Brave", os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data")),
                       ("Яндекс", os.path.join(local, "Yandex", "YandexBrowser", "User Data")),
                       ("Opera", os.path.join(roaming, "Opera Software", "Opera Stable"))):
        if not os.path.isdir(base):
            continue
        for prof in ("", "Default", "Profile 1", "Profile 2"):
            p = os.path.join(base, prof, "History") if prof else os.path.join(base, "History")
            if os.path.isfile(p):
                found.append((name, p, "chromium"))
    ff = os.path.join(roaming, "Mozilla", "Firefox", "Profiles")
    if os.path.isdir(ff):
        for prof in os.listdir(ff):
            p = os.path.join(ff, prof, "places.sqlite")
            if os.path.isfile(p):
                found.append(("Firefox", p, "firefox"))
    return found


def browser_history(t0: float, t1: float, limit: int = 3000, dbs: list = None) -> list:
    """[(время, браузер, заголовок, адрес)] за период, новые сверху."""
    out = []
    for name, path, kind in (dbs if dbs is not None else _browser_dbs()):
        tmp = os.path.join(tempfile.mkdtemp(prefix="atlas_hist_"), "h.sqlite")
        try:
            shutil.copy2(path, tmp)                       # браузер держит файл открытым — читаем копию
            c = sqlite3.connect(tmp)
            if kind == "chromium":                        # микросекунды с 1601 года
                base = 11644473600 * 1_000_000
                rows = c.execute("SELECT v.visit_time, u.title, u.url FROM visits v JOIN urls u ON u.id = v.url "
                                 "WHERE v.visit_time BETWEEN ? AND ? ORDER BY v.visit_time DESC LIMIT ?",
                                 (int(t0 * 1e6) + base, int(t1 * 1e6) + base, limit)).fetchall()
                out += [((t - base) / 1e6, name, ti or "", url) for t, ti, url in rows]
            else:                                         # Firefox: микросекунды с 1970
                rows = c.execute("SELECT v.visit_date, p.title, p.url FROM moz_historyvisits v JOIN moz_places p "
                                 "ON p.id = v.place_id WHERE v.visit_date BETWEEN ? AND ? "
                                 "ORDER BY v.visit_date DESC LIMIT ?", (int(t0 * 1e6), int(t1 * 1e6), limit)).fetchall()
                out += [(t / 1e6, name, ti or "", url) for t, ti, url in rows]
            c.close()
        except Exception as e:
            print(f"[память ПК] история {name}: {e}")
        finally:
            shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)
    out = [r for r in out if r[3].startswith("http") and not _PRIVATE.search(r[2] + " " + r[3])]
    return sorted(out, key=lambda r: -r[0])


def _site(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1) if m else url


# =============================================================================
# Ответы
# =============================================================================
def summary(period: str = "today", db: str = None, history=None) -> str:
    t0, t1, label = period_range(period)
    rows = _rows(t0, t1, db)
    if not rows:
        return f"Nothing recorded {label} yet (Atlas records only while it is running)."
    apps, docs = {}, {}
    for s, e, app, title, doc in rows:
        dur = max(e - s, SAMPLE_S)
        apps[app] = apps.get(app, 0) + dur
        if doc:
            k = (doc, app)
            docs[k] = docs.get(k, 0) + dur
    parts = [f"{label.capitalize()}: за компьютером {_mins(sum(apps.values()))}."]
    parts.append("Программы: " + ", ".join(f"{a} — {_mins(t)}" for a, t in sorted(apps.items(), key=lambda x: -x[1])[:5]) + ".")
    if docs:
        parts.append("Документы: " + "; ".join(f"{d} ({a}, {_mins(t)})" for (d, a), t in
                                              sorted(docs.items(), key=lambda x: -x[1])[:6]) + ".")
    hist = history(t0, t1) if history else browser_history(t0, t1)
    if hist:
        sites = {}
        for _, _, title, url in hist:
            sites[_site(url)] = sites.get(_site(url), 0) + 1
        parts.append("Сайты: " + ", ".join(s for s, _ in sorted(sites.items(), key=lambda x: -x[1])[:6]) + ".")
    return " ".join(parts)


def _words(q: str) -> list:
    stop = {"где", "что", "тот", "та", "то", "который", "которую", "которое", "про", "о", "об", "я", "мой", "моя",
            "мне", "найди", "открой", "верни", "сайт", "файл", "документ", "the", "a", "about", "my", "find", "open"}
    return [w[:5] if len(w) > 5 else w for w in re.findall(r"[\w.]+", (q or "").lower()) if w not in stop and len(w) > 1]


def _score(words: list, text: str) -> int:
    t = (text or "").lower()
    return sum(1 for w in words if w in t)


def search(query: str, period: str = "", db: str = None, history=None, limit: int = 8) -> list:
    """Совпадения в окнах и истории браузеров → [{"kind","when","label","doc"|"url","score","ts"}]."""
    words = _words(query)
    if not words:
        return []
    t0, t1, _ = period_range(period) if period else (time.time() - KEEP_DAYS * 86400, time.time(), "")
    found = {}
    for s, e, app, title, doc in _rows(t0, t1, db):
        sc = _score(words, f"{title} {app}")
        if not sc:
            continue
        key = ("doc", doc or title, app)
        it = found.setdefault(key, {"kind": "doc" if doc else "window", "label": f"{app}: {doc or title}",
                                    "doc": doc, "app": app, "score": sc, "ts": e, "dur": 0})
        it["dur"] += max(e - s, SAMPLE_S)
        it["ts"] = max(it["ts"], e)
    for ts, browser, title, url in (history(t0, t1) if history else browser_history(t0, t1)):
        sc = _score(words, f"{title} {url}")
        if not sc:
            continue
        key = ("url", url)
        if key not in found:
            found[key] = {"kind": "site", "label": f"{title or _site(url)} ({_site(url)})", "url": url,
                          "score": sc + 0.5, "ts": ts, "dur": 0}
    items = sorted(found.values(), key=lambda it: (-it["score"], -it["ts"]))[:limit]
    for it in items:
        it["when"] = _when(it["ts"])
    return items


def search_text(query: str, period: str = "", **kw) -> str:
    items = search(query, period, **kw)
    if not items:
        return f"Nothing about '{query}' in the computer's memory for that time."
    return "Found: " + "; ".join(f"{i['when']} — {i['label']}" + (f", {_mins(i['dur'])}" if i.get("dur") else "")
                                 for i in items)


def _open_doc(name: str) -> str:
    from file_control import find_file
    path = find_file(name, search_whole_disk=False) or find_file(name)
    if not path:
        return ""
    os.startfile(path)
    return path


def reopen(query: str, db: str = None, history=None, opener=None) -> str:
    """Открыть снова лучшее совпадение: документ — в его программе, сайт — в браузере."""
    items = search(query, db=db, history=history, limit=5)
    target = next((i for i in items if i["kind"] in ("doc", "site")), None)
    if not target:
        return f"Couldn't find '{query}' in what you did on this computer."
    if target["kind"] == "site":
        if opener:
            opener(target["url"])
        else:
            import webbrowser
            webbrowser.open(target["url"])
        return f"Opened {target['label']} — last visited {target['when']}."
    path = (opener or _open_doc)(target["doc"])
    if not path:
        return f"Found {target['doc']} ({target['when']}), but the file is not where it was — maybe moved or deleted."
    return f"Opened {target['doc']} — you last worked on it {target['when']}."


def last_work(db: str = None, before: float = None, min_s: float = 120) -> dict:
    """Документ, над которым работал перед этим запуском Atlas (не меньше 2 минут) → {} если нет."""
    before = before or SESSION_START
    rows = _rows(before - 3 * 86400, before, db)
    for s, e, app, title, doc in reversed(rows):
        if doc and e - s >= min_s:
            return {"doc": doc, "app": app, "when": _when(e), "end": e}
    return {}


def forget(period: str = "today", db: str = None) -> int:
    t0, t1, _ = period_range(period)
    with _lock:
        c = _connect(db)
        try:
            n = c.execute("DELETE FROM work_log WHERE start >= ? AND start <= ?", (t0, t1)).rowcount
            c.commit()
            return n
        finally:
            c.close()
