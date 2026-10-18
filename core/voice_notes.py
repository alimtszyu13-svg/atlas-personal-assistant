"""
Голосовые заметки с умной сортировкой: говоришь мысль — Atlas сам понимает, что это, и кладёт на место.

    «запиши мысль: надо переделать главную страницу»      → задача (в список дел)
    «идея: в Atlas сделать режим для презентаций»          → идея (список идей)
    «запиши: купить молоко и батарейки»                    → покупки (список покупок)
    «запиши: в пятницу в 18:00 позвонить бабушке»          → напоминание на это время
    «заметка: пароль от вайфая на роутере снизу»           → просто заметка
    «что у меня в идеях?», «список покупок», «что я записывал на неделе?», «купил молоко» — вычеркнуть
По воскресеньям в 19:00 Atlas коротко напоминает, сколько за неделю идей и открытых задач (один раз).

Без модели — по словам (быстро); модель — только если по словам неясно. Хранится в atlas_voice_notes.json.
"""
import json
import os
import re
import threading
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "atlas_voice_notes.json")
_lock = threading.Lock()
KINDS = ("task", "idea", "buy", "reminder", "note")
_RU = {"task": "задача", "idea": "идея", "buy": "покупки", "reminder": "напоминание", "note": "заметка"}


def _ru(text: str) -> bool:
    return bool(re.search(r"[а-яё]", text or "", re.I))


def load() -> dict:
    try:
        with open(FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": [], "digest": ""}


def _save(d: dict) -> None:
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, FILE)


_TIME = re.compile(r"(?:сегодня|завтра|послезавтра|в\s+(?:понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)|"
                   r"через\s+\d+\s*(?:минут\w*|час\w*|дн\w*)|в\s+\d{1,2}[:.]\d{2}|в\s+\d{1,2}\s+час\w*|\d{1,2}\s+"
                   r"(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)|"
                   r"tomorrow|today|at \d{1,2}(?::\d{2})?\s*(?:am|pm)?|in \d+ (?:minutes|hours|days))", re.I)
_BUY = re.compile(r"^(?:надо\s+|нужно\s+)?(?:купить|докупить|закажи|заказать|взять в магазине|купи)\b|список покупок|"
                  r"^buy\b|^(?:need to )?get\b.*(?:from|at) the (?:store|shop)", re.I)
_IDEA = re.compile(r"^(?:идея|мысль на будущее|а что если|можно было бы|было бы круто|придумал|idea|what if)\b|"
                   r"(?:сделать|добавить)\s+(?:фичу|функци\w*|режим)\b|идея для", re.I)
_TASK = re.compile(r"^(?:надо|нужно|не забыть|не забудь|сделать|доделать|переделать|исправить|отправить|написать|"
                   r"позвонить|сдать|подготовить|выучить|прочитать|todo|need to|have to|must|fix|finish|send|call)\b", re.I)


def classify(text: str, ask=None) -> str:
    t = (text or "").strip()
    if _BUY.search(t):
        return "buy"
    if _TIME.search(t) and re.search(r"позвон|сдать|встреч|напомн|созвон|прийти|забрать|оплат|call|meet|pay|remind", t, re.I):
        return "reminder"
    if _IDEA.search(t):
        return "idea"
    if _TASK.search(t):
        return "task"
    if ask is None:
        return "note"
    try:
        out = ask("Classify the user's note as exactly one word: task, idea, buy, reminder or note.", t, max_tokens=5)
        w = (out or "").strip().lower().split()[0].strip(".")
        return w if w in KINDS else "note"
    except Exception:
        return "note"


def _clean(text: str) -> str:
    t = re.sub(r"^(?:идея|мысль|заметка|idea|note)\s*[:,—–-]\s*", "", (text or "").strip(), flags=re.I)
    return t.strip(" .")[:500]


def _items_from_buy(text: str) -> list:
    t = re.sub(r"^(?:надо\s+|нужно\s+)?(?:купить|докупить|закажи|заказать|купи|buy)\s+", "", text.strip(" ."), flags=re.I)
    return [x.strip() for x in re.split(r"\s*(?:,|\bи\b|\band\b|;)\s*", t) if x.strip()]


