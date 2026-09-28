"""
Три вещи за раз:

1) Самолечение: уведомление больше не теряется. Окно Atlas часто закрыто другими
   окнами, и всплывающая карточка просто не видна, а вслух Atlas молчал, если в тот
   момент был занят. Теперь он дожидается тишины и говорит об исправлении вслух,
   карточка висит дольше, а ошибка отправки уведомления пишется в лог.
2) Заставка: время идёт по кадрам, которые реально показаны. Раньше она считала по
   часам, и если окно появлялось на экране позже, чем загружалась страница,
   заставка «проигрывалась» невидимой. Настройка Windows «без анимаций» больше не
   включает короткую версию, а случайный щелчок в первую секунду её не пропускает.
3) Новый модуль — «Ритуалы»: несколько действий одной фразой («доброе утро» →
   погода, дела, новости, задачи) или по расписанию, плюс Atlas сам замечает
   привычки и предлагает сделать из них ритуал.

Запуск из корня проекта:  python apply_next.py
Резервная копия: backup_next_<время>/
"""
import ast
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
FILES = ["ai_brain.py", "main.py", "web_gui.py", "atlas_ui.html", "core/healer.py"]
for f in FILES:
    if not os.path.exists(os.path.join(ROOT, f)):
        sys.exit(f"Не найден {f} — запусти из корня проекта Atlas (и сначала apply_heal.py).")
backup = os.path.join(ROOT, time.strftime("backup_next_%Y%m%d_%H%M%S"))
for f in FILES:
    os.makedirs(os.path.dirname(os.path.join(backup, f)), exist_ok=True)
    shutil.copy2(os.path.join(ROOT, f), os.path.join(backup, f))
print(f"Резервная копия: {backup}")
src = {f: open(os.path.join(ROOT, f), encoding="utf-8").read() for f in FILES}
report = []


def rep(f, old, new, what):
    s = src[f]
    if new in s:
        report.append(f"  ✓ {f}: {what} (уже было)")
    elif s.count(old) == 1:
        src[f] = s.replace(old, new, 1)
        report.append(f"  ✓ {f}: {what}")
    else:
        report.append(f"  ! {f}: {what} — фрагмент не найден, пропущено")


# ===========================================================================
# 1. Самолечение: уведомление вслух, когда Atlas освободится
# ===========================================================================
H = "core/healer.py"
rep(H, '''    try:
        from ui_state import notify
        notify("warn", "heal_ready", f"#{pid} · {job['rel']}: {dx_ru}")
    except Exception:
        pass
    if _speak and _idle():
        try:
            from voice import get_response_language
            ru = get_response_language() == "ru"
        except Exception:
            ru = True
        _speak("Сэр, я нашёл у себя ошибку и подготовил исправление — оно в разделе «Самолечение»."
               if ru else "Sir, I found a bug in my own code and prepared a fix — it's in the Self-repair section.")''',
    '''    try:
        from ui_state import notify
        notify("warn", "heal_ready", f"#{pid} · {job['rel']}: {dx_ru}")
    except Exception as e:
        print(f"[самолечение] уведомление в интерфейс не ушло: {e}")
    if _speak:
        threading.Thread(target=_announce, args=(pid,), daemon=True).start()''',
    "о готовом исправлении Atlas скажет вслух, когда освободится")
rep(H, "def _worker() -> None:", '''def _announce(pid: int) -> None:
    """Сказать вслух, когда Atlas освободится: окно Atlas часто закрыто другими окнами,
    и всплывающая карточка просто не видна."""
    quiet = 0
    for _ in range(450):                      # ждём до ~15 минут
        quiet = quiet + 1 if _idle() else 0
        if quiet >= 2:
            break
        time.sleep(2)
    try:
        from voice import get_response_language
        ru = get_response_language() == "ru"
    except Exception:
        ru = True
    print(f"[самолечение] сообщаю голосом об исправлении #{pid}")
    _speak(f"Сэр, я нашёл у себя ошибку и подготовил исправление номер {pid}. "
           "Скажите «примени исправление» или откройте раздел «Самолечение»."
           if ru else f"Sir, I found a bug in my own code and prepared fix number {pid}. "
           "Say 'apply the fix' or open the Self-repair section.")


def _worker() -> None:''', "функция голосового оповещения")

U = "atlas_ui.html"
rep(U, "      if (n.key === 'heal_ready') { o.action = t('act_heal'); o.onAction = function () { openPage('heal'); }; }",
    "      if (n.key === 'heal_ready') { o.action = t('act_heal'); o.life = 30000; o.onAction = function () { openPage('heal'); }; }",
    "карточка об исправлении висит 30 секунд")

# ===========================================================================
# 2. Заставка: время по показанным кадрам
# ===========================================================================
rep(U, "  function introShort() { return !!(layout._prefs && layout._prefs.shortIntro) || reduceMotion; }",
    "  function introShort() { return !!(layout._prefs && layout._prefs.shortIntro); }",
    "короткая заставка — только по вашей настройке")
