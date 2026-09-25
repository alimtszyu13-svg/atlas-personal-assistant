"""
Реестр навыков Atlas.

Инструмент = функция + декоратор @skill. Схему для модели (JSON Schema)
декоратор собирает сам: имена и типы — из подписи функции, описание —
из docstring. Больше не нужно писать JSON руками и править четыре файла.
"""
import importlib
import inspect
import pkgutil
import typing

REGISTRY = {}   # имя → {"fn", "group", "read_only", "schema"}

_PY2JSON = {str: "string", int: "integer", float: "number",
            bool: "boolean", list: "array", dict: "object"}


def _schema_for(fn) -> dict:
    """Параметры функции → JSON Schema. Параметр без значения по умолчанию — обязательный."""
    sig = inspect.signature(fn)
    hints = typing.get_type_hints(fn)
    props, required = {}, []
    for name, p in sig.parameters.items():
        t = hints.get(name, str)
        origin = typing.get_origin(t) or t
        js = {"type": _PY2JSON.get(origin, "string")}
        if origin is list:
            args = typing.get_args(t)
            js["items"] = {"type": _PY2JSON.get(args[0], "string") if args else "string"}
        props[name] = js
        if p.default is inspect.Parameter.empty:
            required.append(name)
    return {"type": "object", "properties": props, "required": required}


def skill(group: str, read_only: bool = False, description: str = None, params: dict = None):
    """Регистрирует функцию как инструмент Atlas.

    group       — группа для роутера ("files", "system", "fun", ...)
    read_only   — только читает данные (такие можно запускать параллельно)
    description — описание для модели; по умолчанию первый абзац docstring
    params      — пояснения к параметрам: {"имя": "что это"}
    """
    def deco(fn):
        desc = description or (inspect.getdoc(fn) or fn.__name__).split("\n\n")[0]
        schema = _schema_for(fn)
        for n, d in (params or {}).items():
            if n in schema["properties"]:
                schema["properties"][n]["description"] = d
        REGISTRY[fn.__name__] = {
            "fn": fn, "group": group, "read_only": read_only,
            "schema": {"type": "function", "function": {
                "name": fn.__name__, "description": desc, "parameters": schema}},
        }
        return fn
    return deco


def load_skills(package: str = "skills") -> dict:
    """Импортирует все модули из папки skills/ — их декораторы регистрируют навыки."""
    pkg = importlib.import_module(package)
    for m in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"{package}.{m.name}")
    return REGISTRY