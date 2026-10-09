"""
Автопилот повторов: Atlas замечает, что ты изо дня в день начинаешь с одного и того же,
и сам предлагает делать это одной фразой.

Как: память компьютера (core/worklog) делится на «сеансы» (перерыв больше 90 минут — новый сеанс).
Из первых 20 минут каждого сеанса берётся набор: программы, документы, сайты. Если в одно и то же
время дня (утро / день / вечер) хотя бы 3 разных дня повторяются минимум 2 одинаковых пункта —
это привычка. Atlas предлагает её один раз; согласишься — станет рабочим местом
(«открой рабочее место утро»), и его можно открывать сам по фразе.

    find_habits()           → найденные привычки
    next_offer()            → текст предложения или "" (каждую привычку — один раз)
    accept(name)            → рабочее место из последней предложенной привычки
"""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime

from core import worklog

SESSION_GAP = 90 * 60
HEAD_S = 20 * 60
MIN_DAYS = 3
MIN_ITEMS = 2
LOOK_DAYS = 14
_BUCKETS = (("утро", 5, 12), ("день", 12, 18), ("вечер", 18, 24), ("ночь", 0, 5))
_SKIP = {"atlas", "python", "проводник", "explorer", "терминал", "powershell", "windowsterminal", "taskmgr"}
_lock = threading.Lock()
_state = {"last": None}


