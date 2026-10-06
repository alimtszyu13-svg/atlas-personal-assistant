"""
Цикл мышления Atlas: понять → (план → выполнить) → ответить. Модель зовётся как можно реже.

    _brain_ask(question, speech)  — один ход разговора
    execute_plan(...)             — несколько действий подряд без модели (или в фоне)
    рабочая память                — цель, последние действия, последний ответ («запиши туда ещё»)
    короткий ответ                — результат простого инструмента → фраза крошечным запросом
    портрет пользователя          — главное о человеке из графа памяти
"""
import concurrent.futures
import json
import os
import re
import threading
import time
from datetime import datetime

import tool_router
from brain import prompt, providers, state, tools
from brain.state import TaskCancelled, _check_cancel
from core import llm_gateway
from core import memory
from database import log_task

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _lang_ru(question: str) -> bool:
    return "(Respond in Russian.)" in question


# =============================================================================
# Рабочая память
# =============================================================================
_AUTO_WAIT_AFTER = {"open_app", "launch_steam_game", "open_url", "open_file", "open_found_file", "open_deep_link",
                    "play_on_spotify", "open_youtube", "open_vscode_project"}


def _short_args(args: dict) -> str:
    return ", ".join(f"{k}={str(v)[:30]}" for k, v in (args or {}).items())[:90]


def _note_action(name: str, args: dict, ok: bool) -> None:
    if name in ("execute_plan", "update_plan"):
        return
    state.situation["actions"].append(f"{name}({_short_args(args)}) {'ok' if ok else 'FAILED'}")
    del state.situation["actions"][:-8]


def _situation_msg():
    s, parts = state.situation, []
    if s["goal"]:
        parts.append(f"current goal: {s['goal']}")
    if s["actions"]:
        parts.append("recent actions: " + "; ".join(s["actions"][-6:]))
    if s["reply"]:
        parts.append(f"your last reply: {s['reply'][:160]}")
    if not parts:
        return None
    return {"role": "system", "content": "Situation (use it to continue tasks and resolve 'it/this/there'): " + " | ".join(parts)}


# =============================================================================
# План: несколько действий подряд без модели
# =============================================================================
def _exec_steps(steps: list, cancellable: bool = True):
    """→ (всё ли удалось, отчёт по шагам)."""
    lines = []
    for i, st in enumerate(steps or [], 1):
        if cancellable:
            _check_cancel()
        if not isinstance(st, dict):
            continue
        name = str(st.get("tool") or "").strip()
        if not name and st.get("wait") is not None:
            time.sleep(min(10.0, max(0.0, float(st.get("wait") or 0))))
            continue
        if name not in tools.AVAILABLE_FUNCTIONS or name == "execute_plan":
            lines.append(f"{i}. {name}: no such tool — use a real tool name")
            return False, lines
        args = st.get("args") if isinstance(st.get("args"), dict) else {}
        res, ok, _ms = tools._run_one_tool(name, dict(args))
        ok = ok and not tools._TRACE_FAIL.search(str(res)[:220])
        _note_action(name, args, ok)
        lines.append(f"{i}. {name} → {'OK' if ok else 'FAILED'}: {str(res)[:300]}")
        if not ok:
            return False, lines
        nxt = steps[i] if i < len(steps) else None
        if (name in _AUTO_WAIT_AFTER and isinstance(nxt, dict) and nxt.get("wait") is None
                and str(nxt.get("tool") or "").startswith(("desktop_", "browser_"))):
            time.sleep(1.5)                       # окно программы должно успеть появиться
        if st.get("wait"):
            time.sleep(min(10.0, float(st["wait"])))
    return True, lines


