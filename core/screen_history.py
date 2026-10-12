"""
Машина времени экрана: Atlas запоминает, что было на экране, и находит это по словам или по времени.

    «включи машину времени» / «выключи машину времени»   (по умолчанию выключена)
    «что было на экране вчера в 15:00»                  → открывает тот снимок
    «найди тот график про продажи» / «где я видел цену 3500»   → снимок, где был этот текст, и когда
    «что я смотрел час назад»
    «не записывай экран 30 минут» / «пауза машины времени»
    «забудь последний час экрана» / «удали историю экрана за сегодня»

Как: раз в 20 секунд, если ты за компьютером и экран изменился, — снимок (сжатый JPEG ~80 КБ) и его текст
(Windows OCR) в базу с поиском. Окна инкогнито, менеджеры паролей, банки — не снимаются. Хранится
SCREEN_HISTORY_DAYS дней (по умолчанию 7, ≈0,5–1 ГБ), только на этом компьютере:
Документы/Atlas/История экрана. Ничего никуда не отправляется.
"""
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "atlas_screen_history.json")
EVERY = 20
KEEP_DAYS = int(os.getenv("SCREEN_HISTORY_DAYS") or 7)
WIDTH = 1600
DIFF_MIN = 0.02
_s = {"thread": None, "stop": None, "last_sig": None, "pause_until": 0, "said": None, "hit": None}
_lock = threading.Lock()


def _ru(text: str) -> bool:
    return bool(re.search(r"[а-яё]", text or "", re.I))


def folder() -> str:
    p = os.path.join(os.path.expanduser("~/Documents"), "Atlas", "История экрана")
    os.makedirs(p, exist_ok=True)
    return p


def cfg() -> dict:
    try:
        with open(CFG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"on": False}


def _save_cfg(c: dict) -> None:
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)


