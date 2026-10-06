"""
Инструменты отдельных возможностей Atlas — каждая в своём разделе и одной регистрацией.

    самолечение · ритуалы · мастерская навыков · браузер · программы Windows · учёба ·
    итоги дня · голо-экран · граф памяти · жесты · мини-окно · проверка памяти

Сами возможности живут в core/*; здесь — только «ручки» для модели.
"""
import json
import os
import re
import sqlite3
from datetime import datetime as _dt

import tool_router
from brain import state
from brain.tools import register, unregister, AVAILABLE_FUNCTIONS, TOOLS_SCHEMA, TOOL_PAIRS
from core import llm_gateway

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_S, _I, _N, _B = {"type": "string"}, {"type": "integer"}, {"type": "number"}, {"type": "boolean"}


def _lang() -> str:
    try:
        from voice import get_response_language
        return get_response_language()
    except Exception:
        return "ru"


_heal_lang = _lang          # старое имя


def _pick(r: dict) -> str:
    return r["msg_ru"] if _lang() == "ru" else r["msg_en"]


# =============================================================================
# Самолечение
# =============================================================================
def heal_status() -> str:
    """Lists fixes of Atlas's own code that are waiting for the user's confirmation."""
    from core import healer
    items = [i for i in healer.list_items(10) if i["status"] == "ready"]
    if not items:
        return "No fixes are waiting for confirmation."
    return "\n".join(f"#{i['id']} {i['file']}: {i['dx_en'] or i['dx_ru']}" for i in items)


def heal_apply(fix_id: int = 0) -> str:
    """Applies a prepared fix — ONLY when the user explicitly said to apply it."""
    from core import healer
    return _pick(healer.apply(fix_id))


def heal_reject(fix_id: int = 0) -> str:
    from core import healer
    return _pick(healer.reject(fix_id))


# =============================================================================
# Ритуалы
# =============================================================================
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
    return "\n".join(f"#{r['id']} «{r['name']}» [{r['status']}] say: {', '.join(r['triggers'])}; "
                     f"steps: {', '.join(s['tool'] for s in r['steps'])}" + (f"; at {r['schedule']}" if r['enabled'] else "")
                     for r in rows)


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


# =============================================================================
# Мастерская навыков (навыки, которые Atlas написал сам, подключаются без перезапуска)
# =============================================================================
def _forge_register(name: str) -> bool:
    from core import skill_forge
    got = skill_forge.load_skill(name)
    if not got:
        return False
    fn, schema = got
    register(name, fn, schema=schema, group="learned")
    return True


def _forge_unregister(name: str) -> None:
    unregister(name)
    tool_router.GROUPS.get("learned", set()).discard(name)
    try:
        tool_router._tool_index["mat"] = None        # смысловой индекс пересоберётся
    except Exception:
        pass


def learn_skill(request: str) -> str:
    """Starts learning a NEW ability in the background."""
    from core import skill_forge
    return skill_forge.learn(request)


def install_skill(skill_id: int = 0) -> str:
    from core import skill_forge
    return _pick(skill_forge.install(skill_id))


def reject_skill(skill_id: int = 0) -> str:
    from core import skill_forge
    return _pick(skill_forge.reject(skill_id))


def list_learned_skills() -> str:
    from core import skill_forge
    inst = skill_forge.installed()
    ready = [i for i in skill_forge.list_items(10) if i["status"] == "ready"]
    parts = [f"Installed: {', '.join(m['name'] for m in inst) or 'none'}."]
    if ready:
        parts.append("Waiting for confirmation: " + "; ".join(f"#{i['id']} {i['name']}" for i in ready))
    return " ".join(parts)


def remove_skill(name: str) -> str:
    from core import skill_forge
    return _pick(skill_forge.remove(name))


# =============================================================================
# Учёба, итоги, голо-экран, граф, жесты, мини-окно
# =============================================================================
def study_start(deck: str = "", count: int = 10) -> str:
    from core import study
    return study.start(deck, count)


