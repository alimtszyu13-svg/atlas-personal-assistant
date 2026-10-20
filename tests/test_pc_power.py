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
        assert "atlas_messengers.json" in open(os.path.join(ROOT, f), encoding="utf-8").read(), f
    import skills.pc_power  # noqa: F401
    from core.skills import REGISTRY as reg
    for n in ("arrange_windows", "watch_for", "list_watches", "cancel_watch", "disk_usage_report",
              "plan_disk_cleanup", "confirm_disk_cleanup", "snip_screen_text", "send_message",
              "confirm_message_send", "read_messages"):
        assert n in reg, n


# --- мессенджеры ------------------------------------------------------------------------------------
class FakeTG:
    """Окно Telegram 1000×800 на (0, 0): слева чаты, справа переписка; OCR — по состоянию."""

    def __init__(self, app="Telegram", title="Telegram", chats=None, top=True, web=False):
        self.app, self.title, self.web = app, title, web
        self.chats = chats or {"Мама ❤": [("them", "Ты где?"), ("me", "щас буду"), ("them", "Купи хлеб")],
                               "Алим Сыдыков": [("them", "Завтра в 7?")], "Дана": [("them", "Привет")]}
        self.open, self.query, self.focused_search, self.log = None, None, False, []
        self.typed, self.sent, self.top = "", [], top
        self.fg = 0

    # окна
    def windows(self):
        w = [{"hwnd": 5, "app": self.app, "title": self.title, "min": False, "max": False, "rect": (0, 0, 1000, 800)}]
        other = [{"hwnd": 9, "app": "Code", "title": "main.py", "min": False, "max": False, "rect": (0, 0, 9, 9)}]
        return w + other if self.top else other + w

    def launch(self, app):
        self.log.append(("launch", app))

    def activate(self, h):
        self.fg = h
        return True

    def foreground(self):
        return self.fg

    def rect(self, h):
        return (0, 0, 1000, 800)

    def ocr(self, rect):
        lines = []
        names = list(self.chats)
        if self.query is not None:
            from core import messengers as M
            st = M.stems(self.query) or [self.query.lower()]
            lines.append({"text": self.query, "x": 20, "y": 70, "w": 120, "h": 20})
            names = [n for n in names if M.line_matches(n, st)]
        else:
            lines.append({"text": "Поиск", "x": 20, "y": 70, "w": 60, "h": 20})
        for i, n in enumerate(names):
            lines.append({"text": n, "x": 70, "y": 120 + i * 70, "w": 150, "h": 20})
            last = self.chats[n][-1][1]
            lines.append({"text": last, "x": 70, "y": 145 + i * 70, "w": 150, "h": 18})
        if self.open:
            lines.append({"text": self.open, "x": 420, "y": 30, "w": 140, "h": 22})
            lines.append({"text": "был(а) недавно", "x": 420, "y": 55, "w": 120, "h": 16})
            for i, (who, t) in enumerate(self.chats[self.open][-8:]):
                x = 760 if who == "me" else 330
                lines.append({"text": t + " 12:3" + str(i), "x": x, "y": 200 + i * 60, "w": 180, "h": 20})
        return {"ru": lines, "en": []}

    def click(self, x, y):
        self.log.append(("click", x, y))
        if x < 450 and y > 100:
            ocr = self.ocr(None)["ru"]
            hit = next((ln for ln in ocr if ln["x"] <= x <= ln["x"] + ln["w"] and ln["y"] <= y <= ln["y"] + ln["h"]
                        and ln["text"] in self.chats), None)
            if hit:
                self.open, self.query = hit["text"], None
        elif x < 450:
            self.focused_search = True

    def keys(self, combo):
        self.log.append(("keys", combo))
        if combo == "esc" and self.app == "Telegram":
            self.open, self.focused_search = None, True
        if combo == "enter" and self.typed and self.open:
            self.chats[self.open].append(("me", self.typed))
            self.sent.append((self.open, self.typed))
            self.typed = ""

    def paste(self, text):
        self.log.append(("paste", text))
        if self.focused_search and not self.open:
            self.query = text
        elif self.open:
            self.typed += text

    def focus_edit(self, h, rx):
        return False

    def sleep(self, s):
        pass


