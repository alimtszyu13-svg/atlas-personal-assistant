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
import json

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

GRAPH_MIN_SIM = 0.45           # насколько сущность должна быть близка к вопросу
_USER_ALIASES = {"user", "the user", "пользователь", "я", "me", "alim", "алим", "Tszyu", "цзю", "алим цзю", "alim tszyu", "creator", "разработчик", "создатель", "автор"}
_ME = re.compile(r"\b(?:мой|моя|моё|мое|мои|моего|моей|моих|мне|меня|обо мне|"
                 r"my|me|mine)\b", re.I)
_graph = {"ids": None, "labels": None, "mat": None, "dirty": True}

_EXTRACT_PROMPT = (
    "You maintain the long-term memory of Atlas, the user's assistant. From the conversation "
    "return JSON only: {\"summary\": str, \"facts\": [{\"s\": str, \"r\": str, \"o\": str, "
    "\"single\": bool}]}. summary: 1-3 short sentences with concrete facts, decisions, plans, "
    "names and numbers, in the language the user mostly used; empty string if nothing is worth "
    "remembering (greetings, small talk). facts: 0-8 durable facts about the user and their world "
    "(goals, dates, preferences, people, projects, scores). Use \"User\" as the subject for the "
    "user. r is short snake_case, e.g. prepares_for, exam_date, weak_area, strong_area, likes, "
    "dislikes, lives_in, studies_at, works_on, uses, goal, deadline, teacher_of, has_score. "
    "single=true when only one value can be true at a time (a date, current city, current score), "
    "false for lists (likes). Don't invent facts; skip anything uncertain."
)

_lock = threading.Lock()
_cache = {"rows": None, "mat": None, "dirty": True}


def _connect():
    c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS turns ("
              "id INTEGER PRIMARY KEY, ts REAL, role TEXT, text TEXT, episode INTEGER)")
    c.execute("CREATE TABLE IF NOT EXISTS episodes ("
              "id INTEGER PRIMARY KEY, started REAL, ended REAL, summary TEXT, emb BLOB)")
    c.execute("CREATE TABLE IF NOT EXISTS nodes ("
              "id INTEGER PRIMARY KEY, name TEXT UNIQUE, label TEXT, emb BLOB)")
    c.execute("CREATE TABLE IF NOT EXISTS edges (id INTEGER PRIMARY KEY, src INTEGER, rel TEXT, "
              "dst INTEGER, conf REAL, updated REAL, active INTEGER DEFAULT 1)")
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

# ---------------------------------------------------------------------------
# Граф знаний: сущности (nodes) и связи между ними (edges)
# ---------------------------------------------------------------------------
def _node(c, label: str) -> int:
    """id сущности; создаёт её (с вектором смысла), если такой ещё нет."""
    label = label.strip()
    key = re.sub(r"\s+", " ", label.lower())
    if key in _USER_ALIASES:
        key, label = "user", "User"
    row = c.execute("SELECT id FROM nodes WHERE name=?", (key,)).fetchone()
    if row:
        return row[0]
    from file_search import _embed
    emb = _embed([label])[0].astype(np.float32)
    _graph["dirty"] = True
    return c.execute("INSERT INTO nodes (name, label, emb) VALUES (?,?,?)",
                     (key, label, emb.tobytes())).lastrowid


def upsert_fact(subject: str, relation: str, value: str,
                single: bool = True, conf: float = 0.8) -> None:
    """Записывает связь. Повтор — повышает уверенность; для однозначной связи
    (single) новое значение заменяет старое, старое помечается неактивным."""
    rel = re.sub(r"\W+", "_", relation.strip().lower()).strip("_")
    now = time.time()
    with _lock:
        c = _connect()
        sid, oid = _node(c, subject), _node(c, value)
        same = c.execute("SELECT id FROM edges WHERE src=? AND rel=? AND dst=? AND active=1",
                         (sid, rel, oid)).fetchone()
        if same:
            c.execute("UPDATE edges SET conf=MIN(1.0, conf+0.1), updated=? WHERE id=?",
                      (now, same[0]))
        else:
            if single:
                c.execute("UPDATE edges SET active=0 WHERE src=? AND rel=? AND active=1",
                          (sid, rel))
            c.execute("INSERT INTO edges (src, rel, dst, conf, updated) VALUES (?,?,?,?,?)",
                      (sid, rel, oid, conf, now))
        c.commit()
        c.close()


