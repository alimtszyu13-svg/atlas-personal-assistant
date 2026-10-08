"""
Модули, которые умеют работать только на компьютере, — в облаке заменяются заглушками.

Заглушка — это модуль, у которого есть ЛЮБАЯ функция: вызов возвращает «недоступно из облака».
Слово «недоступ» мозг считает неудачей, поэтому не скажет «готово» за то, чего не сделал.

import_with_autostub() — запасная сетка: если какой-то модуль проекта не загрузился в облаке
(нет Windows, нет библиотеки), он тоже подменяется заглушкой, а запуск продолжается.
"""
import importlib
import os
import sys
import traceback
import types

UNAVAILABLE = ("Это недоступно из облака: компьютер сейчас не на связи, а Atlas отвечает с сервера. "
               "Скажи об этом коротко и предложи, что можно сделать отсюда.")

# Только для компьютера: окна, файлы, звук, браузер, программы, железо
PC_ONLY = (
    "system_control", "file_control", "system_info", "reminders", "theme_control",
    "system_advanced", "media_control", "dev_tools", "listening_mode",
    "browser_agent", "deep_links", "web_gui", "window_hotkey", "hotkeys", "selection_hotkey",
    "core.desktop_agent", "core.holo", "core.gestures", "core.day_report", "core.healer", "core.skill_forge",
    "core.missions", "core.proactive", "core.speaker_id", "core.bus",
)


def _unavailable(name: str):
    def f(*args, **kwargs):
        return UNAVAILABLE
    f.__name__ = name
    f.__doc__ = "Недоступно из облака."
    return f


class _StubModule(types.ModuleType):
    """Любой атрибут — функция «недоступно из облака». Константы и классы, если их спросят, — тоже вызываемые."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        f = _unavailable(name)
        setattr(self, name, f)
        return f


def stub(name: str, **attrs) -> types.ModuleType:
    m = _StubModule(name)
    m.__dict__.update(attrs)
    m.__atlas_stub__ = True
    m.__path__ = []                                   # чтобы «from x.y import z» тоже работало
    sys.modules[name] = m
    parent, _, child = name.rpartition(".")
    if parent and parent in sys.modules:
        setattr(sys.modules[parent], child, m)
    return m


def install_pc_stubs(extra=()) -> list:
    done = []
    for name in tuple(PC_ONLY) + tuple(extra):
        stub(name)
        done.append(name)
    return done


def _project_module_of(exc: BaseException):
    """Какой модуль проекта упал при загрузке (последний файл проекта в трассировке)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        path = os.path.abspath(frame.filename)
        if path.startswith(root) and "/cloud/" not in path.replace("\\", "/") and path.endswith(".py"):
            rel = os.path.relpath(path, root)[:-3].replace(os.sep, ".")
            return rel[:-9] if rel.endswith(".__init__") else rel
    return None


def import_with_autostub(name: str, keep=(), limit: int = 40):
    """Импортировать модуль; если по дороге что-то не загрузилось — подменить это заглушкой и повторить.
    keep — модули, которые подменять нельзя (без них облако бессмысленно): тогда ошибка поднимается."""
    stubbed = []
    for _ in range(limit):
        try:
            return importlib.import_module(name), stubbed
        except ModuleNotFoundError as e:
            missing = e.name or ""
            if not missing or missing in keep or missing == name:
                raise
            stub(missing)
            stubbed.append(missing)
        except Exception as e:
            bad = _project_module_of(e)
            if not bad or bad in keep or bad == name or bad in stubbed:
                raise
            for k in [k for k in sys.modules if k == bad or k.startswith(bad + ".")]:
                del sys.modules[k]
            stub(bad)
            stubbed.append(bad)
        for k in [k for k in list(sys.modules) if k == name]:
            del sys.modules[k]
    raise RuntimeError(f"не удалось загрузить {name}: слишком много заглушек ({stubbed})")