def _fresh_messengers():
    from core import messengers as M
    M.FILE = os.path.join(TMP, "messengers.json")
    if os.path.exists(M.FILE):
        os.remove(M.FILE)
    M._s.update(pending=None, said=None)
    return M


@test
def messengers_parse():
    M = _fresh_messengers()
    assert M.intent("ответь маме в телеграме, что опоздаю на 10 минут") == \
        ("reply", "telegram", "маме", "опоздаю на 10 минут", None)
    assert M.intent("напиши Алиму в ватсапе: буду в 7") == ("reply", "whatsapp", "Алиму", "буду в 7", "буду в 7")
    assert M.intent("напиши «привет» Бекзату в телеграм")[2:] == ("Бекзату", "", "привет")
    assert M.intent("спроси Дану в инсте, когда сдавать проект")[1:4] == ("instagram", "Дану",
                                                                         "спроси когда сдавать проект")
    assert M.intent("ответь ему, что согласен") == ("reply_here", None, "согласен", None)
    assert M.intent("что написала мама в телеграме?") == ("read", "telegram", "мама")
    assert M.intent("что мне пишут в телеграме") == ("read", "telegram", "")
    assert M.intent("text mom on whatsapp that I am late")[1:4] == ("whatsapp", "mom", "I am late")
    assert M.stems("Маме") == ["мам"] and M.stems("Алиму Сыдыкову") == ["алим", "сыдыков"]
    assert M.line_matches("Мама ❤", ["мам"]) and not M.line_matches("Мадина", ["мам"])
    for other in ("напиши письмо маме", "скажи что-нибудь смешное", "tell me a joke",
                  "отправь файл на телефон", "да"):
        assert M.intent(other) is None, other
    # «напиши маме/Алиму» без названия мессенджера — теперь берёт мессенджер (а не Блокнот), приложение выберем потом
    assert M.intent("напиши маме что я дома") == ("reply", None, "маме", "я дома", None)
    assert M.intent("Напиши Алиму, что буду в 7")[:3] == ("reply", None, "Алиму")


@test
def messengers_reply_confirm_send():
    M = _fresh_messengers()
    ui, asked = FakeTG(), []

    def ask(system, user, max_tokens=0):
        asked.append(user)
        return "«Мам, опоздаю минут на 10»" if "Change it" not in user else "мам опоздаю на 10 мин"
    said = M.handle("ответь маме в телеграме, что опоздаю на 10 минут", ui=ui, ask=ask)
    assert said == "Пишу в Telegram, чат «Мама ❤»: «Мам, опоздаю минут на 10». Отправить?", said
    assert ("click", 145, 130) in ui.log and ui.open == "Мама ❤", ui.log          # чат был виден в списке
    assert "Мама ❤: Ты где?" in asked[0] and "Я: щас буду" in asked[0] and "Мама ❤: Купи хлеб" in asked[0], asked[0]
    assert not ui.sent, "без «да» — не отправляем"
    assert M.intent("да", last="что-то другое") is None
    said = M.handle("сделай короче", last=said, ui=ui, ask=ask)
    assert said.endswith("«Мам опоздаю на 10 мин». Отправить?"), said
    out = M.handle("да", last=said, ui=ui)
    assert ui.sent == [("Мама ❤", "Мам опоздаю на 10 мин")] and out == "Отправил.", (ui.sent, out)
    assert M.handle("да", last=said, ui=ui) is None, "черновик уже отправлен"
    assert M.known_app("маму") == "telegram"
    assert M.intent("напиши маме что я дома")[1] == "telegram", "мама — в Телеграме, запомнили"