rep(U, "    intro.scale = introShort() ? 0.35 : 1;\n    intro.t0 = performance.now(); intro.fired = {};",
    "    intro.scale = introShort() ? 0.35 : 1;\n    intro.t0 = performance.now(); intro.fired = {}; intro.elapsed = 0;",
    "отсчёт заставки с нуля")
rep(U, "    intro.scale = 1; intro.t0 = performance.now(); intro.fired = {};",
    "    intro.scale = 1; intro.t0 = performance.now(); intro.fired = {}; intro.elapsed = 0;",
    "отсчёт финала с нуля")
rep(U, "  function startIntro() {\n    if (intro.started) return;\n",
    "  function startIntro() {\n    if (intro.started) return;\n"
    "    if (document.hidden) {                          // окно ещё не на экране — ждём, пока покажется\n"
    "      document.addEventListener('visibilitychange', function onVis() {\n"
    "        if (!document.hidden) { document.removeEventListener('visibilitychange', onVis); startIntro(); }\n"
    "      });\n"
    "      return;\n"
    "    }\n", "заставка стартует, когда окно реально видно")
rep(U, "    const t = (performance.now() - intro.t0) / 1000 / intro.scale;\n    if (t < TL.end - 0.4) intro.t0 = performance.now() - (TL.end - 0.4) * 1000 * intro.scale;",
    "    const t = (intro.elapsed || 0) / intro.scale;\n"
    "    if (t < 1.0) return;                              // случайный щелчок в первую секунду не пропускает заставку\n"
    "    if (t < TL.end - 0.4) intro.elapsed = (TL.end - 0.4) * intro.scale;",
    "пропуск заставки — не раньше первой секунды")
rep(U, "  function updateIntro() {\n    if (!intro.active) return;\n    if (!intro.started) return;                                   // ждём настроек — экран тёмный\n    const t = (performance.now() - intro.t0) / 1000 / intro.scale;",
    "  function updateIntro(dt, raw) {\n    if (!intro.active) return;\n    if (!intro.started) return;                                   // ждём настроек — экран тёмный\n"
    "    intro.elapsed = (intro.elapsed || 0) + (raw ? dt : Math.min(dt || 0, 0.05));   // время идёт по показанным кадрам\n"
    "    const t = intro.elapsed / intro.scale;",
    "время заставки — по показанным кадрам")
rep(U, "    updateIntro();\n    const I = intro;", "    intro.lastFrame = performance.now();\n    updateIntro(dt);\n    const I = intro;",
    "кадр передаёт время в заставку")
rep(U, "  function startOutro() {",
    "  // страховка: если кадры почему-то не рисуются (нет WebGL), заставка всё равно доходит до конца\n"
    "  setInterval(function () {\n"
    "    if (intro.active && intro.started && !document.hidden && performance.now() - (intro.lastFrame || 0) > 1000) updateIntro(0.25, true);\n"
    "  }, 250);\n"
    "  function startOutro() {", "страховка заставки без кадров")

