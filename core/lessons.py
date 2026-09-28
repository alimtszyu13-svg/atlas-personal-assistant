"""
Самообучение Atlas.

Три механизма:
  1) Уроки из поправок.  «Не используй браузер для открытия» → правило, которое
     подмешивается в похожих ситуациях и после перезапуска.
  2) Уроки из ошибок.    Инструмент не сработал, а потом получилось другим →
     «рецепт»: в следующий раз сразу правильный путь.
  3) Память роутера.     Какие инструменты реально понадобились для какого
     вопроса → на похожий вопрос нужные инструменты приходят сразу.

Всё хранится в memory.db (таблицы lessons и tool_memory) и ищется по смыслу
той же MiniLM, что и поиск файлов.
"""
import json
import os
import re
import sqlite3
import threading
import time

import numpy as np

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory.db")
LESSON_K = 3                 # уроков на один вопрос
LESSON_MIN_SIM = 0.42        # ниже — урок к ситуации не относится
LESSON_DUP_SIM = 0.86        # выше — это тот же урок, обновляем
TOOLMEM_MIN_SIM = 0.62       # насколько вопрос должен быть похож на прошлый
TOOLMEM_K = 3
TOOLMEM_MAX_ROWS = 3000

_lock = threading.Lock()
_cache = {"lessons": None, "tools": None}

# поправка пользователя: «не так», «не используй…», «в следующий раз…», «don't…»
CORRECTION_RE = re.compile(
    r"(?:\bне\s+(?:используй|делай|надо|нужно|открывай|включай|ищи|говори|трогай|называй|отвечай|спрашивай)"
    r"|\bне так\b|\bя же (?:сказал|просил)|\bнеправильно|\bв следующий раз|\bвсегда\b|\bникогда\b"
    r"|\bлучше (?:используй|делай|открывай)|\bзапомни,? что\b|\bперестань\b"
    r"|\bdon'?t\b|\bdo not\b|\bnever\b|\balways\b|\bnext time\b|\bwrong\b|\bnot like that\b|\binstead\b|\bstop doing\b)",
    re.I)
# признаки неудачного результата инструмента (смотрим только начало текста)
_FAIL_RE = re.compile(
    r"something went wrong|не найден|not found|has been closed|не смог|couldn'?t|could not|failed|"
    r"ошибк|error|отказываюсь|не удалось|не открыт|недоступ", re.I)

# «сначала открой браузер» — это не ошибка способа, а пропущенный шаг: рецепт из неё вредный
_PRECOND_RE = re.compile(r"сначала вызови|ещё не открыт|еще не открыт|not open yet|call \w+ first", re.I)

_RULE_PROMPT = (
    "You help a voice assistant (Atlas) learn from the user's corrections. You get the recent dialog "
    "and the user's latest message. Decide whether the latest message corrects HOW Atlas behaves: "
    "which tool or method to use, what not to do, how to answer. If yes, return JSON "
    "{\"lesson\": \"<one specific imperative rule in English, max 25 words>\", "
    "\"situation\": \"<when it applies, max 15 words>\"}. If it is not a correction of behaviour "
    "(a new request, small talk, or a fact about the user), return {\"lesson\": \"\"}. JSON only."
)


def _connect():
    c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS lessons (id INTEGER PRIMARY KEY, ts REAL, kind TEXT, "
              "situation TEXT, text TEXT, emb BLOB, uses INTEGER DEFAULT 0, last_used REAL, "
              "active INTEGER DEFAULT 1)")
    c.execute("CREATE TABLE IF NOT EXISTS tool_memory (id INTEGER PRIMARY KEY, ts REAL, "
              "question TEXT, tools TEXT, emb BLOB)")
    return c


def _embed(texts):
    from file_search import _embed as e
    return e(texts)


def _clean(q: str) -> str:
    return re.sub(r"^\s*\([^)]*\)\s*", "", q or "").strip()     # убираем «(Respond in Russian.)»


def is_correction(question: str) -> bool:
    return bool(CORRECTION_RE.search(_clean(question)))