@test
def messengers_search_exact_cancel_here_read():
    M = _fresh_messengers()
    ui = FakeTG(chats={**{f"Канал {i}": [("them", "реклама")] for i in range(8)},
                       "Алим Сыдыков": [("them", "Завтра в 7?")]})
    ui.chats = dict(list(ui.chats.items()))
    ui.ocr_limit = 3
    orig = ui.ocr

    def ocr_first_three(rect):                                   # в списке видно только 3 верхних чата
        res = orig(rect)
        if ui.query is None:
            res["ru"] = [ln for ln in res["ru"] if ln["y"] < 330 or ln["x"] > 300]
        return res
    ui.ocr = ocr_first_three
    said = M.handle("напиши Алиму в телеграме: да, буду", ui=ui, ask=lambda *a, **k: 1 / 0)
    assert ("paste", "алим") in ui.log and ui.open == "Алим Сыдыков", ui.log
    assert said == "Пишу в Telegram, чат «Алим Сыдыков»: «Да, буду». Отправить?", said
    assert M.handle("нет", last=said, ui=ui) == "Не отправляю." and not ui.sent
    said = M.handle("напиши Бекзату в телеграме что привет", ui=FakeTG(), ask=lambda *a, **k: "привет")
    assert said.startswith("Не нашёл чат «Бекзату»"), said
    ui = FakeTG()
    ui.open = "Дана"
    said = M.handle("ответь ей, что согласна", ui=ui, ask=lambda s, u, max_tokens=0: "согласна!")
    assert said == "Пишу в Telegram, чат «Дана»: «Согласна!». Отправить?", said
    assert M.handle("ответь на это", ui=FakeTG(top=False)) is None, "не в мессенджере — не наше"
    out = M.handle("что написала мама в телеграме", ui=FakeTG(),
                   ask=lambda s, u, max_tokens=0: "Мама спрашивает, где ты, и просит купить хлеб.")
    assert out == "Мама спрашивает, где ты, и просит купить хлеб.", out
    seen = []
    M.handle("что мне пишут в телеграме", ui=FakeTG(), ask=lambda s, u, max_tokens=0: seen.append(u) or "Мама и Алим.")
    assert "Купи хлеб" in seen[0] and "Алим Сыдыков" in seen[0], seen
    ig = FakeTG(app="chrome", title="Входящие • Instagram", chats={"dana.k": [("them", "hi")]}, web=True)
    out = M.handle("спроси Айдара в инсте, когда тренировка", ui=ig, ask=lambda *a, **k: "?")
    assert out.startswith("Не вижу чат «Айдара» в списке Instagram"), out


@test
def messengers_read_sides():
    M = _fresh_messengers()
    ui = FakeTG()
    ui.open = "Мама ❤"
    conv = M.read_chat(5, False, ui)
    assert conv == [("them", "Ты где?"), ("me", "щас буду"), ("them", "Купи хлеб")], conv


@test
def messengers_pick_app_when_none_named():
    """«Напиши маме» без мессенджера → берём открытый; если нет — спрашиваем, а не пишем в Блокнот."""
    M = _fresh_messengers()
    os.environ.pop("ATLAS_DEFAULT_MESSENGER", None)

    class NoMsg:                                              # на экране нет ни одного мессенджера
        def windows(self):
            return [{"hwnd": 9, "app": "Code", "title": "main.py", "min": False, "max": False, "rect": (0, 0, 9, 9)}]

    # 1) мессенджер открыт (Telegram) → сразу пишем туда, Блокнот не трогаем
    said = M.handle("напиши маме что скоро буду", ui=FakeTG(), ask=lambda s, u, max_tokens=0: "«Скоро буду»")
    assert said == "Пишу в Telegram, чат «Мама ❤»: «Скоро буду». Отправить?", said

    # 2) мессенджера нет и контакт незнаком → Atlas спрашивает, какой, и запоминает намерение
    M = _fresh_messengers()                                  # забываем «мам→telegram» из шага 1
    q = M.handle("напиши маме что скоро буду", ui=NoMsg(), ask=lambda *a, **k: 1 / 0)
    assert q.startswith("В каком мессенджере") and "маме" in q, q
    assert M._s["pending"]["need_app"] and M._s["pending"]["gist"] == "скоро буду"
    # 3) отвечаем «в телеграме» → продолжает в Telegram
    out = M.handle("в телеграме", last=q, ui=FakeTG(), ask=lambda s, u, max_tokens=0: "«Скоро буду»")
    assert out == "Пишу в Telegram, чат «Мама ❤»: «Скоро буду». Отправить?", out
    # 4) по умолчанию из .env — без вопроса
    M._s.update(pending=None, said=None)
    os.environ["ATLAS_DEFAULT_MESSENGER"] = "whatsapp"
    try:
        assert M._resolve_app("кому-то", NoMsg()) == "whatsapp"
    finally:
        os.environ.pop("ATLAS_DEFAULT_MESSENGER", None)


