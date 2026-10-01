"""
Итоги дня и недели.

Что собирается:
  • Время в программах: раз в минуту Atlas смотрит, какая программа на переднем плане,
    и записывает ТОЛЬКО её название (VS Code, Chrome, Telegram…) — без заголовков окон,
    без содержимого. Если ты 5 минут не трогал мышь и клавиатуру, минута не считается.
  • Разговоры с Atlas: сколько вопросов и о чём (пересказы из памяти).
  • Учёба: повторения, точность, серия дней.
  • Чему Atlas научился (мастерская навыков) и коммиты в проекте за день.

Итоги можно спросить голосом («подведи итоги дня», «как прошла неделя»), посмотреть
в разделе «Итоги», а в «Ритуалах» включить вечерний отчёт по расписанию.
"""
import ctypes
import os
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "memory.db")
SAMPLE_S = 60
IDLE_S = 300

_PRETTY = {"code.exe": "VS Code", "chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox",
           "telegram.exe": "Telegram", "discord.exe": "Discord", "spotify.exe": "Spotify", "steam.exe": "Steam",
           "explorer.exe": "Проводник", "winword.exe": "Word", "excel.exe": "Excel", "powerpnt.exe": "PowerPoint",
           "wps.exe": "WPS Office", "notepad.exe": "Блокнот", "windowsterminal.exe": "Терминал",
           "powershell.exe": "PowerShell", "pycharm64.exe": "PyCharm", "obs64.exe": "OBS", "vlc.exe": "VLC",
           "whatsapp.exe": "WhatsApp", "zoom.exe": "Zoom", "teams.exe": "Teams"}

_lock = threading.Lock()
_started = False


def _connect():
    c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY, ts REAL, app TEXT)")
    return c


def _lang() -> str:
    try:
        from voice import get_response_language
        return get_response_language()
    except Exception:
        return "ru"


# ---------------------------------------------------------------------------
# Время в программах
# ---------------------------------------------------------------------------
def _idle_seconds() -> float:
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return 0.0
    return (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000.0


def _foreground_app() -> str:
    import ctypes.wintypes as wt
    import psutil
    u = ctypes.windll.user32
    h = u.GetForegroundWindow()
    if not h:
        return ""
    pid = wt.DWORD()
    u.GetWindowThreadProcessId(h, ctypes.byref(pid))
    try:
        exe = psutil.Process(pid.value).name().lower()
    except Exception:
        return ""
    if exe in ("python.exe", "pythonw.exe"):
        n = u.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, buf, n + 1)
        return "Atlas" if buf.value.strip().upper() == "ATLAS" else "Python"
    return _PRETTY.get(exe, exe[:-4].capitalize() if exe.endswith(".exe") else exe)


def _sampler() -> None:
    while True:
        time.sleep(SAMPLE_S)
        try:
            if _idle_seconds() > IDLE_S:
                continue
            app = _foreground_app()
            if app:
                with _lock:
                    c = _connect()
                    c.execute("INSERT INTO activity (ts, app) VALUES (?, ?)", (time.time(), app))
                    c.commit()
                    c.close()
        except Exception as e:
            print(f"[итоги] учёт времени: {e}")
            time.sleep(300)


# ---------------------------------------------------------------------------
# Сбор данных
# ---------------------------------------------------------------------------
def _range(period: str):
    now = datetime.now()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "yesterday":
        return (day0 - timedelta(days=1)).timestamp(), day0.timestamp(), (day0 - timedelta(days=1)).strftime("%d.%m")
    if period == "week":
        return (day0 - timedelta(days=6)).timestamp(), now.timestamp(), (day0 - timedelta(days=6)).strftime("%d.%m") + "–" + now.strftime("%d.%m")
    return day0.timestamp(), now.timestamp(), now.strftime("%d.%m")


def _q(c, sql, args=()):
    try:
        return c.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []                                   # таблицы ещё нет — модуль не использовался