# ===========================================================================
# 3. Ритуалы
# ===========================================================================
ROUT = '"""\nРитуалы Atlas.\n\nРитуал — несколько действий одной фразой («доброе утро» → погода, дела на сегодня,\nновости, список задач) или по расписанию. Инструменты выполняются напрямую, без\nмодели, а в конце ОДИН запрос к модели складывает результаты в короткую речь —\nтак ритуал быстрый и почти не тратит лимит.\n\nПривычки. Atlas записывает, какими инструментами пользуется, и ищет наборы,\nкоторые повторяются примерно в одно и то же время хотя бы 3 разных дня. Такой\nнабор он предлагает сделать ритуалом — создаётся только с согласия.\n"""\nimport itertools\nimport json\nimport os\nimport re\nimport sqlite3\nimport statistics\nimport threading\nimport time\nfrom datetime import datetime\n\nDB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory.db")\nSESSION_GAP = 20 * 60          # действия ближе 20 минут друг к другу — одна «сессия»\nHABIT_MIN_DAYS = 3             # столько разных дней должен повториться набор\nHABIT_MAX_HOUR_SPREAD = 1.5    # насколько «в одно время» (стандартное отклонение, часы)\nHABIT_LOOKBACK_DAYS = 21\nSKIP_TOOLS = {\n    "update_plan", "remember_fact", "recall_conversations", "forget_memory", "save_memory",\n    "recall_memories", "create_routine", "list_routines", "run_routine", "delete_routine",\n    "heal_status", "heal_apply", "heal_reject", "start_mission", "mission_status", "cancel_mission",\n    "set_response_language", "set_always_listening", "set_voice", "set_elevenlabs_voice",\n    "set_microphone", "set_speaker", "set_theme", "read_screen", "click_on_screen",\n    "browser_click", "browser_type", "browser_scroll", "browser_read_page", "browser_press_key",\n    "browser_screenshot_describe", "browser_close", "guess_number", "open_search_result",\n}\nMORNING_TEMPLATE = {\n    "name_ru": "Доброе утро", "name_en": "Good morning",\n    "triggers": ["доброе утро", "good morning"],\n    "steps": [{"tool": "get_weather", "args": {}}, {"tool": "list_today_events", "args": {}},\n              {"tool": "get_news", "args": {"count": 3}}, {"tool": "list_todos", "args": {}}],\n}\n\n_lock = threading.Lock()\n_speak = None\n_started = False\n\n\ndef _connect():\n    c = sqlite3.connect(DB, check_same_thread=False, timeout=30)\n    c.execute("PRAGMA journal_mode=WAL")\n    c.execute("CREATE TABLE IF NOT EXISTS routines (id INTEGER PRIMARY KEY, name TEXT, triggers TEXT, "\n              "steps TEXT, schedule TEXT DEFAULT \'\', days TEXT DEFAULT \'\', enabled INTEGER DEFAULT 0, "\n              "status TEXT DEFAULT \'active\', created REAL, last_run REAL)")\n    c.execute("CREATE TABLE IF NOT EXISTS habit_log (id INTEGER PRIMARY KEY, ts REAL, tool TEXT, "\n              "args TEXT, question TEXT)")\n    return c\n\n\ndef _lang() -> str:\n    try:\n        from voice import get_response_language\n        return get_response_language()\n    except Exception:\n        return "ru"\n\n\ndef _norm(text: str) -> str:\n    t = re.sub(r"^\\s*\\([^)]*\\)\\s*", "", text or "").lower()          # «(Respond in Russian.)»\n    t = re.sub(r"\\b(?:атлас|atlas)\\b", " ", t)\n    t = re.sub(r"[^\\w\\s]", " ", t)\n    return re.sub(r"\\s+", " ", t).strip()\n\n\ndef _row(r) -> dict:\n    return {"id": r[0], "name": r[1], "triggers": json.loads(r[2] or "[]"), "steps": json.loads(r[3] or "[]"),\n            "schedule": r[4] or "", "days": r[5] or "", "enabled": bool(r[6]), "status": r[7],\n            "last_run": time.strftime("%d.%m %H:%M", time.localtime(r[9])) if r[9] else ""}\n\n\n_COLS = "id, name, triggers, steps, schedule, days, enabled, status, created, last_run"\n\n\n# ---------------------------------------------------------------------------\n# Хранение\n# ---------------------------------------------------------------------------\ndef list_all(include_suggested: bool = True) -> list:\n    with _lock:\n        c = _connect()\n        statuses = "(\'active\', \'suggested\')" if include_suggested else "(\'active\')"\n        rows = c.execute(f"SELECT {_COLS} FROM routines WHERE status IN {statuses} ORDER BY status, id").fetchall()\n        c.close()\n    return [_row(r) for r in rows]\n\n\ndef get(key) -> dict:\n    """По id или по имени (без учёта регистра)."""\n    with _lock:\n        c = _connect()\n        if str(key).isdigit():\n            r = c.execute(f"SELECT {_COLS} FROM routines WHERE id=?", (int(key),)).fetchone()\n        else:\n            r = c.execute(f"SELECT {_COLS} FROM routines WHERE lower(name)=lower(?) AND status=\'active\'",\n                          (str(key).strip(),)).fetchone()\n        c.close()\n    return _row(r) if r else None\n\n\ndef create(name: str, triggers: list, steps: list, schedule: str = "", days: str = "",\n           status: str = "active") -> int:\n    clean_steps = [{"tool": s.get("tool"), "args": s.get("args") or {}} for s in steps\n                   if isinstance(s, dict) and s.get("tool")]\n    trig = [t.strip().lower() for t in (triggers or []) if str(t).strip()]\n    with _lock:\n        c = _connect()\n        rid = c.execute("INSERT INTO routines (name, triggers, steps, schedule, days, enabled, status, created) "\n                        "VALUES (?,?,?,?,?,?,?,?)",\n                        (name.strip(), json.dumps(trig, ensure_ascii=False), json.dumps(clean_steps, ensure_ascii=False),\n                         schedule or "", days or "", 1 if (schedule and status == "active") else 0, status,\n                         time.time())).lastrowid\n        c.commit()\n        c.close()\n    print(f"[ритуалы] {\'предложен\' if status == \'suggested\' else \'создан\'} ритуал «{name}»: "\n          f"{\', \'.join(s[\'tool\'] for s in clean_steps)}")\n    return rid\n\n\ndef _update(rid: int, **fields) -> None:\n    if not fields:\n        return\n    sets = ", ".join(f"{k}=?" for k in fields)\n    with _lock:\n        c = _connect()\n        c.execute(f"UPDATE routines SET {sets} WHERE id=?", (*fields.values(), int(rid)))\n        c.commit()\n        c.close()\n\n\ndef delete(rid: int) -> None:\n    _update(rid, status="deleted", enabled=0)\n\n\ndef accept(rid: int) -> None:\n    r = get(rid)\n    if r:\n        _update(rid, status="active", enabled=1 if r["schedule"] else 0)\n\n\ndef dismiss(rid: int) -> None:\n    _update(rid, status="dismissed", enabled=0)\n\n\ndef set_schedule(rid: int, hhmm: str, enabled: bool) -> None:\n    hhmm = hhmm if re.fullmatch(r"\\d{1,2}:\\d{2}", hhmm or "") else ""\n    _update(rid, schedule=hhmm, enabled=1 if (enabled and hhmm) else 0)\n\n\ndef create_morning() -> int:\n    ru = _lang() == "ru"\n    ex = get(MORNING_TEMPLATE["name_ru"] if ru else MORNING_TEMPLATE["name_en"])\n    if ex:\n        return ex["id"]\n    return create(MORNING_TEMPLATE["name_ru"] if ru else MORNING_TEMPLATE["name_en"],\n                  MORNING_TEMPLATE["triggers"], MORNING_TEMPLATE["steps"])\n\n\n# ---------------------------------------------------------------------------\n# Запуск\n# ---------------------------------------------------------------------------\ndef match_trigger(question: str):\n    """Фраза пользователя запускает ритуал? «Доброе утро, Атлас» → ритуал «Доброе утро»."""\n    q = _norm(question)\n    if not q or len(q.split()) > 8:\n        return None\n    for r in list_all(include_suggested=False):\n        for t in r["triggers"]:\n            tn = _norm(t)\n            if tn and (q == tn or (re.search(rf"(?:^|\\s){re.escape(tn)}(?:\\s|$)", q)\n                                   and len(q.split()) - len(tn.split()) <= 2)):\n                return r\n    return None\n\n\ndef _compose(name: str, results: list) -> str:\n    ru = _lang() == "ru"\n    body = "\\n\\n".join(f"[{tool}]\\n{str(res)[:900]}" for tool, res in results)\n    try:\n        from ai_brain import client, MODEL_FAST, MODEL_SMART\n        from core import llm_gateway\n        model = llm_gateway.reserve_any([MODEL_FAST, MODEL_SMART], len(body) // 3 + 700)\n        kw = dict(model=model, max_tokens=500, messages=[\n            {"role": "system", "content":\n                "You are Atlas, a composed, witty voice assistant. Turn the tool results into ONE short spoken "\n                "briefing: 3-5 natural sentences, no lists, no markdown, mention only what matters, skip empty "\n                "results. " + ("Answer in Russian." if ru else "Answer in English.") +\n                f" This is the user\'s routine «{name}»; open with a fitting short greeting."},\n            {"role": "user", "content": body}])\n        if "gpt-oss" in model:\n            kw["reasoning_effort"] = "low"\n        r = client.chat.completions.create(**kw)\n        text = (r.choices[0].message.content or "").strip()\n        if text:\n            return text\n    except Exception as e:\n        print(f"[ритуалы] не смог сложить брифинг: {e}")\n    return " ".join(str(res)[:200] for _, res in results)\n\n\ndef run(key, speak: bool = False) -> str:\n    r = get(key)\n    if not r:\n        return "Такого ритуала нет." if _lang() == "ru" else "There\'s no such routine."\n    from ai_brain import _run_one_tool\n    print(f"[ритуалы] выполняю «{r[\'name\']}»: {\', \'.join(s[\'tool\'] for s in r[\'steps\'])}")\n    results = []\n    for s in r["steps"]:\n        res, ok, _ms = _run_one_tool(s["tool"], dict(s.get("args") or {}))\n        if ok and str(res).strip():\n            results.append((s["tool"], res))\n    _update(r["id"], last_run=time.time())\n    text = _compose(r["name"], results) if results else (\n        "Ритуал выполнен, но нечего рассказать." if _lang() == "ru" else "Routine done, nothing to report.")\n    if speak and _speak:\n        _speak(text)\n    return text\n\n\n# ---------------------------------------------------------------------------\n# Привычки\n# ---------------------------------------------------------------------------\ndef log_tools(question: str, turn: list) -> None:\n    """Какие инструменты Atlas вызвал в этом ходе — для поиска привычек."""\n    rows = []\n    for m in turn:\n        if isinstance(m, dict) and m.get("role") == "assistant":\n            for tc in (m.get("tool_calls") or []):\n                fn = tc.get("function") if isinstance(tc, dict) else None\n                if not isinstance(fn, dict) or fn.get("name") in SKIP_TOOLS or not fn.get("name"):\n                    continue\n                rows.append((time.time(), fn["name"], fn.get("arguments") or "{}", _norm(question)[:200]))\n    if not rows:\n        return\n    with _lock:\n        c = _connect()\n        c.executemany("INSERT INTO habit_log (ts, tool, args, question) VALUES (?,?,?,?)", rows)\n        c.commit()\n        c.close()\n\n\ndef suggest() -> list:\n    """Находит повторяющиеся наборы действий и предлагает их ритуалами. Возвращает новые предложения."""\n    since = time.time() - HABIT_LOOKBACK_DAYS * 86400\n    with _lock:\n        c = _connect()\n        log = c.execute("SELECT ts, tool, args FROM habit_log WHERE ts>? ORDER BY ts", (since,)).fetchall()\n        known = [set(s["tool"] for s in json.loads(r[0] or "[]"))\n                 for r in c.execute("SELECT steps FROM routines").fetchall()]\n        c.close()\n    sessions, cur = [], []\n    for ts, tool, args in log:\n        if cur and ts - cur[-1][0] > SESSION_GAP:\n            sessions.append(cur)\n            cur = []\n        cur.append((ts, tool, args))\n    if cur:\n        sessions.append(cur)\n    seen = {}                                   # набор инструментов → [(день, час), …]\n    last_args = {}\n    for s in sessions:\n        tools = sorted({t for _, t, _ in s})[:6]\n        for _, t, a in s:\n            last_args[t] = a\n        d = datetime.fromtimestamp(s[0][0])\n        for k in range(2, min(4, len(tools)) + 1):\n            for combo in itertools.combinations(tools, k):\n                seen.setdefault(combo, []).append((d.date(), d.hour + d.minute / 60))\n    cands = []\n    for combo, occ in seen.items():\n        days = {}\n        for day, hour in occ:\n            days.setdefault(day, hour)\n        if len(days) < HABIT_MIN_DAYS:\n            continue\n        hours = list(days.values())\n        if len(hours) > 1 and statistics.pstdev(hours) > HABIT_MAX_HOUR_SPREAD:\n            continue\n        if any(set(combo) <= k for k in known):\n            continue\n        cands.append((len(combo), len(days), combo, statistics.mean(hours)))\n    cands.sort(reverse=True)\n    new, taken = [], []\n    ru = _lang() == "ru"\n    for _size, _days, combo, hour in cands:\n        if any(set(combo) <= t for t in taken) or len(new) >= 2:\n            continue\n        taken.append(set(combo))\n        h, m = int(hour), int(round((hour % 1) * 60 / 5) * 5) % 60\n        part = ("Утренний" if hour < 12 else "Дневной" if hour < 18 else "Вечерний") if ru else \\\n               ("Morning" if hour < 12 else "Afternoon" if hour < 18 else "Evening")\n        name = f"{part} ритуал" if ru else f"{part} routine"\n        used = {t for r in list_all(include_suggested=True) for t in r["triggers"]}\n        triggers = [t for t in (["доброе утро", "good morning"] if hour < 12 else []) if t not in used] or [name.lower()]\n        steps = []\n        for t in combo:\n            try:\n                steps.append({"tool": t, "args": json.loads(last_args.get(t) or "{}")})\n            except Exception:\n                steps.append({"tool": t, "args": {}})\n        rid = create(name, triggers, steps, schedule=f"{h:02d}:{m:02d}", status="suggested")\n        new.append(rid)\n        try:\n            from ui_state import notify\n            notify("info", "routine_suggest", f"{name}: {\', \'.join(combo)} · ~{h:02d}:{m:02d}")\n        except Exception as e:\n            print(f"[ритуалы] уведомление: {e}")\n    return new\n\n\n# ---------------------------------------------------------------------------\n# Расписание и фоновый поиск привычек\n# ---------------------------------------------------------------------------\ndef _idle() -> bool:\n    try:\n        from ui_state import shared_state\n        return shared_state.get("state") == "idle"\n    except Exception:\n        return True\n\n\ndef _scheduler() -> None:\n    time.sleep(30)\n    last_mine = 0.0\n    while True:\n        try:\n            now = datetime.now()\n            for r in list_all(include_suggested=False):\n                if not (r["enabled"] and r["schedule"]):\n                    continue\n                if r["days"] and str(now.weekday()) not in r["days"]:\n                    continue\n                hh, mm = map(int, r["schedule"].split(":"))\n                due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)\n                ran_today = r["last_run"] and r["last_run"].startswith(now.strftime("%d.%m"))\n                if 0 <= (now - due).total_seconds() < 15 * 60 and not ran_today:\n                    for _ in range(60):\n                        if _idle():\n                            break\n                        time.sleep(5)\n                    run(r["id"], speak=True)\n            if time.time() - last_mine > 6 * 3600:\n                last_mine = time.time()\n                suggest()\n        except Exception as e:\n            print(f"[ритуалы] планировщик: {e}")\n        time.sleep(20)\n\n\ndef start(speak=None) -> None:\n    global _speak, _started\n    _speak = speak\n    if _started:\n        return\n    _started = True\n    _connect().close()\n    threading.Thread(target=_scheduler, daemon=True, name="routines").start()\n    print("[ритуалы] включены: фразы-триггеры, расписание, поиск привычек")\n'
rp = os.path.join(ROOT, "core", "routines.py")
if os.path.exists(rp) and open(rp, encoding="utf-8").read() == ROUT:
    report.append("  ✓ core/routines.py (уже был)")