@test
def whatsapp_store_window_found_by_title():
    """WhatsApp из Microsoft Store: окно под ApplicationFrameHost — ловим по заголовку, и в окнах, и в мессенджерах."""
    from core import window_layout as W, messengers as M
    wins = [{"hwnd": 1, "app": "applicationframehost", "title": "WhatsApp", "min": False, "max": False,
             "rect": (0, 0, 800, 600)},
            {"hwnd": 2, "app": "chrome", "title": "YouTube — Chrome", "min": False, "max": False, "rect": (0, 0, 8, 8)}]
    assert (W.find({"app": "whatsapp"}, wins) or {}).get("hwnd") == 1, "окна: WhatsApp по заголовку"
    assert W.find({"app": "word"}, wins) is None, "не хватаем лишнее по заголовку"
    fw = M.find_window("whatsapp", wins)
    assert fw and fw["hwnd"] == 1 and fw["web"] is False, "мессенджеры: то же окно, не как веб"



@test
def messengers_never_write_to_someone_elses_mom():
    """«Напиши маме» не открывает «Маму Данияра», даже если её чат выше в списке."""
    M = _fresh_messengers()
    chats = {"Мама Данияра": [("them", "Даник дома?")], "Мама ❤": [("them", "Купи хлеб")], "Дана": [("them", "Привет")]}
    ui = FakeTG(chats=dict(chats))
    said = M.handle("напиши маме в телеграме что скоро буду", ui=ui, ask=lambda s, u, max_tokens=0: "«Скоро буду»")
    assert ui.open == "Мама ❤" and said == "Пишу в Telegram, чат «Мама ❤»: «Скоро буду». Отправить?", (ui.open, said)
    # мамы в списке нет совсем — только мама друга: не открываем, спрашиваем
    M = _fresh_messengers()
    ui = FakeTG(chats={"Мама Данияра": [("them", "Даник дома?")], "Дана": [("them", "Привет")]})
    said = M.handle("напиши маме в телеграме что скоро буду", ui=ui, ask=lambda *a, **k: 1 / 0)
    assert ui.open is None and "«Мама Данияра»" in said and "не мама" in said, (ui.open, said)
    assert not ui.sent
    # «маме Данияра» — тогда это и есть нужный чат
    M = _fresh_messengers()
    ui = FakeTG(chats=dict(chats))
    said = M.handle("напиши маме Данияра в телеграме что Даник у нас", ui=ui, ask=lambda s, u, max_tokens=0: "Даник у нас")
    assert ui.open == "Мама Данияра", (ui.open, said)


@test
def messengers_ambiguous_name_asks_which():
    M = _fresh_messengers()
    ui = FakeTG(chats={"Алим Сыдыков": [("them", "Завтра в 7?")], "Алим Касымов": [("them", "ок")]})
    said = M.handle("напиши Алиму в телеграме что буду в 7", ui=ui, ask=lambda s, u, max_tokens=0: "Буду в 7")
    assert ui.open is None and "«Алим Сыдыков»" in said and "«Алим Касымов»" in said, said
    out = M.handle("второму", last=said, ui=ui, ask=lambda s, u, max_tokens=0: "Буду в 7")
    assert ui.open == "Алим Касымов" and out.startswith("Пишу в Telegram, чат «Алим Касымов»"), (ui.open, out)
    M.handle("да", last=out, ui=ui)
    assert ui.sent == [("Алим Касымов", "Буду в 7")], ui.sent
    assert M.known_title("Алиму", "telegram") == "Алим Касымов", "подтвердил — запомнили, кто такой «Алим»"


