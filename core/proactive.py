"""
Проактивность: Atlas сам замечает важное и говорит об этом — но вовремя.

Сенсоры (раз в CHECK_EVERY с): буфер обмена, диск, память, батарея, время
за компьютером. Правила: что считать поводом и как часто можно о нём
напоминать. Бюджет внимания: не больше MAX_SPOKEN_PER_HOUR реплик в час,
молчим в полноэкранном режиме и пока Atlas сам занят. Некритичное и
«не вовремя» — тихо в чат, без голоса. Всё локально: буфер обмена никуда
не отправляется.
"""
import ctypes
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import psutil
from ui_state import shared_state

CHECK_EVERY = 5              # секунд между проверками
MAX_SPOKEN_PER_HOUR = 4      # бюджет внимания
BREAK_AFTER_MIN = 90         # напомнить о перерыве после стольких минут без паузы
IDLE_RESET_S = 300           # пауза 5 мин без мыши/клавиатуры = перерыв был
DISK_LOW_GB = 5
RAM_HIGH_PCT = 90

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
kernel32.GetTickCount.restype = ctypes.c_uint
user32.GetClipboardSequenceNumber.restype = ctypes.c_uint


# ---------------------------------------------------------------------------
# Сенсоры
# ---------------------------------------------------------------------------
class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


class _RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                ("r", ctypes.c_long), ("b", ctypes.c_long)]