def _load_graph(c) -> None:
    if _graph["dirty"] or _graph["mat"] is None:
        rows = c.execute("SELECT id, label, emb FROM nodes").fetchall()
        _graph["ids"] = [r[0] for r in rows]
        _graph["labels"] = {r[0]: r[1] for r in rows}
        _graph["mat"] = (np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32)
                         .reshape(len(rows), -1) if rows else None)
        _graph["dirty"] = False


def graph_facts(query: str, limit: int = 10) -> list:
    """Связи вокруг сущностей из вопроса: по смыслу, по имени и «мой/мне» → User."""
    from file_search import _embed
    q = re.sub(r"^\s*\([^)]*\)\s*", "", query or "").strip()
    if not q:
        return []
    with _lock:
        c = _connect()
        _load_graph(c)
        if _graph["mat"] is None:
            c.close()
            return []
        seeds = set()
        sims = _graph["mat"] @ _embed([q])[0]
        for j in np.argsort(-sims)[:3]:
            if sims[j] >= GRAPH_MIN_SIM:
                seeds.add(_graph["ids"][j])
        low = q.lower()
        for nid, label in _graph["labels"].items():
            if len(label) >= 3 and label.lower() in low:
                seeds.add(nid)
        if _ME.search(q) or _MEMORY_INTENT.search(q):
            row = c.execute("SELECT id FROM nodes WHERE name='user'").fetchone()
            if row:
                seeds.add(row[0])
        if not seeds:
            c.close()
            return []
        ph = ",".join("?" * len(seeds))
        rows = c.execute(
            f"SELECT src, rel, dst FROM edges WHERE active=1 AND "
            f"(src IN ({ph}) OR dst IN ({ph})) ORDER BY conf DESC, updated DESC LIMIT ?",
            (*seeds, *seeds, limit)).fetchall()
        c.close()
    L = _graph["labels"]
    return [f"{L.get(s, '?')} — {r} → {L.get(d, '?')}" for s, r, d in rows]

def _summarize(turns: list):
    """→ (статус, пересказ, факты). Статус: ok | nothing | busy | error."""
    try:
        from ai_brain import client, MODEL_FAST
        from core import llm_gateway
        if llm_gateway.busy(MODEL_FAST):
            return "busy", None, []               # лимит нужен голосовым командам
        dialog = "\n".join(f"{'User' if r == 'user' else 'Atlas'}: {t}" for r, t in turns)[-6000:]
        llm_gateway.reserve(MODEL_FAST, len(dialog) // 3 + 700)
        r = client.chat.completions.create(
            model=MODEL_FAST, reasoning_effort="low", max_tokens=800,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _EXTRACT_PROMPT},
                      {"role": "user", "content": dialog}])
        raw = r.choices[0].message.content or ""
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}
        summary = (data.get("summary") or "").strip() or None
        facts = [f for f in data.get("facts") or []
                 if isinstance(f, dict) and f.get("s") and f.get("r") and f.get("o")]
        if not summary and not facts:
            return "nothing", None, []
        return "ok", summary, facts
    except Exception as e:
        print(f"[память] пересказ не удался: {e}")
        return "error", None, []


def consolidate(force: bool = False) -> None:
    """Непересказанные реплики → эпизод + факты в граф. Без force — только после паузы."""
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
    status, summary, facts = _summarize([(r[2], r[3]) for r in rows])
    if status in ("busy", "error"):
        return                                    # попробуем в следующий раз
    ep = 0                                        # 0 = «нечего помнить», но реплики учтены
    if summary:
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
    for f in facts[:8]:
        try:
            upsert_fact(str(f["s"]), str(f["r"]), str(f["o"]), single=bool(f.get("single", True)))
            print(f"[граф] {f['s']} — {f['r']} → {f['o']}")
        except Exception as e:
            print(f"[граф] не записал факт: {e}")
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
    """Системное сообщение: факты из графа + близкие эпизоды, или None."""
    facts = graph_facts(query)
    min_sim = MEMORY_INTENT_MIN_SIM if _MEMORY_INTENT.search(query or "") else RECALL_MIN_SIM
    lines, total = [], 0
    for ended, summ in recall(query, min_sim=min_sim):
        line = f"- ({_ago(ended)}) {summ}"
        if total + len(line) > RECALL_MAX_CHARS:
            break
        lines.append(line)
        total += len(line)
    if not facts and not lines:
        return None
    parts = []
    if facts:
        parts.append("Known facts:\n" + "\n".join(f"- {f}" for f in facts))
    if lines:
        parts.append("Past conversations:\n" + "\n".join(lines))
    return ("Memory about the user (use it if helpful; don't recite it unprompted):\n"
            + "\n".join(parts))