def execute_plan(goal: str, steps: list, done_message: str = "", background: bool = False) -> str:
    """Runs several actions in order, instantly, in one go."""
    state.situation["goal"] = str(goal)[:160]
    if background:
        def run():
            ok, lines = _exec_steps(steps, cancellable=False)
            try:
                from voice import get_response_language
                ru = get_response_language() == "ru"
            except Exception:
                ru = True
            if ok:
                text = done_message or ("Готово, сэр." if ru else "Done, sir.")
            else:
                last = lines[-1] if lines else ""
                text = f"Фоновая задача остановилась: {last[:160]}" if ru else f"The background task stopped: {last[:160]}"
            print(f"[мозг] фоновая задача «{goal}»: {'готово' if ok else 'остановилась'}")
            try:
                from ui_state import notify
                notify("ok" if ok else "warn", "task_done", text[:200])
            except Exception:
                pass
            if state.announce["fn"]:
                state.announce["fn"](text)
        threading.Thread(target=run, daemon=True, name="plan-bg").start()
        return "BACKGROUND_STARTED"
    ok, lines = _exec_steps(steps)
    return ("ALL_DONE\n" if ok else "STOPPED — a step failed; decide what to do next:\n") + "\n".join(lines)


# =============================================================================
# Что модель знает перед ответом
# =============================================================================
def _context_snapshot(question: str) -> str:
    """Дата, активное окно и — если речь про «это» — выделенный текст или буфер обмена."""
    parts = [datetime.now().strftime("%A %d.%m.%Y, %H:%M")]
    title = ""
    try:
        import pygetwindow as gw
        w = gw.getActiveWindow()
        title = (w.title or "").strip() if w else ""
        if title:
            parts.append(f"active window: {title[:80]}")
    except Exception as e:
        print(f"[context] window read failed: {e}")
    q = question.lower()
    wants_selection = any(k in q for k in ("select", "highlight", "выдел"))
    wants_clipboard = wants_selection or any(k in q for k in (
        "это", "this", "that", "скопир", "copied", "буфер", "clipboard", "ошибк", "error", "трейсбек", "traceback", "разбер"))
    if wants_selection and title and title.upper() != "ATLAS":     # Ctrl+C — только если в фокусе не сам Atlas
        try:
            import pyautogui
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.15)
        except Exception as e:
            print(f"[context] ctrl+c failed: {e}")
    if wants_clipboard:
        try:
            import pyperclip
            clip = pyperclip.paste().strip()
            if clip:
                parts.append("clipboard: " + (clip[-800:] if "Traceback" in clip else clip[:300]))   # у трейсбека важен конец
            else:
                print("[context] clipboard is empty")
        except Exception as e:
            print(f"[context] clipboard read failed: {e}")
    return "Current context (use it to resolve vague references like 'this'/'это'/'selected text'): " + " | ".join(parts)


_COMPLEX_HINTS = ("сравни", "compare", "проанализ", "analy", "исслед", "research", "составь", "план", "plan",
                  "напиши", "write", "объясни", "explain", "почему", "why", "потом", "затем", "then", "найди и", "find and")


def _effort_for(question: str, groups: set) -> str:
    """high — только для сложных многошаговых задач, иначе low (быстрее в разы)."""
    q = question.lower()
    return "high" if any(h in q for h in _COMPLEX_HINTS) or "browser" in groups or len(q.split()) > 20 else "low"


_profile_cache = {"t": 0.0, "text": ""}


def _user_profile() -> str:
    """Главное о пользователе из графа памяти, коротко (кэш на 2 минуты)."""
    if time.time() - _profile_cache["t"] < 120:
        return _profile_cache["text"]
    text = ""
    try:
        import sqlite3
        c = sqlite3.connect(os.path.join(_ROOT, "memory.db"), timeout=5)
        try:
            row = c.execute("SELECT id FROM nodes WHERE name='user'").fetchone()
            if row:
                rows = c.execute("SELECT e.rel, n.label FROM edges e JOIN nodes n ON n.id = e.dst "
                                 "WHERE e.src = ? AND e.active = 1 ORDER BY e.conf DESC, e.updated DESC LIMIT 14",
                                 (row[0],)).fetchall()
                facts = [f"{r.replace('_', ' ')}: {lbl}" for r, lbl in rows if lbl]
                if facts:
                    text = ("About the user (from memory — use it only to understand what they want and why; don't "
                            "recite it unless asked and never start actions because of it): " + "; ".join(facts))
        finally:
            c.close()
    except Exception as e:
        print(f"[мозг] портрет пользователя недоступен: {e}")
    _profile_cache.update(t=time.time(), text=text)
    return text