@test
def messengers_teach_how_contact_is_saved():
    M = _fresh_messengers()
    out = M.handle("запомни, что мама у меня в телеграме записана как Мамуля")
    assert out == "Запомнил: мама в Telegram — это «Мамуля».", out
    ui = FakeTG(chats={"Мама Данияра": [("them", "?")], "Мамуля": [("them", "Ты где?")]})
    said = M.handle("напиши маме в телеграме что скоро буду", ui=ui, ask=lambda s, u, max_tokens=0: "Скоро буду")
    assert ui.open == "Мамуля" and "«Мамуля»" in said, (ui.open, said)


@test
def messengers_send_checks_the_chat_again():
    """Пока думал над «да», в окне открыли другой чат — не вставляем текст туда."""
    M = _fresh_messengers()
    ui = FakeTG()
    said = M.handle("напиши маме в телеграме что скоро буду", ui=ui, ask=lambda s, u, max_tokens=0: "Скоро буду")
    ui.open = "Дана"
    out = M.handle("да", last=said, ui=ui)
    assert not ui.sent and "другой чат" in out, (ui.sent, out)


@test
def messengers_write_literate():
    """Текст сообщения: типографика без модели и корректор, который не переписывает смысл."""
    M = _fresh_messengers()
    assert M._tidy("мам ,опоздаю на 10 минут - автобус сломался...", True) == "Мам, опоздаю на 10 минут — автобус сломался…"
    assert M._tidy('скинь "Дюну" плиз!!!', True) == "Скинь «Дюну» плиз!"
    assert M._tidy("ok see you at 7", False) == "Ok see you at 7"
    fixed = lambda s, u, max_tokens=0: "Мам, опоздаю минут на 10, автобус сломался"
    assert M._polish("мам опоздаю минут на 10 автобус сломался", True, fixed) == "Мам, опоздаю минут на 10, автобус сломался"
    rewrote = lambda s, u, max_tokens=0: "Дорогая мама! Сообщаю, что из-за поломки транспорта я задержусь."
    assert M._polish("мам опоздаю минут на 10", True, rewrote) == "мам опоздаю минут на 10", "переписал смысл — не берём"
    assert M._polish("мам опоздаю", True, lambda *a, **k: 1 / 0) == "мам опоздаю", "корректор упал — текст как был"
    # в живой работе (без ask из теста) черновик проходит корректор
    import core.quick_llm as Q
    calls, orig = [], Q.ask
    Q.ask = lambda s, u, max_tokens=0, **k: calls.append(s) or ("Алим, привет! Во сколько завтра тренировка?"
                                                               if "proofreader" in s else "алим привет во сколько завтра тренировка")
    try:
        out = M.draft("спроси во сколько завтра тренировка", [], "Алим", "telegram", True)
    finally:
        Q.ask = orig
    assert out == "Алим, привет! Во сколько завтра тренировка?" and any("proofreader" in c for c in calls), (out, calls)
    assert "запятой" in calls[0] and "Never copy the user's typos" in calls[0], "правила грамотности — в запросе"


