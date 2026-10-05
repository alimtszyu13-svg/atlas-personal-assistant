"""
Тесты мозга Atlas — фиксируют поведение, чтобы рефакторинг и будущие правки его не ломали.

    python tests/test_brain.py

Работают без интернета, ключей и Windows: всё вокруг мозга заменено подставными версиями.
"""
import os
import sqlite3
import sys
import tempfile
import time
import traceback

for _stream in (sys.stdout, sys.stderr):        # Windows: вывод в файл/трубу идёт в cp1251, где нет ✓ и ✗
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests import stubs  # noqa: E402

stubs.install()
import ai_brain  # noqa: E402
from brain import features, instant, planner, providers, state, tools  # noqa: E402

RU = "(Respond in Russian.) "
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def fresh(script_replies=None):
    """Чистый разговор + подставная модель."""
    ai_brain.reset_conversation()
    state.situation.update(goal="", actions=[], reply="")
    state.turn.update(question="", lesson_msg=None, plan=None, failures=0)
    state._cancel_event.clear()
    stubs.CALLS.clear()
    s = stubs.Script(script_replies or [])
    providers._call_model_stream = s
    return s


# ------------------------------------------------------------------ устройство
@test
def facade_keeps_old_names():
    for name in ("ask_ai", "cancel_current_task", "remember_exchange", "reset_conversation", "set_announcer",
                 "AVAILABLE_FUNCTIONS", "TOOLS_SCHEMA", "client", "MODEL_SMART", "MODEL_FAST", "_candidates",
                 "_client_for", "_call_model_stream", "_run_one_tool", "_cancel_event", "TaskCancelled", "SYSTEM_PROMPT",
                 "GEM_MODEL", "gem_client", "conversation_history", "_situation", "_current_question", "execute_plan"):
        assert hasattr(ai_brain, name) or getattr(ai_brain, name, None) is not None, name


@test
def every_schema_has_a_function_and_names_are_unique():
    names = [t["function"]["name"] for t in tools.TOOLS_SCHEMA]
    assert len(names) == len(set(names)), "повторы в описаниях инструментов"
    missing = [n for n in names if n not in tools.AVAILABLE_FUNCTIONS]
    assert not missing, f"описание без функции: {missing}"
    for must in ("execute_plan", "browser_read_text", "desktop_type", "holo_show", "memory_review", "mini_mode", "study_start"):
        assert must in names, must
    assert "execute_plan" in ai_brain.tool_router.CORE


@test
def reset_keeps_the_same_history_list():
    h = ai_brain.conversation_history
    h.append({"role": "user", "content": "x"})
    ai_brain.reset_conversation()
    assert h is ai_brain.conversation_history and len(h) == 1 and h[0]["content"] == ai_brain.SYSTEM_PROMPT


@test
def trim_history_in_place_without_orphan_tool_messages():
    fresh()
    h = state.conversation_history
    for i in range(30):
        h.append({"role": "tool", "content": "r"} if i % 3 == 0 else {"role": "user", "content": str(i)})
    state._trim_history()
    assert h is state.conversation_history and len(h) <= state.MAX_HISTORY_MESSAGES and h[1]["role"] != "tool"


# ------------------------------------------------------------------ модели
@test
def candidate_order_and_cerebras_minute_limit():
    p = providers
    saved = (p.cerebras_client, p.gh_client, p.gem_client)
    try:
        p.cerebras_client = p.gh_client = p.gem_client = object()
        p._cerebras_down["until"] = p._gh_down["until"] = p._gem_down["until"] = 0
        assert p._candidates(p.MODEL_SMART, p.MODEL_FAST) == [p.CEREBRAS_MODEL, p.MODEL_SMART, p.MODEL_FAST, p.GEM_MODEL, p.GH_MODEL]
        p._cerebras_down["until"] = time.time() + 60
        assert p._candidates(p.MODEL_SMART, p.MODEL_FAST)[0] == p.MODEL_SMART
        p._cerebras_down["until"] = 0
        p._cer_calls.clear()
        p._cer_cool["until"] = 0
        now = time.time()
        for i in range(5):
            p._cer_calls.append(now - 58 + i * 0.1)
        cool_before = len(p.llm_gateway.COOL)
        p._candidates(p.MODEL_SMART, p.MODEL_FAST)
        p._candidates(p.MODEL_SMART, p.MODEL_FAST)
        assert len(p.llm_gateway.COOL) == cool_before + 1, "пауза Cerebras должна ставиться один раз"
    finally:
        p.cerebras_client, p.gh_client, p.gem_client = saved
        p._cer_calls.clear()