# =============================================================================
# Короткий ответ после простых инструментов
# =============================================================================
SLIM_TOOLS = {
    "get_weather", "holo_weather", "get_disk_usage", "get_cpu_usage", "get_memory_usage", "get_battery_status",
    "get_uptime", "get_volume", "get_brightness", "calculate", "convert_units", "list_todos", "list_notes",
    "list_timers", "get_news", "get_exchange_rate", "set_timer", "add_todo", "add_note", "complete_todo",
    "set_volume", "volume_up", "volume_down", "mute_volume", "unmute_volume", "set_brightness", "holo_show",
    "holo_graph", "translate_text", "list_today_events", "list_upcoming_events", "study_stats", "get_my_ip",
    "check_internet_speed", "ping_host", "is_website_up", "word_count", "take_screenshot", "lock_screen",
    "create_event", "get_unread_count", "list_steam_games", "launch_steam_game", "open_app", "close_app",
    "play_on_spotify", "play_pause_media", "next_track", "previous_track", "set_theme", "mini_mode",
    "search_file_content", "open_found_file", "open_search_result", "spotify_seek", "spotify_library", "spotify_volume", "install_on_device",
}
# После этих инструментов часто нужен следующий шаг — короткий ответ только если просьба одношаговая
_MAY_CONTINUE = {"search_file_content", "open_found_file", "open_search_result"}
_MORE_STEPS = re.compile(r"\s(?:и|а потом|потом|затем|and|then)\s|прочитай|расскажи|что там|что в нём|что в нем|"
                         r"перескажи|read it|what's in it|summari", re.I)


def _slim_ok(parsed, outcomes, question: str = "") -> bool:
    if not parsed:
        return False
    for (c, n, a), (r, ok, ms) in zip(parsed, outcomes):
        if n not in SLIM_TOOLS or not ok or tools._TRACE_FAIL.search(str(r)[:220]):
            return False
        if n in _MAY_CONTINUE and _MORE_STEPS.search(re.sub(r"^\s*\([^)]*\)\s*", "", question)):
            return False
    return True


def _slim_answer(question: str, parsed, outcomes, on_text) -> str:
    ru = _lang_ru(question)
    q = re.sub(r"^\s*\([^)]*\)\s*", "", question)
    res = "\n".join(f"{n}: {str(r)[:700]}" for (c, n, a), (r, ok, ms) in zip(parsed, outcomes))
    msgs = [{"role": "system", "content": prompt.SLIM_PROMPT + (" Answer in Russian." if ru else " Answer in English.")},
            {"role": "user", "content": f"User said: {q}\nTool results:\n{res}"}]
    est = llm_gateway.estimate(msgs) + 160
    model = llm_gateway.reserve_any(providers._candidates(providers.MODEL_SMART, providers.MODEL_FAST), est, _check_cancel)
    t0 = time.time()
    resp, usage = providers._call_model_stream(on_text, model=model, messages=msgs, reasoning_effort="low")
    llm_gateway.record(model, est, usage, llm_gateway.chars(msgs), fallback=160)
    print(f"[время] короткий ответ ({model.split('/')[-1]}): {time.time() - t0:.2f}с, ~{est} ток. вместо полного запроса")
    return (resp.get("content") or "").strip() if isinstance(resp, dict) else ""