def study_add(deck: str, front: str, back: str) -> str:
    from core import study
    n = study.add_cards(deck, [{"front": front, "back": back}])
    return f"Added {n} card to «{deck}»." if n else "That card is already in the deck."


def study_generate(deck: str, topic: str, count: int = 10) -> str:
    from core import study
    n = study.generate_cards(deck, topic, count)
    return f"Created {n} cards in deck «{deck}» on: {topic}." if n else "Couldn't create cards — try another topic."


def study_stats() -> str:
    from core import study
    s = study.stats()
    decks_txt = "; ".join(f"{d['deck']}: {d['due']} due of {d['total']}, {d['learned']} learned" for d in s["decks"]) or "no decks"
    acc = f"{s['week_accuracy']}%" if s["week_accuracy"] is not None else "—"
    return f"Decks: {decks_txt}. This week: {s['week_reviews']} reviews, accuracy {acc}. Streak: {s['streak']} days."


def study_stop() -> str:
    from core import study
    return study.stop()


def day_report(period: str = "today") -> str:
    """Facts about the user's day/week for a short spoken recap."""
    from core import day_report as _dr
    return _dr.report_text(period)


def holo_show(query: str) -> str:
    from core import holo
    return holo.show_image(query)["text"]


def holo_weather(place: str) -> str:
    from core import holo
    return holo.show_weather(place)["text"]


def look(question: str = "", source: str = "camera") -> str:
    from core import holo
    return holo.look(question, "screen" if str(source).lower().startswith(("scr", "экр")) else "camera")["text"]


def holo_control(action: str = "expand") -> str:
    from core import holo
    a = str(action).lower()
    a = "close_all" if "all" in a or "все" in a else a
    holo.command(a if a in ("expand", "collapse", "close", "close_all") else "expand")
    return "OK."


def holo_graph(focus: str = "") -> str:
    from core import holo
    return holo.show_graph(focus)["text"]


def gestures_control(action: str) -> str:
    """camera_on | camera_off | mirror | claps_on | claps_off"""
    from core import gestures
    from ui_state import shared_state
    a = str(action).lower()
    if "clap" in a or "хлоп" in a:
        on = not any(w in a for w in ("off", "выкл", "disable"))
        gestures.set_claps(on)
        return "Claps " + ("on: two claps wake Atlas." if on else "off.")
    on = not any(w in a for w in ("off", "выкл", "disable", "stop"))
    mirror = any(w in a for w in ("mirror", "зеркал", "look_at_me", "big"))
    cmd = shared_state.get("gest_cmd") or {"seq": 0}
    new = {"seq": cmd.get("seq", 0) + 1, "camera": on or mirror}
    if mirror or not on:
        new["mirror"] = bool(mirror and on)
    shared_state["gest_cmd"] = new
    if mirror and on:
        return "Mirror view on: the user sees themselves through the camera, Atlas tracks their hands."
    return "Gesture camera " + ("on — show your hand to the camera." if on else "off.")


def mini_mode(on: bool = True) -> str:
    """Collapses Atlas into the floating mini window (on=True) or brings the full window back (on=False)."""
    try:
        import web_gui
        g = web_gui._GUI.get("gui")
    except Exception:
        g = None
    if g is None:
        return "The interface isn't running."
    (g.enter_mini() if on else g.show_main())
    return "Mini mode on — I'm in the corner above your windows." if on else "Full window is back."


# =============================================================================
# Проверка памяти: неверные, устаревшие и случайные «факты»
# =============================================================================
_mem_review = {"flagged": []}


def _json_call(system: str, user: str, max_tokens: int = 900) -> dict:
    from brain import providers
    est = (len(system) + len(user)) // 3 + max_tokens
    model = llm_gateway.reserve_any(providers._candidates(providers.MODEL_SMART, providers.MODEL_FAST), est)
    kw = dict(model=model, max_tokens=max_tokens, response_format={"type": "json_object"},
              messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    if "gpt-oss" in model:
        kw["reasoning_effort"] = "low"
    cl, kw2 = providers._client_for(kw)
    raw = cl.chat.completions.create(**kw2).choices[0].message.content or ""
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0)) if m else {}