def add(text: str, ask=None, now: float = None, reminder=None) -> str:
    raw = _clean(text)
    ru = _ru(raw)
    if not raw:
        return "Что записать?" if ru else "What should I write down?"
    kind = "idea" if re.match(r"^\s*(?:идея|idea)\b", text or "", re.I) else classify(raw, ask)
    now = now or time.time()
    if kind == "reminder":
        out = (reminder or _make_reminder)(raw)
        if out:
            with _lock:
                d = load()
                d["items"].append({"kind": "reminder", "text": raw, "ts": now, "done": False})
                _save(d)
            return out
        kind = "task"
    with _lock:
        d = load()
        if kind == "buy":
            for it in _items_from_buy(raw):
                d["items"].append({"kind": "buy", "text": it, "ts": now, "done": False})
        else:
            d["items"].append({"kind": kind, "text": raw, "ts": now, "done": False})
        _save(d)
    if kind == "task":
        try:
            import notes
            notes.add_todo(raw)
        except Exception:
            pass
    if ru:
        return {"task": "Записал в задачи.", "idea": "Записал в идеи.", "buy": "Добавил в покупки: " +
                ", ".join(_items_from_buy(raw)) + ".", "note": "Записал заметку."}[kind]
    return {"task": "Added to tasks.", "idea": "Saved to ideas.", "buy": "Added to the shopping list.",
            "note": "Note saved."}[kind]


def _make_reminder(text: str) -> str:
    """Напоминание через reminders.set_reminder: время — из фразы."""
    try:
        import reminders
        t = text.lower()
        now = datetime.now()
        m = re.search(r"через\s+(\d+)\s*(минут\w*|час\w*)", t)
        when = None
        if m:
            when = f"in {m.group(1)} {'hours' if m.group(2).startswith('час') else 'minutes'}"
        else:
            hm = re.search(r"в\s+(\d{1,2})[:.](\d{2})|в\s+(\d{1,2})\s+час", t)
            day = now
            from datetime import timedelta
            if "послезавтра" in t:
                day = now + timedelta(days=2)
            elif "завтра" in t or "tomorrow" in t:
                day = now + timedelta(days=1)
            else:
                days = ["понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскресен"]
                for i, d in enumerate(days):
                    if re.search(r"\bв\s+" + d, t):
                        delta = (i - now.weekday()) % 7 or 7
                        day = now + timedelta(days=delta)
            h, mi = (int(hm.group(1)), int(hm.group(2))) if hm and hm.group(1) else \
                ((int(hm.group(3)), 0) if hm else (9, 0))
            when = f"{day:%Y-%m-%d} {h:02d}:{mi:02d}"
        what = re.sub(_TIME, " ", text)
        what = " ".join(what.split()).strip(" ,.")
        out = reminders.set_reminder(what or text, when)
        if out.startswith("Reminder set"):
            return f"Поставил напоминание: {what}." if _ru(text) else f"Reminder set: {what}."
    except Exception as e:
        print(f"[заметки] напоминание: {e}")
    return ""


def listing(kind: str, ru: bool = True, since: float = 0) -> str:
    d = load()
    items = [i for i in d["items"] if (kind == "all" or i["kind"] == kind) and not i["done"] and i["ts"] >= since]
    if not items:
        return {"idea": "Идей пока нет.", "buy": "Список покупок пуст.", "task": "Открытых задач нет."}.get(
            kind, "Ничего не записано.") if ru else "Nothing yet."
    if kind == "buy":
        return ("Купить: " if ru else "To buy: ") + ", ".join(i["text"] for i in items) + "."
    if kind == "all":
        by = {}
        for i in items:
            by.setdefault(i["kind"], []).append(i["text"])
        parts = [f"{_RU[k] if ru else k} ({len(v)}): " + "; ".join(v[:4]) for k, v in by.items()]
        return ". ".join(parts) + "."
    head = {"idea": "Идеи", "task": "Задачи", "note": "Заметки", "reminder": "Напоминания"}.get(kind, kind)
    return f"{head if ru else kind.capitalize()}: " + "; ".join(f"{n}) {i['text']}" for n, i in enumerate(items[:10], 1)) + "."


def bought(text: str, ru: bool = True) -> str:
    t = re.sub(r"^(?:я\s+)?(?:купил\w*|взял\w*|bought|got)\s+", "", text.strip(" ."), flags=re.I)
    names = [x.strip().lower() for x in re.split(r"\s*(?:,|\bи\b|\band\b)\s*", t) if x.strip()]
    n = 0
    with _lock:
        d = load()
        for i in d["items"]:
            if i["kind"] == "buy" and not i["done"] and any(x[:4] in i["text"].lower() for x in names):
                i["done"] = True
                n += 1
        _save(d)
    left = [i["text"] for i in load()["items"] if i["kind"] == "buy" and not i["done"]]
    if not n:
        return "Такого в покупках не было." if ru else "That wasn't on the list."
    return (f"Вычеркнул. Осталось: {', '.join(left)}." if left else "Вычеркнул — список покупок пуст.") if ru else \
        ("Crossed off." + (f" Left: {', '.join(left)}." if left else " List is empty."))


