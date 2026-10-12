"""
Секретарь звонков: тебе звонят в Telegram, WhatsApp, Discord, Zoom, Viber, Skype или Teams на компьютере —
Atlas говорит «Сэр, вам звонит Мама в Telegram» и сразу слушает ответ (без имени):

    «сейчас возьму» / «я сам»                  → Atlas замолкает, дальше ты сам
    «возьми трубку» / «ответь, на громкую»     → принимает звонок (на громкой — звук в колонки, громче)
    «скажи, что я занят, перезвоню позже»      → принимает звонок, говорит собеседнику: «Здравствуйте! Это Atlas,
                                                 ассистент Алима. Он сейчас занят и перезвонит вам позже», кладёт
                                                 трубку. В наушниках собеседник Atlas не услышит — тогда звонок
                                                 сбрасывается, а то же самое уходит сообщением в этот чат.
    «сбрось» / «отклони»                       → отклоняет

Как видит звонок: каждую секунду смотрит окна мессенджеров. Новое окно (или заголовок) со словами «звонит»,
«входящий», «incoming call»… — это звонок; имя берётся с экрана звонка. Кнопки находит по названию (Windows UI
Automation), а если названий нет — по цвету: зелёная — принять, красная — отклонить.
Звонок не отвечен за 3 напоминания — Atlas больше не повторяет.

    python -m core.call_secretary check   → что Atlas видит в окнах мессенджеров прямо сейчас
"""
import re
import threading
import time

APPS = {"telegram": "Telegram", "ayugram": "Telegram", "whatsapp": "WhatsApp", "whatsapp.root": "WhatsApp",
        "discord": "Discord", "zoom": "Zoom", "viber": "Viber", "skype": "Skype", "ms-teams": "Teams",
        "teams": "Teams"}
_CALL = re.compile(r"входящ\w*\s+(?:аудио|видео)?звон\w*|звонит|вызывает|аудиозвонок|видеозвонок|входящий вызов|"
                   r"incoming (?:voice |video )?call|is calling|calling you|voice call|video call|wants to call|"
                   r"(?:аудио|видео)звонок", re.I)
_ACCEPT = re.compile(r"принять|ответить|принять звонок|accept|answer|join|присоединиться", re.I)
_DECLINE = re.compile(r"отклонить|сбросить|отказаться|decline|reject|ignore|dismiss", re.I)
_HANGUP = re.compile(r"завершить|положить трубку|покинуть|end call|hang ?up|leave call|leave", re.I)
REPEAT_S = 9
MAX_ANNOUNCE = 3
_s = {"ring": None, "seen": set(), "thread": None, "announce": None, "on": True, "handled": set()}
_lock = threading.Lock()


def _ru() -> bool:
    try:
        from voice import get_response_language
        return get_response_language() == "ru"
    except Exception:
        return True


def owner_name() -> str:
    try:
        from core import prompter
        names = prompter.cfg().get("names") or []
        if names:
            return names[0]
    except Exception:
        pass
    import os
    return (os.getenv("ATLAS_OWNER_NAME") or "").strip()