def memory_review(apply: bool = False) -> str:
    """Checks Atlas's memory about the user for wrong, outdated or junk facts; apply=True removes the flagged ones."""
    db = os.path.join(_ROOT, "memory.db")
    if apply:
        ids = [f["id"] for f in _mem_review["flagged"]]
        if not ids:
            return "Nothing is waiting for removal — run the check first."
        c = sqlite3.connect(db, timeout=10)
        c.executemany("UPDATE edges SET active=0 WHERE id=?", [(i,) for i in ids])
        c.commit()
        c.close()
        _mem_review["flagged"] = []
        from brain import planner
        planner._profile_cache["t"] = 0.0
        return f"Removed {len(ids)} wrong or outdated facts from memory."
    c = sqlite3.connect(db, timeout=10)
    rows = c.execute("SELECT e.id, s.label, e.rel, d.label, e.updated FROM edges e JOIN nodes s ON s.id=e.src "
                     "JOIN nodes d ON d.id=e.dst WHERE e.active=1 ORDER BY e.updated DESC LIMIT 120").fetchall()
    c.close()
    if not rows:
        return "Memory is empty — nothing to check."
    lines = "\n".join(f"{i} | {s} — {r} → {d} | saved {_dt.fromtimestamp(u or 0):%Y-%m-%d}" for i, s, r, d, u in rows)
    data = _json_call(
        "You audit a personal assistant's memory graph about its user. Flag facts that are wrong or junk: "
        "contradictions (keep the newer one), dates in the past presented as upcoming plans or with an obviously wrong "
        "year, things the user merely asked about or searched (news, exchange rates, a famous person's death) stored as "
        "'likes'/'interests', duplicates, meaningless entries. Keep real facts about the user's life. "
        f"Today is {_dt.now():%Y-%m-%d}. Return JSON only: {{\"remove\": [{{\"id\": int, \"why\": \"short reason\"}}]}}.",
        lines)
    known = {r[0]: f"{r[1]} — {r[2]} → {r[3]}" for r in rows}
    flagged = [{"id": int(x["id"]), "fact": known[int(x["id"])], "why": str(x.get("why", ""))[:80]}
               for x in (data.get("remove") or []) if str(x.get("id", "")).isdigit() and int(x["id"]) in known]
    _mem_review["flagged"] = flagged
    if not flagged:
        return f"Checked {len(rows)} facts — nothing looks wrong."
    listing = "; ".join(f"«{f['fact']}» ({f['why']})" for f in flagged[:8])
    return (f"Checked {len(rows)} facts; {len(flagged)} look wrong or outdated: {listing}"
            + (" …" if len(flagged) > 8 else "") + ". Ask the user whether to remove them (then call memory_review with apply=true).")


