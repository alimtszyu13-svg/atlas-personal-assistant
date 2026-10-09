"""
Напоминания по ситуации, а не по часам:
    «напомни, когда открою Word, проверить эссе»
    «когда зайду в телеграм — напомни написать Маше»
    «когда сяду за компьютер, напомни про SAT»  (сработает, когда вернёшься после перерыва)

Срабатывают один раз. Только на этом компьютере (atlas_context_reminders.json, в git не попадает).
"""
import json
import os
import re
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "atlas_context_reminders.json")
AWAY_S = 10 * 60                     # «сяду за компьютер» — после стольких минут без мыши и клавиатуры
_lock = threading.Lock()

ALIASES = {
    "ворд": ["word", "winword"], "word": ["word", "winword"], "эксель": ["excel"], "excel": ["excel"],
    "повер": ["powerpoint"], "презентац": ["powerpoint"], "телеграм": ["telegram"], "телег": ["telegram"],
    "вотсап": ["whatsapp"], "ватсап": ["whatsapp"], "whatsapp": ["whatsapp"], "дискорд": ["discord"],
    "ютуб": ["youtube"], "youtube": ["youtube"], "браузер": ["chrome", "edge", "firefox", "opera", "brave", "yandex"],
    "хром": ["chrome"], "почт": ["gmail", "outlook", "почта", "mail"], "gmail": ["gmail"], "спотиф": ["spotify"],
    "стим": ["steam"], "vs code": ["visual studio code", "code"], "вс код": ["visual studio code"],
    "код": ["visual studio code", "pycharm"], "pycharm": ["pycharm"], "фотошоп": ["photoshop"],
    "зум": ["zoom"], "zoom": ["zoom"], "блокнот": ["notepad", "блокнот"], "notion": ["notion"], "ноушн": ["notion"],
    "калькулятор": ["calculator", "калькулятор"], "ворд онлайн": ["word"],
}
_RETURN = re.compile(r"сяду за (?:комп|компьютер|ноут|пк)|вернусь (?:к|за) (?:комп|компьютер|пк)|приду|"
                     r"back at (?:the |my )?(?:computer|pc|desk)|sit down at")


def _load() -> list:
    try:
        with open(FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(items: list) -> None:
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, FILE)


def keywords(when: str) -> list:
    t = (when or "").lower().replace("ё", "е")
    if _RETURN.search(t):
        return ["@return"]
    out = []
    for k, v in ALIASES.items():
        if k in t:
            out += v
    if not out:                                         # незнакомая программа: её имя латиницей/кириллицей как есть
        out = [w for w in re.findall(r"[a-zа-я0-9.+#-]{3,}", t)
               if w not in ("когда", "открою", "зайду", "запущу", "включу", "open", "when", "открыть", "into")]
    return list(dict.fromkeys(out))


def add(text: str, when: str) -> str:
    kw = keywords(when)
    if not kw:
        return "Didn't understand when to remind — name the program or say 'when I sit down at the computer'."
    with _lock:
        items = _load()
        items.append({"id": uuid.uuid4().hex[:8], "text": text.strip(), "when": when.strip(), "kw": kw,
                      "created": time.time()})
        _save(items)
    how = "when you come back to the computer after a break" if kw == ["@return"] else f"when {when.strip()} opens"
    return f"Context reminder set: '{text.strip()}' — {how}. Confirm briefly."


def listing() -> str:
    items = _load()
    if not items:
        return "No context reminders."
    return "Context reminders: " + "; ".join(f"«{i['text']}» — {i['when']}" for i in items)


def cancel(query: str = "") -> str:
    with _lock:
        items = _load()
        q = (query or "").lower()
        keep = [i for i in items if q and q not in (i["text"] + " " + i["when"]).lower()]
        _save(keep)
    n = len(items) - len(keep)
    return f"Cancelled {n} context reminder(s)." if n else "No such context reminder."


def due(app: str, title: str, idle_before: float) -> list:
    """Какие напоминания срабатывают прямо сейчас (и убрать их)."""
    hay = f"{app} {title}".lower()
    fire = []
    with _lock:
        items = _load()
        keep = []
        for it in items:
            hit = (idle_before >= AWAY_S) if it["kw"] == ["@return"] else any(k in hay for k in it["kw"])
            (fire if hit else keep).append(it)
        if fire:
            _save(keep)
    return fire


def start(announce, every: float = 3) -> threading.Thread:
    def loop():
        from core import worklog
        last_idle, last_key = 0.0, ""
        while True:
            time.sleep(every)
            try:
                if not os.path.exists(FILE):
                    continue
                idle = worklog._idle()
                app, title, _ = worklog._foreground()
                key = f"{app}|{title}"
                came_back = last_idle if idle < 5 else 0.0
                if key != last_key or came_back:
                    for it in due(app, title, came_back):
                        print(f"[напоминание] {it['text']} ({it['when']})")
                        announce(f"Напоминание: {it['text']}.")
                last_idle, last_key = idle, key
            except Exception as e:
                print(f"[напоминания по ситуации] {e}")
                time.sleep(30)
    t = threading.Thread(target=loop, daemon=True, name="context-reminders")
    t.start()
    return t
