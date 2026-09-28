"""
Ритуалы Atlas.

Ритуал — несколько действий одной фразой («доброе утро» → погода, дела на сегодня,
новости, список задач) или по расписанию. Инструменты выполняются напрямую, без
модели, а в конце ОДИН запрос к модели складывает результаты в короткую речь —
так ритуал быстрый и почти не тратит лимит.

Привычки. Atlas записывает, какими инструментами пользуется, и ищет наборы,
которые повторяются примерно в одно и то же время хотя бы 3 разных дня. Такой
набор он предлагает сделать ритуалом — создаётся только с согласия.
"""
import itertools
import json
import os
import re
import sqlite3
import statistics
import threading
import time
from datetime import datetime

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory.db")
SESSION_GAP = 20 * 60          # действия ближе 20 минут друг к другу — одна «сессия»
HABIT_MIN_DAYS = 3             # столько разных дней должен повториться набор
HABIT_MAX_HOUR_SPREAD = 1.5    # насколько «в одно время» (стандартное отклонение, часы)
HABIT_LOOKBACK_DAYS = 21
SKIP_TOOLS = {
    "update_plan", "remember_fact", "recall_conversations", "forget_memory", "save_memory",
    "recall_memories", "create_routine", "list_routines", "run_routine", "delete_routine",
    "heal_status", "heal_apply", "heal_reject", "start_mission", "mission_status", "cancel_mission",
    "set_response_language", "set_always_listening", "set_voice", "set_elevenlabs_voice",
    "set_microphone", "set_speaker", "set_theme", "read_screen", "click_on_screen",
    "browser_click", "browser_type", "browser_scroll", "browser_read_page", "browser_press_key",
    "browser_screenshot_describe", "browser_close", "guess_number", "open_search_result",
}
MORNING_TEMPLATE = {
    "name_ru": "Доброе утро", "name_en": "Good morning",
    "triggers": ["доброе утро", "good morning"],
    "steps": [{"tool": "get_weather", "args": {}}, {"tool": "list_today_events", "args": {}},
              {"tool": "get_news", "args": {"count": 3}}, {"tool": "list_todos", "args": {}}],
}

_lock = threading.Lock()
_speak = None
_started = False


def _connect():
    c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS routines (id INTEGER PRIMARY KEY, name TEXT, triggers TEXT, "
              "steps TEXT, schedule TEXT DEFAULT '', days TEXT DEFAULT '', enabled INTEGER DEFAULT 0, "
              "status TEXT DEFAULT 'active', created REAL, last_run REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS habit_log (id INTEGER PRIMARY KEY, ts REAL, tool TEXT, "
              "args TEXT, question TEXT)")
    return c


def _lang() -> str:
    try:
        from voice import get_response_language
        return get_response_language()
    except Exception:
        return "ru"


def _norm(text: str) -> str:
    t = re.sub(r"^\s*\([^)]*\)\s*", "", text or "").lower()          # «(Respond in Russian.)»
    t = re.sub(r"\b(?:атлас|atlas)\b", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _row(r) -> dict:
    return {"id": r[0], "name": r[1], "triggers": json.loads(r[2] or "[]"), "steps": json.loads(r[3] or "[]"),
            "schedule": r[4] or "", "days": r[5] or "", "enabled": bool(r[6]), "status": r[7],
            "last_run": time.strftime("%d.%m %H:%M", time.localtime(r[9])) if r[9] else ""}


_COLS = "id, name, triggers, steps, schedule, days, enabled, status, created, last_run"


# ---------------------------------------------------------------------------
# Хранение
# ---------------------------------------------------------------------------
def list_all(include_suggested: bool = True) -> list:
    with _lock:
        c = _connect()
        statuses = "('active', 'suggested')" if include_suggested else "('active')"
        rows = c.execute(f"SELECT {_COLS} FROM routines WHERE status IN {statuses} ORDER BY status, id").fetchall()
        c.close()
    return [_row(r) for r in rows]


def get(key) -> dict:
    """По id или по имени (без учёта регистра)."""
    with _lock:
        c = _connect()
        if str(key).isdigit():
            r = c.execute(f"SELECT {_COLS} FROM routines WHERE id=?", (int(key),)).fetchone()
        else:
            r = c.execute(f"SELECT {_COLS} FROM routines WHERE lower(name)=lower(?) AND status='active'",
                          (str(key).strip(),)).fetchone()
        c.close()
    return _row(r) if r else None


def create(name: str, triggers: list, steps: list, schedule: str = "", days: str = "",
           status: str = "active") -> int:
    clean_steps = [{"tool": s.get("tool"), "args": s.get("args") or {}} for s in steps
                   if isinstance(s, dict) and s.get("tool")]
    trig = [t.strip().lower() for t in (triggers or []) if str(t).strip()]
    with _lock:
        c = _connect()
        rid = c.execute("INSERT INTO routines (name, triggers, steps, schedule, days, enabled, status, created) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (name.strip(), json.dumps(trig, ensure_ascii=False), json.dumps(clean_steps, ensure_ascii=False),
                         schedule or "", days or "", 1 if (schedule and status == "active") else 0, status,
                         time.time())).lastrowid
        c.commit()
        c.close()
    print(f"[ритуалы] {'предложен' if status == 'suggested' else 'создан'} ритуал «{name}»: "
          f"{', '.join(s['tool'] for s in clean_steps)}")
    return rid