def start_memory_autoreview(every_days: float = 3, delay: float = 120) -> None:
    """Раз в несколько дней Atlas сам проверяет память. Нашёл мусор — показывает уведомление и
    спрашивает в разговоре: «удалить?». Удаляет только после «да» (обычный memory_review(apply=True))."""
    import threading
    import time as _t
    mark = os.path.join(_ROOT, "memory_review.json")

    def due() -> bool:
        try:
            with open(mark, encoding="utf-8") as f:
                return _t.time() - json.load(f).get("t", 0) > every_days * 86400
        except Exception:
            return True

    def run():
        _t.sleep(delay)                                   # не мешаем запуску
        if not due():
            return
        try:
            from ui_state import shared_state
            for _ in range(60):                           # ждём, пока Atlas свободен (до 10 минут)
                if shared_state.get("state", "idle") == "idle":
                    break
                _t.sleep(10)
            result = memory_review()
            with open(mark, "w", encoding="utf-8") as f:
                json.dump({"t": _t.time()}, f)
            n = len(_mem_review["flagged"])
            print(f"[память] плановая проверка: подозрительных фактов — {n}")
            if not n:
                return
            facts = "; ".join(f"«{x['fact']}»" for x in _mem_review["flagged"][:4])
            ru = _lang() == "ru"
            text = (f"Я проверил свою память и нашёл устаревшие или неверные факты ({n}): {facts}. Удалить их?" if ru
                    else f"I checked my memory and found {n} outdated or wrong facts: {facts}. Shall I remove them?")
            state.conversation_history.append({"role": "assistant", "content": text})   # «да» поймёт модель
            try:
                from ui_state import notify
                notify("warn", "task_done", text[:200])
            except Exception:
                pass
        except Exception as e:
            print(f"[память] плановая проверка не удалась: {e}")

    threading.Thread(target=run, daemon=True, name="memory-autoreview").start()


# =============================================================================
# Регистрация — в том же порядке, что и раньше (от порядка зависит список для модели)
# =============================================================================
def _triggers(group: str, words) -> None:
    tool_router.TRIGGERS[group] = tuple(words)


def _add_triggers(group: str, words) -> None:
    tool_router.TRIGGERS[group] = tuple(set(tool_router.TRIGGERS.get(group, ())) | set(words))