else:
    open(rp, "w", encoding="utf-8", newline="\n").write(ROUT)
    report.append("  ✓ core/routines.py: ритуалы и привычки")

A = "ai_brain.py"
BLOCK = '''

# === Ритуалы ===
def create_routine(name: str, triggers: list, steps: list, at: str = "", days: str = "") -> str:
    """Creates a routine: several tool calls run by one phrase or on a schedule."""
    from core import routines
    bad = [s.get("tool") for s in steps if not isinstance(s, dict) or s.get("tool") not in AVAILABLE_FUNCTIONS]
    if bad or not steps:
        return f"Can't create: unknown or missing tools {bad}. Use real tool names from your tool list."
    rid = routines.create(name, triggers or [name], steps, schedule=at, days=days)
    return (f"Routine «{name}» created (#{rid}): {', '.join(s['tool'] for s in steps)}. "
            f"Trigger phrases: {', '.join(triggers or [name])}." + (f" Runs daily at {at}." if at else ""))


def list_routines() -> str:
    from core import routines
    rows = routines.list_all()
    if not rows:
        return "No routines yet."
    return "\\n".join(f"#{r['id']} «{r['name']}» [{r['status']}] say: {', '.join(r['triggers'])}; "
                     f"steps: {', '.join(s['tool'] for s in r['steps'])}"
                     + (f"; at {r['schedule']}" if r['enabled'] else "") for r in rows)


def run_routine(name: str) -> str:
    from core import routines
    return routines.run(name)


def delete_routine(name: str) -> str:
    from core import routines
    r = routines.get(name)
    if not r:
        return f"No routine named «{name}»."
    routines.delete(r["id"])
    return f"Routine «{r['name']}» deleted."


AVAILABLE_FUNCTIONS.update({"create_routine": create_routine, "list_routines": list_routines,
                            "run_routine": run_routine, "delete_routine": delete_routine})
TOOLS_SCHEMA.extend([
    {"type": "function", "function": {"name": "create_routine", "description": "Creates a routine: a named set of tool calls that runs by a trigger phrase (e.g. 'доброе утро') or daily at a time. steps = real tool names with their arguments.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "triggers": {"type": "array", "items": {"type": "string"}}, "steps": {"type": "array", "items": {"type": "object", "properties": {"tool": {"type": "string"}, "args": {"type": "object"}}, "required": ["tool"]}}, "at": {"type": "string", "description": "HH:MM to run daily, optional"}, "days": {"type": "string", "description": "weekday digits 0=Mon..6=Sun, empty = every day"}}, "required": ["name", "steps"]}}},
    {"type": "function", "function": {"name": "list_routines", "description": "Lists the user's routines and routines Atlas suggested from habits.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "run_routine", "description": "Runs a routine by name now.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "delete_routine", "description": "Deletes a routine by name.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
])
for _n in ("create_routine", "list_routines", "run_routine", "delete_routine"):
    tool_router.register_tool(_n, "routines")
tool_router.TRIGGERS["routines"] = ("ритуал", "рутин", "каждое утро", "по утрам", "каждый вечер", "брифинг",
                                    "routine", "briefing", "every morning", "every evening")

_ask_ai_prev_routines = ask_ai


def ask_ai(question: str, speech=None) -> str:
    """Фраза-триггер ритуала выполняется сразу, без модели; остальное — как раньше.
    После каждого ответа инструменты хода записываются для поиска привычек."""
    try:
        from core import routines
        r = routines.match_trigger(question)
        if r:
            print(f"[ритуалы] фраза запускает ритуал «{r['name']}»")
            conversation_history.append({"role": "user", "content": question})
            text = routines.run(r["id"])
            conversation_history.append({"role": "assistant", "content": text})
            return text
    except Exception as e:
        print(f"[ритуалы] {e}")
    reply = _ask_ai_prev_routines(question, speech)
    try:
        from core import routines
        idx = max(i for i, m in enumerate(conversation_history)
                  if isinstance(m, dict) and m.get("role") == "user" and m.get("content") == question)
        threading.Thread(target=routines.log_tools, args=(question, list(conversation_history[idx:])),
                         daemon=True).start()
    except ValueError:
        pass
    except Exception as e:
        print(f"[ритуалы] журнал привычек: {e}")
    return reply
'''
if "# === Ритуалы ===" in src[A]:
    report.append(f"  ✓ {A}: ритуалы (уже было)")
