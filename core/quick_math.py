"""
Мгновенный калькулятор и конвертер — без модели, за миллисекунды.

    «сколько будет 15% от 34 тысяч»       → 5 100
    «34 тысячи плюс 15 процентов»        → 39 100
    «2 в степени 10», «корень из 144»    → 1024, 12
    «200 долларов в сомах»               → по курсу (тот же источник, что «курс доллара»)
    «5 миль в километры», «30 градусов по фаренгейту в цельсии», «2 гигабайта в мегабайтах»
    «сколько дней до 1 декабря», «какой день будет через 45 дней», «сколько мне лет, если я родился в 2008»
"""
import ast
import math
import operator
import re
from datetime import date, timedelta

_NUMW = {"ноль": 0, "один": 1, "одна": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
         "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
         "пятнадцать": 15, "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "сто": 100,
         "двести": 200, "триста": 300, "пятьсот": 500, "полтора": 1.5, "полторы": 1.5, "половина": 0.5,
         "half": 0.5, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "hundred": 100}
_MULT = [(r"тыс(?:яч[аиу]?|\.|)(?![а-я])|k\b|thousand", 1e3), (r"млн|миллион\w*|million", 1e6),
         (r"млрд|миллиард\w*|billion", 1e9)]
_MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма[йя]": 5, "июн": 6, "июл": 7, "август": 8,
           "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4,
           "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_RU_DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_RU_MON = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
           "ноября", "декабря"]

# единица → (величина, множитель к базовой)
UNITS = {
    "km": ("len", 1000), "m": ("len", 1), "cm": ("len", 0.01), "mm": ("len", 0.001), "mi": ("len", 1609.344),
    "ft": ("len", 0.3048), "in": ("len", 0.0254), "yd": ("len", 0.9144),
    "kg": ("mass", 1), "g": ("mass", 0.001), "t": ("mass", 1000), "lb": ("mass", 0.45359237), "oz": ("mass", 0.0283495),
    "l": ("vol", 1), "ml": ("vol", 0.001), "gal": ("vol", 3.78541),
    "kmh": ("speed", 1 / 3.6), "ms": ("speed", 1), "mph": ("speed", 0.44704),
    "b": ("data", 1), "kb": ("data", 1024), "mb": ("data", 1024 ** 2), "gb": ("data", 1024 ** 3), "tb": ("data", 1024 ** 4),
    "s": ("time", 1), "min": ("time", 60), "h": ("time", 3600), "day": ("time", 86400), "week": ("time", 604800),
    "c": ("temp", None), "f": ("temp", None), "k": ("temp", None),
}
_UNIT_WORDS = [
    (r"километр\w*\s+в\s+час|км/ч|kmh|km/h", "kmh"), (r"мил\w*\s+в\s+час|mph", "mph"), (r"метр\w*\s+в\s+секунд\w*|м/с", "ms"),
    (r"километр\w*|км\b|kilometer\w*|km\b", "km"), (r"сантиметр\w*|см\b|centimeter\w*|cm\b", "cm"),
    (r"миллиметр\w*|мм\b|millimeter\w*|mm\b", "mm"), (r"метр\w*|\bм\b|meters?|metres?", "m"),
    (r"мил[ьяие]\w*|miles?", "mi"), (r"фут\w*|feet|foot|ft\b", "ft"), (r"дюйм\w*|inch\w*", "in"),
    (r"ярд\w*|yards?", "yd"),
    (r"килограмм\w*|кг\b|kilo\w*|kg\b", "kg"), (r"грамм\w*|гр?\b|grams?", "g"), (r"тонн\w*|tons?", "t"),
    (r"фунт\w*(?!\s+стерл)|pounds?|lbs?\b", "lb"), (r"унци\w*|ounces?|oz\b", "oz"),
    (r"миллилитр\w*|мл\b|ml\b", "ml"), (r"литр\w*|\bл\b|liters?|litres?", "l"), (r"галлон\w*|gallons?", "gal"),
    (r"терабайт\w*|тб\b|tb\b", "tb"), (r"гигабайт\w*|гб\b|гиг\w*|gb\b", "gb"), (r"мегабайт\w*|мб\b|mb\b", "mb"),
    (r"килобайт\w*|кб\b|kb\b", "kb"), (r"байт\w*|bytes?", "b"),
    (r"секунд\w*|сек\b|seconds?", "s"), (r"минут\w*|мин\b|minutes?", "min"), (r"час\w*|hours?", "h"),
    (r"(?:дн[ейяю]|день|сут\w*|days?)", "day"), (r"недел\w*|weeks?", "week"),
    (r"(?:градус\w*\s+)?(?:по\s+)?цельси\w*|celsius|°c", "c"), (r"(?:градус\w*\s+)?(?:по\s+)?фаренгейт\w*|fahrenheit|°f", "f"),
    (r"кельвин\w*|kelvin", "k"),
]
_CUR = {r"доллар\w*|бакс\w*|\$|usd|dollars?": "USD", r"евро|eur|euros?|€": "EUR", r"рубл\w*|rub\b|₽|rubles?": "RUB",
        r"сом\w*|kgs|soms?": "KGS", r"тенге|kzt": "KZT", r"юан\w*|cny|yuan": "CNY", r"фунт\w* стерл\w*|gbp": "GBP",
        r"лир\w*|try\b": "TRY", r"сум\w*|uzs": "UZS"}

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Mod: operator.mod}


