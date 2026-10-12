"""
Окна голосом, «скажи, когда…», уборка диска, текст с экрана — без настоящих окон, сети и мыши.

    python tests/test_pc_power.py
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

TMP = tempfile.mkdtemp(prefix="atlas_power_")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# --- окна ---------------------------------------------------------------------------------
class FakeWin:
    def __init__(self, mons=1):
        self.wins = [
            {"hwnd": 1, "app": "chrome", "title": "YouTube - Google Chrome", "min": False, "max": True,
             "rect": (0, 0, 1920, 1040)},
            {"hwnd": 2, "app": "WINWORD", "title": "эссе.docx - Word", "min": False, "max": False,
             "rect": (100, 100, 900, 700)},
            {"hwnd": 3, "app": "Telegram", "title": "Telegram", "min": True, "max": False, "rect": (0, 0, 10, 10)},
            {"hwnd": 4, "app": "Code", "title": "main.py - atlas - Visual Studio Code", "min": False, "max": False,
             "rect": (50, 50, 1500, 900)},
        ]
        self.mons = [{"work": (0, 0, 1920, 1040), "primary": True}] + \
            ([{"work": (1920, 0, 4480, 1400), "primary": False}] if mons > 1 else [])
        self.log = []

    def windows(self):
        return [dict(w) for w in self.wins]

    def monitors(self):
        return self.mons

    def monitor_of(self, h):
        return 0

    def _w(self, h):
        return next(w for w in self.wins if w["hwnd"] == h)

    def move(self, h, rect):
        self.log.append(("move", h, tuple(rect)))
        w = self._w(h)
        w.update(rect=tuple(rect), min=False, max=False)

    def show(self, h, how):
        self.log.append((how, h))
        w = self._w(h)
        w["min"], w["max"] = how == "min", how == "max"

    def focus(self, h):
        self.log.append(("focus", h))


@test
def windows_parse_phrases():
    from core import window_layout as W
    assert W.intent("поставь хром слева, а ворд справа")["moves"] == [{"app": "chrome", "pos": "left"},
                                                                      {"app": "word", "pos": "right"}]
    assert W.intent("разверни телеграм")["moves"] == [{"app": "telegram", "pos": "max"}]
    mv = W.intent("перенеси хром на второй монитор")["moves"][0]
    assert mv["app"] == "chrome" and mv["monitor"] == "next" and not mv["pos"], mv
    assert W.intent("перенеси хром на левый монитор")["moves"][0].get("pos") is None, "«левый монитор» — не «слева»"
    assert W.intent("раздели экран между кодом и браузером") == {"do": "tile", "ru": True,
                                                                 "targets": [{"app": "code"}, {"app": "browser"}]}
    assert W.intent("сверни всё, кроме кода")["keep"] == [{"app": "code"}]
    assert W.intent("верни окна как было")["do"] == "undo"
    assert W.intent("поставь телеграм в правый верхний угол")["moves"][0]["pos"] == "tr"
    assert W.intent("put chrome on the left and word on the right")["ru"] is False
    for other in ("поставь таймер на 5 минут", "перенеси встречу на завтра", "поставь музыку на паузу",
                  "разложи файлы по папкам", "открой хром", "разверни", "что слева на экране?"):
        assert W.intent(other) is None, other


@test
def windows_place_split_and_undo():
    from core import window_layout as W
    fw = FakeWin()
    out = W.run(W.intent("поставь хром слева, а ворд справа"), fw)
    assert ("move", 1, (0, 0, 960, 1040)) in fw.log and ("move", 2, (960, 0, 1920, 1040)) in fw.log, fw.log
    assert out.startswith("Chrome слева") and "Word справа" in out, out
    out = W.run(W.intent("верни окна как было"), fw)
    assert "Вернул" in out and ("max", 1) in fw.log and fw._w(2)["rect"] == (100, 100, 900, 700), (out, fw.log)
    assert "ещё не двигал" in W.run({"do": "undo", "ru": True}, fw)
    fw.log.clear()
    out = W.run(W.intent("сверни всё, кроме кода"), fw)
    assert ("min", 1) in fw.log and ("min", 2) in fw.log and ("min", 4) not in fw.log and ("focus", 4) in fw.log
    assert "кроме: VS Code" in out, out
    fw = FakeWin()
    out = W.run(W.intent("раздели экран между кодом и хромом"), fw)
    assert ("move", 4, (0, 0, 960, 1040)) in fw.log and ("move", 1, (960, 0, 1920, 1040)) in fw.log, fw.log
    out = W.run(W.intent("поставь дискорд слева"), fw)
    assert "Не нашёл открытое окно Discord" in out, out
    assert W.handle("сделай отступ слева", FakeWin()) is None, "не про окна — дальше, к модели"
    fw = FakeWin()
    assert W.handle("сверни окно", fw) == "Chrome свёрнут." and ("min", 1) in fw.log
    assert W.handle("поставь его справа", FakeWin()) == "Chrome справа."
    out = W.handle("поставь эссе слева", FakeWin())
    assert out.startswith("Word слева"), out            # по заголовку окна


@test
def windows_monitors_and_model_spec():
    from core import window_layout as W
    fw = FakeWin(mons=2)
    out = W.run(W.intent("перенеси хром на второй монитор"), fw)
    assert ("max", 1) in fw.log and any(e[0] == "move" and e[2][0] >= 1920 for e in fw.log), fw.log
    assert "мониторе 2" in out, out
    fw = FakeWin(mons=2)
    W.run(W.intent("поставь ворд справа на втором мониторе"), fw)
    assert ("move", 2, (3200, 0, 4480, 1400)) in fw.log, fw.log
    fw = FakeWin()
    out = W.arrange("chrome:left; telegram:max", fw)
    assert ("move", 1, (0, 0, 960, 1040)) in fw.log and ("max", 3) in fw.log, (out, fw.log)
    assert W.tile_rects(3, (0, 0, 100, 100)) == [(0, 0, 50, 100), (50, 0, 100, 50), (50, 50, 100, 100)]


# --- «скажи, когда…» -------------------------------------------------------------------------
class FakeEnv:
    def __init__(self):
        self.t = 1000.0
        self.dirs = {"downloads": [], "desktop": []}
        self.ps = []
        self.page = "Товар. Нет в наличии"
        self.net = True
        self.bat = (40, True)
        self.idle_s = 0
        self.pages = 0

    def folder(self, name):
        return name

    def listdir(self, path):
        return list(self.dirs.get(path, []))

    def procs(self):
        return [dict(p) for p in self.ps]

    def fetch(self, url):
        self.pages += 1
        return self.page

    def online(self):
        return self.net

    def battery(self):
        return self.bat

    def idle(self):
        return self.idle_s

    def current_page(self):
        return "https://shop.kg/item/1", "Наушники — Shop.kg"

    def now(self):
        return self.t


def _fresh_watchers():
    from core import watchers as W
    W.FILE = os.path.join(TMP, "watchers.json")
    if os.path.exists(W.FILE):
        os.remove(W.FILE)
    W._s["items"] = None
    return W


@test
def watchers_parse():
    W = _fresh_watchers()
    k = lambda t: (W.intent(t) or (None, {}))[1].get("kind") if W.intent(t) and W.intent(t)[0] == "add" else None
    assert k("скажи, когда скачается файл") == "download"
    assert k("сообщи, когда закончится рендер") == "busy"
    assert W.intent("скажи, когда закроется премьер")[1]["target"] == "adobe premiere pro"
    it = W.intent("скажи, когда на этой странице появится в наличии")[1]
    assert it["kind"] == "web" and it["target"] == "current" and it["text"] == "в наличии", it
    it = W.intent("скажи, когда на ozon.ru/product/1 появится «в наличии»")[1]
    assert it["target"] == "https://ozon.ru/product/1" and it["text"] == "в наличии", it
    assert W.intent("дай знать, когда страница изменится")[1]["text"] == ""
    assert k("скажи, когда в загрузках появится файл") == "folder"
    assert k("скажи, когда вернётся интернет") == "internet"
    assert W.intent("скажи когда ноутбук зарядится до 80%")[1]["target"] == 80
    assert W.intent("напиши мне на телефон, когда закончится экспорт в давинчи")[1]["phone"] is True
    assert k("let me know when the download finishes") == "download"
    assert W.intent("за чем ты следишь") == ("list",)
    assert W.intent("перестань следить за загрузкой") == ("cancel", "загрузкой")
    for other in ("скажи, когда у меня встреча", "напомни, когда открою ворд", "скажи когда закроется магазин",
                  "когда закончится семестр"):
        assert W.intent(other) is None, other


@test
def watchers_download_and_busy():
    W = _fresh_watchers()
    env, said = FakeEnv(), []
    notify = lambda w, m, e: said.append(m)
    env.dirs["downloads"] = ["old.pdf", "film.mkv.crdownload"]
    assert "Слежу за загрузкой (1 файл)" in W.handle("скажи, когда скачается файл", env)
    assert W.check_all(env, notify) == [] and not said
    env.dirs["downloads"] = ["old.pdf", "film.mkv"]
    W.check_all(env, notify)
    assert said == ["Загрузка закончилась: film.mkv."], said
    assert "ни за чем" in W.listing()
    # рендер: самая загруженная программа — Blender; освободилась на 30 с → готово
    env.ps = [{"pid": 10, "name": "blender.exe", "cpu": 380.0}, {"pid": 11, "name": "chrome.exe", "cpu": 12.0},
              {"pid": 0, "name": "System Idle Process", "cpu": 900.0}]
    assert W.handle("сообщи, когда закончится рендер", env) == "Хорошо, скажу, когда blender освободит процессор."
    env.ps[0]["cpu"] = 3.0
    W.check_all(env, notify)
    env.t += 20
    W.check_all(env, notify)
    assert len(said) == 1, "ещё не 30 секунд тишины"
    env.ps[0]["cpu"] = 200.0
    env.t += 20
    W.check_all(env, notify)
    env.ps[0]["cpu"] = 2.0
    W.check_all(env, notify)
    env.t += 31
    W.check_all(env, notify)
    assert said[-1] == "blender закончил — процессор свободен.", said
    env.ps = [{"pid": 11, "name": "chrome.exe", "cpu": 1.0}]
    assert "ничем не загружен" in W.handle("скажи, когда закончится установка", env)
    assert "не запущен" in W.handle("скажи, когда закроется премьер", env)
    env.ps = [{"pid": 20, "name": "Adobe Premiere Pro.exe", "cpu": 50.0}]
    W.handle("скажи, когда закроется премьер", env)
    env.ps = []
    W.check_all(env, notify)
    assert said[-1] == "Adobe Premiere Pro закрылся.", said


@test
def watchers_web_folder_net_battery_persist():
    W = _fresh_watchers()
    env, said = FakeEnv(), []
    notify = lambda w, m, e: said.append(m)
    out = W.handle("скажи, когда на этой странице появится «в наличии»", env)
    assert "Проверяю раз в 2 минуты" in out and "Наушники" in out, out
    assert "уже есть" in W.handle("скажи, когда на shop.kg появится «нет в наличии»", env)
    assert not W.present("Нет в наличии", "в наличии") and W.present("Нет в наличии. В наличии в Бишкеке", "в наличии")
    env.t += 60
    W.check_all(env, notify)
    n = env.pages
    env.t += 30
    W.check_all(env, notify)
    assert env.pages == n, "страница — не чаще раза в 2 минуты"
    env.page = "Товар. В наличии: 3 шт"
    env.t += 130
    W.check_all(env, notify)
    assert said[-1].startswith("На странице «Наушники — Shop.kg» появилось «в наличии»"), said
    env.net = False
    assert "Скажу, как только интернет" in W.handle("скажи, когда вернётся интернет", env)
    W.handle("скажи когда ноутбук зарядится до 80%", env)
    W.handle("скажи, когда на рабочем столе появится файл", env)
    assert len(W._load()) == 3
    W._s["items"] = None                                   # «перезапуск Atlas»: список с диска
    assert len(W._load()) == 3, open(W.FILE, encoding="utf-8").read()
    env.net, env.bat = True, (81, True)
    env.dirs["desktop"] = ["~$черновик.docx", "отчёт.pdf"]
    W.check_all(env, notify)
    assert "Интернет вернулся." in said and "Заряд 81% — можно отключать." in said and \
        "В папке появился файл: отчёт.pdf." in said, said
    assert W._load() == []
    W.handle("скажи, когда в загрузках появится файл", env)
    env.t += W.TTL + 1
    W.check_all(env, notify)
    assert W._load() == [], "через 6 часов наблюдение заканчивается"
    W.handle("скажи, когда вернётся интернет", FakeEnv())   # интернет есть — не добавляем
    assert W._load() == []
    env.net = False
    W.handle("скажи, когда вернётся интернет", env)
    W.handle("скажи когда ноутбук зарядится", env)
    assert "Больше не слежу: интернет вернётся" in W.cancel("интернет")
    assert "Перестал следить (1)" in W.cancel("")


@test
def watchers_phone_when_away():
    W = _fresh_watchers()
    from core import handoff
    sent = []
    orig = handoff.notify_phone
    handoff.notify_phone = lambda t, b, **k: sent.append(b) or True
    try:
        env = FakeEnv()
        env.idle_s = 5
        W._notify({"phone": False}, "Готово.", env)
        assert sent == []
        env.idle_s = 300
        W._notify({"phone": False}, "Готово.", env)
        W._notify({"phone": True}, "Ещё.", FakeEnv())
        assert sent == ["Готово.", "Ещё."], sent
    finally:
        handoff.notify_phone = orig


# --- уборка диска -----------------------------------------------------------------------------
def _mk(path, size, age_days=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    t = time.time() - age_days * 86400
    os.utime(path, (t, t))


class DiskEnv:
    def __init__(self, base):
        self.base = base
        self.binned, self.bin_size, self.emptied = [], 5 * 1024 ** 2, False

    def drives(self):
        return [{"name": "C:", "total": 256 * 1024 ** 3, "free": 12 * 1024 ** 3}]

    def folders(self):
        return {n: os.path.join(self.base, d) for n, d in (("Загрузки", "Downloads"), ("Документы", "Documents"),
                                                           ("Рабочий стол", "Desktop"))}

    def temp_dirs(self):
        return [os.path.join(self.base, "Temp")]

    def browser_caches(self):
        return [("Chrome", os.path.join(self.base, "Chrome", "Default", "Cache"))]

    def recycle_bin(self):
        return self.bin_size

    def empty_bin(self):
        self.emptied = True
        return True

    def to_bin(self, paths):
        for p in paths:
            self.binned.append(os.path.basename(p))
            os.remove(p)
        return len(paths)

    def remove(self, path):
        if path.endswith(".locked"):
            return False
        os.remove(path)
        return True

    def now(self):
        return time.time()


@test
def disk_usage_plan_and_clean():
    from core import disk_clean as D
    base = os.path.join(TMP, "disk")
    MB = 1024 ** 2
    _mk(os.path.join(base, "Temp", "old.tmp"), 30 * MB, age_days=3)
    _mk(os.path.join(base, "Temp", "fresh.tmp"), 10 * MB)                          # моложе суток — не трогаем
    _mk(os.path.join(base, "Temp", "sub", "old2.log"), 2 * MB, age_days=5)
    _mk(os.path.join(base, "Chrome", "Default", "Cache", "data_1"), 20 * MB)
    _mk(os.path.join(base, "Chrome", "Default", "Cache", "x.locked"), 1 * MB)
    _mk(os.path.join(base, "Downloads", "setup_old.exe"), 15 * MB, age_days=60)
    _mk(os.path.join(base, "Downloads", "setup_new.exe"), 15 * MB, age_days=2)    # свежий — оставляем
    _mk(os.path.join(base, "Downloads", "лекция.pdf"), 3 * MB, age_days=90)       # не установщик — никогда
    _mk(os.path.join(base, "Documents", "фильм.mkv"), 210 * MB)
    env = DiskEnv(base)
    u = D.usage(env)
    s = D.describe_usage(u)
    assert "Диск C: свободно 12 ГБ из 256 ГБ (занято 95%)" in s and "Документы — 210 МБ" in s and \
        "фильм.mkv (210 МБ)" in s, s
    p = D.plan(env)
    names = {os.path.basename(x[0]) for x in p["temp"]}
    assert names == {"old.tmp", "old2.log"}, names
    assert [os.path.basename(x[0]) for x in p["installers"]] == ["setup_old.exe"]
    said = D.handle("почисти компьютер", env=env)
    assert said.startswith("Могу освободить примерно 68 МБ") and "кэш Chrome — 21 МБ" in said and \
        "в Корзину" in said and said.endswith("Чистить?"), said
    assert D.intent("да", last="что-то другое") is None, "«да» на другой вопрос — не уборка"
    out = D.confirm("да", env=env, wait=True)
    assert out.startswith("Уборка закончена: освободил 67 МБ") and "Корзине" in out, out
    left = sorted(f for _, _, fs in os.walk(base) for f in fs)
    assert left == ["fresh.tmp", "setup_new.exe", "x.locked", "лекция.pdf", "фильм.mkv"], left
    assert env.binned == ["setup_old.exe"] and not env.emptied
    assert "Нечего подтверждать" in D.confirm("да", env=env, wait=True)
    _mk(os.path.join(base, "Temp", "old3.tmp"), 40 * MB, age_days=3)
    D.handle("почисти компьютер и корзину", env=env)
    assert D.handle("нет", env=env, last=D._s["said"]) == "Хорошо, ничего не трогаю." and not D.pending()
    D.handle("почисти компьютер", env=env)
    assert D.intent("да, и корзину тоже", last=D._s["said"]) == ("confirm",)
    D.confirm("да, и корзину тоже", env=env, wait=True)
    assert env.emptied, "корзина — только когда попросили"
    for t, want in (("что занимает место", ("usage",)), ("освободи место на диске", ("plan",)),
                    ("очисти историю буфера", None), ("очисти корзину", None), ("clean up my pc", ("plan",))):
        assert D.intent(t) == want, (t, D.intent(t))


# --- текст с экрана ------------------------------------------------------------------------------
class Img:
    size = (800, 300)

    def save(self, path):
        open(path, "wb").write(b"png")

    def resize(self, wh):
        return self


@test
def screen_snip_copy_explain_translate():
    from core import screen_snip as S
    S.folder = lambda: TMP
    copied = []
    kw = dict(picker=lambda: [10, 10, 810, 310], grabber=lambda b: Img(), copier=copied.append)
    short, full = S.snip("copy", True, reader=lambda i: "Error 0x80070005\nAccess is denied", **kw)
    assert short == "Скопировал текст — 2 строки: «Error 0x80070005»…" and full.startswith("Error"), short
    assert copied == ["Error 0x80070005\nAccess is denied"]
    assert os.path.basename(S.last()["path"]).startswith("snip_")
    asked = []
    short, full = S.snip("explain", True, reader=lambda i: "Error 0x80070005",
                         ask=lambda sys_, t, max_tokens=0: asked.append(sys_) or "Нет прав доступа: запусти от админа.",
                         **kw)
    assert short == "Нет прав доступа: запусти от админа." and "error message" in asked[0]
    short, full = S.snip("translate", True, reader=lambda i: "Привет, мир",
                         ask=lambda sys_, t, max_tokens=0: asked.append(sys_) or "Hello, world", **kw)
    assert short == "Hello, world" and "into English" in asked[-1] and copied[-1] == "Hello, world"
    assert S.snip("copy", True, picker=lambda: None)[0] == "Отменил."
    short, _ = S.snip("copy", True, reader=lambda i: "", **kw)
    assert short.startswith("Текста на снимке не нашёл. Снимок сохранил: snip_"), short
    for t, want in (("распознай текст с экрана", "copy"), ("объясни то, что выделю", "explain"),
                    ("переведи то, что я выделю", "translate"), ("переведи текст с экрана", "translate"),
                    ("сделай снимок области", "copy"), ("объясни это", None), ("переведи это на английский", None),
                    ("что на экране", None), ("сожми выделенное", None)):
        assert S.intent(t) == want, (t, S.intent(t))


@test
def wiring():
    src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert "_pc_power(command)" in src and "watchers.start(_announce)" in src
    assert 'add_hotkey("ctrl+alt+x"' in open(os.path.join(ROOT, "selection_hotkey.py"), encoding="utf-8").read()
    cloud = open(os.path.join(ROOT, "cloud", "atlas_cloud.py"), encoding="utf-8").read()
    for n in ("arrange_windows", "watch_for", "plan_disk_cleanup", "snip_screen_text"):
        assert f'"{n}"' in cloud, n
    for f in (".gitignore", ".dockerignore"):
        assert "atlas_watchers.json" in open(os.path.join(ROOT, f), encoding="utf-8").read(), f
    import skills.pc_power  # noqa: F401
    from core.skills import REGISTRY as reg
    for n in ("arrange_windows", "watch_for", "list_watches", "cancel_watch", "disk_usage_report",
              "plan_disk_cleanup", "confirm_disk_cleanup", "snip_screen_text"):
        assert n in reg, n


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