@test
def not_found_then_i_opened_it_myself():
    """Лог 20.10: «напиши маме «тест 1»» — чат не нашёлся; «Чат открыт. Пиши тест 1.» ушло в общий мозг,
    тот напечатал и соврал «отправлен». Теперь — в открытый чат, с «Отправить?», и запоминаем, как записана мама."""
    M = _fresh_messengers()
    ui = FakeTG(chats={"Ана Сыдыкова": [("them", "?")], "Дана": [("them", "Привет")]})
    out = M.handle("напиши маме в телеграме «тест 1»", ui=ui)
    assert "открыл" in out and "«Мама»" in out, out
    ui.open, ui.query = "Ана Сыдыкова", None                        # открыл сам
    out = M.handle("Чат открыт. Пиши тест 1.", last=out, ui=ui)
    assert out.startswith("Пишу в Telegram, в открытый чат «Ана Сыдыкова»:") and "ест 1" in out, out
    M.handle("да", last=out, ui=ui)
    assert ui.sent and ui.sent[-1][0] == "Ана Сыдыкова", ui.sent
    assert M.known_title("маме", "telegram") == "Ана Сыдыкова", "в следующий раз найдёт сам"
    ui.open = None
    out = M.handle("напиши маме в телеграме «тест 2»", ui=ui)
    assert "«Ана Сыдыкова»" in out and "Отправить?" in out, out
    M._remember("маме", "telegram", "Мама старая запись")         # запомненное имя устарело — ищем заново, не глохнем
    ui.open = None
    out = M.handle("напиши маме в телеграме «тест 3»", ui=ui)
    assert "открыл" in out, out
    for phrase in ("открыл", "готово", "я открыл, напиши: буду в 7"):
        assert M.intent(phrase, last=out)[0] == "here", phrase
    assert M.intent("я открыл, напиши: буду в 7", last=out) == ("here", "буду в 7")



@test
def write_to_myself_and_yes_after_brain_rephrase():
    """Лог 20.10 20:16: «напиши мне самому привет» ушло в заметки; «отправляй» после пересказа мозгом не сработало."""
    M = _fresh_messengers()
    ui = FakeTG(app="WhatsApp.Root", title="WhatsApp",
                chats={"+996 707 050 809 (вы)": [("me", "старое")], "Мама ❤": [("them", "?")]})
    assert M.intent("Напиши мне самому привет.") == ("reply", None, "себе", "привет", None)
    out = M.handle("напиши мне самому в ватсап «Привет»", ui=ui)
    assert out == "Пишу в WhatsApp, чат «+996 707 050 809 (вы)»: «Привет». Отправить?", out
    brain_said = "Надо подтвердить отправку сообщения. Скажите «да» или «нет»."
    assert M.intent("Отправляй.", last=brain_said) == ("send",), "мозг пересказал вопрос — «да» всё равно наше"
    assert M.intent("Отправляй.", last="Погода: +5, ясно.") is None
    M.handle("Отправляй.", last=brain_said, ui=ui)
    assert ui.sent == [("+996 707 050 809 (вы)", "Привет")], ui.sent
    assert M.known_app("мне самому") == "whatsapp" and M.known_app("вы") == "whatsapp"


@test
def message_tools_never_claim_sent():
    M = _fresh_messengers()
    import re as _re
    import types
    tsrc = open(os.path.join(ROOT, "brain", "tools.py"), encoding="utf-8").read()
    expr = tsrc[tsrc.index("_TRACE_FAIL = ") + len("_TRACE_FAIL = "):tsrc.index("_trace_lock")]
    T = types.SimpleNamespace(_TRACE_FAIL=eval(expr, {"re": _re}))      # тот же признак неудачи, что видит мозг
    src = open(os.path.join(ROOT, "skills", "pc_power.py"), encoding="utf-8").read()
    assert "messengers.tool_send_message(" in src and "messengers.tool_confirm()" in src
    orig = M.tool_prepare, M.send
    try:
        M.tool_prepare = lambda *a: "Не нашёл чат «вы» в WhatsApp. Скажи, как контакт записан."
        r = M.tool_send_message("whatsapp", "вы", "Привет")
        assert r.startswith("NOT SENT") and T._TRACE_FAIL.search(r[:220]), r
        M.tool_prepare = lambda *a: (M._s.update(pending={"created": __import__("time").time(), "ru": True})
                                     or "Пишу в WhatsApp, чат «Вы»: «Привет». Отправить?")
        r = M.tool_send_message("whatsapp", "вы", "Привет")
        assert r.startswith("DRAFT READY — NOT SENT YET") and T._TRACE_FAIL.search(r[:220]), \
            "черновик в плане — не «всё готово»: done_message «отправлено» не прозвучит"
        for said, start in (("Отправил.", "SENT"), ("Вставил и нажал Enter — проверь, что ушло.", "PROBABLY SENT"),
                            ("Не получилось переключиться на WhatsApp — не отправил.", "NOT SENT")):
            M.send = lambda ui=None, s=said: s
            assert M.tool_confirm().startswith(start), said
    finally:
        M.tool_prepare, M.send = orig
        M._s["pending"] = None
    import system_control as S
    assert "whatsapp:" in S.APP_MAP["whatsapp"][0], "WhatsApp из Store — только по протоколу"