def idle_seconds() -> float:
    """Сколько секунд не трогали мышь и клавиатуру."""
    lii = _LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(lii)
    user32.GetLastInputInfo(ctypes.byref(lii))
    return ((kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000.0


def is_fullscreen() -> bool:
    """Окно на весь экран (игра, фильм, презентация)? Рабочий стол не считается."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Progman", "WorkerW"):
        return False
    rc = _RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rc))
    w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    return rc.l <= 0 and rc.t <= 0 and rc.r >= w and rc.b >= h


def _clip_text() -> str:
    try:
        import pyperclip
        return pyperclip.paste() or ""
    except Exception:
        return ""


@dataclass
class Snapshot:
    now: float
    clip: str = ""                # текст буфера — только если он изменился
    disk_free_gb: float = 999.0
    ram_pct: float = 0.0
    top_proc: str = ""
    battery: object = None
    active_min: float = 0.0


# ---------------------------------------------------------------------------
# Правила: снимок → текст реплики или None
# ---------------------------------------------------------------------------
def _ru() -> bool:
    from voice import get_response_language
    return get_response_language() == "ru"


_TRACEBACK = re.compile(r"Traceback \(most recent call last\)|^\s*File \".+\", line \d+", re.M)


def rule_traceback(s: Snapshot):
    if s.clip and _TRACEBACK.search(s.clip):
        last = [l for l in s.clip.strip().splitlines() if l.strip()][-1].strip()[:80]
        return (f"Вижу ошибку в буфере обмена: {last}. Скажите «Атлас, разбери ошибку» — посмотрю."
                if _ru() else
                f"I see an error in your clipboard: {last}. Say 'Atlas, explain the error' and I'll look.")


def rule_disk(s: Snapshot):
    if s.disk_free_gb < DISK_LOW_GB:
        return (f"На диске C осталось всего {s.disk_free_gb:.1f} гигабайта, сэр."
                if _ru() else f"Only {s.disk_free_gb:.1f} gigabytes left on drive C, sir.")


_ram_hits = {"n": 0}


def rule_ram(s: Snapshot):
    _ram_hits["n"] = _ram_hits["n"] + 1 if s.ram_pct > RAM_HIGH_PCT else 0
    if _ram_hits["n"] >= 2:                       # две проверки подряд — не случайный всплеск
        return (f"Память занята на {s.ram_pct:.0f}%, больше всех ест {s.top_proc}."
                if _ru() else f"Memory is at {s.ram_pct:.0f}%, mostly {s.top_proc}.")


def rule_battery(s: Snapshot):
    b = s.battery
    if b is not None and not b.power_plugged and b.percent <= 15:
        return (f"Заряд {b.percent:.0f}%, сэр, пора к розетке."
                if _ru() else f"Battery at {b.percent:.0f}%, sir. Time for a charger.")


def rule_break(s: Snapshot):
    if s.active_min >= BREAK_AFTER_MIN:
        return (f"Вы за компьютером уже {int(s.active_min)} минут без перерыва. Пять минут отдыха?"
                if _ru() else
                f"You've been at it for {int(s.active_min)} minutes straight. Five-minute break?")


@dataclass
class Rule:
    name: str
    check: Callable
    cooldown: int                # секунд до следующего напоминания об этом же
    voice: bool = True           # False — только в чат
    last: float = 0.0


RULES = [
    Rule("ошибка в буфере", rule_traceback, 120),
    Rule("мало места", rule_disk, 6 * 3600),
    Rule("память", rule_ram, 1800),
    Rule("батарея", rule_battery, 1800),
    Rule("перерыв", rule_break, BREAK_AFTER_MIN * 60),
]


# ---------------------------------------------------------------------------
# Бюджет внимания и доставка
# ---------------------------------------------------------------------------
_spoken = deque()                # время последних голосовых реплик


def _quiet_reason():
    now = time.time()
    while _spoken and now - _spoken[0] > 3600:
        _spoken.popleft()
    if is_fullscreen():
        return "полноэкранный режим"
    if shared_state.get("state") not in (None, "idle"):
        return "Atlas занят"
    if len(_spoken) >= MAX_SPOKEN_PER_HOUR:
        return "бюджет часа исчерпан"
    return None


def _deliver(rule: Rule, text: str, speak) -> None:
    try:                                   # панель уведомлений в интерфейсе
        from ui_state import notify
        _k = {"rule_traceback": "clipboard"}.get(rule.check.__name__,
                                                   rule.check.__name__.replace("rule_", ""))
        notify("info" if _k == "break" else "warn", _k, text)
    except Exception as e:
        print(f"[проактивность] уведомление интерфейсу: {e}")
    why = None if rule.voice else "только чат"
    why = why or _quiet_reason()
    print(f"[проактивность] {rule.name}: {text}" + (f"  (тихо: {why})" if why else ""))
    if why:
        shared_state["chat_history"].append(("Atlas", text))
    else:
        _spoken.append(time.time())
        speak(text)


def start(speak) -> None:
    """speak(text) — как Atlas говорит (из main.py)."""
    def _run():
        last_seq = user32.GetClipboardSequenceNumber()
        session_start = time.time()
        while True:
            time.sleep(CHECK_EVERY)
            try:
                s = Snapshot(now=time.time())
                seq = user32.GetClipboardSequenceNumber()
                if seq != last_seq:                       # буфер читаем только при изменении
                    last_seq = seq
                    s.clip = _clip_text()[:4000]
                s.disk_free_gb = psutil.disk_usage("C:\\").free / 1e9
                s.ram_pct = psutil.virtual_memory().percent
                if s.ram_pct > RAM_HIGH_PCT:
                    procs = [p for p in psutil.process_iter(["name", "memory_info"])
                             if p.info["memory_info"]]
                    top = max(procs, key=lambda p: p.info["memory_info"].rss, default=None)
                    s.top_proc = top.info["name"] if top else "?"
                s.battery = psutil.sensors_battery()
                if idle_seconds() > IDLE_RESET_S:
                    session_start = time.time()           # был перерыв — отсчёт заново
                s.active_min = (time.time() - session_start) / 60
                for rule in RULES:
                    if s.now - rule.last < rule.cooldown:
                        continue
                    text = rule.check(s)
                    if text:
                        rule.last = s.now
                        _deliver(rule, text, speak)
            except Exception as e:
                print(f"[проактивность] ошибка сенсоров: {e}")
    threading.Thread(target=_run, daemon=True).start()
    print("[проактивность] включена")