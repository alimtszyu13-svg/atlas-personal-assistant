"""
Самопроверка действий: подставные датчики вместо Windows — проверяем, что Atlas ловит «сказал, но не сделал».

    python tests/test_verify.py
"""
import json
import os
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ATLAS_TESTING", "1")
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TMP = tempfile.mkdtemp(prefix="atlas_verify_")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def fake(**kw):
    from core import verify as V
    V.LOG = os.path.join(TMP, "verify.jsonl")
    clk = Clock()
    base = {"procs": lambda: {"explorer", "chrome"}, "windows": lambda: [("chrome", "google - chrome")],
            "volume": lambda: (50, False), "brightness": lambda: 70, "notes": lambda: {"notes": [], "todos": []},
            "theme": lambda: "dark", "folder": lambda n, loc: os.path.join(TMP, n), "sleep": clk.sleep, "clock": clk}
    base.update(kw)
    V.set_probes(**base)
    V.ALTERNATIVES = {}
    return V, clk


@test
def open_app_checked_and_failure_reported():
    V, clk = fake()
    snap = V.before("open_app", {"app_name": "chrome"})
    assert V.after("open_app", {"app_name": "chrome"}, "Открываю chrome.", True, snap) == ("Открываю chrome.", True)
    snap = V.before("open_app", {"app_name": "photoshop"})
    res, ok = V.after("open_app", {"app_name": "photoshop"}, "Открываю photoshop.", True, snap)
    assert not ok and V.MARK in res and "photoshop не запустился" in res and "different way" in res, res
    assert 5.5 <= clk.t - 1000 <= 7, "ждал около 6 секунд, не дольше"
    log = [json.loads(x) for x in open(V.LOG, encoding="utf-8")]
    assert log[-1]["tool"] == "open_app" and log[-1]["args"] == {"app_name": "photoshop"}
    # процесс появился не сразу — дождались
    state = {"n": 0}

    def procs():
        state["n"] += 1
        return {"explorer", "photoshop"} if state["n"] > 3 else {"explorer"}
    V, clk = fake(procs=procs, windows=lambda: [])
    res, ok = V.after("open_app", {"app_name": "photoshop"}, "Открываю photoshop.", True, {})
    assert ok and res == "Открываю photoshop.", res
    # новое окно у процесса с другим именем — тоже успех
    V, clk = fake(procs=lambda: set(), windows=lambda: [("chrome", "x"), ("ps", "untitled-1")])
    snap = {"wins": 1}                                         # до запуска было одно окно, стало два
    res, ok = V.after("open_app", {"app_name": "фотошоп"}, "Открываю фотошоп.", True, snap)
    assert ok, res


@test
def alternative_way_tried_before_giving_up():
    V, clk = fake()
    started = {"on": False}

    def alt(args):
        started["on"] = True
        return "ярлык «Adobe Photoshop 2025»"
    V.ALTERNATIVES = {"open_app": alt}
    V.set_probes(procs=lambda: {"explorer", "photoshop"} if started["on"] else {"explorer"})
    res, ok = V.after("open_app", {"app_name": "photoshop"}, "Открываю photoshop.", True, {})
    assert ok and "second try" in res and "Adobe Photoshop 2025" in res, res
    from brain_fail import FAIL_RE
    assert not FAIL_RE.search(res[:220]), "успех второй попытки не должен выглядеть как ошибка"
    V.ALTERNATIVES = {"open_app": lambda a: "start photoshop"}
    V.set_probes(procs=lambda: {"explorer"})
    res, ok = V.after("open_app", {"app_name": "photoshop"}, "Открываю photoshop.", True, {})
    assert not ok and "второй способ (start photoshop) тоже не помог" in res, res