def _db(path: str = None):
    c = sqlite3.connect(path or os.path.join(folder(), "index.db"), check_same_thread=False, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS shots (id INTEGER PRIMARY KEY, ts REAL, file TEXT, app TEXT, title TEXT, "
              "text TEXT)")
    try:
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS shots_fts USING fts5(text, title, content='shots', "
                  "content_rowid='id', tokenize='unicode61 remove_diacritics 2')")
        c.execute("CREATE TRIGGER IF NOT EXISTS shots_ai AFTER INSERT ON shots BEGIN INSERT INTO shots_fts(rowid, text, "
                  "title) VALUES (new.id, new.text, new.title); END")
        c.execute("CREATE TRIGGER IF NOT EXISTS shots_ad AFTER DELETE ON shots BEGIN INSERT INTO shots_fts(shots_fts, "
                  "rowid, text, title) VALUES ('delete', old.id, old.text, old.title); END")
    except sqlite3.OperationalError:
        pass
    return c


# --- окружение (в тестах подменяется) ------------------------------------------------------------------
class Env:
    def grab(self):
        from PIL import ImageGrab
        return ImageGrab.grab(all_screens=False)

    def ocr(self, img) -> str:
        from file_search import _ocr_pil, _ocr_lock
        with _ocr_lock:
            return _ocr_pil(img) or ""

    def foreground(self):
        from core import worklog
        app, title, _ = worklog._foreground()
        return app or "", title or ""

    def idle(self) -> float:
        from core import focus
        return focus._idle()

    def open(self, path: str) -> None:
        os.startfile(path)

    def now(self) -> float:
        return time.time()


_env = None


def _get_env():
    global _env
    if _env is None:
        _env = Env()
    return _env


def signature(img) -> list:
    """Грубый отпечаток экрана 32×18 в оттенках серого — понять, изменился ли он."""
    small = img.convert("L").resize((32, 18))
    return list(small.getdata())


def changed(a, b) -> float:
    if not a or not b:
        return 1.0
    return sum(1 for x, y in zip(a, b) if abs(x - y) > 18) / len(a)


def _private(app: str, title: str) -> bool:
    try:
        from core import worklog
        return bool(worklog._PRIVATE.search(title or "")) or bool(re.search(r"keepass|1password|bitwarden|lastpass",
                                                                             (app or "") + " " + (title or ""), re.I))
    except Exception:
        return bool(re.search(r"инкогнито|incognito|inprivate|private|пароль|password|банк|bank", title or "", re.I))


def capture_once(env=None, db: str = None, base: str = None) -> dict:
    """Один снимок, если экран изменился. → запись или {}."""
    env = env or _get_env()
    now = env.now()
    if now < _s["pause_until"]:
        return {}
    app, title = env.foreground()
    if _private(app, title):
        return {}
    img = env.grab()
    sig = signature(img)
    if changed(sig, _s["last_sig"]) < DIFF_MIN:
        return {}
    _s["last_sig"] = sig
    d = datetime.fromtimestamp(now)
    day = os.path.join(base or folder(), d.strftime("%Y-%m-%d"))
    os.makedirs(day, exist_ok=True)
    path = os.path.join(day, d.strftime("%H-%M-%S") + ".jpg")
    im = img.convert("RGB")
    if im.width > WIDTH:
        im = im.resize((WIDTH, int(im.height * WIDTH / im.width)))
    im.save(path, quality=62, optimize=True)
    text = env.ocr(img)
    with _lock:
        c = _db(db)
        try:
            c.execute("INSERT INTO shots (ts, file, app, title, text) VALUES (?,?,?,?,?)",
                      (now, path, app, title[:300], text[:20000]))
            c.commit()
        finally:
            c.close()
    return {"ts": now, "file": path, "app": app, "title": title, "text": text}


def cleanup(days: int = None, db: str = None, now: float = None) -> int:
    lim = (now or time.time()) - (days or KEEP_DAYS) * 86400
    return forget_range(0, lim, db)


def forget_range(lo: float, hi: float, db: str = None) -> int:
    with _lock:
        c = _db(db)
        try:
            rows = c.execute("SELECT id, file FROM shots WHERE ts>=? AND ts<?", (lo, hi)).fetchall()
            for _, f in rows:
                try:
                    os.remove(f)
                except Exception:
                    pass
            c.execute("DELETE FROM shots WHERE ts>=? AND ts<?", (lo, hi))
            c.commit()
        finally:
            c.close()
    return len(rows)


# --- поиск ------------------------------------------------------------------------------------------------
def _fts_query(words: list) -> str:
    return " OR ".join(f'"{w}"*' for w in words if w)


_STOP = set("""найди найти покажи где когда тот ту то те это этот эту экран экране на в во по про я ты видел видела
смотрел смотрела было была были который которая которое что как мне мой моя был там тогда тоже где-то недавно
сегодня вчера утром вечером днём днем назад час часа минут неделе машин времени find show where saw seen the a
on screen that this was""".split())


def search(text: str, db: str = None, lo: float = 0, hi: float = 1e12, limit: int = 5) -> list:
    words = [w if not re.search(r"[а-я]", w) else w[:max(4, len(w) - 2)]
             for w in re.findall(r"[\w.,-]+", (text or "").lower().replace("ё", "е"))
             if w not in _STOP and (len(w) >= 3 or w.isdigit())]
    words = [w.strip(".,") for w in words if w.strip(".,")]
    if not words:
        return []
    with _lock:
        c = _db(db)
        try:
            try:
                rows = c.execute("SELECT s.ts, s.file, s.app, s.title, s.text FROM shots_fts f JOIN shots s ON s.id=f.rowid "
                                 "WHERE shots_fts MATCH ? AND s.ts>=? AND s.ts<? ORDER BY bm25(shots_fts) LIMIT 50",
                                 (_fts_query(words), lo, hi)).fetchall()
            except sqlite3.OperationalError:
                rows = c.execute("SELECT ts, file, app, title, text FROM shots WHERE ts>=? AND ts<?", (lo, hi)).fetchall()
        finally:
            c.close()
    scored = []
    for ts, f, app, title, body in rows:
        hay = ((body or "") + " " + (title or "")).lower().replace("ё", "е")
        hit = sum(1 for w in words if w in hay)
        if hit:
            scored.append((hit, ts, f, app, title, body))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [{"ts": s[1], "file": s[2], "app": s[3], "title": s[4], "text": s[5], "hits": s[0]} for s in scored[:limit]]


def at_time(ts: float, db: str = None) -> dict:
    with _lock:
        c = _db(db)
        try:
            r = c.execute("SELECT ts, file, app, title, text FROM shots ORDER BY abs(ts-?) LIMIT 1", (ts,)).fetchone()
        finally:
            c.close()
    return {"ts": r[0], "file": r[1], "app": r[2], "title": r[3], "text": r[4]} if r else {}


def when_from(text: str, now: float):
    """«вчера в 15:00», «в 14:30», «час назад», «утром» → момент времени или None."""
    t = (text or "").lower()
    d = datetime.fromtimestamp(now)
    day = d.replace(hour=0, minute=0, second=0, microsecond=0)
    if re.search(r"позавчера", t):
        day -= timedelta(days=2)
    elif re.search(r"вчера|yesterday", t):
        day -= timedelta(days=1)
    m = re.search(r"(?:в|at)\s+(\d{1,2})(?:[:.](\d{2}))?\s*(?:час\w*)?", t)
    if m and int(m.group(1)) < 24:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if re.search(r"вечера|дня|pm", t) and h < 12:
            h += 12
        return (day + timedelta(hours=h, minutes=mi)).timestamp()
    m = re.search(r"(\d+|полчаса|час)\s*(минут\w*|час\w*)?\s*назад|(\d+)\s*(minutes?|hours?) ago", t)
    if m:
        if "полчаса" in m.group(0):
            return now - 1800
        n = int(m.group(1) or m.group(3)) if (m.group(1) or "").isdigit() or m.group(3) else 1
        unit = (m.group(2) or m.group(4) or "час")
        return now - n * (60 if re.match(r"минут|minute", unit) else 3600)
    if re.search(r"утром|morning", t):
        return (day + timedelta(hours=10)).timestamp()
    if re.search(r"вечером|evening", t):
        return (day + timedelta(hours=20)).timestamp()
    if re.search(r"днём|днем|afternoon", t):
        return (day + timedelta(hours=14)).timestamp()
    if day != d.replace(hour=0, minute=0, second=0, microsecond=0):
        return (day + timedelta(hours=15)).timestamp()
    return None


def _say_when(ts: float, now: float, ru: bool) -> str:
    d, n = datetime.fromtimestamp(ts), datetime.fromtimestamp(now)
    days = (n.date() - d.date()).days
    hm = d.strftime("%H:%M")
    if ru:
        return f"сегодня в {hm}" if days == 0 else f"вчера в {hm}" if days == 1 else d.strftime("%d.%m в %H:%M")
    return f"today at {hm}" if days == 0 else f"yesterday at {hm}" if days == 1 else d.strftime("%b %d at %H:%M")


def _app_name(app: str, title: str) -> str:
    a = (app or "").lower().replace(".exe", "")
    nice = {"chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "browser": "Яндекс Браузер", "code": "VS Code",
            "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint", "telegram": "Telegram", "discord": "Discord"}
    page = re.split(r"\s+[-—–]\s+", title or "")[0].strip()
    return (nice.get(a, app or "") + (f" — «{page[:60]}»" if page and page.lower() != a else "")).strip(" —")


# --- фразы ---------------------------------------------------------------------------------------------------
_ON = re.compile(r"(?:включи|запусти|активируй|start|turn on|enable)\s+(?:машин\w* времени|истори\w* экрана|"
                 r"запись истории экрана|screen (?:history|time machine))", re.I)
_OFF = re.compile(r"(?:выключи|останови|отключи|stop|turn off|disable)\s+(?:машин\w* времени|истори\w* экрана|"
                  r"screen (?:history|time machine))", re.I)
_PAUSE = re.compile(r"(?:не записывай|не снимай|не запоминай)\s+экран\w*(?:\s+(\d+)\s*(минут\w*|час\w*))?|"
                    r"пауза\s+(?:машин\w* времени|истори\w* экрана)", re.I)
_FORGET = re.compile(r"(?:забудь|удали|сотри)\s+(?:последн\w+\s+)?(\d+\s+)?(час\w*|минут\w*|сегодня\w*|всё|все|истори\w*)"
                     r"(?:\s+истори\w*)?\s+(?:с\s+)?экран\w*|(?:удали|сотри|забудь)\s+истори\w+\s+экрана(?:\s+за\s+(сегодня|"
                     r"час|день))?", re.I)
_FIND = re.compile(r"(?:найди|покажи|где|когда)\s+(?:я\s+)?(?:тот|ту|то|те|экран\w*|снимок)?\s*.{0,60}?"
                   r"(?:видел\w*|видела|смотрел\w*|был\w*\s+на\s+экране|на\s+экране)|"
                   r"что\s+(?:было|я\s+(?:смотрел|видел|делал))\s+на\s+экране|что\s+я\s+смотрел\s+(.+?назад|вчера|утром|"
                   r"вечером|в\s+\d)|what was on (?:my |the )?screen|find (?:the|that) .+ i saw", re.I)


def intent(text: str, last: str = None):
    t = " ".join((text or "").split()).strip(" ?!.")
    if not t:
        return None
    if _s.get("hit") and (last is None or last == _s.get("said")) and re.match(r"^(?:да|открой|покажи|yes|open)\b", t, re.I):
        return ("open",)
    if _OFF.search(t):
        return ("off",)
    if _ON.search(t):
        return ("on",)
    m = _PAUSE.search(t)
    if m:
        mins = int(m.group(1)) * (60 if m.group(2) and m.group(2).startswith("час") else 1) if m.group(1) else 30
        return ("pause", mins)
    if _FORGET.search(t):
        return ("forget", t)
    if _FIND.search(t):
        return ("find", t)
    return None


def handle(text: str, env=None, db: str = None, last: str = None):
    it = intent(text, last)
    if not it:
        return None
    env = env or _get_env()
    ru = _ru(text)
    k = it[0]
    c = cfg()
    if k == "on":
        c["on"] = True
        _save_cfg(c)
        start()
        return (f"Машина времени включена: запоминаю экран, пока ты за компьютером, {KEEP_DAYS} дней, только здесь. "
                "Инкогнито и пароли не снимаю." if ru else "Screen time machine is on — local only.")
    if k == "off":
        c["on"] = False
        _save_cfg(c)
        stop()
        return "Машина времени выключена. Уже сохранённое осталось — «удали историю экрана», если не нужно." if ru else \
            "Screen time machine is off."
    if k == "pause":
        _s["pause_until"] = env.now() + it[1] * 60
        return f"Хорошо, {it[1]} минут экран не запоминаю." if ru else f"Not recording the screen for {it[1]} min."
    if k == "forget":
        t = it[1].lower()
        now = env.now()
        if re.search(r"сегодня|день", t):
            lo = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0).timestamp()
        elif re.search(r"всё|все|истори\w+\s+экрана\s*$", t) and not re.search(r"час|минут", t):
            lo = 0
        else:
            m = re.search(r"(\d+)\s*(час|минут)", t)
            n = int(m.group(1)) if m else 1
            lo = now - n * (3600 if not m or m.group(2).startswith("час") else 60)
        n = forget_range(lo, now + 1, db)
        return f"Стёр {n} снимков экрана." if ru else f"Deleted {n} screen snapshots."
    if k == "open":
        hit = _s["hit"]
        _s["hit"] = None
        try:
            env.open(hit["file"])
        except Exception as e:
            return f"Не открылся снимок: {e}"[:200]
        return "Открываю." if ru else "Opening."
    explicit = bool(re.search(r"экран|screen", text, re.I))
    if not cfg().get("on") and not os.path.exists(os.path.join(folder(), "index.db")) and db is None:
        if not explicit:
            return None                                   # «что я смотрел вчера» — ответит память компьютера
        return ("Машина времени выключена — скажи «включи машину времени», и с этого момента я буду помнить экран."
                if ru else "The screen time machine is off — say “turn on screen time machine”.")
    now = env.now()
    q = it[1]
    ts = when_from(q, now)
    words_q = re.sub(r"(?:вчера|позавчера|сегодня|утром|вечером|днём|днем|в\s+\d{1,2}(?:[:.]\d{2})?\s*(?:час\w*)?|"
                     r"\d+\s*(?:минут\w*|час\w*)\s*назад|час назад|полчаса назад)", " ", q, flags=re.I)
    hits = search(words_q, db, *((ts - 6 * 3600, ts + 6 * 3600) if ts and re.search(r"вчера|позавчера|утром|вечером", q, re.I)
                                 else (0, 1e12)))
    if hits:
        h = hits[0]
    elif ts:
        h = at_time(ts, db)
        if h and abs(h["ts"] - ts) > 3 * 3600:
            h = {}
    else:
        h = {}
    if not h:
        if not explicit:
            return None
        return ("Не нашёл такого в истории экрана." if ru else "Nothing like that in the screen history.")
    try:
        env.open(h["file"])
    except Exception:
        pass
    where = _app_name(h.get("app", ""), h.get("title", ""))
    return (f"Вот: {_say_when(h['ts'], now, ru)}, {where}. Открыл снимок." if ru else
            f"Here: {_say_when(h['ts'], now, ru)}, {where}. Opened the snapshot.")


# --- фон --------------------------------------------------------------------------------------------------------
def start(every: float = EVERY) -> bool:
    if _s["thread"] and _s["thread"].is_alive():
        return True
    if not cfg().get("on"):
        return False
    stop_ev = threading.Event()
    _s["stop"] = stop_ev

    def loop():
        env = _get_env()
        last_clean = 0
        while not stop_ev.is_set():
            stop_ev.wait(every)
            if stop_ev.is_set():
                break
            try:
                if env.idle() < 120:
                    capture_once(env)
                if time.time() - last_clean > 3600:
                    cleanup()
                    last_clean = time.time()
            except Exception as e:
                print(f"[машина времени] {e}")
    _s["thread"] = threading.Thread(target=loop, daemon=True, name="screen-history")
    _s["thread"].start()
    print("[машина времени] запоминаю экран")
    return True


def stop() -> None:
    if _s.get("stop"):
        _s["stop"].set()
