"""
Самолечение Atlas.

1) Поймать.     Ошибка в коде Atlas (упавший инструмент, поток, ask_ai) перехватывается
                вместе с трейсбеком. Сетевые сбои, лимиты, истёкшие токены — не баги
                в коде, их пропускаем.
2) Понять.      Собираем контекст: строки вокруг ошибки, функцию целиком, трейсбек.
3) Предложить.  Модель пишет точечную правку «найти → заменить» и объясняет причину.
4) Проверить.   Правка применяется к копии: фрагмент найден ровно один раз, код
                разбирается и компилируется, модуль импортируется в отдельном процессе.
5) Спросить.    Уведомление в интерфейсе + раздел «Самолечение» с изменениями и кнопками.
6) Применить.   Только после подтверждения. Резервная копия в .heal_backup/ и отдельный
                git-коммит «atlas-heal», чтобы любое исправление откатывалось одной командой.

Никогда: применять без подтверждения, трогать файлы вне проекта, .env и не-.py файлы.
"""
import ast
import difflib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "memory.db")
BACKUP_DIR = os.path.join(ROOT, ".heal_backup")
REPEAT_HOURS = 6                     # одну и ту же ошибку не разбираем чаще
MAX_EDITS = 3
MAX_REPLACE_LINES = 60

EXTERNAL_MODULES = ("openai", "httpx", "httpcore", "groq", "requests", "urllib3", "google",
                    "googleapiclient", "elevenlabs", "playwright", "websockets", "aiohttp",
                    "fishaudio", "sounddevice", "pygame", "vosk")
EXTERNAL_RE = re.compile(
    r"rate_limit|\b429\b|quota|invalid_grant|timed? ?out|timeout|connection|network|\bssl\b|"
    r"getaddrinfo|name resolution|unauthori[sz]ed|\b401\b|\b403\b|\b50[0234]\b|service unavailable|"
    r"has been closed|target closed|no internet|device unavailable|portaudio", re.I)

_PROMPT = (
    "You are the self-repair module of Atlas, a Python voice assistant. You get an error that happened "
    "in Atlas's own code, the traceback and the surrounding source. Decide whether it is a bug in this "
    "code (code_bug=true) or an external problem such as network, API limits, missing files or user "
    "data (code_bug=false). For a bug, propose the SMALLEST safe fix as search/replace edits: "
    "'search' must be copied EXACTLY from the given source (same indentation, several whole lines, "
    "unique in the file), 'replace' is the new text. Do not refactor, do not change behaviour beyond "
    "the fix, keep comments' language. Return JSON only: {\"code_bug\": bool, \"diagnosis_ru\": "
    "\"1-2 sentences in Russian\", \"diagnosis_en\": \"1-2 sentences in English\", \"edits\": "
    "[{\"file\": \"relative/path.py\", \"search\": \"...\", \"replace\": \"...\"}], \"confidence\": 0..1}. "
    "Use edits: [] when code_bug is false."
)

_queue = []
_seen = {}
_lock = threading.Lock()
_speak = None
_started = False


# ---------------------------------------------------------------------------
# База предложений
# ---------------------------------------------------------------------------
def _connect():
    c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS heal (id INTEGER PRIMARY KEY, ts REAL, sig TEXT, file TEXT, "
              "line INTEGER, place TEXT, error TEXT, diagnosis_ru TEXT, diagnosis_en TEXT, edits TEXT, "
              "diff TEXT, checks TEXT, confidence REAL, status TEXT)")
    return c


def _known(sig: str) -> bool:
    c = _connect()
    row = c.execute("SELECT 1 FROM heal WHERE sig=? AND ts>? LIMIT 1",
                    (sig, time.time() - 7 * 86400)).fetchone()
    c.close()
    return row is not None


def _save(job, status, dx_ru, dx_en, edits, diff, checks, conf) -> int:
    c = _connect()
    pid = c.execute(
        "INSERT INTO heal (ts, sig, file, line, place, error, diagnosis_ru, diagnosis_en, edits, diff, "
        "checks, confidence, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (time.time(), job["sig"], job["rel"], job["line"], job["where"], job["error"], dx_ru, dx_en,
         json.dumps(edits, ensure_ascii=False), diff, checks, conf, status)).lastrowid
    c.commit()
    c.close()
    return pid