@test
def volume_brightness_mute():
    V, _ = fake(volume=lambda: (30, False))
    assert V.after("set_volume", {"level": 30}, "Volume set to 30 percent.", True)[1]
    V, _ = fake(volume=lambda: (70, False))
    res, ok = V.after("set_volume", {"level": 30}, "Volume set to 30 percent.", True)
    assert not ok and "громкость 70%, а нужно 30%" in res and "другое устройство" in res
    vols = iter([(50, False), (60, False)])
    V, _ = fake(volume=lambda: next(vols))
    snap = V.before("volume_up", {})
    assert snap == {"vol": (50, False)} and V.after("volume_up", {}, "Volume increased to 60 percent.", True, snap)[1]
    V, _ = fake(volume=lambda: (100, False))
    assert V.after("volume_up", {}, "Volume increased to 100 percent.", True, {"vol": (100, False)})[1], "уже максимум"
    V, _ = fake(volume=lambda: (40, False))
    assert not V.after("mute_volume", {}, "Muted.", True)[1]
    V, _ = fake(brightness=lambda: 40)
    assert not V.after("set_brightness", {"level": 80}, "Brightness set to 80 percent.", True)[1]
    assert V.after("set_brightness", {"level": 80}, "Couldn't change brightness — this display may not support it.",
                   True)[1], "инструмент сам сказал «не могу» — повторно не ругаем"

    def broken():
        raise OSError("нет звуковой карты")
    V, _ = fake(volume=broken)
    assert V.after("set_volume", {"level": 30}, "Volume set to 30 percent.", True) == ("Volume set to 30 percent.", True), \
        "датчик сломан — не мешаем"


@test
def files_notes_theme_and_skips():
    V, _ = fake()
    res, ok = V.after("create_folder", {"name": "Проект", "location": "Desktop"}, "Created folder 'Проект'", True)
    assert not ok and "папки" in res
    os.makedirs(os.path.join(TMP, "Проект"))
    assert V.after("create_folder", {"name": "Проект"}, "Created folder 'Проект'", True)[1]
    V, _ = fake(notes=lambda: {"notes": ["купить хлеб"], "todos": []})
    assert V.after("add_note", {"text": "купить хлеб"}, "Note saved.", True)[1]
    assert not V.after("add_todo", {"task": "сдать курсовую"}, "Task added.", True)[1]
    V, _ = fake(theme=lambda: "dark")
    assert not V.after("set_theme", {"theme": "light"}, "Switched to light mode.", True)[1]
    assert V.after("open_app", {"app_name": "фотошоп"}, "Не знаю приложения фотошоп.", True)[1], \
        "инструмент сам признал неудачу — проверять нечего"
    assert V.after("get_weather", {}, "25°C", True) == ("25°C", True), "чтение не проверяем"
    assert V.after("open_app", {"app_name": "x"}, "boom", False) == ("boom", False)
    st = V.stats()
    assert st["checked"] > 5 and st["failed"] > 3


@test
def off_in_tests_and_by_env():
    from core import verify as V
    V.set_probes()
    assert not V.enabled(), "в тестах без подставных датчиков — выключено"
    fake()
    os.environ["ATLAS_VERIFY"] = "off"
    try:
        assert not V.enabled()
        assert V.after("set_volume", {"level": 1}, "ok", True) == ("ok", True)
    finally:
        os.environ.pop("ATLAS_VERIFY", None)
        V.set_probes()


@test
def brain_sees_failure_and_retries():
    """Мозг получает «[CHECK FAILED]», считает шаг неудачным и пробует другой путь."""
    from tests import stubs
    stubs.install()
    from brain import tools, planner
    import ai_brain
    V, _ = fake(procs=lambda: set(), windows=lambda: [])
    import core.verify as real
    assert real is V
    res, ok, _ms = tools._run_one_tool("open_app", {"app_name": "notepad"})
    assert not ok and V.MARK in res, res
    V.set_probes(procs=lambda: {"notepad"})
    res, ok, _ms = tools._run_one_tool("open_app", {"app_name": "notepad"})
    assert ok and V.MARK not in res, res
    V.set_probes()


def _fail_re():
    import re
    src = open(os.path.join(ROOT, "brain", "tools.py"), encoding="utf-8").read()
    m = re.search(r"_TRACE_FAIL = (re\.compile\(.*?re\.I\))", src, re.S)
    return eval(m.group(1), {"re": re})


import types  # noqa: E402
sys.modules["brain_fail"] = types.SimpleNamespace(FAIL_RE=_fail_re())


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