else:
    src[A] = src[A].rstrip() + "\n" + BLOCK
    report.append(f"  ✓ {A}: ритуалы — инструменты, фразы-триггеры, журнал привычек")

M = "main.py"
rep(M, "healer.start(lambda text: _speak_and_update(text, interruptible=False))   # самолечение\n",
    "healer.start(lambda text: _speak_and_update(text, interruptible=False))   # самолечение\n"
    "from core import routines\n"
    "routines.start(lambda text: _speak_and_update(text, interruptible=False))  # ритуалы и привычки\n",
    "запуск ритуалов")

W = "web_gui.py"
METHODS = '''    # ------------------------------------------------------------------
    # Ритуалы
    # ------------------------------------------------------------------
    def get_routines_ui(self) -> list:
        from core import routines
        return routines.list_all()

    def run_routine_ui(self, rid: int) -> str:
        from core import routines
        return routines.run(rid, speak=True)

    def delete_routine_ui(self, rid: int) -> None:
        from core import routines
        routines.delete(rid)

    def accept_routine_ui(self, rid: int) -> None:
        from core import routines
        routines.accept(rid)

    def dismiss_routine_ui(self, rid: int) -> None:
        from core import routines
        routines.dismiss(rid)

    def schedule_routine_ui(self, rid: int, hhmm: str, enabled: bool) -> None:
        from core import routines
        routines.set_schedule(rid, hhmm, enabled)

    def create_morning_ui(self) -> int:
        from core import routines
        return routines.create_morning()


'''
if "def get_routines_ui" in src[W]:
    report.append(f"  ✓ {W}: ритуалы для интерфейса (уже было)")