# ---------------------------------------------------------------------------
# Уроки
# ---------------------------------------------------------------------------
def _load_lessons(c):
    if _cache["lessons"] is None:
        rows = c.execute("SELECT id, kind, situation, text, emb FROM lessons WHERE active=1").fetchall()
        mat = (np.frombuffer(b"".join(r[4] for r in rows), dtype=np.float32).reshape(len(rows), -1)
               if rows else None)
        _cache["lessons"] = ([(r[0], r[1], r[2], r[3]) for r in rows], mat)
    return _cache["lessons"]


def add_lesson(text: str, situation: str, kind: str = "rule") -> None:
    """Сохраняет урок; почти такой же уже есть — обновляет его текст."""
    text, situation = (text or "").strip(), (situation or "").strip()
    if not text:
        return
    emb = _embed([f"{situation}: {text}"])[0].astype(np.float32)
    with _lock:
        c = _connect()
        rows, mat = _load_lessons(c)
        if mat is not None:
            sims = mat @ emb
            j = int(np.argmax(sims))
            if sims[j] >= LESSON_DUP_SIM:
                c.execute("UPDATE lessons SET text=?, situation=?, ts=?, emb=? WHERE id=?",
                          (text, situation, time.time(), emb.tobytes(), rows[j][0]))
                c.commit()
                c.close()
                _cache["lessons"] = None
                print(f"[уроки] обновил: {text}")
                return
        c.execute("INSERT INTO lessons (ts, kind, situation, text, emb) VALUES (?,?,?,?,?)",
                  (time.time(), kind, situation, text, emb.tobytes()))
        c.commit()
        c.close()
        _cache["lessons"] = None
    print(f"[уроки] новый урок ({'правило' if kind == 'rule' else 'рецепт'}): {text}")


def lessons_block(question: str):
    """Системное сообщение с уроками, подходящими к вопросу, или None."""
    q = _clean(question)
    if not q:
        return None
    with _lock:
        c = _connect()
        rows, mat = _load_lessons(c)
        if mat is None:
            c.close()
            return None
        sims = mat @ _embed([q])[0]
        picked = [j for j in np.argsort(-sims)[:LESSON_K] if sims[j] >= LESSON_MIN_SIM]
        if picked:
            now = time.time()
            c.executemany("UPDATE lessons SET uses=uses+1, last_used=? WHERE id=?",
                          [(now, rows[j][0]) for j in picked])
            c.commit()
        c.close()
    if not picked:
        return None
    return ("Lessons learned from earlier mistakes and the user's corrections — follow them:\n"
            + "\n".join(f"- {rows[j][3]}" for j in picked))


def learn_from_correction(question: str, context: list) -> None:
    """Поправка пользователя → правило (через быструю модель, в фоне)."""
    try:
        from ai_brain import client, MODEL_FAST
        from core import llm_gateway
        lines = []
        for m in context[-10:]:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            if role == "user":
                lines.append(f"User: {_clean(str(m.get('content') or ''))[:300]}")
            elif role == "assistant":
                calls = [tc.get("function", {}).get("name") for tc in (m.get("tool_calls") or [])
                         if isinstance(tc, dict)]
                if calls:
                    lines.append(f"Atlas used tools: {', '.join(n for n in calls if n)}")
                if m.get("content"):
                    lines.append(f"Atlas: {str(m['content'])[:300]}")
            elif role == "tool":
                lines.append(f"Tool result: {str(m.get('content') or '')[:160]}")
        dialog = "\n".join(lines) + f"\nLATEST USER MESSAGE: {_clean(question)}"
        for _ in range(6):                       # голосовые команды важнее — ждём свободного лимита
            if not llm_gateway.busy(MODEL_FAST, 0.6):
                break
            time.sleep(10)
        llm_gateway.reserve(MODEL_FAST, len(dialog) // 3 + 500)
        r = client.chat.completions.create(
            model=MODEL_FAST, reasoning_effort="low", max_tokens=300,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _RULE_PROMPT},
                      {"role": "user", "content": dialog}])
        raw = r.choices[0].message.content or ""
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}
        if (data.get("lesson") or "").strip():
            add_lesson(data["lesson"], data.get("situation", ""), "rule")
    except Exception as e:
        print(f"[уроки] не смог извлечь правило: {e}")