def _connect(db: str = None):
    c = sqlite3.connect(db or worklog.DB, check_same_thread=False, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS autopilot_offers (key TEXT PRIMARY KEY, ts REAL, habit TEXT, answer TEXT)")
    return c


def _bucket(hour: int) -> str:
    return next(n for n, a, b in _BUCKETS if a <= hour < b)


def _sessions(rows: list) -> list:
    """Строки work_log (start, end, app, title, doc, exe) → [[строки сеанса]]."""
    out, cur, last_end = [], [], None
    for r in sorted(rows, key=lambda r: r[0]):
        if last_end is not None and r[0] - last_end > SESSION_GAP:
            out.append(cur)
            cur = []
        cur.append(r)
        last_end = max(last_end or 0, r[1])
    if cur:
        out.append(cur)
    return out


def _site(url: str) -> str:
    return worklog._site(url)


def find_habits(rows: list = None, history: list = None, now: float = None, db: str = None) -> list:
    """→ [{"key","bucket","time","days","items":[{"kind","label",...}]}], самые частые первыми."""
    now = now or time.time()
    t0 = now - LOOK_DAYS * 86400
    if rows is None:
        with worklog._lock:
            c = worklog._connect(db)
            try:
                rows = c.execute("SELECT start, end, app, title, doc, exe FROM work_log WHERE start >= ? "
                                 "ORDER BY start", (t0,)).fetchall()
            finally:
                c.close()
    if history is None:
        history = worklog.browser_history(t0, now)
    starts = {}                                         # (корзина) → {пункт: {дни}}, времена начала, примеры
    for ses in _sessions(rows):
        s0 = ses[0][0]
        d = datetime.fromtimestamp(s0)
        b = _bucket(d.hour)
        head = [r for r in ses if r[0] - s0 <= HEAD_S]
        keys = {}
        for start, end, app, title, doc, exe in head:
            if not app or app.lower() in _SKIP:
                continue
            if doc:
                keys[("doc", doc)] = {"kind": "doc", "doc": doc, "label": doc, "exe": exe or ""}
            elif not any(x in app.lower() for x in worklog._BROWSERS):
                keys[("app", app)] = {"kind": "app", "app": app, "exe": exe or "", "label": app}
        for ts, _, title, url in history:
            if s0 <= ts <= s0 + HEAD_S:
                site = _site(url)
                keys.setdefault(("site", site), {"kind": "url", "url": url, "label": site})
        g = starts.setdefault(b, {"items": {}, "times": [], "days": set()})
        g["times"].append(d.hour * 60 + d.minute)
        g["days"].add(d.date())
        for k, item in keys.items():
            slot = g["items"].setdefault(k, {"item": item, "days": set()})
            slot["days"].add(d.date())
            if item.get("exe"):
                slot["item"]["exe"] = item["exe"]
    habits = []
    for b, g in starts.items():
        common = [(k, v) for k, v in g["items"].items() if len(v["days"]) >= MIN_DAYS]
        if len(common) < MIN_ITEMS:
            continue
        common.sort(key=lambda kv: (-len(kv[1]["days"]), kv[0][1]))
        items = [v["item"] for _, v in common[:6]]
        days = min(len(v["days"]) for _, v in common[:6])
        ts = sorted(g["times"])
        mid = ts[len(ts) // 2]
        key = b + "|" + "|".join(sorted(f"{k[0]}:{k[1]}" for k, _ in common[:6]))
        habits.append({"key": key, "bucket": b, "time": f"{mid // 60:02d}:{mid % 60:02d}", "days": days,
                       "items": items})
    return sorted(habits, key=lambda h: -h["days"])


def _offered(c, key: str) -> bool:
    return c.execute("SELECT 1 FROM autopilot_offers WHERE key=?", (key,)).fetchone() is not None


def _lang() -> str:
    try:
        from voice import get_response_language
        return get_response_language()
    except Exception:
        return "ru"


_WHEN = {"утро": "утром", "день": "днём", "вечер": "вечером", "ночь": "ночью"}


def offer_text(h: dict, lang: str = "ru") -> str:
    labels = [i["label"] for i in h["items"]]
    joiner = " и " if lang == "ru" else " and "
    listed = ", ".join(labels[:-1]) + joiner + labels[-1] if len(labels) > 1 else labels[0]
    if lang == "ru":
        return (f"Заметил: {_WHEN.get(h['bucket'], h['bucket'])} около {h['time']} ты обычно открываешь {listed}. "
                f"Сделать из этого рабочее место «{h['bucket']}»? Скажи «да, сделай» — и потом хватит фразы "
                f"«режим {h['bucket']}».")
    return (f"I noticed that around {h['time']} you usually open {listed}. Make it a workspace called "
            f"'{h['bucket']}'? Say 'yes, do it' — then 'open workspace {h['bucket']}' brings it all back.")


def next_offer(db: str = None, habits: list = None) -> str:
    """Самая частая ещё не предложенная привычка → текст предложения (и она запоминается как предложенная)."""
    habits = find_habits(db=db) if habits is None else habits
    with _lock:
        c = _connect(db)
        try:
            for h in habits:
                if _offered(c, h["key"]):
                    continue
                c.execute("INSERT INTO autopilot_offers (key, ts, habit, answer) VALUES (?, ?, ?, '')",
                          (h["key"], time.time(), json.dumps(h, ensure_ascii=False, default=str)))
                c.commit()
                _state["last"] = h
                return offer_text(h, _lang())
        finally:
            c.close()
    return ""


def _last_offer(db: str = None):
    if _state["last"]:
        return _state["last"]
    with _lock:
        c = _connect(db)
        try:
            r = c.execute("SELECT habit FROM autopilot_offers WHERE answer='' ORDER BY ts DESC LIMIT 1").fetchone()
        finally:
            c.close()
    return json.loads(r[0]) if r else None


def accept(name: str = "", db: str = None) -> str:
    """Согласие на последнее предложение → рабочее место из пунктов привычки."""
    h = _last_offer(db)
    if not h:
        return "There is no habit suggestion to accept right now."
    from core import workspaces
    name = (name or h["bucket"]).strip()
    items = []
    for it in h["items"]:
        if it["kind"] == "app" and it.get("exe"):
            items.append({"kind": "app", "exe": it["exe"], "label": it["label"]})
        elif it["kind"] == "doc":
            items.append({"kind": "doc", "doc": it["doc"], "exe": it.get("exe", ""), "label": it["label"]})
        elif it["kind"] == "url":
            items.append({"kind": "url", "url": it["url"], "label": it["label"]})
    if not items:
        return "Couldn't build a workspace from that habit (programs have no known path yet)."
    with workspaces._lock:
        c = workspaces._connect(db)
        try:
            c.execute("INSERT OR REPLACE INTO workspaces (name, saved, items) VALUES (?, ?, ?)",
                      (workspaces._key(name), time.time(), json.dumps(items, ensure_ascii=False)))
            c.commit()
        finally:
            c.close()
    _answer(h["key"], "yes", db)
    return f"Made workspace '{name}': " + ", ".join(i["label"] for i in items) + \
        f". Say 'open workspace {name}' (or 'режим {name}') to open it all."


def decline(db: str = None) -> str:
    h = _last_offer(db)
    if not h:
        return "Nothing to decline."
    _answer(h["key"], "no", db)
    return "Okay, I won't suggest that again."


def _answer(key: str, answer: str, db: str = None) -> None:
    with _lock:
        c = _connect(db)
        try:
            c.execute("UPDATE autopilot_offers SET answer=? WHERE key=?", (answer, key))
            c.commit()
        finally:
            c.close()
    _state["last"] = None


def start(announce, every_s: int = 3600) -> threading.Thread:
    """Раз в час (не в первые 10 минут после запуска) ищет новую привычку; предлагает не чаще раза в день."""
    if not worklog.enabled() or (os.getenv("AUTOPILOT") or "on").lower() in ("off", "0", "no"):
        return None

    def loop():
        time.sleep(600)
        last_offer = 0.0
        while True:
            try:
                if time.time() - last_offer > 86400 and worklog._idle() < 120:
                    text = next_offer()
                    if text:
                        last_offer = time.time()
                        announce(text)
                        try:                        # чтобы «да, сделай» мозг понял, на что это ответ
                            from brain import state
                            state.conversation_history.append({"role": "assistant", "content": text})
                        except Exception:
                            pass
            except Exception as e:
                print(f"[автопилот] {e}")
            time.sleep(every_s)
    t = threading.Thread(target=loop, daemon=True, name="autopilot")
    t.start()
    return t