def safe_eval(expr: str):
    """Только числа, + − × ÷, степень (до 10^6), скобки, sqrt."""
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and (abs(b) > 100 or abs(a) > 1e6):
                raise ValueError("слишком большая степень")
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "sqrt" and len(n.args) == 1:
            return math.sqrt(ev(n.args[0]))
        raise ValueError("нельзя")
    if len(expr) > 120:
        raise ValueError("длинно")
    return ev(ast.parse(expr, mode="eval"))


def fmt(v) -> str:
    if isinstance(v, float):
        if abs(v) >= 1e15 or (v and abs(v) < 1e-6):
            return f"{v:.4g}"
        v = round(v, 2)
        if v == int(v):
            v = int(v)
    s = f"{v:,}".replace(",", " ") if isinstance(v, int) else f"{v:,}".replace(",", " ")
    return s.replace(".", ",") if re.search(r"\d\.\d", s) else s


def _numbers(t: str) -> str:
    """«34 тысячи» → 34000, «полтора миллиона» → 1500000, «пять» → 5, «2,5» → 2.5."""
    t = re.sub(r"(\d) (?=\d{3}\b)", r"\1", t)                        # «34 000» → 34000
    t = re.sub(r"(\d),(\d)", r"\1.\2", t)
    for w, v in sorted(_NUMW.items(), key=lambda kv: -len(kv[0])):
        t = re.sub(rf"(?<![\w]){w}(?![\w])", str(v), t)
    for rx, mul in _MULT:
        t = re.sub(r"(\d+(?:\.\d+)?)\s*(?:" + rx + ")", lambda m: repr(float(m.group(1)) * mul).rstrip("0").rstrip("."), t)
        t = re.sub(r"(?<![\d.])(?:" + rx + ")", repr(mul).rstrip("0").rstrip("."), t)
    return t


def _expr(t: str) -> str:
    e = " " + t + " "
    for a, b in (("умножить на", "*"), ("умножь на", "*"), ("помножить на", "*"), ("times", "*"),
                 ("multiplied by", "*"), ("разделить на", "/"), ("делить на", "/"), ("поделить на", "/"),
                 ("divided by", "/"), ("плюс", "+"), ("plus", "+"), ("минус", "-"), ("minus", "-"),
                 ("в степени", "**"), ("to the power of", "**"), ("в квадрате", "**2"), ("squared", "**2"),
                 ("в кубе", "**3"), ("cubed", "**3"), ("×", "*"), ("÷", "/"), ("^", "**"), (" x ", " * "), (" х ", " * ")):
        e = e.replace(a, f" {b} ")
    e = re.sub(r"(?:квадратный\s+)?корень(?:\s+квадратный)?\s+(?:из\s+)?([\d.]+)", r" sqrt(\1) ", e)
    e = re.sub(r"square root of ([\d.]+)", r" sqrt(\1) ", e)
    e = re.sub(r"(?<=\d)\s+на\s+(?=\d)", " * ", e)
    e = re.sub(r"[?=]", "", e)
    return " ".join(e.split())


