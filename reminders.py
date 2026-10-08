"""
Таймеры и напоминания — общие для компьютера и облака.

Хранятся в atlas_reminders.json и через общую память (Supabase) видны везде: поставил голосом на
компьютере — придёт уведомлением на телефон; поставил с телефона — компьютер скажет вслух, если включён.

    set_timer(10, "чай")                         → через 10 минут
    set_reminder("SAT", "2026-12-05 08:30")      → в указанное время (по часовому поясу ATLAS_TZ)
    set_reminder("позвонить маме", "19:00")      → сегодня в 19:00 (или завтра, если уже прошло)
    list_timers() / cancel_reminder("чай" или "2")

Фоновый поток (start_reminder_thread) в срок говорит напоминание вслух (на компьютере)
и/или шлёт уведомление на телефон (облако; компьютер — только если общей памяти нет).
"""
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(ROOT, "atlas_reminders.json")
TZ_NAME = os.getenv("ATLAS_TZ") or "Asia/Bishkek"
LATE_OK = 6 * 3600          # напоминание, проспанное компьютером или сервером, ещё сработает в течение 6 часов
KEEP = 7 * 86400            # выполненные и удалённые хранятся неделю (нужно для слияния), потом убираются
_FIXED_HOURS = {"Asia/Bishkek": 6, "Asia/Almaty": 5, "Asia/Tashkent": 5, "Europe/Moscow": 3, "UTC": 0}
_lock = threading.RLock()
_fired = set()              # уже сработавшие в этом процессе — второй раз не скажем
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TZ_NAME)
    except Exception:          # на Windows без tzdata — постоянное смещение
        return timezone(timedelta(hours=_FIXED_HOURS.get(TZ_NAME, 0)))


def _now() -> datetime:
    return datetime.now(_tz())


# ---------------------------------------------------------------------------
# Файл
# ---------------------------------------------------------------------------
def _load() -> dict:
    try:
        with open(FILE, encoding="utf-8") as f:
            doc = json.load(f)
        if isinstance(doc, dict) and isinstance(doc.get("items"), dict):
            return doc
    except Exception:
        pass
    return {"items": {}}


def _save(doc: dict) -> None:
    cut = time.time() - KEEP
    doc["items"] = {k: v for k, v in doc["items"].items()
                    if not ((v.get("done") or v.get("deleted")) and float(v.get("updated", 0)) < cut)}
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    os.replace(tmp, FILE)


def _active(doc: dict) -> list:
    """Ещё не сработавшие, по времени. → [(id, item)]"""
    items = [(k, v) for k, v in doc["items"].items() if not v.get("done") and not v.get("deleted")]
    return sorted(items, key=lambda kv: float(kv[1]["due"]))


def _add(text: str, due: float, kind: str) -> str:
    rid = uuid.uuid4().hex[:12]
    with _lock:
        doc = _load()
        doc["items"][rid] = {"text": text.strip()[:300], "due": due, "kind": kind, "updated": time.time(),
                             "done": False, "deleted": False}
        _save(doc)
    return rid


def _when(ts: float) -> str:
    d = datetime.fromtimestamp(ts, _tz())
    today = _now().date()
    if d.date() == today:
        return d.strftime("today %H:%M")
    if d.date() == today + timedelta(days=1):
        return d.strftime("tomorrow %H:%M")
    return f"{_DAYS[d.weekday()]} {d:%d.%m %H:%M}"


# ---------------------------------------------------------------------------
# Инструменты Atlas
# ---------------------------------------------------------------------------
def set_timer(minutes: float, message: str = "Timer's up!") -> str:
    """Ставит таймер на указанное количество минут."""
    try:
        minutes = float(minutes)
    except (TypeError, ValueError):
        return "Couldn't set the timer: the number of minutes is missing."
    if minutes <= 0:
        return "Couldn't set the timer: the time must be in the future."
    _add(message or "Timer's up!", time.time() + minutes * 60, "timer")
    return f"Timer set for {minutes:g} minutes."


def _parse_time(when: str):
    """'2026-12-05 08:30' / '2026-12-05T08:30' / '05.12 08:30' / '08:30' / 'in 20 minutes' → время (сек) или None."""
    w = (when or "").strip().lower()
    now = _now()
    m = re.fullmatch(r"(?:in|через)\s+(\d+(?:[.,]\d+)?)\s*(m|min|minutes?|мин\w*|h|hours?|час\w*)", w)
    if m:
        n = float(m.group(1).replace(",", "."))
        return now.timestamp() + n * (3600 if m.group(2)[0] in "hч" else 60)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})[ t](\d{1,2}):(\d{2})(?::\d{2})?", w)
    if m:
        y, mo, d, h, mi = map(int, m.groups())
        return now.replace(year=y, month=mo, day=d, hour=h, minute=mi, second=0, microsecond=0).timestamp()
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s+(\d{1,2}):(\d{2})", w)
    if m:
        d, mo, y, h, mi = m.groups()
        dt = now.replace(year=int(y or now.year), month=int(mo), day=int(d), hour=int(h), minute=int(mi),
                         second=0, microsecond=0)
        if not y and dt < now - timedelta(days=1):
            dt = dt.replace(year=dt.year + 1)
        return dt.timestamp()
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", w)
    if m:
        dt = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        return dt.timestamp()
    return None


