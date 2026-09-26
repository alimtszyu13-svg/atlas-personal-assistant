"""
Фоновые миссии: долгие задачи, которые Atlas выполняет, пока ты занят другим.

- start()  — создаёт миссию и запускает её в отдельном потоке.
- У миссии своя история и только безопасные инструменты (поиск, чтение
  страниц, файлы, расчёты). Браузер, клики, удаление — нет: в фоне без
  присмотра им не место.
- Лимиты: миссия уступает голосовым командам — ждёт, пока минутный лимит
  модели занят больше чем на 60%.
- Результат — в заметки; Atlas сообщает о готовности.
"""
import inspect
import json
import os
import sqlite3
import threading
import time
from ui_state import shared_state

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "missions.db")
MAX_STEPS = 12
TOOL_RESULT_CHARS = 2000
MISSION_TOOLS = {
    "search_web", "read_webpage", "search_file_content", "calculate", "convert_units",
    "get_weather", "get_news", "recall_conversations", "translate_text", "word_count",
}
MISSION_PROMPT = (
    "You are Atlas working on a background mission for the user. Work autonomously: search, "
    "then read real sources with read_webpage and cross-check them. Be efficient — about 8 tool "
    "calls at most. Finish with a concise, well-structured result in the user's language "
    "(plain text, short lines, source URLs at the end). Never ask the user questions — make "
    "reasonable assumptions and state them."
)

_lock = threading.Lock()
_running = {}              # id миссии → Event отмены
_speak = None


class _Cancelled(Exception):
    pass


def _connect():
    c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS missions (id INTEGER PRIMARY KEY, goal TEXT, "
              "status TEXT, created REAL, finished REAL, progress TEXT, result TEXT)")
    return c


def _set(mid: int, **fields) -> None:
    with _lock:
        c = _connect()
        cols = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE missions SET {cols} WHERE id=?", (*fields.values(), mid))
        c.commit()
        c.close()


def init(speak) -> None:
    """speak(text) — как Atlas сообщает о готовности. Незавершённые миссии прошлого запуска
    помечаются прерванными."""
    global _speak
    _speak = speak
    with _lock:
        c = _connect()
        c.execute("UPDATE missions SET status='interrupted' WHERE status='running'")
        c.commit()
        c.close()


def start(goal: str) -> int:
    with _lock:
        c = _connect()
        mid = c.execute("INSERT INTO missions (goal, status, created, progress) VALUES (?,?,?,?)",
                        (goal, "running", time.time(), "")).lastrowid
        c.commit()
        c.close()
    ev = threading.Event()
    _running[mid] = ev
    threading.Thread(target=_worker, args=(mid, goal, ev), daemon=True).start()
    print(f"[миссия #{mid}] старт: {goal}")
    return mid


def cancel(mid: int = 0):
    if not _running:
        return None
    mid = mid or max(_running)
    ev = _running.get(mid)
    if ev:
        ev.set()
        return mid
    return None