# --- окружение (в тестах подменяется) -----------------------------------------------------------
class Env:
    def __init__(self):
        from core import messengers
        self.ui = messengers.UI()

    def windows(self) -> list:
        return self.ui.windows()

    def rect(self, hwnd):
        return self.ui.rect(hwnd)

    def ocr(self, rect) -> list:
        d = self.ui.ocr(rect)
        return (d.get("ru") or []) + [ln for ln in (d.get("en") or [])]

    def buttons(self, hwnd) -> list:
        """[(название, x, y)] кнопок окна по UI Automation."""
        out = []
        try:
            import uiautomation as auto
            ctl = auto.ControlFromHandle(hwnd)
            for c, _ in auto.WalkControl(ctl, maxDepth=20):
                if c.ControlTypeName in ("ButtonControl", "HyperlinkControl") and (c.Name or "").strip():
                    r = c.BoundingRectangle
                    out.append((c.Name.strip(), (r.left + r.right) // 2, (r.top + r.bottom) // 2))
        except Exception as e:
            print(f"[звонки] UIA: {e}")
        return out

    def color_buttons(self, rect) -> dict:
        """Зелёная и красная круглые кнопки по цвету → {"green": (x, y), "red": (x, y)}."""
        import numpy as np
        from PIL import ImageGrab
        img = np.asarray(ImageGrab.grab(bbox=tuple(rect), all_screens=True).convert("RGB")).astype(int)
        r, g, b = img[..., 0], img[..., 1], img[..., 2]
        masks = {"green": (g > 140) & (g - r > 55) & (g - b > 35), "red": (r > 175) & (r - g > 95) & (r - b > 75)}
        out = {}
        for name, m in masks.items():
            ys, xs = np.nonzero(m)
            if len(xs) > 250:
                cy, cx = int(np.median(ys)), int(np.median(xs))
                out[name] = (rect[0] + cx, rect[1] + cy)
        return out

    def click(self, x, y):
        self.ui.click(x, y)

    def activate(self, hwnd):
        return self.ui.activate(hwnd)

    def headphones(self) -> bool:
        try:
            import voice
            return voice.output_is_headphones()
        except Exception:
            return False

    def louder(self):
        try:
            import system_advanced
            system_advanced.set_volume(75)
        except Exception:
            pass

    def say(self, text: str):
        import voice
        voice.speak(text, interruptible=False)

    def listen_now(self):
        try:
            import voice
            voice.trigger_push_to_talk()
        except Exception:
            pass

    def sleep(self, s):
        time.sleep(s)

    def now(self):
        return time.time()


_env = None


def _get_env():
    global _env
    if _env is None:
        _env = Env()
    return _env


# --- звонок на экране ----------------------------------------------------------------------------------
def caller_from(lines: list) -> str:
    """Имя звонящего — самая крупная строка экрана звонка, не служебная."""
    cand = []
    for ln in lines:
        t = (ln.get("text") or "").strip(" .:•")
        if not t or _CALL.search(t) or _ACCEPT.fullmatch(t) or _DECLINE.fullmatch(t) or len(t) > 40:
            continue
        if re.fullmatch(r"[\d:\s]+|telegram|whatsapp|discord|zoom|viber|skype|teams|\W+", t, re.I):
            continue
        cand.append((ln.get("h", 0), -ln.get("y", 0), t))
    if not cand:
        return ""
    return max(cand)[2]


def detect(wins: list, env) -> dict:
    """Окно входящего звонка → {"hwnd", "app", "caller"} или None."""
    for w in wins:
        app = APPS.get(w["app"].lower())
        if not app or w.get("min"):
            continue
        title = w.get("title") or ""
        fresh = w["hwnd"] not in _s["seen"]
        if not (_CALL.search(title) or fresh):
            continue
        try:
            rect = env.rect(w["hwnd"])
            lines = env.ocr(rect)
        except Exception as e:
            print(f"[звонки] OCR: {e}")
            continue
        text = " ".join(ln.get("text", "") for ln in lines) + " " + title
        if _CALL.search(text):
            return {"hwnd": w["hwnd"], "app": app, "caller": caller_from(lines) or "", "rect": rect}
    return None


def _button(hwnd: int, rect, kind: str, env):
    rx = {"accept": _ACCEPT, "decline": _DECLINE, "hangup": _HANGUP}[kind]
    for name, x, y in env.buttons(hwnd):
        if rx.search(name):
            return x, y
    col = env.color_buttons(rect)
    return col.get("green" if kind == "accept" else "red")


def press(kind: str, ring: dict = None, env=None) -> bool:
    env = env or _get_env()
    ring = ring or _s["ring"]
    if not ring:
        return False
    env.activate(ring["hwnd"])
    env.sleep(0.2)
    try:
        rect = env.rect(ring["hwnd"])
    except Exception:
        rect = ring.get("rect")
    pt = _button(ring["hwnd"], rect, kind, env)
    if not pt:
        return False
    env.click(*pt)
    return True


# --- фразы ------------------------------------------------------------------------------------------------
_SELF = re.compile(r"(?:сейчас|щас|сам)\s+(?:возьму|отвечу|подойду)|я сам|возьму сам|сам отвечу|понял|понятно|"
                   r"i'?ll (?:take|get|answer) it|got it", re.I)
_TAKE = re.compile(r"(?:возьми|подними|прими|ответь|бери)\w*(?:\s+(?:трубку|звонок|на звонок))?|answer(?: it| the call)?|"
                   r"pick (?:it )?up|take the call", re.I)
_BUSY = re.compile(r"(?:скажи|передай|ответь|tell)\w*.{0,20}(?:занят|не могу|перезвон|позже|busy|call back|later)|"
                   r"(?:что|that)\s+(?:я\s+)?(?:занят|перезвоню|busy)", re.I)
_DROP = re.compile(r"(?:сбрось|сбрасывай|отклони|отбей|не бери|не отвечай|decline|reject|hang up|ignore)", re.I)
_LOUD = re.compile(r"громк|колонк|speaker", re.I)


def genitive(name: str) -> str:
    """«Алим» → «Алима», «Дана» → «Даны», «Айгерим» → «Айгерим» (женские на согласную не склоняются — не угадать)."""
    n = (name or "").strip()
    if not n or not re.search(r"[а-яё]$", n, re.I):
        return n
    low = n.lower()
    if low.endswith(("а",)):
        return n[:-1] + ("и" if low[-2:-1] in "гкхжшчщ" else "ы")
    if low.endswith("я"):
        return n[:-1] + "и"
    if low.endswith("й"):
        return n[:-1] + "я"
    if low.endswith("ь"):
        return n[:-1] + "я"
    if re.search(r"[бвгджзклмнпрстфхцчшщ]$", low):
        return n + "а"
    return n


def busy_text(said: str, ru: bool) -> str:
    who = owner_name()
    m = re.search(r"через\s+(\d+\s*(?:минут\w*|час\w*)|полчаса|час|пару минут|\w+\s+минут\w*)", said or "", re.I)
    when = ("через " + m.group(1)) if m else "позже"
    if ru:
        female = bool(who) and re.search(r"[ая]$", who.lower()) and who.lower() not in ("никита", "илья", "кузьма")
        who_s = f"ассистент {genitive(who)}" if who else "ассистент"
        he = "Она" if female else "Он"
        return f"Здравствуйте! Это Atlas, {who_s}. {he} сейчас занят{'а' if female else ''} и перезвонит вам {when}."
    m2 = re.search(r"in\s+(\d+)\s*(minutes?|hours?)", said or "", re.I)
    when_en = f"in {m2.group(1)} {m2.group(2)}" if m2 else "later"
    who_en = f"{who}'s assistant" if who else "an assistant"
    return f"Hello! This is Atlas, {who_en}. They're busy right now and will call you back {when_en}."


def intent(text: str):
    if not _s["ring"]:
        return None
    t = " ".join((text or "").split()).strip(" .!")
    if _BUSY.search(t):
        return ("busy", t)
    if _DROP.search(t):
        return ("drop",)
    if _SELF.search(t):
        return ("self",)
    if _TAKE.search(t) and (len(t.split()) <= 6 or re.search(r"трубк|звон|call", t, re.I)):
        return ("take", bool(_LOUD.search(t)))
    return None


def handle(text: str, env=None):
    it = intent(text)
    if not it:
        return None
    env = env or _get_env()
    ring = _s["ring"]
    ru = bool(re.search(r"[а-яё]", text, re.I)) if re.search(r"[a-zа-яё]", text or "", re.I) else _ru()
    k = it[0]
    _s["handled"].add(ring["hwnd"])
    if k == "self":
        _s["ring"] = None
        return "Хорошо." if ru else "OK."
    if k == "drop":
        ok = press("decline", ring, env)
        _s["ring"] = None
        return ("Сбросил." if ok else "Не нашёл кнопку — сбрось сам.") if ru else \
            ("Declined." if ok else "Couldn't find the button.")
    if k == "take":
        ok = press("accept", ring, env)
        _s["ring"] = None
        if not ok:
            return "Не нашёл кнопку «Принять» — возьми сам." if ru else "Couldn't find the Accept button."
        if it[1]:
            env.louder()
            if env.headphones():
                return ("Принял. Звук сейчас в наушниках — громкая будет, если переключить вывод на колонки." if ru else
                        "Answered. Sound is in your headphones right now.")
            return "Принял, на громкой." if ru else "Answered, on speaker."
        return "Принял." if ru else "Answered."
    # «скажи, что занят»
    msg = busy_text(it[1], ru)
    if not env.headphones() and press("accept", ring, env):
        env.sleep(1.6)
        env.say(msg)
        env.sleep(0.6)
        press("hangup", ring, env)
        _s["ring"] = None
        return "Сказал, что ты занят, и положил трубку." if ru else "Told them you're busy and hung up."
    press("decline", ring, env)
    _s["ring"] = None
    sent = _text_back(ring, it[1], ru, env)
    return (f"Сбросил и написал {ring['caller'] or 'ему'}: «{sent}»." if sent else
            "Сбросил. Написать не получилось — напиши сам.") if ru else \
        (f"Declined and texted: “{sent}”." if sent else "Declined; couldn't text them.")


def _text_back(ring: dict, said: str, ru: bool, env) -> str:
    """Наушники — собеседник голос Atlas не услышит: сообщение в чат того, кто звонил."""
    app = {"Telegram": "telegram", "WhatsApp": "whatsapp", "Discord": "discord"}.get(ring["app"])
    if not app or not ring.get("caller"):
        return ""
    m = re.search(r"через\s+([\w\s]{1,20}?)(?:\s*$|[,.])", said or "", re.I)
    text = (f"Сейчас не могу говорить, перезвоню {'через ' + m.group(1).strip() if m else 'позже'}." if ru else
            "Can't talk right now, I'll call you back later.")
    try:
        from core import messengers
        env.sleep(1.0)
        r = messengers.open_chat(app, ring["caller"])
        if not r.get("ok") or not r.get("verified"):
            return ""
        messengers._s["pending"] = {"app": app, "hwnd": r["hwnd"], "contact": ring["caller"], "name": r["name"],
                                    "text": text, "verified": True, "created": time.time(), "gist": "", "conv": [],
                                    "ru": ru, "web": r.get("web", False)}
        out = messengers.send()
        return text if out in ("Отправил.", "Sent.") or "Enter" in out else ""
    except Exception as e:
        print(f"[звонки] сообщение: {e}")
        return ""


# --- слежка ---------------------------------------------------------------------------------------------
def tick(env=None, announce=None) -> str:
    """Один проход: новый звонок → объявить. Возвращает объявление или ''."""
    env = env or _get_env()
    wins = env.windows()
    alive = {w["hwnd"] for w in wins}
    ring = _s["ring"]
    if ring and ring["hwnd"] not in alive:                       # звонок закончился / приняли / сбросили
        print(f"[звонки] звонок {ring['caller']} закончился")
        _s["ring"] = None
        ring = None
    said = ""
    if ring:
        if ring["n"] < MAX_ANNOUNCE and env.now() - ring["last"] >= REPEAT_S:
            ring["n"] += 1
            ring["last"] = env.now()
            said = _line(ring, again=True)
    else:
        found = detect([w for w in wins if w["hwnd"] not in _s["handled"]], env)
        if found:
            found.update(n=1, last=env.now())
            _s["ring"] = found
            said = _line(found)
    _s["seen"] = alive
    _s["handled"] &= alive
    if said:
        print(f"[звонки] {said}")
        f = announce or _s.get("announce")
        if f:
            f(said)
        env.listen_now()                                         # ответ — без имени
    return said


def _line(ring: dict, again: bool = False) -> str:
    who = ring.get("caller") or ""
    if _ru():
        if again:
            return f"Сэр, всё ещё звонит {who}." if who else "Сэр, звонок всё ещё идёт."
        return f"Сэр, вам звонит {who} в {ring['app']}." if who else f"Сэр, вам звонят в {ring['app']}."
    if again:
        return f"Sir, {who} is still calling." if who else "Sir, the call is still ringing."
    return f"Sir, {who} is calling you on {ring['app']}." if who else f"Sir, you have a call on {ring['app']}."


def start(announce=None, every: float = 1.0) -> threading.Thread:
    _s["announce"] = announce
    if _s["thread"] and _s["thread"].is_alive():
        return _s["thread"]

    def loop():
        try:
            env = _get_env()
            _s["seen"] = {w["hwnd"] for w in env.windows()}        # окна, открытые до запуска, — не звонки
        except Exception as e:
            print(f"[звонки] не запустилось: {e}")
            return
        while True:
            time.sleep(every)
            if not _s["on"]:
                continue
            try:
                tick(env)
            except Exception as e:
                print(f"[звонки] {e}")
                time.sleep(5)
    _s["thread"] = threading.Thread(target=loop, daemon=True, name="call-secretary")
    _s["thread"].start()
    return _s["thread"]


if __name__ == "__main__":                       # python -m core.call_secretary check
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    env = _get_env()
    wins = [w for w in env.windows() if APPS.get(w["app"].lower())]
    if not wins:
        print("Окон мессенджеров не вижу. Открой Telegram / WhatsApp и запусти ещё раз (лучше — во время звонка).")
    for w in wins:
        rect = env.rect(w["hwnd"])
        lines = env.ocr(rect)
        print(f"\n{w['app']} «{w['title']}» {rect}")
        print("  текст:", [ln["text"] for ln in lines][:15])
        print("  звонок:", bool(_CALL.search(" ".join(ln["text"] for ln in lines) + " " + w["title"])),
              "| звонящий:", caller_from(lines) or "—")
        print("  кнопки:", [b[0] for b in env.buttons(w["hwnd"])][:15])
        print("  по цвету:", env.color_buttons(rect))
