"""
«Атлас, отчёт» — как у Джарвиса: одним голосом про всё важное сейчас, без модели (за 1–2 секунды).

Порядок: время → погода → ближайшее в календаре → напоминания → почта → компьютер (заряд, диск, что грузит
процессор) → загрузки и «скажи, когда…» → запись, суфлёр, игровой режим → что ждёт твоего ответа
(черновик в мессенджере, план уборки) → задачи и идеи. Пустое не называется. Каждый пункт в это время
загорается вокруг звезды (тот же «ход мыслей», что и у инструментов).

    «отчёт» / «доложи обстановку» / «статус» / «что у нас» / «report» / «status report»
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime

TIMEOUT = 3.5
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="report")


def _ru() -> bool:
    try:
        from voice import get_response_language
        return get_response_language() == "ru"
    except Exception:
        return True


def _plural(n: int, one: str, few: str, many: str) -> str:
    k = abs(n) % 100
    w = many if 11 <= k <= 19 else one if k % 10 == 1 else few if 2 <= k % 10 <= 4 else many
    return f"{n} {w}"


# --- источники (каждый может упасть или не успеть — отчёт всё равно будет) ---------------------------------
def s_weather():
    from core import briefing
    return briefing.weather()


def s_events():
    from core import briefing
    ev = briefing.events()
    return ev[:2] if ev else None


def s_reminders():
    from core import briefing
    r = briefing.reminders_today()
    return r[:2] if r else None


def s_mail():
    from brain import tools
    fn = tools.AVAILABLE_FUNCTIONS.get("get_unread_count")
    if not fn:
        return None
    out = str(fn())
    m = re.search(r"(\d+)", out)
    return int(m.group(1)) if m else None


def s_pc():
    import psutil
    out = {}
    b = psutil.sensors_battery()
    if b:
        out["battery"] = (int(b.percent), bool(b.power_plugged))
    try:
        out["disk"] = psutil.disk_usage("C:\\").free / 1024 ** 3
    except Exception:
        pass
    out["cpu"] = psutil.cpu_percent(interval=0.4)
    out["ram"] = psutil.virtual_memory().percent
    if out["cpu"] > 60:
        top = []
        for p in psutil.process_iter(["name"]):
            try:
                top.append((p.cpu_percent(None), p.info["name"]))
            except Exception:
                pass
        time.sleep(0.4)
        top = []
        for p in psutil.process_iter(["name"]):
            try:
                top.append((p.cpu_percent(None), p.info["name"]))
            except Exception:
                pass
        top = [t for t in top if t[1] and "idle" not in t[1].lower()]
        if top:
            out["hog"] = max(top)[1].replace(".exe", "")
    return out


def s_watch():
    from core import watchers
    items = watchers._load()
    return [watchers._label(w) for w in items][:3] or None


def s_modes():
    out = []
    try:
        from core import meeting_notes
        if meeting_notes.active():
            out.append(("rec", meeting_notes._s.get("title", ""), int((time.time() - meeting_notes._s["started"]) / 60)))
    except Exception:
        pass
    for mod, key in (("prompter", "prompter"), ("game_mode", "game"), ("focus", "focus")):
        try:
            m = __import__(f"core.{mod}", fromlist=["x"])
            if m.active():
                out.append((key,))
        except Exception:
            pass
    return out or None


def s_waiting():
    out = []
    try:
        from core import messengers
        p = messengers.pending()
        if p:
            out.append(("msg", p["name"]))
    except Exception:
        pass
    try:
        from core import disk_clean
        if disk_clean.pending():
            out.append(("clean",))
    except Exception:
        pass
    return out or None


def s_notes():
    from core import voice_notes
    items = voice_notes.load()["items"]
    tasks = sum(1 for i in items if i["kind"] == "task" and not i["done"])
    ideas = sum(1 for i in items if i["kind"] == "idea" and not i["done"] and i["ts"] > time.time() - 7 * 86400)
    buy = sum(1 for i in items if i["kind"] == "buy" and not i["done"])
    try:
        from core import briefing
        tasks = max(tasks, int(briefing.todos_open() or 0))
    except Exception:
        pass
    return {"tasks": tasks, "ideas": ideas, "buy": buy} if (tasks or ideas or buy) else None


SOURCES = [("weather", s_weather), ("events", s_events), ("reminders", s_reminders), ("mail", s_mail),
           ("pc", s_pc), ("watch", s_watch), ("modes", s_modes), ("waiting", s_waiting), ("notes", s_notes)]


def collect(sources=None, timeout: float = TIMEOUT) -> dict:
    futs = {_pool.submit(fn): name for name, fn in (sources or SOURCES)}
    done, _ = wait(futs, timeout=timeout)
    out = {}
    for f in done:
        try:
            v = f.result()
            if v not in (None, "", [], {}):
                out[futs[f]] = v
        except Exception as e:
            print(f"[отчёт] {futs[f]}: {e}")
    return out


def compose(d: dict, now: datetime = None, ru: bool = True) -> list:
    """→ [(ключ, фраза)] по порядку."""
    now = now or datetime.now()
    out = [("time", f"Сэр, отчёт на {now:%H:%M}." if ru else f"Sir, status report at {now:%H:%M}.")]
    if "weather" in d:
        out.append(("weather", f"На улице {d['weather']}." if ru else f"Outside: {d['weather']}."))
    if "events" in d:
        ev = "; ".join(f"{w} — {s}" for w, s in d["events"])
        out.append(("events", f"В календаре: {ev}." if ru else f"Calendar: {ev}."))
    if "reminders" in d:
        r = "; ".join(f"{x['when'].split(', ')[-1]} — {x['text']}" for x in d["reminders"])
        out.append(("reminders", f"Напоминания: {r}." if ru else f"Reminders: {r}."))
    if d.get("mail"):
        n = d["mail"]
        out.append(("mail", f"В почте {_plural(n, 'непрочитанное письмо', 'непрочитанных письма', 'непрочитанных писем')}."
                    if ru else f"{n} unread emails."))
    pc = d.get("pc") or {}
    bits = []
    if "battery" in pc:
        p, plugged = pc["battery"]
        if p < 30 and not plugged:
            bits.append(f"заряд {p} % — стоит подключить зарядку" if ru else f"battery {p}% — plug in soon")
        elif p < 100 or not plugged:
            bits.append(f"заряд {p} %" + (" на зарядке" if plugged else "") if ru else f"battery {p}%")
    if "disk" in pc and pc["disk"] < 20:
        bits.append(f"на диске C осталось всего {pc['disk']:.0f} ГБ" if ru else f"only {pc['disk']:.0f} GB left on C:")
    if pc.get("cpu", 0) > 60:
        bits.append((f"процессор загружен на {pc['cpu']:.0f} %" + (f", больше всех — {pc['hog']}" if pc.get("hog") else ""))
                    if ru else f"CPU at {pc['cpu']:.0f}%" + (f", mostly {pc['hog']}" if pc.get("hog") else ""))
    if pc.get("ram", 0) > 85:
        bits.append(f"память занята на {pc['ram']:.0f} %" if ru else f"RAM at {pc['ram']:.0f}%")
    if bits:
        out.append(("pc", ("Компьютер: " if ru else "Computer: ") + ", ".join(bits) + "."))
    elif pc:
        out.append(("pc", "Компьютер в порядке." if ru else "The computer is fine."))
    if "watch" in d:
        out.append(("watch", ("Слежу: " if ru else "Watching: ") + "; ".join(d["watch"]) + "."))
    for m in d.get("modes") or []:
        if m[0] == "rec":
            out.append(("rec", f"Идёт запись «{m[1]}», {m[2]} мин." if ru else f"Recording “{m[1]}”, {m[2]} min."))
        elif m[0] == "prompter":
            out.append(("prompter", "Суфлёр включён." if ru else "Call prompter is on."))
        elif m[0] == "game":
            out.append(("game", "Игровой режим включён." if ru else "Game mode is on."))
        elif m[0] == "focus":
            out.append(("focus", "Идёт режим фокуса." if ru else "Focus mode is on."))
    for w in d.get("waiting") or []:
        if w[0] == "msg":
            out.append(("waiting", f"Ждёт твоего «да»: сообщение для {w[1]}." if ru else f"Waiting for your OK: message to {w[1]}."))
        elif w[0] == "clean":
            out.append(("waiting", "Ждёт твоего «да»: уборка диска." if ru else "Waiting for your OK: disk clean-up."))
    n = d.get("notes")
    if n:
        bits = []
        if n["tasks"]:
            bits.append(_plural(n["tasks"], "открытая задача", "открытые задачи", "открытых задач") if ru else f"{n['tasks']} open tasks")
        if n["ideas"]:
            bits.append(_plural(n["ideas"], "новая идея", "новые идеи", "новых идей") if ru else f"{n['ideas']} new ideas")
        if n["buy"]:
            bits.append(f"{n['buy']} в списке покупок" if ru else f"{n['buy']} to buy")
        out.append(("notes", ("У тебя " if ru else "You have ") + ", ".join(bits) + "."))
    if len(out) <= 2:
        out.append(("ok", "Больше ничего важного." if ru else "Nothing else important."))
    return out


def _glow(key: str) -> None:
    """Пункт отчёта — огоньком вокруг звезды (тот же канал, что «ход мыслей»)."""
    try:
        from brain import tools
        tools._trace_push({"t": "tool", "name": f"report_{key}", "args": {}, "state": "ok"})
    except Exception:
        pass


def build(sources=None, ru: bool = None) -> str:
    ru = _ru() if ru is None else ru
    try:
        from brain import tools
        tools._trace_push({"t": "start", "q": "отчёт" if ru else "report"})
    except Exception:
        pass
    parts = compose(collect(sources), ru=ru)
    for key, _ in parts:
        _glow(key)
    try:
        from brain import tools
        tools._trace_push({"t": "end", "ok": True})
    except Exception:
        pass
    return " ".join(p for _, p in parts)


_ASK = re.compile(r"^(?:атлас[,\s]+)?(?:отч[её]т|доложи(?:\s+обстановку)?|статус|обстановка|что у нас(?:\s+по\s+(?:всему|делам))?|"
                  r"дай отч[её]т|сводк\w*\s+по\s+всему|report|status(?: report)?|sitrep|what'?s the situation)\s*[.!?]?$", re.I)


def handle(text: str):
    if not _ASK.match(" ".join((text or "").split()).strip()):
        return None
    return build(ru=bool(re.search(r"[а-яё]", text, re.I)) if re.search(r"[a-zа-яё]", text, re.I) else None)