_ASK = re.compile(r"^(?:а\s+)?(?:сколько будет|сколько это|сколько|посчитай|вычисли|подсчитай|рассчитай|переведи|"
                  r"конвертируй|what(?:'s| is)|how much is|how many|calculate|convert)\s+(.+)$", re.I)


def _percent(t: str):
    m = re.fullmatch(r"([\d.]+)\s*(?:%|процент\w*|percent)\s+(?:от|of)\s+([\d.]+)", t)
    if m:
        return float(m.group(1)) * float(m.group(2)) / 100
    m = re.fullmatch(r"([\d.]+)\s*([+-]|плюс|минус|plus|minus)\s*([\d.]+)\s*(?:%|процент\w*|percent)", t)
    if m:
        a, b = float(m.group(1)), float(m.group(3))
        return a * (1 + b / 100) if m.group(2) in ("+", "плюс", "plus") else a * (1 - b / 100)
    m = re.fullmatch(r"(?:какой процент|сколько процентов|what percent(?:age)?)\s+(?:составляет\s+)?([\d.]+)\s+(?:от|of|is of)\s+([\d.]+)", t)
    if m:
        return ("pct", float(m.group(1)) * 100 / float(m.group(2)))
    return None


def _unit(word: str):
    for rx, u in _UNIT_WORDS:
        if re.fullmatch(r"(?:" + rx + r")", word.strip(), re.I):
            return u
    return None


def convert(value: float, src: str, dst: str):
    a, b = UNITS[src], UNITS[dst]
    if a[0] != b[0]:
        return None
    if a[0] == "temp":
        c = value if src == "c" else (value - 32) * 5 / 9 if src == "f" else value - 273.15
        return c if dst == "c" else c * 9 / 5 + 32 if dst == "f" else c + 273.15
    return value * a[1] / b[1]


_UNIT_NAME = {"km": "км", "m": "м", "cm": "см", "mm": "мм", "mi": "миль", "ft": "футов", "in": "дюймов", "yd": "ярдов",
              "kg": "кг", "g": "г", "t": "т", "lb": "фунтов", "oz": "унций", "l": "л", "ml": "мл", "gal": "галлонов",
              "kmh": "км/ч", "ms": "м/с", "mph": "миль/ч", "b": "байт", "kb": "КБ", "mb": "МБ", "gb": "ГБ", "tb": "ТБ",
              "s": "секунд", "min": "минут", "h": "часов", "day": "дней", "week": "недель", "c": "°C", "f": "°F", "k": "K"}


def _cur(word: str):
    for rx, c in _CUR.items():
        if re.fullmatch(r"(?:" + rx + r")", word.strip(), re.I):
            return c
    return None


