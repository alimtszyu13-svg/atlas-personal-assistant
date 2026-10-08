"""
Утренняя сводка: погода, календарь, напоминания, карточки к повторению, дела — одним уведомлением.

    build()            → текст сводки (тот же ответ на «что у меня сегодня» / «утренняя сводка»)
    settings() / set_settings(on, time)
    start()            → фоновый поток в облаке: каждое утро в своё время присылает сводку на телефон

Настройки и отметка «сегодня уже отправлено» лежат в atlas_push.json (общая память),
поэтому после перезапуска облака сводка не придёт второй раз.
"""
import json
import threading
import time
import urllib.request
from datetime import datetime, timedelta

LAT, LON = 42.87, 74.59            # Бишкек
DEFAULT_TIME = "07:30"
_WMO = {0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно", 45: "туман", 48: "туман",
        51: "морось", 53: "морось", 55: "морось", 61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
        66: "ледяной дождь", 67: "ледяной дождь", 71: "небольшой снег", 73: "снег", 75: "сильный снег",
        77: "снежная крупа", 80: "ливни", 81: "ливни", 82: "сильные ливни", 85: "снегопад", 86: "сильный снегопад",
        95: "гроза", 96: "гроза с градом", 99: "гроза с градом"}


def _tz():
    import reminders
    return reminders._tz()


def _now() -> datetime:
    return datetime.now(_tz())


def _plural(n: int, one: str, few: str, many: str) -> str:
    a, b = n % 10, n % 100
    return f"{n} " + (one if a == 1 and b != 11 else few if 2 <= a <= 4 and not 12 <= b <= 14 else many)


# ---------------------------------------------------------------------------
# Части сводки (каждая сама по себе: не получилось — просто без неё)
# ---------------------------------------------------------------------------
def weather() -> str:
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&timezone=auto&forecast_days=1"
           "&current=temperature_2m,weather_code&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max")
    with urllib.request.urlopen(url, timeout=8) as r:
        d = json.loads(r.read())
    cur, day = d["current"], d["daily"]
    lo, hi = round(day["temperature_2m_min"][0]), round(day["temperature_2m_max"][0])
    text = f"{round(cur['temperature_2m'])}°, {_WMO.get(cur.get('weather_code'), 'без осадков')}, днём до {hi}°, ночью {lo}°"
    rain = (day.get("precipitation_probability_max") or [None])[0]
    if rain is not None and rain >= 50:
        text += f", дождь {rain}% — возьми зонт"
    return text


def events() -> list:
    """[(«09:00», «SAT»)] — события сегодня по твоему поясу."""
    import calendar_control
    if getattr(calendar_control, "__atlas_stub__", False):
        return []
    day = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    items = calendar_control._get_service().events().list(
        calendarId="primary", timeMin=day.isoformat(), timeMax=(day + timedelta(days=1)).isoformat(),
        singleEvents=True, orderBy="startTime").execute().get("items", [])
    out = []
    for e in items:
        start = e.get("start", {})
        when = "весь день"
        if start.get("dateTime"):
            when = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00")).astimezone(_tz()).strftime("%H:%M")
        out.append((when, e.get("summary") or "без названия"))
    return out


def reminders_today() -> list:
    import reminders
    end = _now().replace(hour=23, minute=59, second=59).timestamp()
    return [r for r in reminders.upcoming() if r["due"] <= end]


def cards_due() -> int:
    from core import study
    return sum(int(d.get("due") or 0) for d in study.decks())


def todos_open() -> int:
    import notes
    return sum(1 for t in notes._load().get("todos", []) if not t.get("done"))


def build() -> str:
    h = _now().hour
    hello = "Доброе утро" if 5 <= h < 12 else "Добрый день" if h < 18 else "Добрый вечер" if h < 23 else "Доброй ночи"
    parts = [f"{hello}!"]
    for name, fn in (("weather", weather), ("events", events), ("reminders", reminders_today),
                     ("cards", cards_due), ("todos", todos_open)):
        try:
            v = fn()
        except Exception as e:
            print(f"[сводка] {name}: {e}")
            continue
        if name == "weather":
            parts.append(f"Погода: {v}.")
        elif name == "events":
            parts.append("Сегодня в календаре: " + "; ".join(f"{w} — {s}" for w, s in v[:4]) + "." if v
                         else "В календаре на сегодня пусто.")
        elif name == "reminders" and v:
            parts.append("Напоминания: " + "; ".join(f"{r['when'].split(', ')[-1]} — {r['text']}" for r in v[:3]) + ".")
        elif name == "cards" and v:
            parts.append(f"К повторению {_plural(v, 'карточка', 'карточки', 'карточек')}.")
        elif name == "todos" and v:
            parts.append(f"Открытых дел: {v}.")
    return " ".join(parts)


def daily_brief() -> str:
    """Инструмент мозга: «утренняя сводка», «что у меня сегодня»."""
    return build()


SCHEMA = {"type": "function", "function": {
    "name": "daily_brief",
    "description": "The user's day at a glance: weather, today's calendar, reminders, flashcards due, open todos. "
                   "Use for 'what's my day', 'morning brief', 'что у меня сегодня', 'утренняя сводка'.",
    "parameters": {"type": "object", "properties": {}}}}


# ---------------------------------------------------------------------------
# Настройки и расписание (в atlas_push.json — синхронизируется)
# ---------------------------------------------------------------------------
def settings() -> dict:
    from core import push
    s = push._load().get("brief") or {}
    return {"on": bool(s.get("on", True)), "time": s.get("time") or DEFAULT_TIME, "sent": s.get("sent", "")}


def _save(**kw) -> dict:
    from core import push
    with push._lock:
        doc = push._load()
        s = dict(doc.get("brief") or {})
        s.update(kw, updated=time.time())
        doc["brief"] = s
        push._save(doc)
    return settings()


def set_settings(on=None, at=None) -> dict:
    kw = {}
    if on is not None:
        kw["on"] = bool(on)
    if at:
        try:
            hh, mm = str(at).strip().split(":")[:2]
            kw["time"] = f"{int(hh) % 24:02d}:{int(mm) % 60:02d}"
        except ValueError:
            pass
    return _save(**kw) if kw else settings()


def due(now: datetime = None) -> bool:
    """Пора слать: включено, время настало (в пределах 3 часов), сегодня ещё не отправляли."""
    s = settings()
    now = now or _now()
    if not s["on"] or s["sent"] == now.date().isoformat():
        return False
    hh, mm = map(int, s["time"].split(":"))
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return at <= now < at + timedelta(hours=3)


def send_now(mark: bool = True) -> dict:
    from core import push
    text = build()
    n = push.notify("Atlas · утро", text, tag="atlas-brief")
    if mark:
        _save(sent=_now().date().isoformat())
    print(f"[сводка] отправлена ({n} устр.): {text}")
    return {"text": text, "sent": n}


def start() -> threading.Thread:
    def loop():
        while True:
            try:
                if due():
                    send_now()
            except Exception as e:
                print(f"[сводка] {e}")
            time.sleep(30)
    t = threading.Thread(target=loop, daemon=True, name="briefing")
    t.start()
    return t