def start() -> None:
    # --- самолечение
    register("heal_status", heal_status, "Lists bug fixes Atlas prepared for its own code that wait for the user's confirmation (self-repair).", group="heal")
    register("heal_apply", heal_apply, "Applies a prepared self-repair fix to Atlas's own code. Call ONLY after the user explicitly asked to apply it ('применяй исправление', 'apply the fix'). fix_id 0 = the latest one.", {"fix_id": _I}, group="heal")
    register("heal_reject", heal_reject, "Rejects a prepared self-repair fix. fix_id 0 = the latest one.", {"fix_id": _I}, group="heal")
    _triggers("heal", ("исправлен", "почин", "самолечен", "баг", "патч", "ошибку в коде", "fix", "bug", "patch", "repair"))

    # --- ритуалы
    register("create_routine", create_routine, "Creates a routine: a named set of tool calls that runs by a trigger phrase (e.g. 'доброе утро') or daily at a time. steps = real tool names with their arguments.",
             {"name": _S, "triggers": {"type": "array", "items": _S},
              "steps": {"type": "array", "items": {"type": "object", "properties": {"tool": _S, "args": {"type": "object"}}, "required": ["tool"]}},
              "at": {"type": "string", "description": "HH:MM to run daily, optional"},
              "days": {"type": "string", "description": "weekday digits 0=Mon..6=Sun, empty = every day"}}, ["name", "steps"], group="routines")
    register("list_routines", list_routines, "Lists the user's routines and routines Atlas suggested from habits.", group="routines")
    register("run_routine", run_routine, "Runs a routine by name now.", {"name": _S}, ["name"], group="routines")
    register("delete_routine", delete_routine, "Deletes a routine by name.", {"name": _S}, ["name"], group="routines")
    _triggers("routines", ("ритуал", "рутин", "каждое утро", "по утрам", "каждый вечер", "брифинг",
                           "routine", "briefing", "every morning", "every evening"))

    # --- мастерская навыков
    register("learn_skill", learn_skill, "Atlas teaches itself a NEW ability that no existing tool provides ('научись…', 'learn to…', 'можешь научиться…'): writes a new tool, checks and tests it in the background, then asks the user to install it. Don't use it for things existing tools already do.",
             {"request": {"type": "string", "description": "what the new skill should do, in the user's words"}}, ["request"], group="forge")
    register("install_skill", install_skill, "Installs a learned skill that passed its tests — ONLY after the user explicitly said to install it. skill_id 0 = the latest ready one.", {"skill_id": _I}, group="forge")
    register("reject_skill", reject_skill, "Rejects a learned skill waiting for confirmation. skill_id 0 = the latest.", {"skill_id": _I}, group="forge")
    register("list_learned_skills", list_learned_skills, "Lists skills Atlas taught itself and ones waiting for confirmation.", group="forge")
    register("remove_skill", remove_skill, "Removes a skill Atlas taught itself, by name.", {"name": _S}, ["name"], group="forge")
    _triggers("forge", ("научись", "научи себя", "новый навык", "навык", "можешь научиться", "learn to", "teach yourself", "new skill", "skill"))
    try:
        from core import skill_forge as _sf
        _sf.start(register=_forge_register, unregister=_forge_unregister)
        loaded = [m["name"] for m in _sf.installed() if _forge_register(m["name"])]
        print(f"[навыки] выученных навыков загружено: {len(loaded)}" + (f" ({', '.join(loaded)})" if loaded else ""))
    except Exception as e:
        print(f"[навыки] мастерская недоступна: {e}")

    # --- браузер: профессиональный режим
    import browser_agent as _ba
    browser = {
        "browser_open": ("Opens a URL in Atlas's own browser. Returns URL, title, scroll position and numbered interactive "
                         "elements [n] with their state (value, checked, expanded, disabled, covered, link target).", {"url": _S}, ["url"]),
        "browser_read_page": ("Fresh numbered list of interactive elements in the visible part of the current page.", {}, []),
        "browser_read_text": ("Reads the MAIN TEXT of the current page (article, search results, product details, prices) "
                              "without menus and ads, in parts. Use it to read; the element list shows only controls.",
                              {"part": {"type": "integer", "description": "1 = start; next parts if the text is long"}}, []),
        "browser_click": ("Clicks element [index] from the latest list and returns the page state after the click "
                          "(follows new tabs automatically).", {"index": _I}, ["index"]),
        "browser_type": ("Clears input [index] and types text; submit=true presses Enter afterwards (search boxes, forms).",
                         {"index": _I, "text": _S, "submit": _B}, ["index", "text"]),
        "browser_select": ("Chooses an option in dropdown [index] by its visible text.", {"index": _I, "option": _S}, ["index", "option"]),
        "browser_scroll": ("Scrolls the page and returns the newly visible elements.",
                           {"direction": {"type": "string", "description": "'down' or 'up'"},
                            "amount": {"type": "integer", "description": "pixels, default 600"}}, []),
        "browser_find": ("Finds text on the page, scrolls to it and returns the elements around it — use when the target "
                         "isn't in the visible list.", {"text": _S}, ["text"]),
        "browser_back": ("Goes back to the previous page.", {}, []),
        "browser_forward": ("Goes forward to the next page.", {}, []),
        "browser_tabs": ("Browser tabs: action='list' | 'switch' (index) | 'close' (index) | 'new' (url).",
                         {"action": _S, "index": _I, "url": _S}, []),
        "browser_wait": ("Waits a little for slow pages or results to load and returns the fresh element list.", {"seconds": _N}, []),
        "browser_screenshot_describe": ("Vision fallback for canvas/video players or when the element list truly lacks the "
                                        "target: numbers every element on a screenshot, a vision model picks one, it gets clicked.",
                                        {"instruction": _S}, ["instruction"]),
    }
    for name, (desc, props, req) in browser.items():
        register(name, getattr(_ba, name), desc, props, req, group="browser")
    TOOL_PAIRS["browser_open"] = sorted(set(TOOL_PAIRS.get("browser_open", [])) | {
        "browser_read_page", "browser_read_text", "browser_click", "browser_type", "browser_find",
        "browser_scroll", "browser_back", "browser_tabs"})

    # --- программы Windows
    from core import desktop_agent as _da
    desktop = {
        "desktop_look": ("Lists numbered controls (buttons, menus, fields, tabs, list items, checkboxes) of the active "
                         "Windows program window with values and states; Atlas's own window is skipped. window = part of "
                         "a window title to look at a specific program.", {"window": _S}, []),
        "desktop_windows": ("Lists open program windows.", {}, []),
        "desktop_switch": ("Brings a program window to the front by part of its title and lists its controls.", {"window": _S}, ["window"]),
        "desktop_click": ("Clicks control [index] from the latest desktop_look list (double=true for double click, "
                          "right=true for context menu).", {"index": _I, "double": _B, "right": _B}, ["index"]),
        "desktop_type": ("Types text (any language) into control [index], or where the cursor is when index is -1. "
                         "replace=true clears the field first, enter=true presses Enter.",
                         {"text": _S, "index": _I, "enter": _B, "replace": _B}, ["text"]),
        "desktop_hotkey": ("Presses a key or shortcut in the program, e.g. 'ctrl+s', 'ctrl+n', 'alt+f4', 'f5', 'enter'.", {"keys": _S}, ["keys"]),
        "desktop_scroll": ("Scrolls inside the program window.",
                           {"direction": {"type": "string", "description": "'down' or 'up'"}, "times": _I}, []),
        "desktop_read_text": ("Reads the visible text of the program window (document, fields, list items).", {}, []),
        "desktop_screenshot_describe": ("Vision fallback for programs whose controls desktop_look can't see (games, "
                                        "Electron apps): numbers controls on a window screenshot, a vision model picks one, "
                                        "it gets clicked.", {"instruction": _S}, ["instruction"]),
    }
    for name, (desc, props, req) in desktop.items():
        register(name, getattr(_da, name), desc, props, req, group="desktop")
    _triggers("desktop", (
        "в программе", "в приложении", "в окне", "окно", "блокнот", "notepad", "word", "ворд", "excel", "эксель",
        "проводник", "explorer", "параметры windows", "настройки windows", "telegram", "телеграм", "discord", "дискорд",
        "paint", "калькулятор", "calculator", "сохрани файл", "сохрани документ", "program", "application", "window"))
    TOOL_PAIRS["desktop_look"] = ["desktop_click", "desktop_type", "desktop_hotkey", "desktop_scroll", "desktop_read_text", "desktop_switch"]
    TOOL_PAIRS["open_app"] = sorted(set(TOOL_PAIRS.get("open_app", [])) | {"desktop_look", "desktop_switch"})

    # --- учёба
    register("study_start", study_start, "Starts a spoken flashcard review session (spaced repetition). deck = deck name (e.g. 'SAT', 'IELTS'), empty = all due cards. Return the tool's text as is — it already contains the first question.", {"deck": _S, "count": _I}, group="study")
    register("study_add", study_add, "Adds one flashcard to a deck (e.g. a word and its meaning).", {"deck": _S, "front": _S, "back": _S}, ["deck", "front", "back"], group="study")
    register("study_generate", study_generate, "Creates flashcards on a topic with the model and adds them to a deck (e.g. deck 'SAT', topic 'hard SAT vocabulary', 10 cards).", {"deck": _S, "topic": _S, "count": _I}, ["deck", "topic"], group="study")
    register("study_stats", study_stats, "Study progress: decks, cards due today, learned, weekly accuracy, streak.", group="study")
    register("study_stop", study_stop, "Ends the current review session with a summary.", group="study")
    _triggers("study", ("повтор", "карточ", "учеб", "учёб", "тренир", "sat", "ielts", "слово", "слова",
                        "словар", "викторин", "flashcard", "quiz", "review", "study", "vocab"))

    # --- итоги дня
    register("day_report", day_report, "Facts about the user's day or week: time in apps, questions to Atlas and topics, study reviews, skills Atlas learned, git commits. period: 'today' | 'yesterday' | 'week'. Turn it into a short, friendly spoken recap (3-5 sentences) with one observation.", {"period": _S}, group="report")
    _triggers("report", ("итоги", "как прошёл день", "как прошел день", "как прошла неделя", "что я делал",
                         "сколько времени", "статистик", "recap", "my day", "my week", "how was my day"))

    # --- голо-экран
    register("holo_show", holo_show, "Shows a picture on Atlas's holographic screen: a landmark, place, animal, object, vehicle, artwork or a famous person BY NAME ('покажи…', 'выведи…', 'show me…'). Speak only a short sentence — the picture is on screen.",
             {"query": {"type": "string", "description": "what to show, e.g. 'Eiffel Tower'"}}, ["query"], group="holo")
    register("holo_weather", holo_weather, "Shows weather for a place on the holographic screen with a timeline a week back and a week ahead, and returns the current weather. Use when the user asks to show / display weather, or asks about weather in another place.", {"place": _S}, ["place"], group="holo")
    register("look", look, "Atlas looks through the webcam (source='camera') or at the screen (source='screen') and answers the question about what it sees ('посмотри', 'что у меня в руке', 'что ты видишь', 'what's on my screen'). The snapshot also appears on the holographic screen.",
             {"question": _S, "source": {"type": "string", "description": "'camera' or 'screen'"}}, group="holo")
    register("holo_control", holo_control, "Controls the holographic screen: action='expand' (разверни), 'collapse' (сверни), 'close' (закрой), 'close_all' (убери всё).", {"action": _S}, ["action"], group="holo")
    _triggers("holo", ("покажи", "выведи", "посмотри", "что у меня в руке", "что ты видишь", "что на экране",
                       "разверни", "сверни", "закрой экран", "убери", "погода в", "show me", "display",
                       "look at", "what do you see", "weather in"))
    register("holo_graph", holo_graph, "Shows Atlas's memory about the user as an interactive knowledge graph on the holographic screen ('покажи, что ты обо мне знаешь', 'покажи граф памяти', 'what do you know about me'). focus = optional word to highlight (e.g. 'SAT').", {"focus": _S}, group="holo")
    _add_triggers("holo", ("что ты обо мне знаешь", "что ты знаешь обо мне", "граф", "памят", "what do you know about me", "knowledge graph"))

    # --- жесты
    register("gestures_control", gestures_control, "Gesture control via webcam: 'включи жесты' → camera_on, 'выключи жесты' → camera_off; live mirror view where the user sees themselves and Atlas tracks their hands ('покажи меня', 'включи зеркало', 'смотри на меня', 'посмотри на мои руки') → mirror; claps wake-up: claps_on | claps_off.", {"action": _S}, ["action"], group="gestures")
    _triggers("gestures", ("жест", "хлоп", "камер", "зеркал", "смотри на меня", "покажи меня", "мои руки", "gesture", "clap", "mirror"))

    # --- план (сам execute_plan живёт в planner.py)
    from brain import planner
    register("execute_plan", planner.execute_plan, (
        "Runs several actions in order, instantly, without further thinking. Use for any request needing 2+ actions "
        "whose arguments you know upfront (e.g. open_app notepad → desktop_type text; open a site → type a search). "
        "steps: [{tool, args}] with real tool names; {wait: seconds} pauses. done_message: what to say if every step "
        "succeeds, in the user's language. background=true: long work that runs while the user keeps talking; the "
        "result is announced aloud when finished."),
        {"goal": {"type": "string", "description": "the user's goal in a few words"},
         "steps": {"type": "array", "items": {"type": "object", "properties": {"tool": _S, "args": {"type": "object"}, "wait": _N}}},
         "done_message": _S, "background": _B}, ["goal", "steps", "done_message"])
    tool_router.CORE.add("execute_plan")

    # --- мини-окно
    register("mini_mode", mini_mode, "Collapses Atlas into a small always-on-top window in the screen corner ('сверни себя', 'мини-режим', 'не мешай, будь в углу') — on=true; brings the full window back ('разверни себя', 'вернись') — on=false.", {"on": _B}, ["on"], group="settings")
    _add_triggers("settings", ("сверни себя", "мини-режим", "мини режим", "мини-атлас", "в угол", "разверни себя", "mini mode", "minimize yourself"))

    # --- проверка памяти
    register("memory_review", memory_review, "Checks what Atlas remembers about the user and finds wrong, contradictory, outdated or junk facts ('проверь свою память', 'почисти память', 'там неправильно'). First call without apply, tell the user what was found and ask; only after they agree call apply=true to remove them.", {"apply": _B}, group="memory")
    _triggers("memory", ("памят", "запомнил", "почисти", "неправильн", "неверн", "ошибся", "memory", "remember"))

    # --- перемотка Spotify
    import system_control as _sc
    if hasattr(_sc, "spotify_seek"):
        register("spotify_seek", _sc.spotify_seek, "Seeks the current Spotify track forward (positive seconds) or back "
                 "(negative), e.g. 15 or -30. For music in Spotify — not for videos in the browser.",
                 {"seconds": {"type": "integer"}}, ["seconds"], group="media")

    if hasattr(_sc, "spotify_volume"):
        register("spotify_volume", _sc.spotify_volume, "Spotify's own volume slider (not the computer volume): "
                 "action='up'/'down' (steps = how many notches), or action='set' with percent 0-100.",
                 {"action": {"type": "string", "description": "'up', 'down' or 'set'"}, "percent": {"type": "integer"},
                  "steps": {"type": "integer"}}, ["action"], group="media")
    if hasattr(_sc, "spotify_play_library"):
        register("spotify_library", _sc.spotify_play_library, "Plays the user's own Spotify library: which='liked' "
                 "for their Liked Songs ('мои любимые треки', 'любимое'), or the name of one of their playlists.",
                 {"which": {"type": "string", "description": "'liked' or a playlist name"}}, ["which"], group="media")

    # --- видео-инструменты браузера: при закрытом браузере не запускают его, а честно говорят «не открыт»
    import functools as _ft
    import browser_agent as _ba2

    def _video_only(name, fn):
        @_ft.wraps(fn)
        def wrapper(*a, **kw):
            if not getattr(_ba2, "_browser_alive", lambda: True)():
                return (f"Браузер не открыт: {name} управляет только видео в браузере Atlas. Для музыки — "
                        "volume_up / volume_down, play_pause_media, next_track, spotify_seek.")
            return fn(*a, **kw)
        return wrapper
    for _n in ("media_play_pause", "media_seek", "media_volume", "media_player_fullscreen", "next_episode", "skip_intro"):
        if _n in AVAILABLE_FUNCTIONS:
            AVAILABLE_FUNCTIONS[_n] = _video_only(_n, getattr(AVAILABLE_FUNCTIONS[_n], "__wrapped__", AVAILABLE_FUNCTIONS[_n]))

    # --- Atlas на других устройствах
    def install_on_device(device: str = "phone") -> str:
        """Opens a QR code to install Atlas on a phone, tablet or TV."""
        from phone.install import install
        return install(device)
    register("install_on_device", install_on_device, "Installs Atlas on another device: shows a QR code on this screen; the "
             "user scans it and taps Install. device: 'phone' | 'tablet' | 'tv' ('установи себя на телефон').",
             {"device": {"type": "string", "description": "'phone', 'tablet' or 'tv'"}}, group="devices")
    _triggers("devices", ("установи себя", "на телефон", "на смартфон", "на планшет", "на телевизор", "на другое устройство",
                          "install yourself", "on my phone", "on the tv"))

    # Сервисы, недоступные в регионе пользователя, модели не показываются (по умолчанию —
    # YouTube Music: в Кыргызстане он закрыт). Список — ATLAS_BLOCKED_TOOLS в .env, через запятую.
    for name in filter(None, (os.getenv("ATLAS_BLOCKED_TOOLS", "play_on_youtube_music")).replace(" ", "").split(",")):
        unregister(name)
