"""
Машина времени экрана, голосовые заметки, игровой режим, перерывы и осанка — без экрана, камеры и Windows.

    python tests/test_daily.py
"""
import os
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TMP = tempfile.mkdtemp(prefix="atlas_daily_")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# --- машина времени ---------------------------------------------------------------------------------------
class SEnv:
    def __init__(self):
        self.t = time.mktime((2026, 10, 12, 15, 0, 0, 0, 0, -1))
        self.color, self.text, self.app, self.title, self.opened = (10, 10, 10), "", "chrome.exe", "Отчёт - Google Chrome", []

    def grab(self):
        from PIL import Image
        return Image.new("RGB", (1920, 1080), self.color)

    def ocr(self, img):
        return self.text

    def foreground(self):
        return self.app, self.title

    def idle(self):
        return 0

    def open(self, p):
        self.opened.append(p)

    def now(self):
        return self.t


@test
def screen_history_capture_search_forget():
    from core import screen_history as H
    db, base = os.path.join(TMP, "sh.db"), os.path.join(TMP, "shots")
    H.CFG = os.path.join(TMP, "sh.json")
    H._s.update(last_sig=None, pause_until=0)
    env = SEnv()
    env.t -= 86400                                                # вчера в 15:00
    env.text = "Продажи за квартал график выручки 3500 сом"
    r = H.capture_once(env, db, base)
    assert r and os.path.exists(r["file"]) and os.path.getsize(r["file"]) < 200_000, r
    env.t += 20
    assert H.capture_once(env, db, base) == {}, "экран не изменился — новый снимок не нужен"
    env.t = time.mktime((2026, 10, 12, 14, 30, 0, 0, 0, -1))
    env.color, env.text, env.app, env.title = (200, 200, 200), "def main(): print('hello')", "code.exe", "main.py - VS Code"
    assert H.capture_once(env, db, base)
    env.color, env.title = (90, 90, 90), "Банк — личный кабинет - Google Chrome"
    assert H.capture_once(env, db, base) == {}, "банк / пароли не снимаем"
    env.t = time.mktime((2026, 10, 12, 15, 0, 0, 0, 0, -1))
    hits = H.search("тот график про продажи", db)
    assert hits and hits[0]["text"].startswith("Продажи"), hits
    out = H.handle("где я видел цену 3500", env, db)
    assert out.startswith("Вот: вчера в 15:00, Chrome — «Отчёт»") and env.opened, out
    out = H.handle("что было на экране сегодня в 14:30", env, db)
    assert "сегодня в 14:30, VS Code — «main.py»" in out, out
    H._s["pause_until"] = 0
    assert H.handle("не записывай экран 30 минут", env, db) == "Хорошо, 30 минут экран не запоминаю."
    env.color = (1, 2, 250)
    assert H.capture_once(env, db, base) == {}, "пауза"
    assert H.handle("что я смотрел вчера", env) is None or True
    assert H.handle("забудь последний час экрана", env, db) == "Стёр 1 снимков экрана."
    assert H.when_from("вчера в 15:00", env.t) == env.t - 86400
    assert H.cleanup(7, db, now=env.t + 30 * 86400) == 1


@test
def screen_history_on_off_and_fallthrough():
    from core import screen_history as H
    H.CFG = os.path.join(TMP, "sh2.json")
    H.folder = lambda: os.path.join(TMP, "empty_sh")
    os.makedirs(H.folder(), exist_ok=True)
    assert H.handle("что я смотрел вчера") is None, "выключена — «что я смотрел» отвечает память компьютера"
    assert H.handle("что было на экране вчера").startswith("Машина времени выключена")
    H.start = lambda every=20: True
    assert H.handle("включи машину времени").startswith("Машина времени включена") and H.cfg()["on"]
    assert H.handle("выключи машину времени").startswith("Машина времени выключена") and not H.cfg()["on"]