@test
def client_routing_and_parameters():
    p = providers
    saved = p.gem_client
    try:
        p.gem_client = types_ns = object()
        msgs = [{"role": "system", "content": "a"}, {"role": "user", "content": "q"}, {"role": "system", "content": "b"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "function": {"name": "x", "arguments": "{}"}}]}]
        cl, kw = p._client_for({"model": p.GEM_MODEL, "messages": msgs, "reasoning_effort": "high"})
        assert cl is types_ns and kw["model"] == p.GEM_MODEL.split(":", 1)[1]
        assert kw["messages"][0] == {"role": "system", "content": "a\n\nb"} and kw["reasoning_effort"] == p._GEM_EFFORT["v"]
        assert "extra_content" in kw["messages"][-1]["tool_calls"][0] and kw["messages"][-1]["content"] == ""
        withsig = [{"role": "assistant", "tool_calls": [{"id": "1", "extra_content": {"g": 1}, "function": {}}]}]
        cl, kw = p._client_for({"model": p.MODEL_SMART, "messages": withsig, "reasoning_effort": "low"})
        assert cl is p.client and "extra_content" not in kw["messages"][0]["tool_calls"][0] and kw["reasoning_effort"] == "low"
        cl, kw = p._client_for({"model": "llama-3.3-70b-versatile", "messages": [], "reasoning_effort": "low"})
        assert "reasoning_effort" not in kw
    finally:
        p.gem_client = saved


@test
def provider_error_falls_back_to_groq():
    p = providers
    saved = (p.gem_client, p._whole_call, p._stream_fixing)
    seen = {}
    try:
        p.gem_client = object()
        p._whole_call = lambda on_text, **kw: (_ for _ in ()).throw(RuntimeError("429 RESOURCE_EXHAUSTED quota"))
        p._stream_fixing = lambda on_text, **kw: (seen.update(model=kw["model"]), ({"role": "assistant", "content": "ok"}, None))[1]
        msg, _ = p._call_model_stream.__wrapped__(lambda d: None, model=p.GEM_MODEL, messages=[]) \
            if hasattr(p._call_model_stream, "__wrapped__") else _real_call(lambda d: None, model=p.GEM_MODEL, messages=[])
        assert msg["content"] == "ok" and seen["model"] == p.MODEL_SMART and p._gem_down["until"] > time.time() + 30
    finally:
        p.gem_client, p._whole_call, p._stream_fixing = saved
        p._gem_down["until"] = 0


_real_call = providers._call_model_stream


@test
def unknown_tool_is_added_and_retried():
    p = providers
    saved = p._stream_raw
    calls = []

    def raw(on_text, **kw):
        calls.append([t["function"]["name"] for t in kw.get("tools") or []])
        if len(calls) == 1:
            raise RuntimeError("tool call validation failed: attempted to call tool 'browser_find' which was not in request.tools")
        return {"role": "assistant", "content": "ok"}, None
    try:
        p._stream_raw = raw
        schema = [t for t in tools.TOOLS_SCHEMA if t["function"]["name"] == "browser_open"]
        p._stream_fixing(lambda d: None, model=p.MODEL_SMART, messages=[], tools=schema)
        assert "browser_find" in calls[1], calls
    finally:
        p._stream_raw = saved


# ------------------------------------------------------------------ мгновенные команды
@test
def instant_routes():
    r = instant._instant_route
    assert r("который час")[0] == "local"
    assert r("сколько будет 17 умножить на 23") == ("local", "391.")
    assert r("сколько будет 2 плюс 2 разделить на 4") == ("local", "2.5.")
    assert r("посчитай 9**9**9") is None
    assert r("запомни знаешь") == ("local", "Что именно запомнить, сэр?")
    assert r("запомни что я люблю пиццу") is None
    assert r("какая погода") == ("tool", "get_weather", {})
    assert r("погода в стамбуле") is None
    assert r("какая загрузка процессора") == ("tool", "get_cpu_usage", {})
    assert r("какие у тебя дела") is None and r("как дела") is None
    assert r("открой блокнот и скажи погоду") is None


@test
def instant_tool_uses_one_tiny_call():
    s = fresh([{"content": "В Бишкеке 25 градусов."}])
    reply = ai_brain.ask_ai(RU + "Атлас, какая погода?")
    assert reply == "В Бишкеке 25 градусов." and len(s.requests) == 1 and "tools" not in s.requests[0]
    assert stubs.CALLS[0][0] == "get_weather"
    assert state.conversation_history[-1]["content"] == reply


@test
def instant_local_needs_no_model():
    s = fresh([])
    assert ai_brain.ask_ai(RU + "сколько будет 17 на 23") == "391." and not s.requests


