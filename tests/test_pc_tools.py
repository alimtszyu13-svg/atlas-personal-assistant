"""
Файлы голосом, калькулятор, «где я это сохранил», «что это?», сценарии, общий быстрый путь ПК/телефона.

    python tests/test_pc_tools.py
"""
import os
import shutil
import sys
import tempfile
import time
import traceback
import zipfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TMP = tempfile.mkdtemp(prefix="atlas_tools_")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def _img(path, size=(1600, 1200), color=(200, 30, 30), alpha=False):
    from PIL import Image
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im = Image.new("RGBA" if alpha else "RGB", size, color + ((128,) if alpha else ()))
    if not alpha:
        import random
        px = im.load()
        rnd = random.Random(1)
        for _ in range(4000):
            px[rnd.randrange(size[0]), rnd.randrange(size[1])] = (rnd.randrange(255),) * 3
    im.save(path, quality=100) if path.lower().endswith(".jpg") else im.save(path)
    return path


def _pdf(path, pages=2):
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    with open(path, "wb") as f:
        w.write(f)
    return path


class FEnv:
    def __init__(self, home, sel=(), folder=""):
        self.home, self.sel, self.folder = home, list(sel), folder

    def selection(self):
        return list(self.sel)

    def explorer_folder(self):
        return self.folder

    def ffmpeg(self):
        exe = shutil.which("ffmpeg")
        if exe:
            return exe
        try:
            import imageio_ffmpeg                      # так ffmpeg стоит у Atlas на Windows
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return ""

    def office_to_pdf(self, src, dst):
        open(dst, "wb").write(b"%PDF-1.4")
        return True


# --- файлы -----------------------------------------------------------------------------------------
@test
def files_intents():
    from core import file_ops as F
    want = {"сожми эти фото": "compress_img", "сожми фото в Загрузках": "compress_img",
            "сожми это видео": "compress_video", "сделай из видео mp3": "mp3", "вытащи звук из видео": "mp3",
            "переведи эти картинки в jpg": "convert_img", "сделай из фото pdf": "img2pdf", "склей эти pdf": "merge",
            "сделай pdf из ворда": "office2pdf", "заархивируй эту папку": "zip", "запакуй эти файлы в zip": "zip",
            "распакуй архив": "unzip", "переименуй фото по дате": "rename", "верни названия": "undo",
            "convert these images to png": "convert_img"}
    for t, do in want.items():
        assert (F.intent(t) or {}).get("do") == do, (t, F.intent(t))
    for t in ("сожми текст", "переведи текст на английский", "сделай скриншот", "сожми выделенное",
              "сделай погромче"):
        assert F.intent(t) is None, t


