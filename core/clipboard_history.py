"""
История буфера обмена — только на этом компьютере (clipboard.db рядом с Atlas, в git не попадает).

«Что я копировал час назад?», «вставь ссылку, которую я копировал утром», «найди номер, который я
копировал вчера», «забудь историю буфера».

Что НЕ записывается:
  • пароли: менеджеры паролей помечают их для Windows («не сохранять в истории») — мы это уважаем;
    плюс всё, что похоже на пароль или ключ (одно «слово» из букв, цифр и символов), и копирование
    из KeePass / Bitwarden / 1Password / LastPass;
  • собственные вставки Atlas (диктовка);
  • огромные куски (больше 20 000 символов).
CLIPBOARD_HISTORY=off в .env — выключить совсем.
"""
import ctypes
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "clipboard.db")
KEEP_DAYS = 30
KEEP_MAX = 3000
MAX_CHARS = 20000
_PASS_APPS = ("keepass", "bitwarden", "1password", "lastpass", "dashlane", "nordpass", "enpass", "roboform")
_lock = threading.Lock()
_ignored = []                          # (текст, до какого времени) — вставки самого Atlas


# ---------------------------------------------------------------------------
# Что это за текст
# ---------------------------------------------------------------------------
URL = re.compile(r"^\s*(?:https?://|www\.)\S+\s*$", re.I)
EMAIL = re.compile(r"^\s*[\w.+-]+@[\w-]+\.[\w.-]+\s*$")
PHONE = re.compile(r"^\s*\+?[\d\s()\-]{7,20}\s*$")


def kind_of(text: str) -> str:
    t = text.strip()
    if URL.match(t):
        return "url"
    if EMAIL.match(t):
        return "email"
    if PHONE.match(t) and sum(c.isdigit() for c in t) >= 7:
        return "phone"
    if "\n" in t and re.search(r"[{};]|def |import |function |=>|</", t):
        return "code"
    return "text"


def looks_secret(text: str) -> bool:
    """Похоже на пароль, токен или ключ — не храним."""
    t = text.strip()
    if not t or " " in t or "\n" in t or kind_of(t) != "text":
        return False
    if re.match(r"^(?:sk|pk|ghp|gho|xox[bp]|AIza|eyJ)[\w\-.]{10,}", t):
        return True                                       # ключи API, токены
    if not 8 <= len(t) <= 128:
        return False
    classes = sum(bool(re.search(p, t)) for p in (r"[a-zа-я]", r"[A-ZА-Я]", r"\d", r"[^\w]"))
    return classes >= 3 and bool(re.search(r"\d", t))


def ignore(text: str, seconds: float = 5) -> None:
    """Atlas сам кладёт это в буфер (вставка диктовки) — не записывать."""
    now = time.time()
    _ignored[:] = [(t, u) for t, u in _ignored if u > now] + [(text, now + seconds)]


def _is_ignored(text: str) -> bool:
    now = time.time()
    return any(t == text and u > now for t, u in _ignored)


# ---------------------------------------------------------------------------
# Хранилище
# ---------------------------------------------------------------------------
def _connect(db: str = None):
    c = sqlite3.connect(db or DB, timeout=5)
    c.execute("CREATE TABLE IF NOT EXISTS clips (id INTEGER PRIMARY KEY, ts REAL, text TEXT, app TEXT, kind TEXT)")
    c.execute("CREATE INDEX IF NOT EXISTS clips_ts ON clips(ts)")
    return c


def add(text: str, app: str = "", ts: float = None, db: str = None) -> bool:
    """Записать копирование. → False, если пропущено (пароль, повтор, вставка Atlas…)."""
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_CHARS:
        return False
    if _is_ignored(text) or looks_secret(text) or any(p in (app or "").lower() for p in _PASS_APPS):
        return False
    ts = ts or time.time()
    with _lock:
        c = _connect(db)
        try:
            last = c.execute("SELECT text FROM clips ORDER BY id DESC LIMIT 1").fetchone()
            if last and last[0] == text:
                return False
            c.execute("INSERT INTO clips (ts, text, app, kind) VALUES (?,?,?,?)", (ts, text, app or "", kind_of(text)))
            c.execute("DELETE FROM clips WHERE ts < ?", (ts - KEEP_DAYS * 86400,))
            c.execute("DELETE FROM clips WHERE id NOT IN (SELECT id FROM clips ORDER BY id DESC LIMIT ?)", (KEEP_MAX,))
            c.commit()
            return True
        finally:
            c.close()


_KINDS = (("url", r"ссылк|адрес сайт|линк|url|link"), ("email", r"почт|email|e-mail|имейл|мейл"),
          ("phone", r"номер|телефон|phone"), ("code", r"\bкод\b|code|скрипт"))


