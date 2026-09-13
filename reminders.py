import threading
import time
from datetime import datetime, timedelta

_active_timers = []  # список словарей: {"time": datetime, "message": str}
_lock = threading.Lock()  # защищаем список от одновременного доступа из разных потоков


def set_timer(minutes: float, message: str = "Timer's up!") -> str:
    """Ставит таймер на указанное количество минут."""
    fire_time = datetime.now() + timedelta(minutes=minutes)
    with _lock:
        _active_timers.append({"time": fire_time, "message": message})
    return f"Timer set for {minutes} minutes."


def list_timers() -> str:
    """Перечисляет все активные таймеры и сколько до них осталось."""
    with _lock:
        if not _active_timers:
            return "You have no active timers."
        parts = []
        for t in _active_timers:
            remaining = (t["time"] - datetime.now()).total_seconds() / 60
            if remaining > 0:
                parts.append(f"'{t['message']}' in {remaining:.1f} minutes")
        if not parts:
            return "You have no active timers."
        return "Active timers: " + ", ".join(parts)


def _check_timers_loop(speak_func):
    """
    Крутится в фоновом потоке всё время работы программы.
    Раз в секунду проверяет, не подошло ли время какого-то таймера.
    speak_func передаётся снаружи — чтобы избежать циклического импорта
    (voice.py не должен ничего знать про reminders.py).
    """
    while True:
        time.sleep(1)
        with _lock:
            now = datetime.now()
            due = [t for t in _active_timers if t["time"] <= now]
            for t in due:
                _active_timers.remove(t)

        for t in due:
            speak_func(t["message"])


def start_reminder_thread(speak_func):
    """Запускает фоновый поток-планировщик. Вызывается один раз из main.py."""
    thread = threading.Thread(target=_check_timers_loop, args=(speak_func,), daemon=True)
    thread.start()