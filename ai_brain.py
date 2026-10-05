"""
Мозг Atlas — входная дверь.

Вся логика живёт в пакете brain/ (см. brain/__init__.py — там карта модулей).
Этот файл только собирает её и отдаёт наружу те же имена, что и раньше:
ask_ai, cancel_current_task, remember_exchange, reset_conversation, set_announcer,
AVAILABLE_FUNCTIONS, TOOLS_SCHEMA, client, MODEL_SMART, _candidates, _client_for … —
поэтому main.py, интерфейс, ядро (core/*) и bench.py работают без изменений.
"""
from dotenv import load_dotenv

load_dotenv()

from brain import state, prompt, providers, tools, features, planner, instant, pipeline   # noqa: E402
from core import llm_gateway, memory                                                         # noqa: E402,F401
import tool_router                                                                          # noqa: E402,F401

# --- запуск, в том же порядке, что раньше ---
tools.start()                     # навыки из реестра, кэш погоды, фоновый замер процессора
features.start()                  # инструменты возможностей: ритуалы, учёба, голо-экран, жесты…
providers.start()                 # в фоне — проверка запасных моделей Groq
state.reset_conversation()
print("[мозг] модули: состояние, модели, инструменты, возможности, планировщик, мгновенные команды")

# --- публичные имена (как раньше) ---
from brain.pipeline import ask_ai                                                            # noqa: E402
from brain.state import (TaskCancelled, cancel_current_task, conversation_history, remember_exchange,  # noqa: E402
                         reset_conversation, set_announcer, clean_messages_for_api, _check_cancel, _cancel_event,
                         _msg_role, _trim_history, _drop_dangling_tool_calls, MAX_HISTORY_MESSAGES, MAX_TOOL_RESULT_CHARS)
from brain.prompt import SYSTEM_PROMPT, SLIM_PROMPT                                          # noqa: E402
from brain.providers import (client, cerebras_client, gh_client, gem_client, MODEL, MODEL_SMART, MODEL_FAST,  # noqa: E402
                             CEREBRAS_MODEL, GH_MODEL, GEM_MODEL, _candidates, _client_for, _call_model_stream,
                             _call_model, _retry_after, _as_message, _NS)
from brain.tools import (AVAILABLE_FUNCTIONS, TOOLS_SCHEMA, READ_ONLY_TOOLS, TOOL_PAIRS, _run_one_tool,  # noqa: E402
                         _repair_args, _trim_schema, _with_pairs, _with_learned, _with_tool, _heal_report,
                         _trace_push, _trace_done, _traced, _trace_wrap_all, _TRACE_FAIL, update_plan, register)
from brain.planner import (execute_plan, _brain_ask, _exec_steps, _situation_msg, _note_action, _context_snapshot,  # noqa: E402
                           _effort_for, _user_profile, _slim_answer, _slim_ok, SLIM_TOOLS)
from brain.features import memory_review, _json_call                                         # noqa: E402
from brain.instant import _instant_route, _calc_local                                         # noqa: E402

_ask_ai_base = planner._brain_ask
_LEGACY = {                         # старые глобальные переменные — теперь части state
    "_situation": lambda: state.situation, "_announce": lambda: state.announce,
    "_lesson_msg": lambda: state.turn["lesson_msg"], "_current_question": lambda: state.turn["question"],
    "current_plan": lambda: state.turn["plan"], "consecutive_failures": lambda: state.turn["failures"],
}


def __getattr__(name):
    """Старое имя, которого нет выше, — ищем в модулях мозга (чтобы ничего снаружи не сломалось)."""
    if name in _LEGACY:
        return _LEGACY[name]()
    for mod in (state, providers, tools, features, planner, instant, prompt, pipeline):
        if hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(f"module 'ai_brain' has no attribute '{name}'")