# ---------------------------------------------------------------------------
# 1. Поймать
# ---------------------------------------------------------------------------
def _project_frame(tb_list):
    for fr in reversed(tb_list):
        p = os.path.abspath(fr.filename)
        if (p.startswith(ROOT) and p.endswith(".py") and "site-packages" not in p
                and os.sep + "venv" + os.sep not in p):
            return fr
    return None


def report(exc: BaseException, where: str = "") -> None:
    """Сообщить об ошибке. Безопасно вызывать откуда угодно — никогда не бросает."""
    try:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            return
        fr = _project_frame(traceback.extract_tb(exc.__traceback__))
        if fr is None:
            return
        msg = f"{type(exc).__name__}: {exc}"
        mod = (type(exc).__module__ or "").split(".")[0]
        if mod in EXTERNAL_MODULES or EXTERNAL_RE.search(msg):
            return                                    # сеть, лимиты, чужие сервисы — не баг в коде
        rel = os.path.relpath(os.path.abspath(fr.filename), ROOT).replace("\\", "/")
        if rel.startswith("core/healer"):
            return
        sig = f"{rel}:{fr.lineno}:{type(exc).__name__}"
        now = time.time()
        with _lock:
            if now - _seen.get(sig, 0) < REPEAT_HOURS * 3600:
                return
            _seen[sig] = now
        if _known(sig):
            return
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-2500:]
        with _lock:
            _queue.append({"sig": sig, "rel": rel, "line": fr.lineno, "func": fr.name,
                           "where": where, "error": msg[:500], "tb": tb})
        print(f"[самолечение] поймал ошибку {sig} — разберу, когда Atlas освободится")
    except Exception as e:
        print(f"[самолечение] report: {e}")


# ---------------------------------------------------------------------------
# 2–4. Понять, предложить, проверить
# ---------------------------------------------------------------------------
def _enclosing_function(code: str, line: int) -> str:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ""
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= line <= end and (best is None or node.lineno > best.lineno):
                best = node
    if best is None:
        return ""
    seg = ast.get_source_segment(code, best) or ""
    return seg if seg.count("\n") <= 140 else ""


def _idle() -> bool:
    try:
        from ui_state import shared_state
        from ai_brain import MODEL_SMART
        from core import llm_gateway
        return shared_state.get("state") == "idle" and not llm_gateway.busy(MODEL_SMART, 0.5)
    except Exception:
        return True


def _ask_model(content: str) -> dict:
    from ai_brain import client, MODEL_SMART, MODEL_FAST
    from core import llm_gateway
    model = llm_gateway.reserve_any([MODEL_SMART, MODEL_FAST], len(content) // 3 + 1800)
    kw = dict(model=model, max_tokens=2500, response_format={"type": "json_object"},
              messages=[{"role": "system", "content": _PROMPT}, {"role": "user", "content": content}])
    if "gpt-oss" in model:
        kw["reasoning_effort"] = "medium"
    r = client.chat.completions.create(**kw)
    raw = r.choices[0].message.content or ""
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0)) if m else {}


def _safe_path(rel: str):
    rel = (rel or "").replace("\\", "/").lstrip("/")
    path = os.path.abspath(os.path.join(ROOT, rel))
    if (not path.startswith(ROOT + os.sep) or not path.endswith(".py") or ".env" in rel
            or "venv/" in rel or rel.startswith(".heal_backup")):
        return None
    return path


def _validate(edits: list):
    """→ (новые тексты {путь: код}, diff, отчёт о проверках, прошло ли)."""
    if not edits or len(edits) > MAX_EDITS:
        return {}, "", "слишком много правок или их нет", False
    new_texts, diffs, notes = {}, [], []
    for e in edits:
        path = _safe_path(e.get("file", ""))
        if path is None or not os.path.exists(path):
            return {}, "", f"файл вне проекта или не найден: {e.get('file')}", False
        cur = new_texts.get(path) or open(path, encoding="utf-8").read()
        search, replace = e.get("search") or "", e.get("replace") or ""
        if not search.strip() or search == replace:
            return {}, "", "пустая или ничего не меняющая правка", False
        if cur.count(search) != 1:
            return {}, "", f"фрагмент для замены найден {cur.count(search)} раз(а) — нужна ровно одна", False
        if replace.count("\n") > MAX_REPLACE_LINES:
            return {}, "", "правка слишком большая для точечного исправления", False
        new_texts[path] = cur.replace(search, replace, 1)
    for path, new in new_texts.items():
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        try:
            ast.parse(new, filename=rel)
            compile(new, rel, "exec")
            notes.append(f"{rel}: синтаксис ✓")
        except SyntaxError as e:
            return {}, "", f"{rel}: синтаксическая ошибка после правки ({e})", False
        old = open(path, encoding="utf-8").read()
        diffs.append("".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                  f"a/{rel}", f"b/{rel}", n=3)))
        if os.path.basename(path) != "main.py":        # main.py запускает весь Atlas — только компиляция
            ok, info = _import_check(path, new)
            notes.append(f"{rel}: импорт {'✓' if ok else '✗ ' + info}")
            if not ok:
                return new_texts, "".join(diffs), "\n".join(notes), False
    return new_texts, "".join(diffs), "\n".join(notes), True