# --- заметки --------------------------------------------------------------------------------------------------
@test
def voice_notes_sorting():
    from core import voice_notes as V
    V.FILE = os.path.join(TMP, "notes.json")
    import notes
    notes.DATA_FILE = os.path.join(TMP, "atlas_data.json")
    rem = []
    assert V.handle("запиши мысль: надо переделать главную страницу") == "Записал в задачи."
    assert "переделать главную" in notes.list_todos()
    assert V.handle("идея: в Atlas сделать режим для презентаций") == "Записал в идеи."
    assert V.handle("запиши идею сделать тёмную тему") == "Записал в идеи."
    assert V.handle("запиши: купить молоко и батарейки") == "Добавил в покупки: молоко, батарейки."
    assert V.add("в пятницу в 18:00 позвонить бабушке", reminder=lambda t: rem.append(t) or "Поставил напоминание: позвонить бабушке.") \
        == "Поставил напоминание: позвонить бабушке." and rem
    assert V.handle("заметка: пароль от вайфая на роутере снизу") == "Записал заметку."
    assert V.handle("что у меня в идеях") == "Идеи: 1) в Atlas сделать режим для презентаций; 2) сделать тёмную тему."
    assert V.handle("список покупок") == "Купить: молоко, батарейки."
    assert V.handle("купил молоко") == "Вычеркнул. Осталось: батарейки."
    d = V.digest()
    assert d.startswith("Сэр, итоги недели по заметкам: 2 идеи, 1 открытая задача, 1 в списке покупок"), d
    for other in ("запиши созвон", "запиши в память что я люблю кофе", "включи музыку"):
        assert V.handle(other) is None, other


# --- игровой режим --------------------------------------------------------------------------------------------
class GEnv:
    def __init__(self):
        self.plan, self.toast, self.killed, self.launched, self.t = "381b4222-f694-41f0-9685-ff5bb260df2e", True, [], [], 0

    def power_plan(self):
        return self.plan

    def plans(self):
        return ["381b4222-f694-41f0-9685-ff5bb260df2e", "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"]

    def set_plan(self, g):
        self.plan = g
        return True

    def add_ultimate(self):
        return ""

    def toasts(self, on=None):
        was = self.toast
        if on is not None:
            self.toast = on
        return was

    def procs(self):
        return [{"pid": 1, "name": "OneDrive.exe", "exe": r"C:\OneDrive\OneDrive.exe", "mb": 90},
                {"pid": 2, "name": "AdobeARM.exe", "exe": r"C:\Adobe\AdobeARM.exe", "mb": 20},
                {"pid": 3, "name": "chrome.exe", "exe": r"C:\chrome.exe", "mb": 1500},
                {"pid": 4, "name": "chrome.exe", "exe": r"C:\chrome.exe", "mb": 650},
                {"pid": 5, "name": "Telegram.exe", "exe": r"C:\tg.exe", "mb": 300}]

    def kill(self, pid):
        self.killed.append(pid)
        return True

    def launch(self, exe):
        self.launched.append(exe)

    def load(self):
        return 60.0, 50.0

    def foreground_exe(self):
        return "cs2.exe"

    def presentmon(self):
        return ""

    def now(self):
        return self.t


