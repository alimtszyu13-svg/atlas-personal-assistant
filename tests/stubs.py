"""
Подставные версии всего, что вокруг мозга: модели, инструменты, ядро (core/*), интерфейс.
Мозг проверяется без интернета, ключей и Windows. Подключать ДО импорта ai_brain:

    import tests.stubs as stubs; stubs.install()
"""
import hashlib
import inspect
import os
import sys
import threading
import types

import numpy as np

CALLS = []          # какие инструменты вызывались: (имя, аргументы)


class _Fn:
    """Функция-заглушка с честной сигнатурой (inspect.signature её видит)."""


def _tool(name, params="", result=None):
    src = f"def {name}({params}):\n    CALLS.append(('{name}', dict(locals())))\n    return RESULT"
    ns = {"CALLS": CALLS, "RESULT": result if result is not None else f"{name}: ok"}
    exec(src, ns)
    return ns[name]


def _module(name, **attrs):
    m = types.ModuleType(name)

    def __getattr__(attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        f = _tool(attr)
        setattr(m, attr, f)
        return f
    m.__getattr__ = __getattr__
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# --------------------------------------------------------------------- модели
class _Completions:
    def create(self, **kw):
        raise RuntimeError("в тестах настоящие запросы к модели запрещены — подмени providers._call_model_stream")


class OpenAI:
    def __init__(self, **kw):
        self.kw = kw
        self.chat = types.SimpleNamespace(completions=_Completions())
        self.models = types.SimpleNamespace(list=lambda: types.SimpleNamespace(data=[]))


def _embed(texts):
    """Детерминированные «смыслы» по словам — для отбора инструментов по смыслу."""
    out = []
    for t in texts:
        v = np.zeros(128, dtype=np.float32)
        for w in str(t).lower().replace("_", " ").split():
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 128] += 1
        n = np.linalg.norm(v)
        out.append(v / n if n else v)
    return np.array(out)


class Gateway(types.ModuleType):
    pass


def install():
    os.environ.setdefault("GROQ_API_KEY", "test")
    for k in ("CEREBRAS_API_KEY", "GEMINI_API_KEY", "GITHUB_MODELS_TOKEN"):
        os.environ.pop(k, None)
    _module("openai", OpenAI=OpenAI)
    _module("dotenv", load_dotenv=lambda *a, **k: None)

    gw = _module("core.llm_gateway", MODEL_TPM={}, MODEL_PENALTY={}, COOL=[], RESERVED=[])
    gw.fit = lambda msgs, schema, limit=0: msgs
    gw.estimate = lambda x: 100
    gw.plan = lambda cands, est: (cands[0], 0, 8000)
    gw.reserve_any = lambda cands, est, chk=None: (gw.RESERVED.append(list(cands)) or cands[0])
    gw.record = lambda *a, **k: None
    gw.chars = lambda x: 100
    gw.cooldown = lambda *a: gw.COOL.append(a)

    lessons = _module("core.lessons", LEARNED=[])
    lessons.lessons_block = lambda q: ""
    lessons.is_correction = lambda q: False
    lessons.record_turn = lambda *a: None
    lessons.learn_from_correction = lambda *a: None
    lessons.learned_tools = lambda q: list(lessons.LEARNED)
    routines = _module("core.routines", TRIGGER=[None], LOGGED=[])
    routines.match_trigger = lambda q: routines.TRIGGER[0]
    routines.run = lambda rid: f"ритуал {rid} выполнен"
    routines.log_tools = lambda q, turn: routines.LOGGED.append(q)
    study = _module("core.study", ACTIVE=[False])
    study.active = lambda: study.ACTIVE[0]
    study.answer = lambda raw: f"тренер: {raw}"
    study.stop = lambda: (study.ACTIVE.__setitem__(0, False), "тренировка окончена")[1]
    forge = _module("core.skill_forge")
    forge.start = lambda **k: None
    forge.installed = lambda: []
    forge.list_items = lambda n=10: []
    forge.load_skill = lambda name: None
    skills = _module("core.skills")
    skills.load_skills = lambda: {}
    memory = _module("core.memory")
    memory.recall_block = lambda q: None
    healer = _module("core.healer", REPORTS=[])
    healer.report = lambda exc, where="": healer.REPORTS.append((str(exc), where))
    desktop = _module("core.desktop_agent")
    desktop.desktop_type = _tool("desktop_type", "text, index=-1, enter=False, replace=False", "Typed.")
    for sub in ("holo", "gestures", "day_report", "missions", "proactive"):
        _module(f"core.{sub}")
    import importlib.util as _ilu                                  # эмоции — настоящие: это чистый текст
    _spec = _ilu.spec_from_file_location("core.emotions", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core", "emotions.py"))
    _em = _ilu.module_from_spec(_spec)
    sys.modules["core.emotions"] = _em
    _spec.loader.exec_module(_em)
    core = _module("core")
    core.__path__ = []
    for sub in ("llm_gateway", "lessons", "routines", "study", "skill_forge", "skills", "memory", "healer",
                "desktop_agent", "holo", "gestures", "day_report", "missions", "proactive", "emotions"):
        setattr(core, sub, sys.modules[f"core.{sub}"])

    _module("system_control", open_app=_tool("open_app", "app_name", "Opened."), close_app=_tool("close_app", "app_name"),
            play_on_spotify=_tool("play_on_spotify", "query=''"))
    _module("info_services", get_weather=_tool("get_weather", "city=''", "Bishkek: 25°C, clear"),
            get_news=_tool("get_news", "count=5", "Новости: всё спокойно"))
    _module("system_info", get_cpu_usage=_tool("get_cpu_usage", "", "CPU 12%"))
    _module("dev_tools", calculate=_tool("calculate", "expression", "391"))
    _module("notes", add_note=_tool("add_note", "text"), list_todos=_tool("list_todos", "", "Список дел пуст"))
    for name in ("file_control", "reminders", "web_search_tool", "email_reader", "theme_control", "system_advanced",
                 "media_control", "listening_mode", "calendar_control", "network_utils", "text_utils", "database",
                 "browser_agent", "deep_links"):
        _module(name)
    sys.modules["database"].log_task = lambda *a: None
    sys.modules["browser_agent"]._browser_alive = lambda: False
    _module("file_search", _embed=_embed)
    voice = _module("voice", _stop_speaking=threading.Event())
    voice.get_response_language = lambda: "ru"
    ui = _module("ui_state", shared_state={"chat_history": []}, NOTES=[])
    ui.notify = lambda *a, **k: ui.NOTES.append(a)
    _module("pygetwindow", getActiveWindow=lambda: None)
    _module("psutil", cpu_percent=lambda interval=2: (__import__("time").sleep(interval), 7.0)[1])


# --------------------------------------------------------------------- сценарий модели
class Script:
    """Подставная модель: отвечает по списку заготовок и запоминает каждый запрос."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, on_text, **kw):
        self.requests.append(kw)
        r = self.replies.pop(0)
        if r.get("content"):
            on_text(r["content"])
        msg = {"role": "assistant", "content": r.get("content")}
        if r.get("tool_calls"):
            import json
            msg["tool_calls"] = [{"id": f"c{i}", "type": "function",
                                  "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                                 for i, (n, a) in enumerate(r["tool_calls"])]
        return msg, None