@test
def files_images_pdf_zip_rename():
    from core import file_ops as F
    home = os.path.join(TMP, "home")
    dl = os.path.join(home, "Downloads")
    a = _img(os.path.join(dl, "IMG_1.jpg"))
    b = _img(os.path.join(dl, "IMG_2.png"), size=(3000, 2000))
    c = _img(os.path.join(dl, "logo.png"), size=(400, 400), alpha=True)
    env = FEnv(home, sel=[a, b])
    out = F.run(F.intent("сожми эти фото"), env, wait=True)
    assert out.startswith("Сжал 2 фото — было") and "«Сжатые»" in out and "оригиналы на месте" in out, out
    sq = sorted(os.listdir(os.path.join(dl, "Сжатые")))
    assert sq == ["IMG_1.jpg", "IMG_2.jpg"], sq
    from PIL import Image
    assert max(Image.open(os.path.join(dl, "Сжатые", "IMG_2.jpg")).size) == 2560
    assert os.path.exists(a) and os.path.exists(b), "оригиналы не трогаем"
    out = F.run(F.intent("сожми фото в Загрузках"), FEnv(home), wait=True)
    assert out.startswith("Сжал 3 фото"), out
    assert "logo.png" in os.listdir(os.path.join(dl, "Сжатые")), "прозрачная — остаётся png"
    out = F.run(F.intent("переведи эти картинки в webp"), env, wait=True)
    assert os.path.exists(os.path.join(dl, "IMG_1.webp")) and "2 картинки" in out, out
    out = F.run(F.intent("сделай из фото pdf"), env, wait=True)
    from pypdf import PdfReader
    assert len(PdfReader(os.path.join(dl, "Фото.pdf")).pages) == 2 and "из 2 картинок" in out, out
    p1, p2 = _pdf(os.path.join(dl, "a.pdf"), 2), _pdf(os.path.join(dl, "b.pdf"), 3)
    out = F.run(F.intent("склей эти pdf"), FEnv(home, sel=[p2, p1]), wait=True)
    assert len(PdfReader(os.path.join(dl, "Объединённый.pdf")).pages) == 5 and "5 страниц" in out, out
    assert "хотя бы два" in F.run(F.intent("склей эти pdf"), FEnv(home, sel=[p1]), wait=True)
    folder = os.path.join(home, "Desktop", "Проект")
    _img(os.path.join(folder, "x.jpg"), size=(50, 50))
    open(os.path.join(folder, "notes.txt"), "w").write("hi")
    out = F.run(F.intent("заархивируй эту папку"), FEnv(home, folder=folder), wait=True)
    zp = os.path.join(home, "Desktop", "Проект.zip")
    assert os.path.exists(zp) and "Запаковал 2 файла" in out, out
    assert sorted(zipfile.ZipFile(zp).namelist()) == ["Проект/notes.txt", "Проект/x.jpg"]
    out = F.run(F.intent("распакуй архив"), FEnv(home, sel=[zp]), wait=True)
    assert os.path.exists(os.path.join(home, "Desktop", "Проект (2)", "Проект", "notes.txt")), out
    r1 = _img(os.path.join(home, "Pictures", "IMG_9.jpg"), size=(40, 40))
    os.utime(r1, (time.mktime((2025, 7, 1, 10, 0, 5, 0, 0, -1)),) * 2)
    out = F.run(F.intent("переименуй фото по дате"), FEnv(home, sel=[r1]), wait=True)
    assert os.listdir(os.path.join(home, "Pictures")) == ["2025-07-01 10-00-05.jpg"], out
    assert "Вернул прежние названия (1)" in F.run(F.intent("верни названия"), FEnv(home), wait=True)
    assert os.listdir(os.path.join(home, "Pictures")) == ["IMG_9.jpg"]
    assert "Выдели файлы в Проводнике" in F.run(F.intent("сожми эти фото"), FEnv(home), wait=True)
    assert F.free_name(a).endswith("IMG_1 (2).jpg")


@test
def files_video_audio_office():
    from core import file_ops as F
    exe = FEnv("").ffmpeg()
    home = os.path.join(TMP, "home2")
    vid = os.path.join(home, "Videos", "лекция.mp4")
    os.makedirs(os.path.dirname(vid))
    if not exe:
        open(vid, "wb").write(b"0" * 1000)               # без ffmpeg — проверяем только запуск в фоне
    if exe:
        import subprocess
        subprocess.run([exe, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-f", "lavfi",
                        "-i", "sine=frequency=440", "-t", "2", "-c:v", "mpeg4", "-c:a", "aac", vid], check=True)
        out = F.run(F.intent("сделай из видео mp3"), FEnv(home, sel=[vid]), wait=True)
        assert out == "Готово: лекция.mp3." and os.path.getsize(os.path.join(home, "Videos", "лекция.mp3")) > 1000, out
        out = F.run(F.intent("сожми это видео"), FEnv(home, sel=[vid]), wait=True)
        assert "«лекция (сжато).mp4»" in out, out
    said = []
    out = F.run(F.intent("сделай из видео mp3"), FEnv(home, sel=[vid]), announce=said.append)
    assert out == "Взялся: 1 файл. Скажу, когда закончу.", out
    for _ in range(100):
        if said:
            break
        time.sleep(0.1)
    assert said and said[0].startswith("Готово: лекция (2).mp3") or not exe, said
    doc = os.path.join(home, "Documents", "эссе.docx")
    os.makedirs(os.path.dirname(doc))
    open(doc, "wb").write(b"x")
    out = F.run(F.intent("сделай pdf из ворда"), FEnv(home, sel=[doc]), wait=True)
    assert out == "Готово: эссе.pdf." and os.path.exists(os.path.join(home, "Documents", "эссе.pdf")), out