# =============================================================================
# Один ход разговора
# =============================================================================
def _ask_model(on_text, msgs_base, schema, model, effort):
    """Запрос к модели через шлюз: подобрать свободную модель, при лимите — ужать или подождать."""
    other = providers.MODEL_FAST if model == providers.MODEL_SMART else providers.MODEL_SMART
    for attempt in range(3):
        try:
            t0 = time.time()
            msgs = llm_gateway.fit(msgs_base, schema)
            est = llm_gateway.estimate(msgs) + llm_gateway.estimate(schema)
            _m, wait, room = llm_gateway.plan(providers._candidates(model, other), est)
            if wait > 3 and room >= 2500:                       # ждать долго, а под запрос покороче место есть
                msgs = llm_gateway.fit(msgs_base, schema, limit=room - 200)
                est = llm_gateway.estimate(msgs) + llm_gateway.estimate(schema)
            model = llm_gateway.reserve_any(providers._candidates(model, other), est, _check_cancel)
            response, usage = providers._call_model_stream(on_text, model=model, messages=msgs, tools=schema,
                                                           reasoning_effort=effort)
            llm_gateway.record(model, est, usage, llm_gateway.chars(msgs) + llm_gateway.chars(schema),
                               fallback=llm_gateway.estimate(response) + (600 if effort == "high" else 150))
            return response, model, time.time() - t0
        except TaskCancelled:
            raise
        except Exception as api_err:
            err, low = str(api_err), str(api_err).lower()
            if attempt < 2 and ("rate_limit" in low or "429" in err):
                wait = providers._retry_after(err)
                llm_gateway.cooldown(model, wait)
                if wait <= 3 and state._cancel_event.wait(wait):
                    raise TaskCancelled()
                continue
            if attempt < 2 and ("tool_use_failed" in low or "tool call validation" in low):
                model = providers.MODEL_SMART
                continue
            raise
    raise RuntimeError("rate_limit: все модели заняты после трёх попыток")