# ---------------------------------------------------------------------------
# Разбор завершённого хода: рецепты из ошибок + память роутера
# ---------------------------------------------------------------------------
def record_turn(question: str, turn: list) -> None:
    q = _clean(question)
    if not q:
        return
    names_by_id, steps = {}, []                  # steps: (имя инструмента, успех, текст)
    for m in turn:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                if isinstance(tc, dict):
                    names_by_id[tc.get("id")] = (tc.get("function") or {}).get("name")
        elif m.get("role") == "tool":
            name = names_by_id.get(m.get("tool_call_id"))
            text = str(m.get("content") or "")
            if name:
                steps.append((name, not _FAIL_RE.search(text[:220]), text[:160]))
    if not steps:
        return
    try:
        good = sorted({n for n, ok, _ in steps if ok})
        if good and len(q.split()) >= 2:          # «да» ничему не учит
            _remember_tools(q, good)
        # рецепт: сначала что-то сломалось, потом другой инструмент сработал
        fails = [(n, t) for n, ok, t in steps if not ok and not _PRECOND_RE.search(t)]
        if fails and len(q.split()) >= 3:
            last_fail_i = max(i for i, s in enumerate(steps) if not s[1])
            later_ok = [n for n, ok, _ in steps[last_fail_i + 1:] if ok and n not in {f[0] for f in fails}]
            if later_ok:
                bad, err = fails[-1]
                add_lesson(f"For requests like «{q[:90]}», {bad} did not work "
                           f"({err[:70].strip()}); use {later_ok[0]} directly.",
                           q[:90], "recipe")
    except Exception as e:
        print(f"[уроки] разбор хода: {e}")


def _remember_tools(q: str, tools: list) -> None:
    emb = _embed([q])[0].astype(np.float32)
    with _lock:
        c = _connect()
        c.execute("INSERT INTO tool_memory (ts, question, tools, emb) VALUES (?,?,?,?)",
                  (time.time(), q[:300], json.dumps(tools), emb.tobytes()))
        n = c.execute("SELECT COUNT(*) FROM tool_memory").fetchone()[0]
        if n > TOOLMEM_MAX_ROWS:                    # храним только свежий опыт
            c.execute("DELETE FROM tool_memory WHERE id IN (SELECT id FROM tool_memory "
                      "ORDER BY id LIMIT ?)", (n - TOOLMEM_MAX_ROWS,))
        c.commit()
        c.close()
        _cache["tools"] = None


def learned_tools(question: str) -> set:
    """Инструменты, которые понадобились на похожие прошлые вопросы."""
    q = _clean(question)
    if not q:
        return set()
    with _lock:
        if _cache["tools"] is None:
            c = _connect()
            rows = c.execute("SELECT tools, emb FROM tool_memory").fetchall()
            c.close()
            _cache["tools"] = ([json.loads(r[0]) for r in rows],
                               np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32)
                               .reshape(len(rows), -1) if rows else None)
        tools, mat = _cache["tools"]
    if mat is None:
        return set()
    sims = mat @ _embed([q])[0]
    out = set()
    for j in np.argsort(-sims)[:TOOLMEM_K]:
        if sims[j] >= TOOLMEM_MIN_SIM:
            out |= set(tools[j])
    return out


# ---------------------------------------------------------------------------
# Для интерфейса
# ---------------------------------------------------------------------------
def list_lessons() -> list:
    with _lock:
        c = _connect()
        rows = c.execute("SELECT id, kind, situation, text, uses, ts FROM lessons WHERE active=1 "
                         "ORDER BY ts DESC LIMIT 100").fetchall()
        c.close()
    return [{"id": r[0], "kind": r[1], "situation": r[2], "text": r[3], "uses": r[4] or 0,
             "when": time.strftime("%d.%m %H:%M", time.localtime(r[5]))} for r in rows]


def forget_lesson(lesson_id: int) -> None:
    with _lock:
        c = _connect()
        c.execute("UPDATE lessons SET active=0 WHERE id=?", (int(lesson_id),))
        c.commit()
        c.close()
        _cache["lessons"] = None
