"""
Сценарии — несколько команд одной фразой.

    «создай режим кино: сверни всё, яркость 40, громкость 60, открой кинопоиск»
    «режим кино» / «включи режим кино»          → всё по очереди, в конце одна короткая фраза
    «добавь в режим кино: включи ночной свет»
    «что в режиме кино?» / «какие у меня режимы?» / «удали режим кино»

Каждый шаг — обычная команда Atlas (как если бы ты её сказал): окна, громкость, яркость, программы,
сайты, музыка, фокус, «скажи, когда…». Простые выполняются мгновенно, остальные — через мозг, молча.
Хранится в atlas_scenarios.json, только на этом компьютере.
"""
import json
import os
import re
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "atlas_scenarios.json")
_lock = threading.Lock()
_FAIL = re.compile(r"не (?:нашёл|нашел|смог|получилось|удалось|знаю|понял)|нет такого|недоступ|ошибк|couldn'?t|"
                   r"can'?t|failed|not found|unknown|error", re.I)
_KIND = r"режим\w*|сценари\w*|mode|scenario|routine"


def _ru(text: str) -> bool:
    return bool(re.search(r"[а-яё]", text or "", re.I))


def _key(name: str) -> str:
    n = re.sub(r"[«»\"'.!?]", "", (name or "").lower().replace("ё", "е")).strip()
    n = re.sub(r"^(?:режим\w*|сценари\w*)\s+", "", n)
    return " ".join(w[:-1] if len(w) > 4 and w[-1] in "аеиоуыяюй" else w for w in n.split())


def load() -> dict:
    try:
        with open(FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d: dict) -> None:
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, FILE)


def split_steps(text: str) -> list:
    parts = re.split(r"\s*(?:;|,|\n|\bа потом\b|\bпотом\b|\bзатем\b|\bпосле этого\b|\bthen\b|\band then\b)\s*", text or "")
    out = []
    for p in parts:
        p = p.strip(" .")
        if not p:
            continue
        sub = re.split(r"\s+и\s+(?=(?:открой|включи|выключи|сверни|разверни|поставь|сделай|запусти|закрой|убавь|"
                       r"прибавь|громкость|яркость|начни|останови|turn|open|close|set|start|play)\b)", p)
        out += [s.strip() for s in sub if s.strip()]
    return out[:15]


def _fix(step: str) -> str:
    """«яркость 40» → «яркость 40%» (так быстрые команды понимают точнее)."""
    s = step.strip()
    m = re.fullmatch(r"(яркость|громкость|brightness|volume)\s+(?:на\s+)?(\d{1,3})\s*%?", s, re.I)
    if m:
        return f"{'поставь ' if _ru(s) else 'set '}{m.group(1).lower()} {m.group(2)}%"
    return s


_CREATE = re.compile(r"^(?:атлас[,\s]+)?(?:создай|сделай|запомни|заведи|добавь|create|make|save)\s+(?:новый\s+|new\s+)?"
                     r"(" + _KIND + r")\s+[«\"]?([\w\s-]{2,30}?)[»\"]?\s*[:—–-]\s*(.+)$", re.I | re.S)
_ADD = re.compile(r"^(?:атлас[,\s]+)?(?:добавь|допиши|add)\s+(?:в|к|to)\s+(?:" + _KIND + r")\s+[«\"]?([\w\s-]{2,30}?)[»\"]?"
                  r"\s*[:—–-]\s*(.+)$", re.I | re.S)
_DEL = re.compile(r"^(?:атлас[,\s]+)?(?:удали|убери|сотри|delete|remove)\s+(?:" + _KIND + r")\s+[«\"]?(.+?)[»\"]?$", re.I)
_SHOW = re.compile(r"^(?:атлас[,\s]+)?(?:что\s+(?:в|делает)|покажи|what(?:'s| is) in)\s+(?:" + _KIND +
                   r")\s+[«\"]?(.+?)[»\"]?\??$", re.I)
_LIST = re.compile(r"(?:какие|мои|список|покажи)\s+(?:у меня\s+)?(?:есть\s+)?(?:режим\w*|сценари\w*)|"
                   r"(?:list|my) (?:modes|scenarios)", re.I)