def _update(rid: int, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        c = _connect()
        c.execute(f"UPDATE routines SET {sets} WHERE id=?", (*fields.values(), int(rid)))
        c.commit()
        c.close()


def delete(rid: int) -> None:
    _update(rid, status="deleted", enabled=0)


def accept(rid: int) -> None:
    r = get(rid)
    if r:
        _update(rid, status="active", enabled=1 if r["schedule"] else 0)


def dismiss(rid: int) -> None:
    _update(rid, status="dismissed", enabled=0)


def set_schedule(rid: int, hhmm: str, enabled: bool) -> None:
    hhmm = hhmm if re.fullmatch(r"\d{1,2}:\d{2}", hhmm or "") else ""
    _update(rid, schedule=hhmm, enabled=1 if (enabled and hhmm) else 0)


def create_morning() -> int:
    ru = _lang() == "ru"
    ex = get(MORNING_TEMPLATE["name_ru"] if ru else MORNING_TEMPLATE["name_en"])
    if ex:
        return ex["id"]
    return create(MORNING_TEMPLATE["name_ru"] if ru else MORNING_TEMPLATE["name_en"],
                  MORNING_TEMPLATE["triggers"], MORNING_TEMPLATE["steps"])


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------
def match_trigger(question: str):
    """Фраза пользователя запускает ритуал? «Доброе утро, Атлас» → ритуал «Доброе утро»."""
    q = _norm(question)
    if not q or len(q.split()) > 8:
        return None
    for r in list_all(include_suggested=False):
        for t in r["triggers"]:
            tn = _norm(t)
            if tn and (q == tn or (re.search(rf"(?:^|\s){re.escape(tn)}(?:\s|$)", q)
                                   and len(q.split()) - len(tn.split()) <= 2)):
                return r
    return None


def _compose(name: str, results: list) -> str:
    ru = _lang() == "ru"
    body = "\n\n".join(f"[{tool}]\n{str(res)[:900]}" for tool, res in results)
    try:
        from ai_brain import client, MODEL_FAST, MODEL_SMART
        from core import llm_gateway
        model = llm_gateway.reserve_any([MODEL_FAST, MODEL_SMART], len(body) // 3 + 700)
        kw = dict(model=model, max_tokens=500, messages=[
            {"role": "system", "content":
                "You are Atlas, a composed, witty voice assistant. Turn the tool results into ONE short spoken "
                "briefing: 3-5 natural sentences, no lists, no markdown, mention only what matters, skip empty "
                "results. " + ("Answer in Russian." if ru else "Answer in English.") +
                f" This is the user's routine «{name}»; open with a fitting short greeting."},
            {"role": "user", "content": body}])
        if "gpt-oss" in model:
            kw["reasoning_effort"] = "low"
        r = client.chat.completions.create(**kw)
        text = (r.choices[0].message.content or "").strip()
        if text:
            return text
    except Exception as e:
        print(f"[ритуалы] не смог сложить брифинг: {e}")
    return " ".join(str(res)[:200] for _, res in results)


def run(key, speak: bool = False) -> str:
    r = get(key)
    if not r:
        return "Такого ритуала нет." if _lang() == "ru" else "There's no such routine."
    from ai_brain import _run_one_tool
    print(f"[ритуалы] выполняю «{r['name']}»: {', '.join(s['tool'] for s in r['steps'])}")
    results = []
    for s in r["steps"]:
        res, ok, _ms = _run_one_tool(s["tool"], dict(s.get("args") or {}))
        if ok and str(res).strip():
            results.append((s["tool"], res))
    _update(r["id"], last_run=time.time())
    text = _compose(r["name"], results) if results else (
        "Ритуал выполнен, но нечего рассказать." if _lang() == "ru" else "Routine done, nothing to report.")
    if speak and _speak:
        _speak(text)
    return text


# ---------------------------------------------------------------------------
# Привычки
# ---------------------------------------------------------------------------
def log_tools(question: str, turn: list) -> None:
    """Какие инструменты Atlas вызвал в этом ходе — для поиска привычек."""
    rows = []
    for m in turn:
        if isinstance(m, dict) and m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                fn = tc.get("function") if isinstance(tc, dict) else None
                if not isinstance(fn, dict) or fn.get("name") in SKIP_TOOLS or not fn.get("name"):
                    continue
                rows.append((time.time(), fn["name"], fn.get("arguments") or "{}", _norm(question)[:200]))
    if not rows:
        return
    with _lock:
        c = _connect()
        c.executemany("INSERT INTO habit_log (ts, tool, args, question) VALUES (?,?,?,?)", rows)
        c.commit()
        c.close()


def suggest() -> list:
    """Находит повторяющиеся наборы действий и предлагает их ритуалами. Возвращает новые предложения."""
    since = time.time() - HABIT_LOOKBACK_DAYS * 86400
    with _lock:
        c = _connect()
        log = c.execute("SELECT ts, tool, args FROM habit_log WHERE ts>? ORDER BY ts", (since,)).fetchall()
        known = [set(s["tool"] for s in json.loads(r[0] or "[]"))
                 for r in c.execute("SELECT steps FROM routines").fetchall()]
        c.close()
    sessions, cur = [], []
    for ts, tool, args in log:
        if cur and ts - cur[-1][0] > SESSION_GAP:
            sessions.append(cur)
            cur = []
        cur.append((ts, tool, args))
    if cur:
        sessions.append(cur)
    seen = {}                                   # набор инструментов → [(день, час), …]
    last_args = {}
    for s in sessions:
        tools = sorted({t for _, t, _ in s})[:6]
        for _, t, a in s:
            last_args[t] = a
        d = datetime.fromtimestamp(s[0][0])
        for k in range(2, min(4, len(tools)) + 1):
            for combo in itertools.combinations(tools, k):
                seen.setdefault(combo, []).append((d.date(), d.hour + d.minute / 60))
    cands = []
    for combo, occ in seen.items():
        days = {}
        for day, hour in occ:
            days.setdefault(day, hour)
        if len(days) < HABIT_MIN_DAYS:
            continue
        hours = list(days.values())
        if len(hours) > 1 and statistics.pstdev(hours) > HABIT_MAX_HOUR_SPREAD:
            continue
        if any(set(combo) <= k for k in known):
            continue
        cands.append((len(combo), len(days), combo, statistics.mean(hours)))
    cands.sort(reverse=True)
    new, taken = [], []
    ru = _lang() == "ru"
    for _size, _days, combo, hour in cands:
        if any(set(combo) <= t for t in taken) or len(new) >= 2:
            continue
        taken.append(set(combo))
        h, m = int(hour), int(round((hour % 1) * 60 / 5) * 5) % 60
        part = ("Утренний" if hour < 12 else "Дневной" if hour < 18 else "Вечерний") if ru else \
               ("Morning" if hour < 12 else "Afternoon" if hour < 18 else "Evening")
        name = f"{part} ритуал" if ru else f"{part} routine"
        used = {t for r in list_all(include_suggested=True) for t in r["triggers"]}
        triggers = [t for t in (["доброе утро", "good morning"] if hour < 12 else []) if t not in used] or [name.lower()]
        steps = []
        for t in combo:
            try:
                steps.append({"tool": t, "args": json.loads(last_args.get(t) or "{}")})
            except Exception:
                steps.append({"tool": t, "args": {}})
        rid = create(name, triggers, steps, schedule=f"{h:02d}:{m:02d}", status="suggested")
        new.append(rid)
        try:
            from ui_state import notify
            notify("info", "routine_suggest", f"{name}: {', '.join(combo)} · ~{h:02d}:{m:02d}")
        except Exception as e:
            print(f"[ритуалы] уведомление: {e}")
    return new


# ---------------------------------------------------------------------------
# Расписание и фоновый поиск привычек
# ---------------------------------------------------------------------------
def _idle() -> bool:
    try:
        from ui_state import shared_state
        return shared_state.get("state") == "idle"
    except Exception:
        return True


def _scheduler() -> None:
    time.sleep(30)
    last_mine = 0.0
    while True:
        try:
            now = datetime.now()
            for r in list_all(include_suggested=False):
                if not (r["enabled"] and r["schedule"]):
                    continue
                if r["days"] and str(now.weekday()) not in r["days"]:
                    continue
                hh, mm = map(int, r["schedule"].split(":"))
                due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                ran_today = r["last_run"] and r["last_run"].startswith(now.strftime("%d.%m"))
                if 0 <= (now - due).total_seconds() < 15 * 60 and not ran_today:
                    for _ in range(60):
                        if _idle():
                            break
                        time.sleep(5)
                    run(r["id"], speak=True)
            if time.time() - last_mine > 6 * 3600:
                last_mine = time.time()
                suggest()
        except Exception as e:
            print(f"[ритуалы] планировщик: {e}")
        time.sleep(20)


def start(speak=None) -> None:
    global _speak, _started
    _speak = speak
    if _started:
        return
    _started = True
    _connect().close()
    threading.Thread(target=_scheduler, daemon=True, name="routines").start()
    print("[ритуалы] включены: фразы-триггеры, расписание, поиск привычек")