def _brain_ask(question: str, speech=None) -> str:
    """Один ход: понять → (план → выполнить) → ответить."""
    from voice import _stop_speaking
    hist, turn, ru = state.conversation_history, state.turn, _lang_ru(question)

    def on_text(delta):
        if speech is not None:
            if _stop_speaking.is_set():
                raise TaskCancelled()
            speech.feed(delta)

    turn["plan"] = None
    state._cancel_event.clear()
    hist.append({"role": "user", "content": question})
    context_msg = {"role": "system", "content": _context_snapshot(question)}
    active_groups = tool_router.groups_for(question)
    schema = tools._with_learned(tools._with_pairs(tool_router.smart_schema(question, tools.TOOLS_SCHEMA, active_groups)))
    schema = tools._trim_schema(question, schema)
    schema = tools._with_intent(question, schema)          # «включи …» — музыка и кино всегда под рукой
    words = re.sub(r"^\s*\([^)]*\)\s*", "", question).split()
    mem_block = memory.recall_block(question) if len(words) > 2 else None   # «да», «открой его» — память не нужна
    names = {t["function"]["name"] for t in schema}
    first_effort = _effort_for(question, {"browser"} if "browser_open" in names else set())
    print(f"[мозг] инструментов: {len(schema)} | {sorted(names)}")

    try:
        step = 0
        for _ in range(10):
            _check_cancel()
            state._trim_history()
            extra = [context_msg]
            for blk in (_situation_msg(),
                        {"role": "system", "content": _user_profile()} if _user_profile() else None,
                        {"role": "system", "content": mem_block} if mem_block else None,
                        turn["lesson_msg"],
                        {"role": "system", "content": "Active plan: " + json.dumps(turn["plan"], ensure_ascii=False)} if turn["plan"] else None,
                        {"role": "system", "content": "The last actions failed. Don't repeat them — rethink the approach, "
                                                      "or tell the user plainly what blocks you."} if turn["failures"] >= 2 else None):
                if blk:
                    extra.append(blk)
            model = providers.MODEL_SMART if step == 0 else providers.MODEL_FAST
            effort = first_effort if step == 0 else "low"
            response, model, dt = _ask_model(on_text, state.clean_messages_for_api(hist) + extra, schema, model, effort)
            print(f"[время] модель ({model.split('/')[-1]}, шаг {step}, {effort}): {dt:.2f}с")

            message = providers._as_message(response)
            dump = message.model_dump()
            dump.pop("annotations", None)
            hist.append(dump)
            if not message.tool_calls:
                reply = (message.content or "").strip() or ("Не расслышал, повторите, пожалуйста, сэр." if ru
                                                            else "I didn't quite catch that — say it again, sir?")
                state.situation["reply"] = reply
                return reply

            calls = message.tool_calls
            parsed = []
            for c in calls:
                try:
                    a = json.loads(c.function.arguments or "{}")
                except Exception:
                    a = {}
                parsed.append((c, c.function.name, {k: v for k, v in a.items() if k} if isinstance(a, dict) else {}))
            if len(calls) > 1 and all(c.function.name in tools.READ_ONLY_TOOLS for c in calls):
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:     # только чтение — параллельно
                    outcomes = list(ex.map(lambda p: tools._run_one_tool(p[1], p[2]), parsed))
            else:
                outcomes = []
                for c, n, a in parsed:
                    _check_cancel()
                    print(f"[мозг] → {n}({_short_args(a)})")
                    outcomes.append(tools._run_one_tool(n, a))

            finished = None
            for (c, n, a), (result, success, ms) in zip(parsed, outcomes):
                step += 1
                g = tool_router.group_of_tool(n)
                tool_router.mark_used(n)
                if g and g not in active_groups:
                    active_groups.add(g)
                    schema = tool_router.add_group(schema, tools.TOOLS_SCHEMA, g)
                rs = str(result)
                ok = success and not tools._TRACE_FAIL.search(rs[:220])
                _note_action(n, a, ok)
                turn["failures"] = 0 if ok else turn["failures"] + 1
                threading.Thread(target=log_task, args=(n, a, result, success, ms), daemon=True).start()
                hist.append({"role": "tool", "tool_call_id": c.id, "content": rs[:state.MAX_TOOL_RESULT_CHARS]})
                if n == "execute_plan" and len(calls) == 1:
                    if rs.startswith("ALL_DONE") and a.get("done_message"):
                        finished = a["done_message"]               # всё выполнено — отвечаем без ещё одного круга
                    elif rs == "BACKGROUND_STARTED":
                        finished = a.get("done_message") and (
                            "Занимаюсь этим в фоне — сообщу, когда будет готово." if ru
                            else "On it in the background — I'll let you know when it's done.")
            if not finished and _slim_ok(parsed, outcomes, question):        # простые инструменты → короткая формулировка
                try:
                    finished = _slim_answer(question, parsed, outcomes, on_text)
                except TaskCancelled:
                    raise
                except Exception as e:
                    print(f"[мозг] короткий ответ не получился ({e}) — продолжаю обычным путём")
            if finished:
                hist.append({"role": "assistant", "content": finished})
                state.situation["reply"] = finished
                return finished
        return "Слишком много шагов — давайте попробуем проще." if ru else "That took too many steps — let's try something simpler."

    except TaskCancelled:
        print("[мозг] прервано пользователем")
        state._drop_dangling_tool_calls()
        hist.append({"role": "assistant", "content": "[Task was cancelled by the user.]"})
        from voice import get_response_language
        return "Хорошо, остановился." if get_response_language() == "ru" else "Alright, stopped."
    except Exception as e:
        print(f"[мозг] ошибка: {e}")
        tools._heal_report(e, "ask_ai")
        state._drop_dangling_tool_calls()
        from voice import get_response_language
        ru = get_response_language() == "ru"
        if "rate_limit" in str(e).lower() or "429" in str(e):
            return ("Упёрся в минутный лимит запросов — дайте мне полминуты, сэр." if ru
                    else "I've hit the per-minute request limit — give me half a minute, sir.")
        return "Не получилось связаться с моделью — попробуйте ещё раз." if ru else "I couldn't reach the model — please try again."