# ------------------------------------------------------------------ мозг
@test
def plan_runs_in_one_model_call_with_argument_repair():
    s = fresh([{"tool_calls": [("execute_plan", {"goal": "список", "done_message": "Готово, список в Блокноте.", "steps": [
        {"tool": "open_app", "args": {"name": "notepad"}}, {"tool": "desktop_type", "args": {"content": "хлеб"}}]})]}])
    tools._cpu_now["v"] = None
    reply = ai_brain.ask_ai(RU + "открой блокнот и напиши список: хлеб")
    assert reply == "Готово, список в Блокноте." and len(s.requests) == 1
    assert ("open_app", {"app_name": "notepad"}) in stubs.CALLS, stubs.CALLS
    assert any(c[0] == "desktop_type" and c[1]["text"] == "хлеб" for c in stubs.CALLS), stubs.CALLS


@test
def argument_aliases_work_even_with_extra_junk():
    fresh()
    assert tools._repair_args("open_app", {"name": "notepad", "junk": 1}) == {"app_name": "notepad", "junk": 1}
    assert tools._repair_args("get_weather", {"place": "Osh"}) == {"city": "Osh"}
    assert tools._repair_args("open_app", {"app_name": "x"}) == {"app_name": "x"}


@test
def typing_without_request_is_blocked():
    fresh()
    state.turn["question"] = RU + "найди файл про SAT"
    res, ok, _ = tools._run_one_tool("desktop_type", {"text": "x"})
    assert not ok and res.startswith("Blocked") and not stubs.CALLS


@test
def simple_tool_answer_is_a_tiny_call():
    s = fresh([{"tool_calls": [("calculate", {"expression": "17*23"})]}, {"content": "391, сэр."}])
    reply = ai_brain.ask_ai(RU + "посчитай мне произведение семнадцати и двадцати трёх, пожалуйста")
    assert reply == "391, сэр." and len(s.requests) == 2
    assert "tools" in s.requests[0] and "tools" not in s.requests[1] and len(s.requests[1]["messages"]) == 2


@test
def failed_step_goes_back_to_the_model():
    s = fresh([{"tool_calls": [("execute_plan", {"goal": "x", "done_message": "Готово.", "steps": [{"tool": "no_such_tool"}]})]},
               {"content": "Не получилось: такого инструмента нет."}])
    reply = ai_brain.ask_ai(RU + "сделай что-то невозможное и потом сообщи")
    assert len(s.requests) == 2 and "не получилось" in reply.lower()
    tool_msg = [m for m in state.conversation_history if m.get("role") == "tool"][-1]["content"]
    assert tool_msg.startswith("STOPPED")


@test
def cancel_stops_the_turn():
    def cancelling(on_text, **kw):
        ai_brain.cancel_current_task()
        return {"role": "assistant", "tool_calls": [{"id": "c0", "type": "function", "function": {
            "name": "execute_plan", "arguments": '{"goal":"g","done_message":"d","steps":[{"tool":"open_app","args":{"app_name":"x"}}]}'}}]}, None
    fresh()
    providers._call_model_stream = cancelling
    reply = ai_brain.ask_ai(RU + "открой много всего и потом ещё")
    assert reply == "Хорошо, остановился." and not stubs.CALLS
    assert state.conversation_history[-1]["content"] == "[Task was cancelled by the user.]"


@test
def background_plan_answers_now_and_announces_later():
    said = []
    ai_brain.set_announcer(said.append)
    s = fresh([{"tool_calls": [("execute_plan", {"goal": "отчёт", "done_message": "Отчёт готов.", "background": True,
                                                 "steps": [{"wait": 0.2}, {"tool": "open_app", "args": {"app_name": "notepad"}}]})]}])
    reply = ai_brain.ask_ai(RU + "в фоне подготовь отчёт и потом открой блокнот")
    assert "в фоне" in reply and len(s.requests) == 1
    time.sleep(0.8)
    assert said == ["Отчёт готов."]


@test
def thinking_trail_has_start_and_end():
    fresh([{"content": "Привет, сэр."}])
    ai_brain.ask_ai(RU + "расскажи, как у тебя настроение сегодня")
    evs = sys.modules["ui_state"].shared_state["trace"]["events"]
    assert evs[0]["t"] == "start" and evs[-1] == {**evs[-1], "t": "end", "ok": True}


@test
def study_and_routines_bypass_the_model():
    st, rt = sys.modules["core.study"], sys.modules["core.routines"]
    s = fresh([])
    st.ACTIVE[0] = True
    assert ai_brain.ask_ai(RU + "obfuscate значит запутывать") == "тренер: obfuscate значит запутывать"
    st.ACTIVE[0] = False
    rt.TRIGGER[0] = {"id": 3, "name": "утро"}
    try:
        assert ai_brain.ask_ai(RU + "доброе утро") == "ритуал 3 выполнен"
    finally:
        rt.TRIGGER[0] = None
    assert not s.requests


@test
def trimming_keeps_important_tools():
    schema = list(tools.TOOLS_SCHEMA)
    kept = {t["function"]["name"] for t in tools._trim_schema("включи фильм железный человек", schema)}
    assert len(kept) <= tools.MAX_TOOLS + len(tools._PIN_TOOLS) and {"play_on_rezka", "holo_show", "open_app"} <= kept