# --- калькулятор -------------------------------------------------------------------------------------
@test
def quick_math_answers():
    from core import quick_math as Q
    d = date(2026, 10, 12)
    cases = {
        "сколько будет 15% от 34 тысяч": "5 100.", "34 тысячи плюс 15 процентов": "39 100.",
        "2 в степени 10": "1 024.", "корень из 144": "12.", "сколько будет 17 умножить на 23": "391.",
        "200 долларов в сомах": "200 USD ≈ 17 500 KGS.", "5 миль в километры": "5 миль = 8,05 км.",
        "100 кг в фунтах": "100 кг = 220,46 фунтов.", "переведи 2 гигабайта в мегабайты": "2 ГБ = 2 048 МБ.",
        "сколько дней до 1 декабря": "До 1 декабря — 50 дней.", "сколько дней до нового года": "До 1 января — 81 день.",
        "какой день будет через 45 дней": "Четверг, 26 ноября 2026.", "какой процент составляет 30 от 120": "25%.",
        "сколько будет полтора миллиона разделить на 3": "500 000.", "what is 12 times 7": "84.",
        "сколько будет 10 / 0": "На ноль делить нельзя.", "сколько будет 34 000 * 2": "68 000.",
    }
    for t, want in cases.items():
        got = Q.answer(t, today=d, rate=lambda a, b: 87.5)
        assert got == want, (t, got)
    for t in ("сколько стоит айфон", "поставь хром слева", "сожми фото в 2 раза", "включи 5 серию",
              "сделай громкость 50", "поставь таймер на 5 минут", "переведи текст на английский", "сколько времени"):
        assert Q.answer(t, today=d) is None, t
    try:
        Q.safe_eval("__import__('os')")
        raise AssertionError("eval небезопасен")
    except ValueError:
        pass
    try:
        Q.safe_eval("9 ** 9999")
        raise AssertionError("огромная степень")
    except ValueError:
        pass


# --- где сохранил ------------------------------------------------------------------------------------------
class WEnv:
    def __init__(self, home, now):
        self.home, self.t, self.opened, self.revealed = home, now, [], []

    def roots(self):
        h = self.home
        return [(os.path.join(h, "Downloads"), "в Загрузках", "in Downloads", 1),
                (os.path.join(h, "Documents"), "в Документах", "in Documents", 3)]

    def recent(self):
        return []

    def open(self, p):
        self.opened.append(p)

    def reveal(self, p):
        self.revealed.append(p)

    def now(self):
        return self.t