def _date_in(t: str, today: date):
    m = re.search(r"(\d{1,2})\s*(?:-?го\s+)?(" + "|".join(_MONTHS) + r")\w*(?:\s+(\d{4}))?", t)
    if m:
        mon = next(v for k, v in _MONTHS.items() if re.match(k, m.group(2)))
        y = int(m.group(3)) if m.group(3) else today.year
        d = date(y, mon, int(m.group(1)))
        if not m.group(3) and d < today:
            d = date(y + 1, mon, int(m.group(1)))
        return d
    m = re.search(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?", t)
    if m:
        y = int(m.group(3)) if m.group(3) else today.year
        y = y + 2000 if y < 100 else y
        d = date(y, int(m.group(2)), int(m.group(1)))
        if not m.group(3) and d < today:
            d = date(y + 1, d.month, d.day)
        return d
    if re.search(r"новог\w* год|new year", t):
        return date(today.year + 1, 1, 1)
    return None


def _days(n: int, ru: bool) -> str:
    if not ru:
        return f"{n} day{'s' if n != 1 else ''}"
    k = n % 100
    w = "дней" if 11 <= k <= 19 else "день" if k % 10 == 1 else "дня" if 2 <= k % 10 <= 4 else "дней"
    return f"{fmt(n)} {w}"


def answer(text: str, today: date = None, rate=None):
    """Фраза → короткий ответ или None (не арифметика / не конвертация)."""
    today = today or date.today()
    raw = " ".join((text or "").lower().replace("ё", "е").split()).strip(" ?!.")
    raw = re.sub(r"^(?:атлас|atlas)[,\s]+", "", raw)
    ru = bool(re.search(r"[а-я]", raw))
    if not raw or len(raw) > 120:
        return None
    # даты
    m = re.search(r"сколько (?:осталось )?(?:дней|дня) (?:до|осталось до)\s+(.+)|how many days (?:until|till|to)\s+(.+)", raw)
    if m:
        d = _date_in(_numbers(m.group(1) or m.group(2)), today)
        if d:
            n = (d - today).days
            when = f"{d.day} {_RU_MON[d.month - 1]}" if ru else d.strftime("%B %d")
            return (f"До {when} — {_days(n, ru)}." if ru else f"{_days(n, ru)} until {when}.")
    m = re.search(r"(?:какое число|какой день|какая дата|what date|what day) (?:будет )?(?:через|in)\s+(\d+)\s*(дн\w*|день|недел\w*|days?|weeks?)", _numbers(raw))
    if m:
        n = int(m.group(1)) * (7 if re.match(r"недел|week", m.group(2)) else 1)
        d = today + timedelta(days=n)
        return (f"{_RU_DAYS[d.weekday()].capitalize()}, {d.day} {_RU_MON[d.month - 1]} {d.year}." if ru else
                d.strftime("%A, %B %d, %Y."))
    m = re.search(r"сколько (?:мне )?(?:будет )?лет,? если (?:я )?родил\w* в (\d{4})|how old .* born in (\d{4})", raw)
    if m:
        y = int(m.group(1) or m.group(2))
        return (f"В этом году исполняется {today.year - y}." if ru else f"Turning {today.year - y} this year.")
    m = _ASK.match(raw)
    body = m.group(1) if m else raw
    p = _percent(_numbers(raw))
    if isinstance(p, tuple):
        return f"{fmt(p[1])}%."
    nraw = _numbers(raw)
    if not m and not (re.search(r"\d", nraw) and re.search(
            r"%|процент|\+|-|\*|/|×|÷|плюс|минус|умнож|раздел|делить|степен|корень|квадрат|куб|\sв\s|\sto\s|times|plus|minus",
            nraw)):
        return None
    body = _numbers(body)
    # проценты
    p = _percent(body.replace(" %", "%"))
    if p is not None:
        if isinstance(p, tuple):
            return f"{fmt(p[1])}%."
        return f"{fmt(p)}."
    # валюты и единицы: «200 долларов в сомах»
    m = re.fullmatch(r"([\d.]+)\s*(.+?)\s+(?:в|во|to|into|in)\s+(.+?)", body)
    if m:
        val = float(m.group(1))
        a, b = m.group(2).strip(), m.group(3).strip()
        ca, cb = _cur(a), _cur(b)
        if ca and cb:
            r = (rate or _rate)(ca, cb)
            if r:
                return f"{fmt(val)} {ca} ≈ {fmt(val * r)} {cb}."
            return ("Курс сейчас недоступен." if ru else "Exchange rate unavailable right now.")
        ua, ub = _unit(a), _unit(b)
        if ua and ub:
            v = convert(val, ua, ub)
            if v is not None:
                return f"{fmt(val)} {_UNIT_NAME[ua]} = {fmt(v)} {_UNIT_NAME[ub]}."
    # арифметика
    e = _expr(body)
    if re.fullmatch(r"[\d\s.+\-*/()%]*(?:sqrt\([\d.]+\)[\d\s.+\-*/()%]*)*", e) and re.search(r"\d", e) and \
            re.search(r"[+\-*/]|sqrt", e):
        try:
            v = safe_eval(e)
        except ZeroDivisionError:
            return "На ноль делить нельзя." if ru else "Can't divide by zero."
        except Exception:
            return None
        return f"{fmt(v)}."
    return None


def _rate(base: str, target: str):
    try:
        try:
            from brain import tools
            fn = tools.AVAILABLE_FUNCTIONS.get("get_exchange_rate")
        except Exception:
            fn = None
        if fn is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "atlas_rate", __import__("os").path.join(__import__("os").path.dirname(__file__), "..", "skills_auto",
                                                         "get_exchange_rate.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            fn = mod.get_exchange_rate
        out = str(fn(base, target))
        m = re.search(r"=\s*([\d.,\s]+)", out) or re.search(r"([\d]+[.,]\d+)", out)
        return float(m.group(1).replace(" ", "").replace(",", ".")) if m else None
    except Exception as e:
        print(f"[калькулятор] курс: {e}")
        return None