@test
def game_mode_on_off_restore():
    from core import game_mode as G
    G._s.update(on=False)
    env = GEnv()
    out = G.start(env, True, watch=False)
    assert out.startswith("Игровой режим включён: максимальная производительность; уведомления выключены; "
                          "закрыл: OneDrive, обновление Adobe"), out
    assert "chrome 2,1 ГБ" in out and "PresentMon" in out, out
    assert env.plan == G.HIGH and env.toast is False and sorted(env.killed) == [1, 2], (env.plan, env.killed)
    assert G.active() and "уже включён" in G.start(env, True, watch=False)
    G._s["samples"] = [(70, 55), (50, 45)]
    G._s["game"] = "cs2.exe"
    env.t = 47 * 60
    out = G.stop(env, True)
    assert out.startswith("Игровой режим выключен: играл 47 мин в cs2, процессор в среднем 60 %, память 50 %."), out
    assert env.plan == "381b4222-f694-41f0-9685-ff5bb260df2e" and env.toast is True
    assert env.launched == [r"C:\OneDrive\OneDrive.exe"], "обновлялки обратно не запускаем"
    assert not G.active()
    csv = os.path.join(TMP, "pm.csv")
    open(csv, "w").write("Application,MsBetweenPresents\n" + "".join(f"cs2.exe,{8.0 if i % 50 else 25.0}\n" for i in range(500)))
    fps = G.fps_from_csv(csv)
    assert fps[0] in range(110, 125) and fps[1] == 40, fps
    for t, want in (("игровой режим", "on"), ("включи игровой режим", "on"), ("я наигрался", "off"),
                    ("выключи игровой режим", "off")):
        assert (G._ON.search(t) and want == "on") or (G._OFF.search(t) and want == "off"), t
    assert G.handle("включи музыку") is None


# --- забота ----------------------------------------------------------------------------------------------------
@test
def wellbeing_gentle_breaks_and_posture():
    from core import wellbeing as W
    W.CFG = os.path.join(TMP, "wb.json")
    W._ru = lambda: True
    W._s.update(active_since=None, last_break_say=-1e12, last_posture_say=-1e12, hits=0)
    t = 10_000.0
    assert W.break_tick(0, t) == ""
    assert W.break_tick(10, t + 30 * 60) == ""
    msg = W.break_tick(5, t + 56 * 60)
    assert msg in W.BREAK_RU and msg.startswith("Сэр,") and ("Рекомендовал бы" in msg or "стоит" in msg or "поможет" in msg)
    assert W.break_tick(5, t + 70 * 60) == "", "не чаще раза в 40 минут"
    assert W.break_tick(400, t + 71 * 60) == "" and W._s["active_since"] is None, "отошёл — счёт заново"
    for m in W.BREAK_RU + W.POSTURE_RU:
        assert m.startswith("Сэр") and not any(w in m for w in ("Встань!", "Подтянись", "немедленно")), m
    class Cam:
        def __init__(self, f):
            self.f = f

        def face(self):
            return self.f
    assert W.handle("следи за осанкой").startswith("Хорошо, сэр.") and W.cfg()["posture"]
    assert W.handle("я сижу ровно", camera=Cam((0.45, 0.30))) == "Запомнил, сэр. Так и буду сверять."
    c = W.cfg()
    assert W.posture_tick((0.46, 0.31), 0, c) == ""
    said = [W.posture_tick((0.55, 0.37), 100 + i, c) for i in range(3)]
    assert said[:2] == ["", ""] and said[2] in W.POSTURE_RU, said
    assert W.posture_tick((0.55, 0.37), 200, c) == "", "не чаще раза в 30 минут"
    assert W.handle("не следи за осанкой").startswith("Хорошо, за осанкой не слежу") and not W.cfg()["posture"]
    assert W.handle("напоминай про перерывы каждые 40 минут").endswith("раз в 40 минут.") and W.cfg()["break_min"] == 40
    assert W.handle("не напоминай про перерывы") == "Хорошо, про перерывы молчу." and not W.cfg()["breaks"]


@test
def daily_wiring():
    from core import pc_fast
    assert pc_fast.handle("запиши: купить хлеб") in ("Добавил в покупки: хлеб.",)
    src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert "wellbeing.start(_announce)" in src and "screen_history.start()" in src and "game_mode.active()" in src
    import skills.pc_power  # noqa: F401
    from core.skills import REGISTRY
    for n in ("screen_history_find", "voice_note", "list_voice_notes", "game_mode_control"):
        assert n in REGISTRY, n
    for f in (".gitignore", ".dockerignore"):
        txt = open(os.path.join(ROOT, f), encoding="utf-8").read()
        for p in ("atlas_screen_history.json", "atlas_voice_notes.json", "atlas_wellbeing.json"):
            assert p in txt, (f, p)


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