@test
def where_saved_finds_and_opens():
    from core import where_saved as W
    home = os.path.join(TMP, "home3")
    now = time.mktime((2026, 10, 12, 15, 0, 0, 0, 0, -1))
    files = {"Downloads/Реферат по истории.docx": now - 20 * 3600, "Downloads/реферат_старый.docx": now - 9 * 86400,
             "Documents/Учёба/Презентация климат.pptx": now - 2 * 3600, "Downloads/setup.exe": now - 600}
    for rel, t in files.items():
        p = os.path.join(home, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write("x")
        os.utime(p, (t, t))
    env = WEnv(home, now)
    said = W.handle("где реферат, который я вчера скачал?", env)
    assert said.startswith("«Реферат по истории.docx» — в Загрузках, вчера в 19:00. Открыть?"), said
    assert W.handle("да", env, last=said) == "Открываю." and env.opened[-1].endswith("Реферат по истории.docx")
    said = W.handle("куда я сохранил презентацию", env)
    assert "«Презентация климат.pptx» — в Документах (папка «Учёба»), сегодня в 13:00" in said, said
    assert W.handle("покажи в папке", env, last=said) == "Показал в папке." and env.revealed
    said = W.handle("что я последним скачал", env)
    assert said.startswith("«setup.exe» — в Загрузках, сегодня в 14:50"), said
    said = W.handle("где лежит реферат", env)
    assert "Реферат по истории" in said and "другой" in said, said
    said2 = W.handle("другой", env, last=said)
    assert said2.startswith("«реферат_старый.docx»"), said2
    out = W.handle("открой реферат который я вчера скачал", env)
    assert out.startswith("Открываю «Реферат по истории.docx»"), out
    assert W.handle("где находится Бишкек", env) is None
    assert W.handle("где лежит ключ от машины", env) is None, "не про файлы — к модели"
    assert W.handle("где доклад, который я скачал", env).startswith("Не нашёл")
    assert W.period("вчера утром", now)[0] == time.mktime((2026, 10, 11, 5, 0, 0, 0, 0, -1))


# --- что это? -------------------------------------------------------------------------------------------------
class CEnv:
    def cursor(self):
        return 500, 300

    def screen(self):
        return 0, 0, 1920, 1080

    def element(self, x, y):
        return {"name": "Commit", "type": "button", "window": "Visual Studio Code"}

    def ocr(self, box):
        assert box == (120, 210, 880, 390), box
        return [("en", [{"text": "Commit staged changes", "x": 420, "y": 290, "w": 200, "h": 20},
                        {"text": "Source Control", "x": 150, "y": 220, "w": 120, "h": 18}])]


@test
def what_is_under_cursor():
    from core import what_is as WI
    seen = WI.look(CEnv())
    assert seen["line"] == "Commit staged changes" and seen["word"] == "staged", seen
    got = []
    out = WI.explain(seen, True, ask=lambda s, u, max_tokens=0: got.append((s, u)) or "Сохраняет изменения в Git.")
    assert out == "Сохраняет изменения в Git." and "name «Commit»" in got[0][1] and "Russian" in got[0][0]
    for t in ("что это под курсором", "что это за кнопка", "что под мышкой?", "what's under the cursor"):
        assert WI.intent(t), t
    for t in ("что это", "что это такое за фильм", "что это значит"):
        assert not WI.intent(t), t
    assert "наведи мышь" in WI.explain({"element": {}, "line": ""})


# --- сценарии -----------------------------------------------------------------------------------------------------
@test
def scenarios_create_run_edit():
    from core import scenarios as S
    S.FILE = os.path.join(TMP, "scen.json")
    ran = []

    def runner(cmd):
        ran.append(cmd)
        return "Не нашёл приложение кинопоиск." if "кинопоиск" in cmd else "Готово."
    out = S.handle("создай режим кино: сверни всё, яркость 40, громкость 60 и открой кинопоиск", runner)
    assert out.startswith("Готово: «кино» — 4 шага: сверни всё; яркость 40; громкость 60; открой кинопоиск"), out
    out = S.handle("режим кино", runner)
    assert ran == ["сверни всё", "поставь яркость 40%", "поставь громкость 60%", "открой кинопоиск"], ran
    assert out == "Режим «кино» включён, но не вышло: открой кинопоиск.", out
    ran.clear()
    assert S.handle("включи режим кино", runner).startswith("Режим «кино» включён") and len(ran) == 4
    assert S.handle("добавь в режим кино: выключи уведомления", runner).startswith("Добавил. Теперь в «кино» 5")
    assert S.handle("что в режиме кино?", runner).startswith("«кино»: сверни всё;")
    assert S.handle("какие у меня режимы", runner) == "Твои режимы: кино."
    assert S.handle("режим учёбы", runner) is None, "нет такого — не наше"
    assert S.handle("включи музыку", runner) is None
    assert S.handle("удали режим кино", runner) == "Удалил режим «кино»."
    assert S.handle("режим кино", runner) is None


# --- общий быстрый путь --------------------------------------------------------------------------------------------
@test
def pc_fast_routes_and_phone():
    from core import pc_fast, scenarios as S, screen_snip, what_is
    S.FILE = os.path.join(TMP, "scen2.json")
    assert pc_fast.handle("сколько будет 15% от 200") == "30."
    assert pc_fast.handle("включи музыку") is None
    called = []
    orig_snip, orig_wi = screen_snip.snip, what_is.handle
    screen_snip.snip = lambda *a, **k: called.append("snip") or ("Скопировал: «x».", "x")
    what_is.handle = lambda *a, **k: called.append("what") or "Кнопка."
    try:
        assert pc_fast.handle("распознай текст с экрана", local=False) is None and not called, "с телефона — не трогаем мышь"
        assert pc_fast.handle("распознай текст с экрана") == "Скопировал: «x»." and called == ["snip"]
        assert pc_fast.handle("что это под курсором") == "Кнопка."
    finally:
        screen_snip.snip, what_is.handle = orig_snip, orig_wi
    S.create("тест", ["сколько будет 2 плюс 2"])
    assert pc_fast.handle("режим тест") == "Режим «тест» включён."
    assert pc_fast.is_question("Открыть?") and not pc_fast.is_question("Готово.")
    src = open(os.path.join(ROOT, "phone", "server.py"), encoding="utf-8").read()
    assert "pc_fast.handle(text, local=False" in src
    hk = open(os.path.join(ROOT, "selection_hotkey.py"), encoding="utf-8").read()
    assert 'add_hotkey("ctrl+alt+w"' in hk and what_is.HOTKEY_PHRASE in hk
    import skills.pc_power  # noqa: F401
    from core.skills import REGISTRY
    for n in ("batch_files", "find_saved_file", "explain_under_cursor", "save_scenario", "run_scenario"):
        assert n in REGISTRY, n
    for f in (".gitignore", ".dockerignore"):
        assert "atlas_scenarios.json" in open(os.path.join(ROOT, f), encoding="utf-8").read()


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