elif src[W].count("class WebGUI:") == 1:
    src[W] = src[W].replace("class WebGUI:", METHODS + "class WebGUI:", 1)
    report.append(f"  ✓ {W}: ритуалы для интерфейса")
else:
    report.append(f"  ! {W}: class WebGUI не найден — пропущено")

rep(U, "you_node: 'Вы',",
    "you_node: 'Вы',\n"
    "      nav_routines: 'Ритуалы', rt_intro: 'Несколько действий одной фразой или по расписанию. Скажите, например, «Атлас, создай ритуал: по фразе „я дома“ — погода и мои задачи». Ещё я сам замечаю привычки и предлагаю сделать из них ритуал.',\n"
    "      rt_empty: 'Ритуалов пока нет.', rt_morning: 'Создать утренний брифинг', rt_suggest: 'Atlas заметил привычку',\n"
    "      rt_create: 'Создать ритуал', rt_dismiss: 'Не нужно', rt_run: 'Запустить', rt_delete: 'Удалить',\n"
    "      rt_say: 'Скажите', rt_sched_on: 'Каждый день в', rt_running: 'Выполняю…', rt_last: 'последний раз',\n"
    "      n_routine_suggest: 'Atlas заметил привычку', act_routine: 'Посмотреть',",
    "подписи ритуалов (RU)")
