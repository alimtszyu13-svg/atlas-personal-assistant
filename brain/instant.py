"""
Мгновенные команды: без большого запроса к модели.

    без модели вообще:  «который час», «какое сегодня число», «сколько будет 17 на 23», «запомни» без содержания
    инструмент сразу + крошечная формулировка:  погода, диск, процессор, память, заряд, курс, новости, список дел

Составные просьбы («открой… и …») сюда не попадают — их разбирает планировщик.
"""
import re
import time
from datetime import datetime as _dt
from types import SimpleNamespace as _NS

from brain import planner, state, tools

from brain.instant_rules import (_RU_MONTHS, _RU_DAYS, _CUR, _MULTI, _REMEMBER_EMPTY,  # noqa: E402,F401
                                  _calc_local, _instant_route)


def try_instant(question: str, speech=None):
    """Ответ, если команда мгновенная; иначе None — тогда работает обычный мозг."""
    raw = re.sub(r"^\s*\([^)]*\)\s*", "", question)
    t = re.sub(r"^(?:атлас|atlas)[,\s]+", "", raw.lower().strip()).strip(" .!?«»\"")
    route = _instant_route(t)
    if not route:
        return None

    def on_text(d):
        if speech is not None:
            speech.feed(d)
    t0 = time.time()
    if route[0] == "local":
        reply = route[1]
        print(f"[мгновенно] «{t}» → без модели, {time.time() - t0:.2f}с")
    else:
        name, args = route[1], route[2]
        if name not in tools.AVAILABLE_FUNCTIONS:
            return None
        result, ok, ms = tools._run_one_tool(name, dict(args))
        if not ok or tools._TRACE_FAIL.search(str(result)[:220]):
            print(f"[мгновенно] {name} не сработал — передаю обычному мозгу")
            return None
        planner._note_action(name, args, True)
        reply = (planner._slim_answer(question, [(_NS(id="instant"), name, args)], [(result, ok, ms)], on_text)
                 or str(result)[:300])
        print(f"[мгновенно] «{t}» → {name} + короткий ответ, {time.time() - t0:.2f}с")
    state.conversation_history.append({"role": "user", "content": question})
    state.conversation_history.append({"role": "assistant", "content": reply})
    state.situation["reply"] = reply
    return reply
