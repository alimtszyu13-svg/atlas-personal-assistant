"""
Фон: единый планировщик, отложенный запуск, «что работает в фоне», безопасное обновление с GitHub.

    python tests/test_background.py
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ATLAS_TESTING", "1")
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TMP = tempfile.mkdtemp(prefix="atlas_bg_")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def fresh():
    from core import scheduler as S
    clk = Clock()
    S._s["clock"] = clk
    with S._lock:
        S._jobs.clear()
    return S, clk


@test
def scheduler_runs_on_time_and_isolates_errors():
    S, clk = fresh()
    runs = []
    S.every(10, lambda: runs.append("a"), "a")
    S.every(5, lambda: 1 / 0, "boom", first=0)
    S.once(3, lambda: runs.append("once"), "once")
    assert S.tick(sync=True) == ["boom"]                          # boom первым (first=0), ошибка не мешает
    clk.t += 3
    assert S.tick(sync=True) == ["once"] and runs == ["once"]
    clk.t += 7
    S.tick(sync=True)
    assert runs == ["once", "a"]
    info = {j["name"]: j for j in S.jobs()}
    assert "once" not in info and info["boom"]["errors"] == 2 and info["a"]["runs"] == 1, info
    assert S.cancel("boom") and not S.cancel("boom")
    S.pause("a")
    clk.t += 100
    assert S.tick(sync=True) == [], "на паузе не запускается"


@test
def heavy_jobs_do_not_overlap_and_game_mode_waits():
    S, clk = fresh()
    gate, started = threading.Event(), []

    def slow():
        started.append(1)
        gate.wait(2)
    S.every(1, slow, "slow", first=0, heavy=True)
    assert S.tick() == ["slow"]
    clk.t += 5
    assert S.tick() == [], "прошлый запуск ещё идёт — второй не начинаем"
    gate.set()
    time.sleep(0.2)
    clk.t += 5
    assert S.tick() == ["slow"] and len(started) >= 1
    from core import game_mode
    S, clk = fresh()
    S.every(1, lambda: None, "index", first=0, essential=False)
    S.every(1, lambda: None, "reminders", first=0)
    orig = game_mode.active
    game_mode.active = lambda: True
    try:
        assert S.tick(sync=True) == ["reminders"], "в игре — только нужное"
    finally:
        game_mode.active = orig


@test
def real_loop_wakes_up():
    from core import scheduler as S
    S._s["clock"] = time.time
    with S._lock:
        S._jobs.clear()
    hit = threading.Event()
    S.start()
    S.once(0.1, hit.set, "ping", heavy=False)
    assert hit.wait(3), "поток планировщика выполнил задачу"
    rows = S.threads(0.2)
    assert any(r["name"] == "atlas-scheduler" for r in rows), rows
    d = S.describe(True, 0.1)
    assert d.startswith("В фоне ") and "задач по расписанию" in d
    assert S.handle("что у тебя работает в фоне?") and S.handle("включи музыку") is None


@test
def migrated_loops_use_scheduler():
    S, clk = fresh()
    from core import voice_notes as V, knowledge as K
    V.FILE = os.path.join(TMP, "vn.json")
    with open(V.FILE, "w", encoding="utf-8") as f:
        json.dump({"items": [{"kind": "idea", "text": "режим презентаций", "ts": time.time(), "done": False}]}, f)
    said = []
    V.start(said.append)
    assert "итоги недели по заметкам" in {j["name"] for j in S.jobs()}
    sunday = datetime(2026, 10, 18, 20, 0)                        # воскресенье, 20:00
    assert V.weekly_check(said.append, now=sunday) and len(said) == 1
    assert V.weekly_check(said.append, now=sunday) == "", "раз в неделю"
    assert V.weekly_check(said.append, now=datetime(2026, 10, 19, 20, 0)) == ""
    K._s["thread"] = None
    K.start()
    job = {j["name"]: j for j in S.jobs()}["база знаний"]
    assert job["heavy"] and job["in_s"] == 60
    src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert "scheduler.start()" in src and 'scheduler.once(30, _worklog, "память ПК")' in src
    assert "updater.start(_quiet_announce)" in src
    K._s["thread"] = None


def _sh(cwd, *cmd):
    return subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _repos():
    base = tempfile.mkdtemp(prefix="upd_", dir=TMP)
    origin, mine, other = (os.path.join(base, x) for x in ("origin.git", "mine", "other"))
    _sh(base, "git", "init", "-q", "--bare", "-b", "main", origin)
    _sh(base, "git", "clone", "-q", origin, other)
    for d in (other,):
        _sh(d, "git", "config", "user.email", "t@t")
        _sh(d, "git", "config", "user.name", "t")
        _sh(d, "git", "checkout", "-q", "-B", "main")
    with open(os.path.join(other, "a.txt"), "w") as f:
        f.write("v1\n")
    _sh(other, "git", "add", "-A")
    _sh(other, "git", "commit", "-q", "-m", "v1")
    _sh(other, "git", "push", "-q", "origin", "main")
    _sh(base, "git", "clone", "-q", origin, mine)
    _sh(mine, "git", "config", "user.email", "t@t")
    _sh(mine, "git", "config", "user.name", "t")
    return origin, mine, other


def _push_change(other, text, name="a.txt"):
    with open(os.path.join(other, name), "w") as f:
        f.write(text)
    _sh(other, "git", "add", "-A")
    _sh(other, "git", "commit", "-q", "-m", text.strip())
    _sh(other, "git", "push", "-q", "origin", "main")


@test
def updater_fast_forwards_tests_and_rolls_back():
    from core import updater as U
    _origin, mine, other = _repos()
    U.ROOT, U.STATE = mine, os.path.join(mine, ".upd.json")
    assert U.apply().startswith("Обновлений нет")
    _push_change(other, "v2\n")
    _push_change(other, "flask\n", "requirements.txt")
    st = U.check()
    assert st["ok"] and st["behind"] == 2 and not st["dirty"], st
    orig_tests = U._tests
    U._tests = lambda: (True, "Итого: всё прошло")
    try:
        out = U.apply()
        assert out.startswith("Обновился: 2 изменений") and "pip install -r requirements.txt" in out, out
        assert open(os.path.join(mine, "a.txt")).read() == "v2\n"
        _push_change(other, "v3-broken\n")
        U._tests = lambda: (False, "✗ test_main.py")
        out = U.apply()
        assert out.startswith("Новая версия не прошла проверку — вернул как было"), out
        assert open(os.path.join(mine, "a.txt")).read() == "v2\n", "откат после проваленных тестов"
        assert U.rollback().startswith("Вернул прошлую версию")
        assert open(os.path.join(mine, "a.txt")).read() == "v1\n"
        with open(os.path.join(mine, "a.txt"), "w") as f:
            f.write("мои правки\n")
        assert U.apply().startswith("Есть незакоммиченные изменения"), "твои правки не трогаем"
        said = []
        assert U.daily_check(said.append) and len(said) == 1
        assert U.daily_check(said.append) == "", "про одну версию — один раз"
    finally:
        U._tests = orig_tests
    assert U.intent("обнови себя") == "apply" and U.intent("есть ли обновления?") == "check"
    assert U.intent("откати обновление") == "rollback" and U.intent("есть обновления windows") is None
    assert U.intent("обнови страницу") is None


def main():
    ok = 0
    for t in TESTS:
        try:
            t()
            ok += 1
            print(f"  ✓ {t.__name__}")
        except Exception:
            print(f"  ✗ {t.__name__}")
            traceback.print_exc()
    print(f"\nТестов пройдено: {ok} из {len(TESTS)}")
    sys.stdout.flush()
    os._exit(0 if ok == len(TESTS) else 1)


if __name__ == "__main__":
    main()
