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

_RU_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]
_RU_DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_CUR = {"доллар": "USD", "долл": "USD", "бакс": "USD", "евро": "EUR", "рубл": "RUB", "юан": "CNY", "тенге": "KZT",
        "фунт": "GBP", "лир": "TRY", "dollar": "USD", "euro": "EUR", "ruble": "RUB"}
_MULTI = re.compile(r"\s(?:и|а потом|потом|затем|после этого|and|then)\s|,\s*(?:и\s+)?(?:запиши|открой|включи|найди|покажи)")
_REMEMBER_EMPTY = re.compile(r"(?:запомни|запиши в памят\w*|remember(?: this| that)?)(?:[,\s]+(?:это|знаешь|пожалуйста|please|ok|ладно))*")


def _calc_local(expr: str):
    """«17 умножить на 23» → 391. Только цифры и знаки — никаких функций и степеней."""
    e = expr.lower().replace(",", ".")
    for a, b in (("умножить на", "*"), ("умножь на", "*"), ("помножить на", "*"), ("times", "*"), ("multiplied by", "*"),
                 ("разделить на", "/"), ("делить на", "/"), ("divided by", "/"), ("плюс", "+"), ("plus", "+"),
                 ("минус", "-"), ("minus", "-"), ("на", "*"), ("х", "*"), ("x", "*"), ("×", "*"), ("÷", "/")):
        e = e.replace(a, f" {b} ")
    e = re.sub(r"[?=]", "", e).strip()
    if not re.fullmatch(r"[\d\s.+\-*/()]+", e) or not re.search(r"\d", e) or "**" in e or len(e) > 60:
        return None
    try:
        v = eval(e, {"__builtins__": {}}, {})
    except Exception:
        return None
    if isinstance(v, float):
        v = round(v, 6)
        if v == int(v):
            v = int(v)
    return v


def _instant_route(t: str):
    """Фраза → ('local', ответ) | ('tool', имя, аргументы) | None."""
    ru = bool(re.search(r"[а-яё]", t))
    if _REMEMBER_EMPTY.fullmatch(t.strip(" ,.!?")):
        return ("local", "Что именно запомнить, сэр?" if ru else "What exactly should I remember, sir?")
    if len(t.split()) > 10 or _MULTI.search(t):
        return None
    if re.fullmatch(r"(?:а\s+)?(?:который час|сколько (?:сейчас )?времени|какое (?:сейчас )?время|what time is it)", t):
        n = _dt.now()
        return ("local", f"Сейчас {n:%H:%M}." if ru else f"It's {n:%H:%M}.")
    if re.fullmatch(r"(?:а\s+)?(?:какое (?:сегодня )?число|какой (?:сегодня )?день(?: недели)?|какая (?:сегодня )?дата|"
                    r"what(?:'s| is) the date(?: today)?|what day is (?:it|today))", t):
        n = _dt.now()
        return ("local", f"Сегодня {_RU_DAYS[n.weekday()]}, {n.day} {_RU_MONTHS[n.month - 1]}." if ru
                else f"Today is {n:%A, %B} {n.day}.")
    m = re.fullmatch(r"(?:сколько будет|посчитай|вычисли|what is|what's|how much is|calculate)\s+(.+)", t)
    if m:
        v = _calc_local(m.group(1))
        if v is not None:
            return ("local", f"{v}.")
    if re.fullmatch(r"(?:а\s+)?(?:какая\s+)?(?:сейчас\s+)?погода(?:\s+(?:сейчас|на улице|сегодня))?|сколько (?:сейчас )?градусов"
                    r"(?: на улице)?|что (?:там )?на улице|холодно (?:ли )?(?:сегодня|на улице)?|"
                    r"what(?:'s| is) the weather(?: like)?(?: today| now)?|how(?:'s| is) the weather", t):
        return ("tool", "get_weather", {})
    if re.search(r"(?:сколько|много ли|осталось).*(?:мест|свободн).*диск|free (?:disk|space)", t):
        return ("tool", "get_disk_usage", {"drive": "D" if re.search(r"\bд\b|диск[еа]? d\b|\bd\b", t) else "C"})
    if re.search(r"(?:загрузк|нагрузк|загружен).*(?:процессор|цп|cpu)|cpu (?:usage|load)", t):
        return ("tool", "get_cpu_usage", {})
    if re.search(r"(?:оперативн|ram usage|memory usage)", t):
        return ("tool", "get_memory_usage", {})
    if re.search(r"(?:заряд|батаре|battery)", t):
        return ("tool", "get_battery_status", {})
    m = re.search(r"курс\s+(\w+)|сколько стоит\s+(\w+)|(dollar|euro|ruble) (?:rate|exchange)", t)
    if m and "get_exchange_rate" in tools.AVAILABLE_FUNCTIONS:
        word = next(g for g in m.groups() if g)
        base = next((v for k, v in _CUR.items() if word.startswith(k)), None)
        if base:
            target = "RUB" if re.search(r"рубл|ruble", t) and base != "RUB" else "KGS"
            return ("tool", "get_exchange_rate", {"base_currency": base, "target_currency": target})
    if re.fullmatch(r"(?:какие |расскажи |главные |последние )*(?:новости|что нового)(?: сегодня| в мире)?|"
                    r"(?:what's the |latest )?news(?: today)?", t):
        return ("tool", "get_news", {"count": 5})
    if re.search(r"(?:какие|покажи|что в|что у меня в).*(?:задач|списке дел|список дел|дел на сегодня)", t) \
            and not re.search(r"добав|удали|отмет", t):
        return ("tool", "list_todos", {})
    return None


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