def _period(text: str, now: datetime):
    """Слова о времени → (с, по) в секундах или None."""
    t = (text or "").lower()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    m = re.search(r"(\d+|пол|час|минут\w*)\s*(минут|час)\w*\s+назад|(\d+)\s*(min|minute|hour)s?\s+ago", t)
    if "вчера" in t or "yesterday" in t:
        a, b = day0 - timedelta(days=1), day0
    elif "утр" in t or "morning" in t:
        a, b = day0.replace(hour=5), day0.replace(hour=12)
    elif "днём" in t or "днем" in t or "afternoon" in t:
        a, b = day0.replace(hour=12), day0.replace(hour=18)
    elif "вечер" in t or "evening" in t:
        a, b = day0.replace(hour=18), day0 + timedelta(days=1)
    elif "сегодня" in t or "today" in t:
        a, b = day0, now
    elif "недел" in t or "week" in t:
        a, b = now - timedelta(days=7), now
    elif m or "час назад" in t or "hour ago" in t or "недавно" in t or "recently" in t:
        if m and m.group(1) and m.group(1).isdigit():
            n = int(m.group(1)) * (60 if m.group(2) == "час" else 1)
        elif m and m.group(3):
            n = int(m.group(3)) * (60 if m.group(4) == "hour" else 1)
        elif "пол" in t:
            n = 30
        else:
            n = 60
        span = max(20, n // 2)                              # «час назад» — примерно: 30 мин – 1,5 ч
        a, b = now - timedelta(minutes=n + span), now - timedelta(minutes=max(0, n - span))
        if "недавно" in t or "recently" in t:
            a, b = now - timedelta(hours=2), now
    else:
        return None
    return a.timestamp(), b.timestamp()


_STOP = set("что я ты мне меня мой моя который которую которое которые копировал копировала скопировал скопировала "
            "буфер буфера обмена вставь найди покажи из в на про с по и или тот та то те это эту этот час назад "
            "утром вчера сегодня вечером днём днем недавно минут минуты ссылку ссылка ссылки почту номер "
            "what did i copy copied clipboard paste find show the a from that this link ago hour today yesterday "
            "morning".split())


def search(query: str = "", period: str = "", limit: int = 5, db: str = None, now: datetime = None) -> list:
    """→ [{"id", "ts", "text", "app", "kind"}] — новые сначала."""
    now = now or datetime.now()
    both = f"{query} {period}"
    span = _period(both, now)
    kind = next((k for k, rx in _KINDS if re.search(rx, both.lower())), None)
    words = [w for w in re.findall(r"\w+", (query or "").lower().replace("ё", "е"))
             if w not in _STOP and len(w) > 2 and not w.isdigit()]
    sql, args = "SELECT id, ts, text, app, kind FROM clips WHERE 1=1", []
    if span:
        sql += " AND ts BETWEEN ? AND ?"
        args += list(span)
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    with _lock:
        c = _connect(db)
        try:
            rows = c.execute(sql + " ORDER BY ts DESC LIMIT 500", args).fetchall()
        finally:
            c.close()
    out = []
    for r in rows:
        low = (r[2] + " " + r[3]).lower().replace("ё", "е")
        if words and not all(w[:5] in low for w in words):
            continue
        out.append({"id": r[0], "ts": r[1], "text": r[2], "app": r[3], "kind": r[4]})
        if len(out) >= limit:
            break
    return out


def describe(items: list) -> str:
    if not items:
        return "Nothing like that in the clipboard history."
    lines = []
    for i, it in enumerate(items, 1):
        when = datetime.fromtimestamp(it["ts"]).strftime("%d.%m %H:%M")
        text = it["text"].strip().replace("\n", " ⏎ ")
        lines.append(f"{i}. [{when}{', ' + it['app'] if it['app'] else ''}] {text[:300]}{'…' if len(text) > 300 else ''}")
    return "Clipboard history (newest first):\n" + "\n".join(lines)


def forget(period: str = "", db: str = None) -> int:
    span = _period(period, datetime.now()) if period and period not in ("all", "всё", "все") else None
    with _lock:
        c = _connect(db)
        try:
            n = (c.execute("DELETE FROM clips WHERE ts BETWEEN ? AND ?", span) if span
                 else c.execute("DELETE FROM clips")).rowcount
            c.commit()
            return n
        finally:
            c.close()


def put_back(item: dict, paste: bool = False, io=None) -> str:
    """Снова положить в буфер; paste — ещё и вставить в окно, где курсор."""
    import pyperclip
    ignore(item["text"])
    pyperclip.copy(item["text"])
    if paste:
        from core import win_input
        io = io or win_input
        h = io.foreground()
        if h and not io.is_atlas(h):
            io.keys("ctrl+v")
            return "pasted into the active window"
        return "copied to the clipboard (the active window is Atlas itself, so press Ctrl+V where you need it)"
    return "copied to the clipboard — Ctrl+V to paste"


# ---------------------------------------------------------------------------
# Слежение (Windows)
# ---------------------------------------------------------------------------
def _excluded_by_owner() -> bool:
    """Менеджер паролей пометил содержимое «не сохранять в истории» — стандарт Windows."""
    try:
        u = ctypes.windll.user32
        for name in ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore"):
            if u.IsClipboardFormatAvailable(u.RegisterClipboardFormatW(name)):
                return True
        f = u.RegisterClipboardFormatW("CanIncludeInClipboardHistory")
        if u.IsClipboardFormatAvailable(f):
            return True                                 # явное «0» ставят только для секретов
    except Exception:
        pass
    return False


def _owner_app() -> str:
    try:
        from core import worklog
        return worklog._foreground()[0]
    except Exception:
        return ""


def start(every: float = 0.7) -> bool:
    if (os.getenv("CLIPBOARD_HISTORY") or "on").lower() in ("off", "0", "no", "нет"):
        print("[буфер] история выключена (CLIPBOARD_HISTORY=off)")
        return False

    def loop():
        import pyperclip
        u = ctypes.windll.user32
        seq = u.GetClipboardSequenceNumber()
        while True:
            time.sleep(every)
            try:
                now_seq = u.GetClipboardSequenceNumber()
                if now_seq == seq:
                    continue
                seq = now_seq
                if _excluded_by_owner():
                    continue
                add(pyperclip.paste(), _owner_app())
            except Exception as e:
                print(f"[буфер] {e}")
                time.sleep(5)
    threading.Thread(target=loop, daemon=True, name="clipboard-history").start()
    print("[буфер] история копирования включена — только на этом компьютере, без паролей")
    return True
