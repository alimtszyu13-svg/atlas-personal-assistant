"""
ask_ai — путь вопроса через Atlas, по шагам и в одном месте.

Раньше это была цепочка из шести обёрток, переопределявших ask_ai одна поверх другой.
Здесь тот же порядок, записанный подряд:

    1. мгновенные команды (кроме тренировки и фраз ритуалов)
    2. идёт тренировка → ответ уходит тренеру
    3. «ход мыслей» вокруг звезды: начало … конец
    4. фраза ритуала → ритуал без модели; после ответа — запись привычек
    5. уроки: прошлые ошибки и поправки → в запрос; поправка пользователя → новое правило
    6. мозг: понять → план → выполнить → ответить
"""
import re
import threading

from brain import instant, planner, state, tools
from brain.state import TaskCancelled


def _study_active() -> bool:
    try:
        from core import study
        return study.active()
    except Exception:
        return False


def _routine_trigger(question: str):
    try:
        from core import routines
        return routines.match_trigger(question)
    except Exception:
        return None


def ask_ai(question: str, speech=None) -> str:
    # 1. мгновенные команды
    if not _study_active() and not _routine_trigger(question):
        try:
            r = instant.try_instant(question, speech)
            if r:
                return r
        except TaskCancelled:
            raise
        except Exception as e:
            print(f"[мгновенно] {e} — передаю обычному мозгу")

    # 2. тренировка: ответ — тренеру, без модели
    try:
        from core import study
        if study.active():
            raw = re.sub(r"^\s*\([^)]*\)\s*", "", question)
            if re.match(r"^\s*(?:атлас|atlas)\b", raw, re.I):
                study.stop()                         # обратились к Atlas — значит, это уже не ответ
            else:
                state.conversation_history.append({"role": "user", "content": question})
                text = study.answer(raw)
                state.conversation_history.append({"role": "assistant", "content": text})
                return text
    except Exception as e:
        print(f"[учёба] {e}")

    # 3. ход мыслей
    tools._trace_wrap_all()                          # и навыки, выученные после запуска
    tools._trace_push({"t": "start", "q": re.sub(r"^\s*\([^)]*\)\s*", "", question)[:90]})
    try:
        reply = _with_routines(question, speech)
    except Exception:
        tools._trace_push({"t": "end", "ok": False})
        raise
    tools._trace_push({"t": "end", "ok": True})
    return reply


def _last_turn_slice(question: str):
    h = state.conversation_history
    idx = max(i for i, m in enumerate(h) if isinstance(m, dict) and m.get("role") == "user" and m.get("content") == question)
    return list(h[idx:])


def _with_routines(question: str, speech) -> str:
    # 4. ритуалы и привычки
    try:
        from core import routines
        r = routines.match_trigger(question)
        if r:
            print(f"[ритуалы] фраза запускает ритуал «{r['name']}»")
            state.conversation_history.append({"role": "user", "content": question})
            text = routines.run(r["id"])
            state.conversation_history.append({"role": "assistant", "content": text})
            return text
    except Exception as e:
        print(f"[ритуалы] {e}")
    reply = _with_lessons(question, speech)
    try:
        from core import routines
        threading.Thread(target=routines.log_tools, args=(question, _last_turn_slice(question)), daemon=True).start()
    except ValueError:
        pass
    except Exception as e:
        print(f"[ритуалы] журнал привычек: {e}")
    return reply


def _with_lessons(question: str, speech) -> str:
    # 5. уроки
    from core import lessons
    state.turn["question"] = question
    try:
        blk = lessons.lessons_block(question)
        state.turn["lesson_msg"] = {"role": "system", "content": blk} if blk else None
        if blk:
            print(f"[уроки] подмешано: {blk.count(chr(10) + '- ')}")
    except Exception as e:
        print(f"[уроки] {e}")
        state.turn["lesson_msg"] = None
    if lessons.is_correction(question):
        ctx = list(state.conversation_history[-10:])
        threading.Thread(target=lessons.learn_from_correction, args=(question, ctx), daemon=True).start()
    # 6. мозг
    reply = planner._brain_ask(question, speech)
    try:
        threading.Thread(target=lessons.record_turn, args=(question, _last_turn_slice(question)), daemon=True).start()
    except ValueError:
        pass
    except Exception as e:
        print(f"[уроки] разбор хода: {e}")
    return reply