def collect(period: str = "today") -> dict:
    period = period if period in ("today", "yesterday", "week") else "today"
    t0, t1, label = _range(period)
    with _lock:
        c = _connect()
        apps = _q(c, "SELECT app, COUNT(*) FROM activity WHERE ts BETWEEN ? AND ? GROUP BY app ORDER BY 2 DESC", (t0, t1))
        per_day = _q(c, "SELECT date(ts, 'unixepoch', 'localtime'), COUNT(*) FROM activity WHERE ts BETWEEN ? AND ? "
                        "GROUP BY 1 ORDER BY 1", (t0, t1))
        questions = (_q(c, "SELECT COUNT(*) FROM turns WHERE role='user' AND ts BETWEEN ? AND ?", (t0, t1)) or [[0]])[0][0]
        topics = [r[0] for r in _q(c, "SELECT summary FROM episodes WHERE ended BETWEEN ? AND ? AND summary IS NOT NULL "
                                      "ORDER BY ended DESC LIMIT 6", (t0, t1))]
        study = (_q(c, "SELECT COUNT(*), SUM(CASE WHEN grade>=3 THEN 1 ELSE 0 END) FROM study_reviews "
                       "WHERE ts BETWEEN ? AND ?", (t0, t1)) or [[0, 0]])[0]
        skills = [r[0] for r in _q(c, "SELECT name FROM forge WHERE status='installed' AND ts BETWEEN ? AND ?", (t0, t1))]
        tools = _q(c, "SELECT tool, COUNT(*) FROM habit_log WHERE ts BETWEEN ? AND ? GROUP BY tool ORDER BY 2 DESC LIMIT 5",
                   (t0, t1))
        c.close()
    commits = []
    try:
        since = datetime.fromtimestamp(t0).strftime("%Y-%m-%d %H:%M:%S")
        until = datetime.fromtimestamp(t1).strftime("%Y-%m-%d %H:%M:%S")
        out = subprocess.run(["git", "-C", ROOT, "log", f"--since={since}", f"--until={until}", "--pretty=%s"],
                             capture_output=True, text=True, timeout=10, encoding="utf-8", errors="ignore")
        commits = [l.strip() for l in out.stdout.splitlines() if l.strip()][:10]
    except Exception:
        pass
    streak = 0
    try:
        from core import study as _st
        streak = _st.stats()["streak"]
    except Exception:
        pass
    total_min = sum(n for _a, n in apps)
    return {"period": period, "label": label, "active_min": total_min,
            "apps": [{"app": a, "min": n} for a, n in apps[:8]],
            "days": [{"day": f"{d[8:10]}.{d[5:7]}", "min": n} for d, n in per_day],
            "questions": questions, "topics": topics, "commits": commits, "skills": skills,
            "tools": [{"tool": t, "n": n} for t, n in tools],
            "study": {"reviews": study[0] or 0, "accuracy": round((study[1] or 0) / study[0] * 100) if study[0] else None,
                      "streak": streak}}


def _hm(minutes: int, ru: bool) -> str:
    h, m = divmod(int(minutes), 60)
    if ru:
        return f"{h} ч {m} мин" if h else f"{m} мин"
    return f"{h} h {m} min" if h else f"{m} min"


def report_text(period: str = "today") -> str:
    """Сводка фактами — модель или ритуал превращают её в короткий рассказ."""
    d = collect(period)
    ru = _lang() == "ru"
    name = {"today": "Сегодня" if ru else "Today", "yesterday": "Вчера" if ru else "Yesterday",
            "week": "За неделю" if ru else "This week"}[d["period"]]
    parts = [f"{name} ({d['label']})."]
    if d["active_min"]:
        parts.append(("Активно за компьютером: " if ru else "Active at the computer: ") + _hm(d["active_min"], ru) + ". "
                     + ("Программы: " if ru else "Apps: ")
                     + ", ".join(f"{a['app']} {_hm(a['min'], ru)}" for a in d["apps"][:5]) + ".")
    if d["period"] == "week" and d["days"]:
        best = max(d["days"], key=lambda x: x["min"])
        parts.append(("Самый активный день: " if ru else "Busiest day: ") + f"{best['day']} ({_hm(best['min'], ru)}).")
    parts.append((f"Вопросов к Atlas: {d['questions']}." if ru else f"Questions to Atlas: {d['questions']}."))
    if d["topics"]:
        parts.append(("Темы: " if ru else "Topics: ") + " | ".join(t[:160] for t in d["topics"][:4]))
    s = d["study"]
    if s["reviews"]:
        parts.append((f"Учёба: {s['reviews']} повторений, точность {s['accuracy']}%, серия {s['streak']} дн."
                      if ru else f"Study: {s['reviews']} reviews, {s['accuracy']}% correct, streak {s['streak']} days."))
    if d["skills"]:
        parts.append(("Atlas научился: " if ru else "Atlas learned: ") + ", ".join(d["skills"]) + ".")
    if d["commits"]:
        parts.append((f"Коммитов: {len(d['commits'])} — " if ru else f"Commits: {len(d['commits'])} — ")
                     + "; ".join(c[:80] for c in d["commits"][:4]) + ".")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Запуск: учёт времени + ритуал «Итоги дня» (по фразе; по расписанию — если включишь)
# ---------------------------------------------------------------------------
def _ensure_routine() -> None:
    try:
        from core import routines
        ru = _lang() == "ru"
        name = "Итоги дня" if ru else "Daily recap"
        if routines.get(name):
            return
        rid = routines.create(name, ["итоги дня", "подведи итоги", "как прошёл день", "daily recap"],
                              [{"tool": "day_report", "args": {"period": "today"}}], schedule="21:30")
        routines.set_schedule(rid, "21:30", False)          # по расписанию — только если включишь в «Ритуалах»
    except Exception as e:
        print(f"[итоги] ритуал: {e}")


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    _connect().close()
    threading.Thread(target=_sampler, daemon=True, name="activity").start()
    _ensure_routine()
    print("[итоги] учёт времени в программах включён (только названия программ, простой не считается)")