@test
def open_chat_of_who_sent_photo():
    M = _fresh_messengers()
    assert M.intent("Открой фотографию, что прислал папа.") == ("open", None, "папа", "фотографию")
    assert M.intent("открой чат с папой в ватсапе")[:3] == ("open", "whatsapp", "папой")
    assert M.intent("открой блокнот") is None and M.intent("открой фото") is None
    ui = FakeTG(chats={"Папа": [("them", "фото")], "Мама ❤": [("them", "?")]})
    out = M.handle("открой фото, что прислал папа в телеграме", ui=ui)
    assert out == "Открыл чат «Папа» в Telegram. Последнее — внизу." and ui.open == "Папа", out



@test
def whatsapp_real_layout_by_structure():
    """Раскладка настоящего WhatsApp 1936×1048 (снято probe 20.10): список ~400 px, заголовок с x=463.
    По долям окна заголовок (x<572) выпадал — чат никогда не был «проверен», а реплики собеседника не читались."""
    M = _fresh_messengers()

    def el(t, x, y, w, h, kind="TextControl"):
        return {"text": t, "x": x, "y": y, "w": w, "h": h, "uia": kind}
    rect = (-8, -8, 1928, 1040)
    rows = [("+996 700 000 000", 179, "(Вы)"), ("Папа", 248, None), ("Мама", 316, None), ("Алибек", 384, None),
            ("Мама Данияра", 453, None)]
    uia = [el("Изменить размер панели списка чатов", 399, 34, 23, 998, "ButtonControl"),
           el("Поиск или новый чат", 121, 106, 128, 10)]
    for name, y, extra in rows:
        uia += [el(f"{name} 19:37 ? Закрепленный чат", 58, y, 332, 69, "DataItemControl"), el(name, 135, y + 13, 40, 19)]
        if extra:
            uia.append(el(extra, 255, y + 12, 31, 19))
    uia += [el("Мама", 463, 44, 40, 19), el("был(а) недавно", 463, 65, 115, 15),
            el("Поиск", 1819, 45, 37, 37, "ButtonControl"), el("Ты где?", 430, 600, 60, 16), el("щас", 1700, 650, 40, 16)]
    assert M.learn_divider(77, uia, rect) == 410
    rg = M._regions(rect, False, 77)
    assert [ln["text"] for ln in M._header(uia, rg)][:2] == ["Мама", "был(а) недавно"], "заголовок теперь виден"
    res = M._choose(uia, rg, M.stems("маме"), True)
    assert res[0] == "click" and res[1]["text"] == "Мама" and res[1]["y"] == 329, res
    assert M._choose(uia, rg, M.stems("Алибеку"), False)[1]["text"] == "Алибек"
    selfrow = [ln for ln in uia if M._SELF_CHAT.search(ln["text"]) and M._cx(ln) < rg["list_x"]]
    assert selfrow and selfrow[0]["y"] == 191
    assert M._regions(rect, False, 999)["chat_x"] == 572, "без структуры окна — как раньше"
    M._DIV.pop(77, None)


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