@test
def prompt_has_browser_desktop_and_safety_rules():
    p = ai_brain.SYSTEM_PROMPT
    for must in ("BROWSER.", "browser_read_text", "DESKTOP.", "desktop_hotkey", "VERIFY AND SAFETY", "passwords",
                 "GOOD THINKING", "HONESTY ABOUT MEMORY"):
        assert must in p, must
    assert state.conversation_history[0]["content"] == p


@test
def file_search_ends_in_one_short_answer_when_nothing_else_is_asked():
    s = fresh([{"tool_calls": [("search_file_content", {"query": "SAT"})]}, {"content": "Нашёл два файла про SAT — открыть?"}])
    reply = ai_brain.ask_ai(RU + "найди, пожалуйста, мой файл, где я писал про подготовку к SAT")
    assert len(s.requests) == 2 and "tools" not in s.requests[1] and "открыть" in reply


@test
def file_search_continues_when_the_user_asked_for_more():
    s = fresh([{"tool_calls": [("search_file_content", {"query": "SAT"})]},
               {"tool_calls": [("open_found_file", {"query": "SAT"})]}, {"content": "Открыл. Там план подготовки."}])
    ai_brain.ask_ai(RU + "найди мой файл про SAT и расскажи, что там написано")
    assert len(s.requests) == 3 and "tools" in s.requests[1]


@test
def short_answers_keep_names_as_is():
    assert "don't translate or guess them" in ai_brain.SLIM_PROMPT


@test
def memory_self_check_asks_before_removing():
    import json as _json
    d = tempfile.mkdtemp()
    saved_root, saved_review = features._ROOT, features.memory_review
    notes = sys.modules["ui_state"].NOTES
    try:
        features._ROOT = d
        def fake_review(apply=False):
            features._mem_review["flagged"] = [{"id": 1, "fact": "User — likes → news", "why": "искал"}]
            return "x"
        features.memory_review = fake_review
        fresh()
        sys.modules["ui_state"].shared_state["state"] = "idle"
        n0 = len(notes)
        features.start_memory_autoreview(delay=0)
        for _ in range(50):
            if os.path.exists(os.path.join(d, "memory_review.json")) and len(notes) > n0:
                break
            time.sleep(0.05)
        last = state.conversation_history[-1]
        assert last["role"] == "assistant" and "Удалить их?" in last["content"] and "likes" in last["content"]
        assert len(notes) == n0 + 1
        h = len(state.conversation_history)                     # второй раз в те же 3 дня — тишина
        features.start_memory_autoreview(delay=0)
        time.sleep(0.3)
        assert len(state.conversation_history) == h
        assert _json.load(open(os.path.join(d, "memory_review.json")))["t"] > 0
    finally:
        features._ROOT, features.memory_review = saved_root, saved_review
        features._mem_review["flagged"] = []


@test
def memory_review_flags_then_removes_only_after_yes():
    d = tempfile.mkdtemp()
    db = os.path.join(d, "memory.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, name TEXT, label TEXT)")
    c.execute("CREATE TABLE edges (id INTEGER PRIMARY KEY, src INTEGER, rel TEXT, dst INTEGER, conf REAL, updated REAL, active INTEGER)")
    c.executemany("INSERT INTO nodes VALUES (?,?,?)", [(1, "user", "User"), (2, "x", "2023-10-01"), (3, "n", "news")])
    c.executemany("INSERT INTO edges (src, rel, dst, conf, updated, active) VALUES (?,?,?,?,?,1)",
                  [(1, "exam_date", 2, .9, time.time()), (1, "likes", 3, .9, time.time())])
    c.commit()
    c.close()
    saved_root, saved_json = features._ROOT, features._json_call
    try:
        features._ROOT = d
        features._json_call = lambda system, user, max_tokens=900: {"remove": [{"id": 1, "why": "прошедший год"}, {"id": 99}]}
        out = features.memory_review()
        assert "1 look wrong" in out and "exam_date" in out
        assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM edges WHERE active=1").fetchone()[0] == 2
        assert features.memory_review(apply=True).startswith("Removed 1")
        assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM edges WHERE active=1").fetchone()[0] == 1
    finally:
        features._ROOT, features._json_call = saved_root, saved_json


def main():
    ok = 0
    for t in TESTS:
        try:
            t()
            ok += 1
            print(f"  ✓ {t.__name__}")
        except Exception:
            print(f"  ✗ {t.__name__}\n" + "".join("      " + ln for ln in traceback.format_exc().splitlines(True)[-6:]))
    print(f"\nТестов пройдено: {ok} из {len(TESTS)}")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if ok == len(TESTS) else 1)


if __name__ == "__main__":
    main()