def _import_check(path: str, new: str):
    """Исправленный модуль выполняется в отдельном процессе — сам Atlas не затрагивается."""
    tmp = tempfile.mkdtemp(prefix="atlas_heal_")
    try:
        target = os.path.join(tmp, os.path.basename(path))
        open(target, "w", encoding="utf-8").write(new)
        code = ("import importlib.util, sys; sys.path.insert(0, sys.argv[2]); "
                "spec = importlib.util.spec_from_file_location('atlas_heal_check', sys.argv[1]); "
                "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)")
        r = subprocess.run([sys.executable, "-c", code, target, os.path.dirname(path)], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            return True, ""
        last = (r.stderr.strip().splitlines() or ["?"])[-1]
        return False, last[:200]
    except subprocess.TimeoutExpired:
        return True, "(импорт слишком долгий — проверена только компиляция)"
    except Exception as e:
        return False, str(e)[:200]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _handle(job: dict) -> None:
    path = os.path.join(ROOT, job["rel"])
    code = open(path, encoding="utf-8").read()
    lines = code.splitlines()
    a, b = max(0, job["line"] - 40), min(len(lines), job["line"] + 25)
    window = "\n".join(lines[a:b])
    func = _enclosing_function(code, job["line"])
    content = (f"File: {job['rel']}\nError at line {job['line']} in function {job['func']} "
               f"(caught in: {job['where'] or 'thread'}):\n{job['error']}\n\nTraceback (end):\n{job['tb']}\n\n"
               f"Source lines {a + 1}-{b} of {job['rel']}:\n```python\n{window}\n```\n")
    if func and func not in window:
        content += f"\nWhole function {job['func']}:\n```python\n{func}\n```\n"
    print(f"[самолечение] разбираю {job['sig']}…")
    data = _ask_model(content)
    dx_ru = (data.get("diagnosis_ru") or "").strip()
    dx_en = (data.get("diagnosis_en") or "").strip()
    conf = float(data.get("confidence") or 0)
    edits = data.get("edits") or []
    if not data.get("code_bug") or not edits:
        _save(job, "nofix", dx_ru, dx_en, [], "", "правка кода не нужна", conf)
        print(f"[самолечение] это не ошибка кода: {dx_ru}")
        return
    _texts, diff, checks, ok = _validate(edits)
    pid = _save(job, "ready" if ok else "failed", dx_ru, dx_en, edits, diff, checks, conf)
    if not ok:
        print(f"[самолечение] правка #{pid} не прошла проверку: {checks}")
        return
    print(f"[самолечение] исправление #{pid} готово — жду подтверждения: {dx_ru}")
    try:
        from ui_state import notify
        notify("warn", "heal_ready", f"#{pid} · {job['rel']}: {dx_ru}")
    except Exception as e:
        print(f"[самолечение] уведомление в интерфейс не ушло: {e}")
    if _speak:
        threading.Thread(target=_announce, args=(pid,), daemon=True).start()


def _announce(pid: int) -> None:
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


def _worker() -> None:
    while True:
        time.sleep(3)
        with _lock:
            job = _queue.pop(0) if _queue else None
        if not job:
            continue
        for _ in range(400):                      # ждём, пока Atlas освободится (до ~20 мин)
            if _idle():
                break
            time.sleep(3)
        try:
            _handle(job)
        except Exception as e:
            print(f"[самолечение] не смог разобрать {job['sig']}: {e}")


# ---------------------------------------------------------------------------
# 5–6. Список, применить, отклонить
# ---------------------------------------------------------------------------
def list_items(limit: int = 30) -> list:
    c = _connect()
    rows = c.execute("SELECT id, ts, file, line, error, diagnosis_ru, diagnosis_en, diff, checks, "
                     "confidence, status FROM heal ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return [{"id": r[0], "when": time.strftime("%d.%m %H:%M", time.localtime(r[1])), "file": r[2],
             "line": r[3], "error": r[4], "dx_ru": r[5], "dx_en": r[6], "diff": r[7] or "",
             "checks": r[8] or "", "confidence": r[9] or 0, "status": r[10]} for r in rows]