_RUN = re.compile(r"^(?:атлас[,\s]+)?(?:включи|запусти|активируй|начни|давай|врубай|врубить|turn on|start|run|activate)?\s*"
                  r"(?:(" + _KIND + r")\s+)?[«\"]?(.+?)[»\"]?(?:\s+(" + _KIND + r"))?\s*[.!]?$", re.I)


def intent(text: str):
    t = " ".join((text or "").split()).strip()
    if not t:
        return None
    m = _CREATE.match(t)
    if m:
        name = m.group(2).strip()
        if m.group(1).lower().startswith("режим") and not name.lower().startswith("режим"):
            name = name
        return ("create", name, split_steps(m.group(3)))
    m = _ADD.match(t)
    if m:
        return ("add", m.group(1).strip(), split_steps(m.group(2)))
    m = _DEL.match(t)
    if m and _key(m.group(1)) in load():
        return ("delete", m.group(1).strip())
    m = _SHOW.match(t)
    if m and _key(m.group(1)) in load():
        return ("show", m.group(1).strip())
    if _LIST.search(t):
        return ("list",)
    m = _RUN.match(t)
    if m and len(t) < 50:
        k = _key(m.group(2))
        d = load()
        if k in d and (m.group(1) or m.group(3) or d[k].get("bare", True)):
            return ("run", k)
    return None


def create(name: str, steps: list, ru: bool = True, add: bool = False) -> str:
    k = _key(name)
    if not k or not steps:
        return "Скажи название и шаги: «создай режим кино: сверни всё, громкость 60»." if ru else \
            "Say a name and steps: “create movie mode: minimize all, volume 60”."
    with _lock:
        d = load()
        if add:
            if k not in d:
                return f"Режима «{name}» нет." if ru else f"No “{name}” mode."
            d[k]["steps"] += steps
        else:
            d[k] = {"name": name.strip(), "steps": steps, "bare": True}
        _save(d)
    st = d[k]["steps"]
    if add:
        return (f"Добавил. Теперь в «{d[k]['name']}» {len(st)} шагов." if ru else f"Added. {len(st)} steps now.")
    return (f"Готово: «{name}» — {len(st)} {'шаг' if len(st) == 1 else 'шага' if len(st) < 5 else 'шагов'}: "
            f"{'; '.join(st)}. Скажи «режим {name}», чтобы включить." if ru else
            f"Saved “{name}” with {len(st)} steps: {'; '.join(st)}.")


def run(key: str, runner, ru: bool = True) -> str:
    d = load().get(key)
    if not d:
        return "Такого режима нет." if ru else "No such mode."
    bad = []
    for step in d["steps"]:
        cmd = _fix(step)
        try:
            out = runner(cmd) or ""
        except Exception as e:
            out = f"error: {e}"
        print(f"[сценарий] {cmd} → {str(out)[:120]}")
        if _FAIL.search(str(out)[:200]):
            bad.append(step)
    name = d["name"]
    if not bad:
        return f"Режим «{name}» включён." if ru else f"“{name}” is on."
    return (f"Режим «{name}» включён, но не вышло: {', '.join(bad)}." if ru else
            f"“{name}” is on, except: {', '.join(bad)}.")


def handle(text: str, runner):
    it = intent(text)
    if not it:
        return None
    ru = _ru(text)
    k = it[0]
    if k == "create":
        return create(it[1], it[2], ru)
    if k == "add":
        return create(it[1], it[2], ru, add=True)
    d = load()
    if k == "list":
        if not d:
            return "Режимов пока нет. Пример: «создай режим кино: сверни всё, громкость 60»." if ru else \
                "No modes yet. Example: “create movie mode: minimize all, volume 60”."
        return ("Твои режимы: " if ru else "Your modes: ") + ", ".join(v["name"] for v in d.values()) + "."
    if k == "show":
        v = d[_key(it[1])]
        return f"«{v['name']}»: " + "; ".join(v["steps"]) + "."
    if k == "delete":
        with _lock:
            d = load()
            v = d.pop(_key(it[1]), None)
            _save(d)
        return (f"Удалил режим «{v['name']}»." if ru else f"Deleted “{v['name']}”.") if v else "Нет такого."
    return run(it[1], runner, ru)