rep(U, "you_node: 'You',",
    "you_node: 'You',\n"
    "      nav_routines: 'Routines', rt_intro: 'Several actions by one phrase or on a schedule. Say, for example, \"Atlas, create a routine: when I say I am home — weather and my tasks\". I also notice your habits and offer to turn them into routines.',\n"
    "      rt_empty: 'No routines yet.', rt_morning: 'Create a morning briefing', rt_suggest: 'Atlas noticed a habit',\n"
    "      rt_create: 'Create routine', rt_dismiss: 'No thanks', rt_run: 'Run', rt_delete: 'Delete',\n"
    "      rt_say: 'Say', rt_sched_on: 'Every day at', rt_running: 'Running…', rt_last: 'last run',\n"
    "      n_routine_suggest: 'Atlas noticed a habit', act_routine: 'Review',",
    "подписи ритуалов (EN)")
rep(U, '    <button data-page="heal" data-i18n-aria="nav_heal">',
    '    <button data-page="routines" data-i18n-aria="nav_routines"><svg width="18" height="18" viewBox="0 0 18 18" fill="none" '
    'stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M3.5 12.5a5.5 5.5 0 0 1 11 0"/><path d="M1.5 14.5h15"/>'
    '<path d="M9 2.5v2M3.3 5.3l1.4 1.4M14.7 5.3l-1.4 1.4"/></svg><span data-i18n="nav_routines">Ритуалы</span></button>\n'
    '    <button data-page="heal" data-i18n-aria="nav_heal">', "кнопка «Ритуалы» слева")
rep(U, "projects: pageProjects, heal: pageHeal })[name]();",
    "projects: pageProjects, heal: pageHeal, routines: pageRoutines })[name]();", "раздел подключён к навигации")