def _latest_ready():
    c = _connect()
    row = c.execute("SELECT id FROM heal WHERE status='ready' ORDER BY id DESC LIMIT 1").fetchone()
    c.close()
    return row[0] if row else None


def apply(pid: int = 0) -> dict:
    pid = int(pid or 0) or _latest_ready()
    if not pid:
        return {"ok": False, "msg_ru": "Нет исправлений, ожидающих подтверждения.",
                "msg_en": "No fixes are waiting for confirmation."}
    c = _connect()
    row = c.execute("SELECT edits, status, diagnosis_en FROM heal WHERE id=?", (pid,)).fetchone()
    c.close()
    if not row or row[1] != "ready":
        return {"ok": False, "msg_ru": f"Исправление #{pid} недоступно для применения.",
                "msg_en": f"Fix #{pid} can't be applied."}
    texts, _diff, checks, ok = _validate(json.loads(row[0]))
    if not ok:
        return {"ok": False, "msg_ru": f"Код изменился с момента проверки — исправление #{pid} больше не подходит ({checks}).",
                "msg_en": f"The code changed since the check — fix #{pid} no longer applies ({checks})."}
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    files = []
    for path, new in texts.items():
        rel = os.path.relpath(path, ROOT)
        shutil.copy2(path, os.path.join(BACKUP_DIR, f"{stamp}_{rel.replace(os.sep, '_')}"))
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
        files.append(rel)
    committed = False
    try:
        subprocess.run(["git", "-C", ROOT, "add", "--", *files], capture_output=True, timeout=20)
        r = subprocess.run(["git", "-C", ROOT, "commit", "-m", f"atlas-heal #{pid}: {(row[2] or '')[:70]}",
                            "--", *files], capture_output=True, text=True, timeout=20)
        committed = r.returncode == 0
    except Exception:
        pass
    c = _connect()
    c.execute("UPDATE heal SET status='applied' WHERE id=?", (pid,))
    c.commit()
    c.close()
    tail_ru = " Откатить: git revert по коммиту «atlas-heal»." if committed else ""
    tail_en = " To undo: git revert the «atlas-heal» commit." if committed else ""
    print(f"[самолечение] исправление #{pid} применено: {', '.join(files)}")
    return {"ok": True,
            "msg_ru": f"Исправление #{pid} применено. Перезапустите меня, чтобы оно заработало.{tail_ru}",
            "msg_en": f"Fix #{pid} applied. Restart me for it to take effect.{tail_en}"}


def reject(pid: int = 0) -> dict:
    pid = int(pid or 0) or _latest_ready()
    if not pid:
        return {"ok": False, "msg_ru": "Нечего отклонять.", "msg_en": "Nothing to reject."}
    c = _connect()
    c.execute("UPDATE heal SET status='rejected' WHERE id=? AND status IN ('ready','failed')", (pid,))
    c.commit()
    c.close()
    return {"ok": True, "msg_ru": f"Исправление #{pid} отклонено.", "msg_en": f"Fix #{pid} rejected."}


# ---------------------------------------------------------------------------
# Запуск: ловушки для необработанных ошибок + фоновый разбор
# ---------------------------------------------------------------------------
def start(speak=None) -> None:
    global _speak, _started
    _speak = speak
    if _started:
        return
    _started = True
    prev_hook = sys.excepthook

    def _hook(t, v, tb):
        report(v, "main thread")
        prev_hook(t, v, tb)
    sys.excepthook = _hook

    prev_thread_hook = threading.excepthook

    def _thook(args):
        if args.exc_value is not None:
            report(args.exc_value, f"thread {getattr(args.thread, 'name', '?')}")
        prev_thread_hook(args)
    threading.excepthook = _thook
    threading.Thread(target=_worker, daemon=True, name="healer").start()
    print("[самолечение] включено: ошибки в коде будут разобраны, исправления — только с подтверждения")
