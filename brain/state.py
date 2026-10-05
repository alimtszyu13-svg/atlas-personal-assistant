"""
Общее состояние разговора.

Всё, что раньше было разбросано глобальными переменными по ai_brain.py, — здесь.
Списки и словари только изменяются на месте и никогда не пересоздаются: поэтому
ai_brain.conversation_history и другие ссылки снаружи всегда смотрят на актуальные данные.
"""
import threading


class TaskCancelled(Exception):
    """Пользователь сказал «стоп» или нажал F8."""


_cancel_event = threading.Event()


def cancel_current_task() -> str:
    """Прерывает текущую задачу модели: цикл инструментов, ожидание лимита, запрос."""
    _cancel_event.set()
    return "Cancelling."


def _check_cancel() -> None:
    if _cancel_event.is_set():
        raise TaskCancelled()


# --- история разговора -----------------------------------------------------
conversation_history: list = []
MAX_HISTORY_MESSAGES = 24       # система + N последних — не даём истории расти бесконечно
MAX_TOOL_RESULT_CHARS = 2500    # большие результаты (поиск, страница) обрезаются перед записью в историю

# --- рабочая память и текущий ход --------------------------------------------
situation = {"goal": "", "actions": [], "reply": ""}               # цель, последние действия, последний ответ
turn = {"question": "", "lesson_msg": None, "plan": None, "failures": 0}
announce = {"fn": None}                                            # «сказать вслух» для фоновых задач


def set_announcer(fn) -> None:
    """main.py даёт функцию «сказать вслух» — ею объявляются итоги фоновых задач."""
    announce["fn"] = fn


def _msg_role(msg):
    return msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)


def clean_messages_for_api(messages):
    """Убирает поля (annotations и т.п.), из-за которых API отвечает 400."""
    out = []
    for msg in messages:
        if isinstance(msg, dict):
            out.append({k: v for k, v in msg.items() if k not in ("annotations", "function_call") and v is not None})
        else:
            out.append(msg)
    return out


def _trim_history() -> None:
    """Система + последние сообщения; хвост не начинается с «осиротевшего» ответа инструмента."""
    h = conversation_history
    if len(h) <= MAX_HISTORY_MESSAGES:
        return
    tail = h[-(MAX_HISTORY_MESSAGES - 1):]
    while tail and _msg_role(tail[0]) == "tool":
        tail = tail[1:]
    h[:] = [h[0]] + tail


def _drop_dangling_tool_calls() -> None:
    """После отмены в хвосте истории могут остаться вызовы без ответов — API на такое ругается."""
    h = conversation_history
    while h:
        last = h[-1]
        role = _msg_role(last)
        if role == "tool" or (role == "assistant" and isinstance(last, dict) and last.get("tool_calls")):
            h.pop()
        else:
            break


def remember_exchange(user_text: str, assistant_text: str) -> None:
    """Записывает в историю то, что выполнил быстрый путь без модели —
    иначе следующее «да, включи» не знает, о чём речь."""
    conversation_history.append({"role": "user", "content": user_text})
    conversation_history.append({"role": "assistant", "content": assistant_text})


def reset_conversation(system_prompt: str = None) -> None:
    from brain import prompt
    conversation_history[:] = [{"role": "system", "content": system_prompt or prompt.SYSTEM_PROMPT}]
