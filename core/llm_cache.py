"""
Кэш служебных ответов модели: тот же вопрос с тем же текстом — ответ мгновенно и без запроса.

Кэшируются только «чистые» задачи без побочных эффектов: перефразировка команды (смысловой слой),
конспект того же текста, ответ по той же записи, перевод, исправление текста. Разговор с мозгом
(инструменты, история) НЕ кэшируется — там важна свежесть.

    get(system, text, max_tokens) → ответ | None;  put(system, text, max_tokens, ответ, ttl)
Файл atlas_llm_cache.db (не в git). LLM_CACHE=off — выключить.
"""
import hashlib
import os
import sqlite3
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "atlas_llm_cache.db")
TTL = 6 * 3600
MAX_ROWS = 3000
_lock = threading.Lock()
_s = {"hits": 0, "misses": 0, "forced": None}


def enabled() -> bool:
    if _s["forced"] is not None:
        return _s["forced"]
    if os.getenv("ATLAS_TESTING") == "1":
        return False
    return (os.getenv("LLM_CACHE") or "on").strip().lower() not in ("off", "0", "false", "no")


def force(on) -> None:
    """Для тестов: True/False — включить/выключить независимо от окружения; None — как обычно."""
    _s["forced"] = on


def key(system: str, text: str, max_tokens: int = 0) -> str:
    return hashlib.sha256(f"{system}\x1f{text}\x1f{max_tokens}".encode("utf-8", "ignore")).hexdigest()


def _connect():
    c = sqlite3.connect(DB, timeout=10, check_same_thread=False)
    c.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT, exp REAL, ts REAL)")
    return c


def get(system: str, text: str, max_tokens: int = 0):
    if not enabled():
        return None
    try:
        with _lock:
            c = _connect()
            try:
                row = c.execute("SELECT v, exp FROM cache WHERE k=?", (key(system, text, max_tokens),)).fetchone()
            finally:
                c.close()
    except Exception:
        return None
    if row and row[1] > time.time():
        _s["hits"] += 1
        return row[0]
    _s["misses"] += 1
    return None


def put(system: str, text: str, max_tokens: int, value: str, ttl: float = TTL) -> None:
    if not enabled() or not value:
        return
    try:
        with _lock:
            c = _connect()
            try:
                now = time.time()
                c.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?)", (key(system, text, max_tokens), value, now + ttl, now))
                if c.execute("SELECT COUNT(*) FROM cache").fetchone()[0] > MAX_ROWS:
                    c.execute("DELETE FROM cache WHERE exp < ? OR k IN (SELECT k FROM cache ORDER BY ts LIMIT ?)",
                              (now, MAX_ROWS // 5))
                c.commit()
            finally:
                c.close()
    except Exception as e:
        print(f"[кэш] {e}")


def stats() -> dict:
    return {"hits": _s["hits"], "misses": _s["misses"]}