def set_reminder(text: str, time_local: str) -> str:
    """Напоминание на дату и время (местное время пользователя)."""
    try:
        due = _parse_time(time_local)
    except ValueError:
        due = None
    if due is None:
        return (f"Couldn't understand the time '{time_local}'. Use 'YYYY-MM-DD HH:MM' (local time), 'HH:MM' "
                "or 'in 20 minutes'.")
    if due <= time.time():
        return "That time has already passed — give a time in the future."
    _add(text or "Reminder", due, "reminder")
    return f"Reminder set for {_when(due)}: {text}. It will come as a phone notification and be said aloud on the computer."


def list_timers() -> str:
    """Активные таймеры и напоминания."""
    with _lock:
        items = _active(_load())
    if not items:
        return "You have no active timers or reminders."
    parts = []
    for n, (_, v) in enumerate(items, 1):
        left = float(v["due"]) - time.time()
        when = f"in {max(left, 0) / 60:.0f} min" if v.get("kind") == "timer" and left < 3 * 3600 else _when(float(v["due"]))
        parts.append(f"{n}. '{v['text']}' — {when}")
    return "Active: " + "; ".join(parts)


list_reminders = list_timers


def cancel_reminder(which: str) -> str:
    """Отменить по номеру из списка или по словам из текста."""
    w = str(which or "").strip().lower()
    with _lock:
        doc = _load()
        items = _active(doc)
        hit = None
        if w.isdigit() and 1 <= int(w) <= len(items):
            hit = items[int(w) - 1]
        else:
            hit = next(((k, v) for k, v in items if w and w in v["text"].lower()), None)
        if not hit:
            return f"No active reminder matches '{which}'."
        hit[1].update(deleted=True, updated=time.time())
        _save(doc)
    return f"Cancelled: '{hit[1]['text']}'."


# ---------------------------------------------------------------------------
# Для экрана телефона
# ---------------------------------------------------------------------------
def upcoming() -> list:
    with _lock:
        items = _active(_load())
    out = []
    today = _now().date()
    for k, v in items:
        d = datetime.fromtimestamp(float(v["due"]), _tz())
        day = "сегодня" if d.date() == today else "завтра" if d.date() == today + timedelta(days=1) else d.strftime("%d.%m")
        out.append({"id": k, "text": v["text"], "when": f"{day}, {d:%H:%M}", "due": float(v["due"]),
                    "kind": v.get("kind", "reminder")})
    return out


def delete(rid: str) -> bool:
    with _lock:
        doc = _load()
        it = doc["items"].get(str(rid))
        if not it:
            return False
        it.update(deleted=True, updated=time.time())
        _save(doc)
    return True


# ---------------------------------------------------------------------------
# Срабатывание
# ---------------------------------------------------------------------------
def due_now() -> list:
    """Подошедшие и ещё не сработавшие (здесь или на другом устройстве). Отмечает их выполненными."""
    now = time.time()
    with _lock:
        doc = _load()
        due = [(k, v) for k, v in _active(doc) if float(v["due"]) <= now and k not in _fired]
        for k, v in due:
            _fired.add(k)
            v.update(done=True, updated=now)
        if due:
            _save(doc)
    return [dict(v, id=k, late=now - float(v["due"])) for k, v in due if now - float(v["due"]) <= LATE_OK]


def _push_by_default() -> bool:
    """Уведомления шлёт облако. Компьютер — только если общей памяти нет (телефон тогда подключён к нему)."""
    try:
        from core import cloud_sync
        return cloud_sync.store_from_env() is None
    except Exception:
        return True


def fire(item: dict, speak_func=None, push: bool = False) -> None:
    text = item["text"]
    if speak_func:
        try:
            speak_func(text if item.get("kind") == "timer" else f"Напоминание: {text}")
        except Exception as e:
            print(f"[напоминания] не смог сказать вслух: {e}")
    if push:
        try:
            from core import push as push_mod
            n = push_mod.notify("Atlas · " + ("таймер" if item.get("kind") == "timer" else "напоминание"), text,
                                tag=item.get("id"))
            print(f"[напоминания] «{text}» → уведомлений доставлено: {n}")
        except Exception as e:
            print(f"[напоминания] уведомление не ушло: {e}")


def _loop(speak_func, push):
    while True:
        time.sleep(2)
        try:
            for item in due_now():
                fire(item, speak_func, _push_by_default() if push is None else push)
        except Exception as e:
            print(f"[напоминания] {e}")


def start_reminder_thread(speak_func=None, push=None):
    """Фоновый поток. Компьютер: start_reminder_thread(speak). Облако: start_reminder_thread(None, push=True)."""
    thread = threading.Thread(target=_loop, args=(speak_func, push), daemon=True, name="reminders")
    thread.start()
    return thread
