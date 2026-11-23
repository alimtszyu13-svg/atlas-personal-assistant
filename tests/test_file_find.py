"""
Поиск файла по имени: индекс → твои папки → диски, с лимитом времени.
Журнал 20.10: «открой WhatsApp» — 35 с обхода всех дисков и открыт случайный файл «WhatsApp Image…».

    python tests/test_file_find.py
"""
import os
import sqlite3
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

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def setup():
    from tests.test_voice_speed import load_voice
    load_voice()                                   # file_control импортирует voice — без звука и сети
    import file_control as F
    home = tempfile.mkdtemp(prefix="atlas_home_")
    disk = tempfile.mkdtemp(prefix="atlas_disk_")
    for d in ("Desktop", "Documents", "Downloads", "Projects/atlas/venv", "Projects/notes"):
        os.makedirs(os.path.join(home, d), exist_ok=True)
    open(os.path.join(home, "Documents", "Курсовая.docx"), "w").close()
    open(os.path.join(home, "Projects", "notes", "план.txt"), "w").close()
    open(os.path.join(home, "Projects", "atlas", "venv", "секрет.txt"), "w").close()
    os.makedirs(os.path.join(disk, "Games"), exist_ok=True)
    open(os.path.join(disk, "Games", "save.dat"), "w").close()
    F.SAFE_DIRS[:] = [os.path.join(home, d) for d in ("Desktop", "Documents", "Downloads")]
    F.os.path.expanduser = lambda p, h=home: p.replace("~", h)
    F._get_available_drives = lambda: [disk]
    F.INDEX_DB = os.path.join(home, "index.db")
    return F, home, disk


@test
def index_first_then_home_then_disk():
    F, home, disk = setup()
    assert F.find_file("Курсовая.docx").endswith("Курсовая.docx")
    assert F.find_file("курсовая").endswith("Курсовая.docx"), "без расширения тоже"
    assert F.find_file("план.txt").endswith("план.txt"), "весь профиль, не только 3 папки"
    assert F.find_file("секрет.txt") is None or "venv" not in F.find_file("секрет.txt"), "venv не обходим"
    assert F.find_file("save.dat").endswith("save.dat"), "другой диск — точное имя"
    assert F.find_file("sav") is None, "частичное совпадение на чужом диске не открываем"
    c = sqlite3.connect(F.INDEX_DB)
    c.execute("CREATE TABLE files (path TEXT PRIMARY KEY, mtime REAL, name TEXT)")
    real = os.path.join(home, "Documents", "Курсовая.docx")
    c.execute("INSERT INTO files VALUES (?,?,?)", (real, 0, "Курсовая.docx"))
    c.execute("INSERT INTO files VALUES (?,?,?)", ("/нет/такого/отчёт.docx", 0, "отчёт.docx"))
    c.commit()
    c.close()
    assert F.find_file("Курсовая.docx") == real
    assert F.find_file("отчёт.docx") is None, "удалённый файл из индекса не возвращаем"


@test
def never_walks_forever():
    F, home, disk = setup()
    F.HOME_BUDGET_S, F.DISK_BUDGET_S = 0.2, 0.3
    real_scandir, real_isdir = os.scandir, os.path.isdir

    class Entry:                                     # «бесконечный диск»: в каждой папке ещё 3 папки
        def __init__(self, path):
            self.path, self.name = path, os.path.basename(path)

        def is_dir(self, follow_symlinks=False):
            return True

    class Slow:
        def __init__(self, d):
            self.d = d

        def __enter__(self):
            time.sleep(0.005)
            return iter([Entry(os.path.join(self.d, f"x{i}")) for i in range(3)])

        def __exit__(self, *a):
            return False
    F.os.scandir = Slow
    F.os.path.isdir = lambda d: True
    try:
        t = time.time()
        assert F.find_file("нет_такого_файла.txt") is None
        assert time.time() - t < 1.5, time.time() - t
    finally:
        F.os.scandir, F.os.path.isdir = real_scandir, real_isdir


@test
def shallow_files_first():
    F, home, disk = setup()
    deep = os.path.join(home, "Desktop", *[f"d{i}" for i in range(30)])
    os.makedirs(deep)
    for i in range(200):
        open(os.path.join(deep, f"f{i}.txt"), "w").close()
    os.makedirs(os.path.join(home, "atlas"))
    open(os.path.join(home, "atlas", "requirements.txt"), "w").close()
    seen = []
    real = os.scandir

    def counting(d):
        seen.append(d)
        return real(d)
    F.os.scandir = counting
    try:
        assert F.find_file("requirements.txt").endswith(os.path.join("atlas", "requirements.txt"))
    finally:
        F.os.scandir = real
    assert len(seen) < 15, f"заглянул в {len(seen)} папок — должен найти на втором уровне"


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