def recent(n: int = 5) -> list:
    with _lock:
        c = _connect()
        rows = c.execute("SELECT id, goal, status, progress FROM missions "
                         "ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        c.close()
    return rows


def _ru() -> bool:
    from voice import get_response_language
    return get_response_language() == "ru"


def _notify(text: str, kind: str = "ok", key: str = "mission_done") -> None:
    try:                                   # панель уведомлений в интерфейсе
        from ui_state import notify
        notify(kind, key, text)
    except Exception as e:
        print(f"[миссии] уведомление интерфейсу: {e}")
    """Ждём до 2 минут, пока Atlas освободится, и говорим голосом; иначе — тихо в чат."""
    def _run():
        from core.proactive import is_fullscreen
        for _ in range(120):
            if _speak and shared_state.get("state") in (None, "idle") and not is_fullscreen():
                _speak(text)
                return
            time.sleep(1)
        shared_state["chat_history"].append(("Atlas", text))
    threading.Thread(target=_run, daemon=True).start()


def _call(model: str, msgs: list, schema: list, ev: threading.Event):
    from ai_brain import client
    from core import llm_gateway

    def check():
        if ev.is_set():
            raise _Cancelled()

    while llm_gateway.busy(model, 0.4):          # уступаем голосовым командам заранее
        if ev.wait(2):
            raise _Cancelled()
    msgs = llm_gateway.fit(msgs, schema)
    est = llm_gateway.estimate(msgs) + llm_gateway.estimate(schema)
    llm_gateway.reserve(model, est, check)
    r = client.chat.completions.create(model=model, messages=msgs, tools=schema,
                                       reasoning_effort="low")
    llm_gateway.record(model, est, getattr(r, "usage", None),
                       llm_gateway.chars(msgs) + llm_gateway.chars(schema), fallback=est + 300)
    return r.choices[0].message


def _worker(mid: int, goal: str, ev: threading.Event) -> None:
    from ai_brain import AVAILABLE_FUNCTIONS, TOOLS_SCHEMA, MODEL_SMART, MODEL_FAST
    schema = [t for t in TOOLS_SCHEMA if t["function"]["name"] in MISSION_TOOLS]
    msgs = [{"role": "system", "content": MISSION_PROMPT}, {"role": "user", "content": goal}]
    steps, result = [], None
    try:
        for step in range(MAX_STEPS):
            if ev.is_set() or (step and ev.wait(2)):   # пауза между шагами — не душим лимит
                raise _Cancelled()
            model = MODEL_SMART if step == 0 else MODEL_FAST
            try:
                m = _call(model, msgs, schema, ev)
            except _Cancelled:
                raise
            except Exception as e:
                if "tool" not in str(e).lower():
                    raise
                m = _call(MODEL_SMART, msgs, schema, ev)   # сбой разметки gpt-oss — повтор
            entry = {"role": "assistant"}
            if m.content:
                entry["content"] = m.content
            if m.tool_calls:
                entry["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name,
                                  "arguments": tc.function.arguments or "{}"}}
                    for tc in m.tool_calls]
            msgs.append(entry)
            if not m.tool_calls:
                result = (m.content or "").strip()
                break
            for tc in m.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                fn = AVAILABLE_FUNCTIONS.get(name) if name in MISSION_TOOLS else None
                if fn:
                    try:
                        valid = inspect.signature(fn).parameters
                        res = fn(**{k: v for k, v in args.items() if k in valid})
                    except Exception as e:
                        res = f"Error: {e}"
                else:
                    res = f"Tool '{name}' is not available in background missions."
                note = f"{name}({', '.join(f'{k}={str(v)[:40]}' for k, v in args.items())})"
                steps.append(note)
                _set(mid, progress=" → ".join(steps[-6:]))
                print(f"[миссия #{mid}] {note}")
                msgs.append({"role": "tool", "tool_call_id": tc.id,
                             "content": str(res)[:TOOL_RESULT_CHARS]})
        if not result:
            result = "Не успел завершить за отведённые шаги. Сделано: " + "; ".join(steps)
        from notes import add_note
        add_note(f"[Миссия #{mid}] {goal}\n{result}")
        _set(mid, status="done", finished=time.time(), result=result)
        print(f"[миссия #{mid}] готово")
        _notify(f"Миссия готова: {goal[:70]}. Результат в заметках." if _ru()
                else f"Mission complete: {goal[:70]}. The result is in your notes.")
    except _Cancelled:
        _set(mid, status="cancelled", finished=time.time())
        print(f"[миссия #{mid}] отменена")
    except Exception as e:
        _set(mid, status="failed", finished=time.time(), result=str(e))
        print(f"[миссия #{mid}] ошибка: {e}")
        _notify(f"Миссия не удалась: {goal[:60]}." if _ru() else f"Mission failed: {goal[:60]}.", kind="warn", key="mission_failed")
    finally:
        _running.pop(mid, None)