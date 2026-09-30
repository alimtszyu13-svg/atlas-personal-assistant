"""
Мастерская навыков: Atlas сам учится новому.

«Атлас, научись конвертировать валюты» →
  1) Написать.    Модель пишет одну функцию-навык, описание для роутера и 2–3 теста.
  2) Проверить.   Код разбирается без запуска: разрешены только безопасные модули
                  (математика, даты, текст, httpx только на чтение), запрещены файлы,
                  процессы, eval/exec, отправка данных (POST/PUT/DELETE).
  3) Испытать.    Навык запускается с тестами в ОТДЕЛЬНОМ процессе с таймаутом.
                  Не прошёл — модель получает ошибку и чинит код (до двух попыток).
  4) Спросить.    Уведомление + раздел «Навыки»: код, тесты, кнопки «Установить» / «Отклонить».
  5) Установить.  Только с подтверждения: skills_auto/<имя>.py + описание, git-коммит
                  «atlas-skill», навык доступен сразу, без перезапуска.
"""
import ast
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "memory.db")
SKILL_DIR = os.path.join(ROOT, "skills_auto")
MAX_FIX_ROUNDS = 2

ALLOWED_IMPORTS = {
    "math", "cmath", "datetime", "time", "json", "re", "random", "statistics", "decimal", "fractions",
    "itertools", "functools", "collections", "string", "calendar", "zoneinfo", "hashlib", "base64",
    "uuid", "textwrap", "unicodedata", "urllib.parse", "httpx", "html", "difflib", "operator",
    "typing", "dataclasses",
}
BANNED_NAMES = {"eval", "exec", "compile", "__import__", "open", "input", "globals", "locals", "vars",
                "getattr", "setattr", "delattr", "breakpoint", "exit", "quit", "memoryview"}
BANNED_ATTRS = {"post", "put", "delete", "patch", "system", "popen", "stream"}

_PROMPT = (
    "You extend Atlas, a Python voice assistant, with ONE new tool. Write a single top-level Python "
    "function that does what the user asked. Rules: only these imports: " + ", ".join(sorted(ALLOWED_IMPORTS)) +
    ". No file, process, OS or shell access; no eval/exec; network ONLY via httpx.get(url, timeout=10) to "
    "free public APIs without keys. The function takes simple JSON-able arguments with defaults where "
    "sensible, never raises for normal input (return a short error text instead), and returns a short "
    "plain-text result suitable to be read aloud or summarised. Put all imports inside the function. "
    "Return JSON only: {\"name\": \"snake_case_tool_name\", \"description_en\": \"what it does and when to "
    "use it, for a tool router\", \"description_ru\": \"одно предложение по-русски\", \"parameters\": "
    "<JSON Schema object for the arguments>, \"code\": \"def snake_case_tool_name(...):\\n    ...\", "
    "\"tests\": [{\"args\": {...}, \"expect\": \"substring expected in the result, or empty\"}]} "
    "with 2-3 tests that do not depend on today's exact data (for live APIs use expect: \"\")."
)

_RUNNER = r'''
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("atlas_skill_test", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
fn = getattr(m, sys.argv[2])
out = []
for t in json.loads(sys.stdin.read()):
    try:
        r = fn(**(t.get("args") or {}))
        ok = isinstance(r, str) and r.strip() != ""
        exp = (t.get("expect") or "").strip().lower()
        if ok and exp and exp not in r.lower():
            ok = False
        out.append({"args": t.get("args"), "ok": ok, "result": str(r)[:300]})
    except Exception as e:
        out.append({"args": t.get("args"), "ok": False, "result": f"{type(e).__name__}: {e}"[:300]})
print(json.dumps(out, ensure_ascii=False))
'''

_lock = threading.Lock()
_speak = None
_register = None          # функция из ai_brain: подключить навык без перезапуска
_unregister = None


# ---------------------------------------------------------------------------
# База заявок
# ---------------------------------------------------------------------------
def _connect():
    c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS forge (id INTEGER PRIMARY KEY, ts REAL, request TEXT, name TEXT, "
              "desc_ru TEXT, desc_en TEXT, code TEXT, params TEXT, tests TEXT, report TEXT, status TEXT)")
    return c


