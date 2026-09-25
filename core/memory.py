"""
Долгая память Atlas: эпизоды разговоров.

- log_turn()          — каждая реплика пишется в memory.db.
- consolidate()       — «сон»: после паузы реплики сжимаются в эпизод —
                        пересказ в 1-3 предложения + вектор смысла (MiniLM).
- recall_block()      — перед ответом подмешивает 2-3 эпизода, близких к
                        вопросу, в пределах ~300 токенов.
"""
import os
import re
import sqlite3
import threading
import time
import numpy as np

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory.db")
IDLE_BEFORE_SLEEP = 300        # секунд тишины перед пересказом
MAX_TURNS_PER_EPISODE = 40
RECALL_K = 3
RECALL_MIN_SIM = 0.35          # ниже — эпизод к вопросу не относится
RECALL_MAX_CHARS = 1200        # ~300 токенов на воспоминания
# «помнишь?», «что мы обсуждали?» — прямой вопрос о памяти: достаём даже слабые совпадения
_MEMORY_INTENT = re.compile(
    r"помни|помнишь|припомн|обсужда|говорил|рассказывал|напомни|знаешь обо мне|"
    r"remember|recall|we discussed|did i (?:tell|say|mention)|know about me", re.I)
MEMORY_INTENT_MIN_SIM = 0.15

_lock = threading.Lock()
_cache = {"rows": None, "mat": None, "dirty": True}


def _connect():
    c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS turns ("
              "id INTEGER PRIMARY KEY, ts REAL, role TEXT, text TEXT, episode INTEGER)")
    c.execute("CREATE TABLE IF NOT EXISTS episodes ("
              "id INTEGER PRIMARY KEY, started REAL, ended REAL, summary TEXT, emb BLOB)")
    return c


def log_turn(role: str, text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    with _lock:
        c = _connect()
        c.execute("INSERT INTO turns (ts, role, text) VALUES (?,?,?)",
                  (time.time(), role, text[:2000]))
        c.commit()
        c.close()


def _summarize(turns: list):
    """→ ("ok", текст) | ("nothing", None) | ("busy", None) | ("error", None)"""
    try:
        from ai_brain import client, MODEL_FAST
        from core import llm_gateway
        if llm_gateway.busy(MODEL_FAST):
            return "busy", None                   # лимит нужен голосовым командам
        dialog = "\n".join(f"{'User' if r == 'user' else 'Atlas'}: {t}" for r, t in turns)[-6000:]
        llm_gateway.reserve(MODEL_FAST, len(dialog) // 3 + 400)
        r = client.chat.completions.create(
            model=MODEL_FAST, reasoning_effort="low", max_tokens=400,
            messages=[{"role": "system", "content":
                       "Summarize this conversation between the user and the assistant Atlas "
                       "in 1-3 short sentences. Keep concrete facts, decisions, plans, "
                       "preferences, names and numbers; skip greetings and small talk. "
                       "Write in the language the user mostly used. If there is nothing "
                       "worth remembering, reply exactly: NOTHING"},
                      {"role": "user", "content": dialog}])
        s = (r.choices[0].message.content or "").strip()
        if not s or s.upper().startswith("NOTHING"):
            return "nothing", None
        return "ok", s
    except Exception as e:
        print(f"[память] пересказ не удался: {e}")
        return "error", None


def consolidate(force: bool = False) -> None:
    """Непересказанные реплики → эпизод. Без force — только после паузы."""
    with _lock:
        c = _connect()
        rows = c.execute("SELECT id, ts, role, text FROM turns "
                         "WHERE episode IS NULL ORDER BY id").fetchall()
        c.close()
    if not rows:
        return
    idle = time.time() - rows[-1][1]
    if not force and idle < IDLE_BEFORE_SLEEP and len(rows) < MAX_TURNS_PER_EPISODE:
        return
    rows = rows[:MAX_TURNS_PER_EPISODE]
    status, summary = _summarize([(r[2], r[3]) for r in rows])
    if status in ("busy", "error"):
        return                                    # попробуем в следующий раз
    ep = 0                                        # 0 = «нечего помнить», но реплики учтены
    if status == "ok":
        from file_search import _embed
        emb = _embed([summary])[0].astype(np.float32)
        with _lock:
            c = _connect()
            ep = c.execute("INSERT INTO episodes (started, ended, summary, emb) VALUES (?,?,?,?)",
                           (rows[0][1], rows[-1][1], summary, emb.tobytes())).lastrowid
            c.commit()
            c.close()
        _cache["dirty"] = True
        print(f"[память] новый эпизод: {summary}")
    with _lock:
        c = _connect()
        c.executemany("UPDATE turns SET episode=? WHERE id=?", [(ep, r[0]) for r in rows])
        c.commit()
        c.close()


def start_sleep_cycle() -> None:
    """Фоновый «сон»: раз в минуту проверяет, не пора ли пересказать разговор."""
    def _run():
        time.sleep(20)
        while True:
            try:
                consolidate()
            except Exception as e:
                print(f"[память] консолидация: {e}")
            time.sleep(60)
    threading.Thread(target=_run, daemon=True).start()


def _ago(ts: float) -> str:
    d = (time.time() - ts) / 86400
    if d < 1:
        return "today"
    if d < 2:
        return "yesterday"
    return f"{int(d)} days ago"


def recall(query: str, k: int = RECALL_K, min_sim: float = RECALL_MIN_SIM) -> list:
    """[(время, пересказ)] — эпизоды, близкие к вопросу по смыслу; свежие чуть выше."""
    from file_search import _embed
    query = re.sub(r"^\s*\([^)]*\)\s*", "", query or "")
    with _lock:
        if _cache["dirty"] or _cache["mat"] is None:
            c = _connect()
            rows = c.execute("SELECT ended, summary, emb FROM episodes").fetchall()
            c.close()
            _cache["rows"] = [(r[0], r[1]) for r in rows]
            _cache["mat"] = (np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32)
                             .reshape(len(rows), -1) if rows else None)
            _cache["dirty"] = False
    if _cache["mat"] is None or not query.strip():
        return []
    sims = _cache["mat"] @ _embed([query])[0]
    now = time.time()
    scored = []
    for (ended, summ), s in zip(_cache["rows"], sims):
        if s >= min_sim:
            recency = float(np.exp(-(now - ended) / 86400 / 30))
            scored.append((0.8 * float(s) + 0.2 * recency, ended, summ))
    scored.sort(reverse=True)
    return [(ended, summ) for _, ended, summ in scored[:k]]


def recall_block(query: str):
    """Готовое системное сообщение с воспоминаниями или None."""
    lines, total = [], 0
    min_sim = MEMORY_INTENT_MIN_SIM if _MEMORY_INTENT.search(query or "") else RECALL_MIN_SIM
    for ended, summ in recall(query, min_sim=min_sim):
        line = f"- ({_ago(ended)}) {summ}"
        if total + len(line) > RECALL_MAX_CHARS:
            break
        lines.append(line)
        total += len(line)
    if not lines:
        return None
    return ("Relevant memories from past conversations with the user "
            "(use them if helpful; don't recite them unprompted):\n" + "\n".join(lines))