def digest(now: float = None, ru: bool = True) -> str:
    now = now or time.time()
    week = [i for i in load()["items"] if i["ts"] >= now - 7 * 86400]
    if not week:
        return ""
    ideas = sum(1 for i in week if i["kind"] == "idea")
    open_tasks = sum(1 for i in load()["items"] if i["kind"] == "task" and not i["done"])
    buy = sum(1 for i in load()["items"] if i["kind"] == "buy" and not i["done"])
    parts = []
    if ideas:
        parts.append(f"{ideas} {'идея' if ideas == 1 else 'идеи' if ideas < 5 else 'идей'}")
    if open_tasks:
        parts.append(f"{open_tasks} {'открытая задача' if open_tasks == 1 else 'открытых задачи' if open_tasks < 5 else 'открытых задач'}")
    if buy:
        parts.append(f"{buy} в списке покупок")
    if not parts:
        return ""
    return ("Сэр, итоги недели по заметкам: " + ", ".join(parts) + ". Сказать подробнее — «что у меня в идеях»." if ru
            else "Weekly notes: " + ", ".join(parts) + ".")


_ADD = re.compile(r"^(?:атлас[,\s]+)?(?:запиши|запишите|сохрани|отметь|занеси|добавь в заметки|note|write down|jot down)"
                  r"(?:\s+(?:мысль|идею|заметку|себе|мне|в заметки|a note|that))?\s*[:,—–-]?\s*(.{3,})$|"
                  r"^(?:идея|мысль|заметка|idea|note)\s*[:,—–-]\s*(.{3,})$", re.I | re.S)
_LIST = re.compile(r"(?:что у меня в|покажи|прочитай|какие у меня)\s+(идеях|идеи|задачах|задачи|заметках|заметки|покупках)|"
                   r"(?:список покупок|что купить|что надо купить)|что я записывал(?:\s+на\s+(?:этой\s+)?неделе)?|"
                   r"(?:my )?(ideas|shopping list|notes)\b", re.I)
_BOUGHT = re.compile(r"^(?:я\s+)?(?:купил\w*|bought)\s+\w", re.I)


def intent(text: str):
    t = " ".join((text or "").split()).strip()
    if not t:
        return None
    m = _ADD.match(t)
    if m:
        body = (m.group(1) or m.group(2) or "").strip()
        if re.match(r"^(?:созвон|встречу|видео|звук|экран|лекци|это видео|в памят|себе в памят)", body, re.I):
            return None                                       # «запиши созвон» — запись; «в память» — память
        kind_hint = "idea" if re.search(r"^(?:атлас[,\s]+)?(?:запиши\s+идею|идея)", t, re.I) else None
        return ("add", ("идея: " + body) if kind_hint else body)
    if _BOUGHT.search(t):
        return ("bought", t)
    m = _LIST.search(t)
    if m:
        w = (m.group(1) or m.group(2) or t).lower()
        kind = "idea" if "иде" in w or "idea" in w else "task" if "задач" in w else "buy" if ("покуп" in w or "купить" in t.lower()
                                                                                             or "shopping" in w) else \
            "all" if "записывал" in t.lower() else "note"
        return ("list", kind, bool(re.search(r"недел", t, re.I)))
    return None


def handle(text: str, ask=None):
    it = intent(text)
    if not it:
        return None
    ru = _ru(text)
    if it[0] == "add":
        return add(it[1], ask)
    if it[0] == "bought":
        return bought(it[1], ru)
    return listing(it[1], ru, since=time.time() - 7 * 86400 if it[2] else 0)


def weekly_check(announce, now: datetime = None) -> str:
    """Воскресенье после 19:00 — итоги недели по заметкам (один раз за неделю). → сказанное или ''."""
    n = now or datetime.now()
    key = n.strftime("%Y-%W")
    if n.weekday() != 6 or n.hour < 19:
        return ""
    d = load()
    if d.get("digest") == key:
        return ""
    msg = digest()
    d["digest"] = key
    _save(d)
    if msg and announce:
        announce(msg)
    return msg or ""


def start(announce, every: float = 300):
    """Проверка раз в every секунд — через общий планировщик (без своего спящего потока)."""
    from core import scheduler
    job = scheduler.every(every, lambda: weekly_check(announce), "итоги недели по заметкам", essential=False)
    scheduler.start()
    return job