PAGE = '''  // --- ритуалы ---
  function pageRoutines() {
    pageBody.appendChild(el('div', 'hint', t('rt_intro')));
    const top = el('div', 'tool-grid');
    const mk = el('button', 'chip', t('rt_morning'));
    mk.addEventListener('click', function () { call('create_morning_ui').then(function () { openPage('routines', true); }).catch(console.log); });
    top.appendChild(mk);
    pageBody.appendChild(top);
    const box = el('div'); box.style.cssText = 'display:flex; flex-direction:column; gap:12px;';
    pageBody.appendChild(box);
    box.appendChild(el('div', 'empty', t('loading')));
    call('get_routines_ui').then(function (rows) {
      box.textContent = '';
      if (!rows.length) { box.appendChild(el('div', 'empty', t('rt_empty'))); return; }
      rows.forEach(function (r) {
        const it = el('div', 'item');
        const head = el('div', 'row');
        head.appendChild(el('b', 'grow', r.name));
        if (r.status === 'suggested') head.appendChild(el('small', 'ok-t', t('rt_suggest')));
        else if (r.last_run) head.appendChild(el('small', 'muted', t('rt_last') + ': ' + r.last_run));
        it.appendChild(head);
        if (r.triggers.length) it.appendChild(el('small', null, t('rt_say') + ': «' + r.triggers.join('», «') + '»'));
        const steps = el('div', 'tool-grid');
        r.steps.forEach(function (s) { steps.appendChild(el('span', 'chip', s.tool.replace(/_/g, ' '))); });
        it.appendChild(steps);
        if (r.status !== 'suggested') {
          const row = el('div', 'row');
          const sw = el('label', 'switch');
          const cb = el('input'); cb.type = 'checkbox'; cb.checked = !!r.enabled;
          sw.appendChild(cb); sw.appendChild(el('span', null, t('rt_sched_on')));
          const tm = el('input'); tm.type = 'time'; tm.className = 'rt-time'; tm.value = r.schedule || '07:30';
          tm.setAttribute('aria-label', t('rt_sched_on'));
          function save() { call('schedule_routine_ui', r.id, tm.value, cb.checked).catch(console.log); }
          cb.addEventListener('change', save); tm.addEventListener('change', save);
          row.appendChild(sw); row.appendChild(tm);
          it.appendChild(row);
        }
        const acts = el('div', 'acts');
        if (r.status === 'suggested') {
          const ok = el('button', null, t('rt_create')), no = el('button', null, t('rt_dismiss'));
          ok.addEventListener('click', function () { call('accept_routine_ui', r.id).then(function () { openPage('routines', true); }).catch(console.log); });
          no.addEventListener('click', function () { call('dismiss_routine_ui', r.id).then(function () { openPage('routines', true); }).catch(console.log); });
          acts.appendChild(ok); acts.appendChild(no);
        } else {
          const run = el('button', null, t('rt_run')), del = el('button', null, t('rt_delete'));
          run.addEventListener('click', function () {
            pop({ icon: 'ok', title: r.name, text: t('rt_running'), life: 4000 });
            call('run_routine_ui', r.id).catch(console.log);
          });
          del.addEventListener('click', function () { call('delete_routine_ui', r.id).then(function () { openPage('routines', true); }).catch(console.log); });
          acts.appendChild(run); acts.appendChild(del);
        }
        it.appendChild(acts);
        box.appendChild(it);
      });
    }).catch(function () { box.textContent = t('load_fail'); });
  }

  // --- самолечение ---'''
rep(U, "  // --- самолечение ---", PAGE, "страница «Ритуалы»")
rep(U, "      if (n.key === 'heal_ready') {",
    "      if (n.key === 'routine_suggest') { o.action = t('act_routine'); o.life = 20000; o.onAction = function () { openPage('routines'); }; }\n"
    "      if (n.key === 'heal_ready') {", "уведомление о привычке открывает «Ритуалы»")
CSS = ("  .rt-time { height: 34px; padding: 0 10px; border: 1px solid var(--line); background: rgba(2,4,10,.6); "
       "color: var(--text); font: 500 13px \"Exo 2\", sans-serif; }\n"
       "  body[data-theme=\"light\"] .rt-time { background: rgba(255,255,255,.85); color: #0E1A33; }\n</style>")
if ".rt-time" in src[U]:
    report.append(f"  ✓ {U}: оформление ритуалов (уже было)")
elif src[U].count("</style>") == 1:
    src[U] = src[U].replace("</style>", CSS, 1)
    report.append(f"  ✓ {U}: оформление ритуалов")

# ===========================================================================
ok = True
for f in ("ai_brain.py", "main.py", "web_gui.py", "core/healer.py"):
    try:
        ast.parse(src[f], filename=f)
    except SyntaxError as e:
        ok = False
        report.append(f"  ! {f}: {e} — файл НЕ изменён")
        continue
    open(os.path.join(ROOT, f), "w", encoding="utf-8", newline="\n").write(src[f])
    report.append(f"  ✓ синтаксис {f}")
try:
    ast.parse(open(rp, encoding="utf-8").read(), filename="core/routines.py")
    report.append("  ✓ синтаксис core/routines.py")
except SyntaxError as e:
    ok = False
    report.append(f"  ! core/routines.py: {e}")
open(os.path.join(ROOT, U), "w", encoding="utf-8", newline="\n").write(src[U])
print("\n".join(report))
print("\nГотово. Запускай: python main.py" if ok else f"\nЕсть ошибка — пришли вывод (копия: {backup}).")
