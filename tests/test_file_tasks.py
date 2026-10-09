"""
Тесты дел с файлами по плану (core/file_plans.py): план → «да» → выполнено → «верни как было».

    python tests/test_file_tasks.py

Всё во временной папке; настоящие файлы не трогаются.
"""
import os
import sys
import tempfile
import time
import traceback

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import file_plans as F  # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def home_with_downloads():
    home = tempfile.mkdtemp(prefix="atlas_home_")
    dl = os.path.join(home, "Downloads")
    os.makedirs(os.path.join(home, "Documents"))
    os.makedirs(dl)
    old = time.time() - 2 * 86400
    for name in ("SAT_practice.pdf", "эссе_климат.docx", "оценки.xlsx", "фото.jpg", "setup.exe", "notes.zip", "странное.xyz"):
        p = os.path.join(dl, name)
        open(p, "w").write(name)
        os.utime(p, (old, old))
    open(os.path.join(dl, "свежий.pdf"), "w").write("x")                 # только что скачан — не трогаем
    open(os.path.join(dl, "кино.mp4.crdownload"), "w").write("x")        # ещё качается
    db = os.path.join(home, "m.db")
    return home, dl, db


@test
def tidy_plan_sorts_old_files_and_leaves_fresh_ones():
    home, dl, db = home_with_downloads()
    plan = F.plan_tidy("загрузки", db=db, home=home, question="разбери загрузки")
    cats = {os.path.basename(o["src"]): o["cat"] for o in plan["ops"]}
    assert cats == {"SAT_practice.pdf": "PDF", "эссе_климат.docx": "Документы", "оценки.xlsx": "Таблицы",
                    "фото.jpg": "Картинки", "setup.exe": "Установщики", "notes.zip": "Архивы",
                    "странное.xyz": "Прочее"}, cats
    text = F.describe(plan)
    assert text.startswith("PLAN (not done yet): move 7 files") and "Nothing is deleted" in text, text
    assert os.path.exists(os.path.join(dl, "SAT_practice.pdf")), "план ничего не двигает"


@test
def runs_only_after_a_new_yes():
    home, dl, db = home_with_downloads()
    F.plan_tidy("downloads", db=db, home=home, question="(Respond in Russian.) разбери загрузки")
    assert "not confirmed" in F.confirm("(Respond in Russian.) разбери загрузки", db=db), "просьба — не согласие"
    assert "not confirmed" in F.confirm("а что там будет?", db=db)
    r = F.confirm("(Respond in Russian.) (Said on the phone.) да, выполняй", db=db)
    assert r.startswith("Done: moved 7 files"), r
    assert os.path.exists(os.path.join(dl, "PDF", "SAT_practice.pdf"))
    assert os.path.exists(os.path.join(dl, "свежий.pdf")) and os.path.exists(os.path.join(dl, "кино.mp4.crdownload"))
    assert "no file plan" in F.confirm("да", db=db), "второй раз тот же план не выполняется"


@test
def undo_puts_everything_back():
    home, dl, db = home_with_downloads()
    F.plan_tidy("downloads", db=db, home=home, question="разбери")
    F.confirm("да", db=db)
    r = F.undo(db=db)
    assert r.startswith("Put back 7 files"), r
    assert os.path.exists(os.path.join(dl, "SAT_practice.pdf")) and not os.path.exists(os.path.join(dl, "PDF")), \
        "файлы на месте, пустые папки убраны"
    assert F.undo(db=db) == "Nothing to undo."


@test
def collect_copies_matching_files_and_keeps_originals():
    home, dl, db = home_with_downloads()
    desk = os.path.join(home, "Desktop")
    os.makedirs(os.path.join(desk, "школа"))
    open(os.path.join(desk, "школа", "Эссе_Литература.docx"), "w").write("e")
    open(os.path.join(desk, "эссе_климат.docx"), "w").write("эссе_климат.docx")  # та же копия, что в Загрузках
    plan = F.plan_collect("все мои эссе", "Эссе", dirs=[dl, desk], db=db, home=home, question="собери эссе")
    names = sorted(os.path.basename(o["src"]) for o in plan["ops"])
    assert names == ["Эссе_Литература.docx", "эссе_климат.docx"], names
    assert "copy 2 files into Documents/Эссе (originals stay" in F.describe(plan)
    F.confirm("давай", db=db)
    dest = os.path.join(home, "Documents", "Эссе")
    assert sorted(os.listdir(dest)) == ["Эссе_Литература.docx", "эссе_климат.docx"]
    assert os.path.exists(os.path.join(desk, "школа", "Эссе_Литература.docx")), "оригинал на месте"
    F.undo(db=db)
    assert not os.path.exists(dest) and os.path.exists(os.path.join(desk, "эссе_климат.docx"))


@test
def name_clash_never_overwrites():
    home, dl, db = home_with_downloads()
    os.makedirs(os.path.join(dl, "PDF"))
    open(os.path.join(dl, "PDF", "SAT_practice.pdf"), "w").write("старый")
    F.plan_tidy("downloads", db=db, home=home, question="разбери")
    F.confirm("да", db=db)
    assert open(os.path.join(dl, "PDF", "SAT_practice.pdf")).read() == "старый"
    assert os.path.exists(os.path.join(dl, "PDF", "SAT_practice (2).pdf"))


@test
def cancel_and_expiry():
    home, dl, db = home_with_downloads()
    F.plan_tidy("downloads", db=db, home=home, question="разбери")
    assert F.cancel(db=db).startswith("Cancelled") and "no file plan" in F.confirm("да", db=db)
    F.plan_tidy("downloads", db=db, home=home, now=time.time() - 3600, question="разбери")
    assert "no file plan" in F.confirm("да", db=db), "план старше 30 минут не выполняется"
    assert os.path.exists(os.path.join(dl, "SAT_practice.pdf"))
    empty = F.plan_collect("квантовая гравитация", dirs=[dl], db=db, home=home)
    assert F.describe(empty).startswith("No files")


@test
def tools_are_registered():
    from core.skills import REGISTRY
    import skills.file_tasks  # noqa: F401
    for n in ("plan_tidy_folder", "plan_collect_files", "confirm_file_plan", "cancel_file_plan", "undo_file_plan"):
        assert n in REGISTRY and REGISTRY[n]["group"] == "file_tasks", n
    import tool_router
    assert "file_tasks" in tool_router._detect("разбери загрузки")
    assert "file_tasks" in tool_router._detect("собери все эссе в одну папку")


def main():
    ok = 0
    for t in TESTS:
        try:
            t()
            ok += 1
            print(f"  ✓ {t.__name__}")
        except Exception:
            print(f"  ✗ {t.__name__}\n" + "".join("      " + ln for ln in traceback.format_exc().splitlines(True)[-6:]))
    print(f"\nТестов пройдено: {ok} из {len(TESTS)}")
    sys.stdout.flush()
    os._exit(0 if ok == len(TESTS) else 1)


if __name__ == "__main__":
    main()
