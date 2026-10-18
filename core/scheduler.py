"""
Один планировщик вместо десятка «спящих» потоков: периодические задачи Atlas живут в одном потоке.

    scheduler.every(600, knowledge.index, "база знаний", first=60, heavy=True, essential=False)
    scheduler.once(30, worklog.start, "память ПК")      — отложенный запуск: Atlas отзывается раньше

  • лёгкие задачи (проверка раз в N секунд) выполняются прямо в потоке планировщика;
  • тяжёлые (heavy=True: индексация, сеть) — в отдельном потоке, и два раза одновременно не запускаются;
  • ошибка задачи не роняет остальные — пишется в лог, задача продолжит по расписанию;
  • в игровом режиме необязательные задачи (essential=False) ждут — FPS важнее;
  • «что у тебя работает в фоне?» → список задач и потоков с нагрузкой на процессор (python -m core.scheduler).
"""
import re
import threading
import time

_lock = threading.RLock()
_jobs = {}
_s = {"thread": None, "stop": threading.Event(), "wake": threading.Event(), "clock": time.time, "boot": time.time()}


class Job:
    def __init__(self, name, fn, every, first, heavy, essential, once):
        self.name, self.fn, self.every, self.heavy, self.essential, self.once = name, fn, every, heavy, essential, once
        self.next = _s["clock"]() + (every if first is None else first)
        self.runs = self.errors = 0
        self.last_ms = 0.0
        self.running = False
        self.paused = False
        self.last_error = ""

    def info(self) -> dict:
        return {"name": self.name, "every": self.every, "runs": self.runs, "errors": self.errors,
                "last_ms": round(self.last_ms), "running": self.running, "paused": self.paused,
                "in_s": max(0, round(self.next - _s["clock"]())), "heavy": self.heavy, "once": self.once}


def every(seconds: float, fn, name: str, first: float = None, heavy: bool = False, essential: bool = True) -> Job:
    """Повторять fn() каждые seconds секунд (первый раз — через first, по умолчанию через seconds)."""
    with _lock:
        j = Job(name, fn, float(seconds), first, heavy, essential, once=False)
        _jobs[name] = j
    _s["wake"].set()
    return j


def once(delay: float, fn, name: str, heavy: bool = True, essential: bool = True) -> Job:
    """Выполнить fn() один раз через delay секунд (отложенный запуск служб)."""
    with _lock:
        j = Job(name, fn, 0.0, delay, heavy, essential, once=True)
        _jobs[name] = j
    _s["wake"].set()
    return j


def cancel(name: str) -> bool:
    with _lock:
        return _jobs.pop(name, None) is not None


def pause(name: str, on: bool = True) -> None:
    with _lock:
        if name in _jobs:
            _jobs[name].paused = on


def _game_on() -> bool:
    try:
        from core import game_mode
        return game_mode.active()
    except Exception:
        return False


def _run(j: Job) -> None:
    t0 = time.perf_counter()
    try:
        j.fn()
    except Exception as e:
        j.errors += 1
        j.last_error = str(e)[:200]
        print(f"[планировщик] {j.name}: {e}")
    finally:
        j.runs += 1
        j.last_ms = (time.perf_counter() - t0) * 1000
        j.running = False
        if j.last_ms > 500 and not j.heavy:
            print(f"[планировщик] {j.name}: {j.last_ms:.0f} мс — слишком долго для лёгкой задачи")


def tick(now: float = None, sync: bool = False) -> list:
    """Выполнить всё, что пора. → имена запущенных. sync=True — тяжёлые тоже здесь (для тестов)."""
    now = _s["clock"]() if now is None else now
    game = None
    started = []
    with _lock:
        due = [j for j in _jobs.values() if j.next <= now and not j.running and not j.paused]
    for j in due:
        if not j.essential:
            game = _game_on() if game is None else game
            if game:
                j.next = now + 60                       # игра — подождём минуту
                continue
        j.running = True
        if j.once:
            with _lock:
                _jobs.pop(j.name, None)
        else:
            j.next = now + j.every
        started.append(j.name)
        if j.heavy and not sync:
            threading.Thread(target=_run, args=(j,), daemon=True, name=f"job:{j.name}").start()
        else:
            _run(j)
    return started


def _next_wait() -> float:
    with _lock:
        if not _jobs:
            return 30.0
        return max(0.05, min(j.next for j in _jobs.values()) - _s["clock"]())


def start() -> threading.Thread:
    if _s["thread"] is not None:
        return _s["thread"]

    def loop():
        while not _s["stop"].is_set():
            _s["wake"].wait(min(_next_wait(), 30.0))
            _s["wake"].clear()
            try:
                tick()
            except Exception as e:
                print(f"[планировщик] {e}")
    _s["thread"] = threading.Thread(target=loop, daemon=True, name="atlas-scheduler")
    _s["thread"].start()
    return _s["thread"]


def jobs() -> list:
    with _lock:
        return [j.info() for j in sorted(_jobs.values(), key=lambda j: j.next)]


# --- что работает в фоне ---------------------------------------------------------------------------------------
def threads(sample: float = 1.0) -> list:
    """Потоки Atlas с нагрузкой на процессор за sample секунд: [{"name", "cpu"}] — самые тяжёлые первыми."""
    names = {t.native_id: t.name for t in threading.enumerate() if getattr(t, "native_id", None)}
    try:
        import psutil
        p = psutil.Process()
        a = {t.id: t.user_time + t.system_time for t in p.threads()}
        time.sleep(sample)
        b = {t.id: t.user_time + t.system_time for t in p.threads()}
        rows = [{"name": names.get(tid, f"поток {tid}"), "cpu": round(100 * (b[tid] - a.get(tid, b[tid])) / sample, 1)}
                for tid in b]
    except Exception:
        rows = [{"name": n, "cpu": None} for n in names.values()]
    return sorted(rows, key=lambda r: -(r["cpu"] or 0))


def describe(ru: bool = True, sample: float = 1.0) -> str:
    th = threads(sample)
    busy = [t for t in th if (t["cpu"] or 0) >= 1]
    js = jobs()
    s = (f"В фоне {len(th)} потоков и {len(js)} задач по расписанию. " if ru else
         f"{len(th)} threads and {len(js)} scheduled jobs in the background. ")
    if busy:
        s += ("Заметно грузят процессор: " if ru else "Busy: ") + ", ".join(f"{t['name']} {t['cpu']} %" for t in busy[:4]) + "."
    else:
        s += "Сейчас всё спокойно — процессор почти не занят." if ru else "All quiet — almost no CPU used."
    bad = [j for j in js if j["errors"]]
    if bad:
        s += (" С ошибками: " if ru else " With errors: ") + ", ".join(j["name"] for j in bad[:3]) + "."
    return s


_ASK = re.compile(r"что (?:у тебя )?(?:работает|запущено|крутится) в фоне|фоновые (?:задачи|процессы)|"
                  r"what(?:'s| is) running in the background|background (?:tasks|jobs)", re.I)


def handle(text: str):
    return describe(bool(re.search(r"[а-яё]", text or "", re.I))) if _ASK.search(text or "") else None


if __name__ == "__main__":
    for r in threads(2.0):
        print(f"{r['cpu'] if r['cpu'] is not None else '—':>6} %  {r['name']}")