def _lang() -> str:
    try:
        from voice import get_response_language
        return get_response_language()
    except Exception:
        return "ru"


def list_items(limit: int = 30) -> list:
    c = _connect()
    rows = c.execute("SELECT id, ts, request, name, desc_ru, desc_en, code, report, status FROM forge "
                     "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return [{"id": r[0], "when": time.strftime("%d.%m %H:%M", time.localtime(r[1])), "request": r[2],
             "name": r[3], "desc_ru": r[4], "desc_en": r[5], "code": r[6] or "",
             "report": json.loads(r[7] or "[]"), "status": r[8]} for r in rows]


# ---------------------------------------------------------------------------
# 2. Проверка кода без запуска
# ---------------------------------------------------------------------------
def static_check(code: str, name: str):
    """→ (ok, причина). Разрешено только безопасное подмножество Python."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"синтаксическая ошибка: {e}"
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if len(funcs) != 1 or funcs[0].name != name:
        return False, f"нужна ровно одна функция верхнего уровня с именем {name}"
    if funcs[0].decorator_list:
        return False, "декораторы запрещены"
    for n in tree.body:
        is_doc = (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                  and isinstance(n.value.value, str))                 # только строка-описание
        if not (isinstance(n, (ast.FunctionDef, ast.Import, ast.ImportFrom)) or is_doc):
            return False, "в модуле допустима только сама функция (и импорты)"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name not in ALLOWED_IMPORTS:
                    return False, f"запрещённый импорт: {a.name}"
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "") not in ALLOWED_IMPORTS:
                return False, f"запрещённый импорт: {node.module}"
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            return False, f"запрещённая функция: {node.id}"
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in BANNED_ATTRS:
                return False, f"запрещённое обращение: .{node.attr}"
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            return False, "global/nonlocal запрещены"
    return True, ""


# ---------------------------------------------------------------------------
# 3. Испытание в отдельном процессе
# ---------------------------------------------------------------------------
def run_tests(code: str, name: str, tests: list):
    tmp = tempfile.mkdtemp(prefix="atlas_skill_")
    path = os.path.join(tmp, f"{name}.py")
    try:
        open(path, "w", encoding="utf-8").write(code)
        r = subprocess.run([sys.executable, "-c", _RUNNER, path, name], input=json.dumps(tests or [{"args": {}}]),
                           capture_output=True, text=True, timeout=40, cwd=tmp)
        if r.returncode != 0:
            return False, [{"args": None, "ok": False, "result": (r.stderr.strip().splitlines() or ["?"])[-1][:300]}]
        report = json.loads(r.stdout.strip().splitlines()[-1])
        return all(t["ok"] for t in report) and bool(report), report
    except subprocess.TimeoutExpired:
        return False, [{"args": None, "ok": False, "result": "превышено время (40 с)"}]
    except Exception as e:
        return False, [{"args": None, "ok": False, "result": str(e)[:300]}]
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. Написать (и починить)
# ---------------------------------------------------------------------------
def _ask(messages: list) -> dict:
    from ai_brain import client, MODEL_SMART, MODEL_FAST
    from core import llm_gateway
    est = sum(len(m["content"]) for m in messages) // 3 + 2500
    model = llm_gateway.reserve_any([MODEL_SMART, MODEL_FAST], est)
    kw = dict(model=model, messages=messages, max_tokens=3000, response_format={"type": "json_object"})
    if "gpt-oss" in model:
        kw["reasoning_effort"] = "medium"
    r = client.chat.completions.create(**kw)
    raw = r.choices[0].message.content or ""
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0)) if m else {}


def _existing_names() -> set:
    try:
        from ai_brain import AVAILABLE_FUNCTIONS
        return set(AVAILABLE_FUNCTIONS)
    except Exception:
        return set()


def _forge(request: str) -> None:
    ru = _lang() == "ru"
    msgs = [{"role": "system", "content": _PROMPT},
            {"role": "user", "content": f"User request: {request}\nExisting tool names (don't reuse): "
                                        f"{', '.join(sorted(_existing_names()))[:3000]}"}]
    data, report, ok, reason = {}, [], False, ""
    for rnd in range(MAX_FIX_ROUNDS + 1):
        data = _ask(msgs)
        name = re.sub(r"\W", "_", str(data.get("name") or "")).strip("_").lower()[:48]
        code = str(data.get("code") or "")
        data["name"] = name
        if not name or name in _existing_names():
            reason = "имя пустое или уже занято"
        else:
            good, reason = static_check(code, name)
            if good:
                ok, report = run_tests(code, name, data.get("tests") or [])
                reason = "" if ok else "тесты не прошли: " + "; ".join(
                    f"{t['args']} → {t['result']}" for t in report if not t["ok"])[:800]
        print(f"[навыки] попытка {rnd + 1}: {'✓ готово' if ok else reason}")
        if ok:
            break
        msgs += [{"role": "assistant", "content": json.dumps(data, ensure_ascii=False)[:6000]},
                 {"role": "user", "content": f"That failed: {reason}. Fix it and return the full JSON again."}]
    status = "ready" if ok else "failed"
    c = _connect()
    pid = c.execute("INSERT INTO forge (ts, request, name, desc_ru, desc_en, code, params, tests, report, status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (time.time(), request, data.get("name"), data.get("description_ru"), data.get("description_en"),
                     data.get("code"), json.dumps(data.get("parameters") or {"type": "object", "properties": {}}),
                     json.dumps(data.get("tests") or [], ensure_ascii=False),
                     json.dumps(report or [{"args": None, "ok": False, "result": reason}], ensure_ascii=False),
                     status)).lastrowid
    c.commit()
    c.close()
    try:
        from ui_state import notify
        notify("info" if ok else "warn", "forge_ready" if ok else "forge_failed",
               f"#{pid} {data.get('name') or ''}: {data.get('description_ru') or request}")
    except Exception as e:
        print(f"[навыки] уведомление: {e}")
    if _speak:
        if ok:
            _speak(f"Я научился новому: {data.get('description_ru') or request}. Код и тесты — в разделе «Навыки». "
                   "Скажите «установи навык», если всё устраивает."
                   if ru else f"I've learned something new: {data.get('description_en') or request}. "
                   "The code and tests are in the Skills section — say 'install the skill' if you're happy with it.")
        else:
            _speak("Не получилось научиться этому надёжно — навык не прошёл проверки. Подробности в разделе «Навыки»."
                   if ru else "I couldn't learn that reliably — the skill failed its checks. Details are in the Skills section.")


def learn(request: str) -> str:
    """Запуск в фоне: разговор не ждёт, пока навык пишется и испытывается."""
    threading.Thread(target=lambda: _safe_forge(request), daemon=True, name="forge").start()
    return ("Принял, учусь. Напишу навык, проверю и испытаю его — сообщу, когда будет готово."
            if _lang() == "ru" else "On it — I'll write the skill, check and test it, and tell you when it's ready.")


def _safe_forge(request: str) -> None:
    try:
        _forge(request)
    except Exception as e:
        print(f"[навыки] не получилось: {e}")
        if _speak:
            _speak("Не получилось научиться: " + str(e)[:120] if _lang() == "ru" else "Couldn't learn that: " + str(e)[:120])


# ---------------------------------------------------------------------------
# 5. Установка, удаление, загрузка при старте
# ---------------------------------------------------------------------------
def _latest(status: str):
    c = _connect()
    row = c.execute("SELECT id FROM forge WHERE status=? ORDER BY id DESC LIMIT 1", (status,)).fetchone()
    c.close()
    return row[0] if row else None


def install(pid: int = 0) -> dict:
    pid = int(pid or 0) or _latest("ready")
    c = _connect()
    row = c.execute("SELECT name, desc_en, desc_ru, code, params, tests, status FROM forge WHERE id=?",
                    (pid or -1,)).fetchone()
    c.close()
    if not row or row[6] != "ready":
        return {"ok": False, "msg_ru": "Нет навыка, готового к установке.", "msg_en": "No skill is ready to install."}
    name, desc_en, desc_ru, code, params, tests, _ = row
    good, reason = static_check(code, name)                 # перепроверка перед установкой
    if not good:
        return {"ok": False, "msg_ru": f"Навык не прошёл повторную проверку: {reason}", "msg_en": f"Re-check failed: {reason}"}
    os.makedirs(SKILL_DIR, exist_ok=True)
    py, meta = os.path.join(SKILL_DIR, f"{name}.py"), os.path.join(SKILL_DIR, f"{name}.json")
    header = f'"""Навык, которому Atlas научился сам: {desc_ru or ""}\nЗаявка #{pid}. Установлен {time.strftime("%d.%m.%Y %H:%M")}."""\n'
    open(py, "w", encoding="utf-8", newline="\n").write(header + code.rstrip() + "\n")
    json.dump({"name": name, "description_en": desc_en, "description_ru": desc_ru, "parameters": json.loads(params),
               "tests": json.loads(tests), "forge_id": pid}, open(meta, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    try:
        subprocess.run(["git", "-C", ROOT, "add", "--", py, meta], capture_output=True, timeout=20)
        subprocess.run(["git", "-C", ROOT, "commit", "-m", f"atlas-skill: {name}", "--", py, meta],
                       capture_output=True, timeout=20)
    except Exception:
        pass
    c = _connect()
    c.execute("UPDATE forge SET status='installed' WHERE id=?", (pid,))
    c.commit()
    c.close()
    if _register:
        _register(name)
    print(f"[навыки] установлен навык {name}")
    return {"ok": True, "msg_ru": f"Навык «{desc_ru or name}» установлен и уже работает.",
            "msg_en": f"Skill '{name}' installed and ready to use."}


def reject(pid: int = 0) -> dict:
    pid = int(pid or 0) or _latest("ready")
    c = _connect()
    c.execute("UPDATE forge SET status='rejected' WHERE id=? AND status IN ('ready','failed')", (pid or -1,))
    c.commit()
    c.close()
    return {"ok": True, "msg_ru": "Навык отклонён.", "msg_en": "Skill rejected."}


def installed() -> list:
    out = []
    if os.path.isdir(SKILL_DIR):
        for f in sorted(os.listdir(SKILL_DIR)):
            if f.endswith(".json"):
                try:
                    out.append(json.load(open(os.path.join(SKILL_DIR, f), encoding="utf-8")))
                except Exception as e:
                    print(f"[навыки] {f}: {e}")
    return out


def load_skill(name: str):
    """→ (функция, схема для модели) или None. Перед загрузкой — повторная проверка кода."""
    py, meta = os.path.join(SKILL_DIR, f"{name}.py"), os.path.join(SKILL_DIR, f"{name}.json")
    try:
        code = open(py, encoding="utf-8").read()
        m = json.load(open(meta, encoding="utf-8"))
        body = code.split('"""', 2)[2] if code.startswith('"""') else code
        good, reason = static_check(body.strip() + "\n", name)
        if not good:
            print(f"[навыки] {name} не загружен: {reason}")
            return None
        import importlib.util
        spec = importlib.util.spec_from_file_location(f"skills_auto.{name}", py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        schema = {"type": "function", "function": {"name": name, "description": m.get("description_en") or name,
                                                    "parameters": m.get("parameters") or {"type": "object", "properties": {}}}}
        return getattr(mod, name), schema
    except Exception as e:
        print(f"[навыки] {name} не загружен: {e}")
        return None


def remove(name: str) -> dict:
    ok = False
    for ext in (".py", ".json"):
        p = os.path.join(SKILL_DIR, f"{name}{ext}")
        if os.path.exists(p):
            os.remove(p)
            ok = True
    if ok and _unregister:
        _unregister(name)
    return {"ok": ok, "msg_ru": f"Навык {name} удалён." if ok else "Такого навыка нет.",
            "msg_en": f"Skill {name} removed." if ok else "No such skill."}


def start(speak=None, register=None, unregister=None) -> None:
    global _speak, _register, _unregister
    _speak, _register, _unregister = speak, register or _register, unregister or _unregister
    _connect().close()
