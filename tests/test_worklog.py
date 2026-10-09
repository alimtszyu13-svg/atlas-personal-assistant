"""
Тесты памяти компьютера (core/worklog.py): запись окон, приватность, «что я делал»,
поиск по окнам и истории браузеров, «открой снова», «на чём я остановился».

    python tests/test_worklog.py

База — временная, история браузеров — подставные файлы; Windows не нужна.
"""
import os
import sqlite3
import sys
import tempfile
import time
import traceback
from datetime import datetime, timedelta

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import worklog as W  # noqa: E402

TMP = tempfile.mkdtemp(prefix="atlas_wl_")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def fresh():
    W.DB = os.path.join(TMP, f"m{len(os.listdir(TMP))}.db")
    return W.DB


def at(days_ago=0, hour=10, minute=0):
    d = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0) - timedelta(days=days_ago)
    return d.timestamp()


@test
def document_name_comes_from_the_window_title():
    assert W.doc_of("Эссе про климат.docx - Word") == "Эссе про климат.docx"
    assert W.doc_of("● main.py - atlas - Visual Studio Code") == "main.py"
    assert W.doc_of("SAT_practice_test_4.pdf — Adobe Acrobat") == "SAT_practice_test_4.pdf"
    assert W.doc_of("Khan Academy - Google Chrome") == ""


@test
def private_windows_keep_only_the_program():
    assert W.clean("Chrome", "Gmail — InPrivate - Microsoft Edge")[1] == ""
    assert W.clean("Chrome", "MBank — вход - Google Chrome")[1] == ""
    assert W.clean("KeePass", "Пароли.kdbx - KeePass")[1] == ""
    assert W.clean("Atlas", "ATLAS") is None and W.clean("", "x") is None


@test
def same_window_in_a_row_is_one_record():
    fresh()
    t = at(0, 9)
    for i in range(10):                                      # 10 наблюдений по 15 с — одна запись
        W.record("Word", "эссе.docx - Word", t + i * 15)
    W.record("Chrome", "Khan Academy - Google Chrome", t + 160)
    W.record("Word", "эссе.docx - Word", t + 175)
    rows = W._rows(t - 1, t + 1000)
    assert len(rows) == 3 and rows[0][1] - rows[0][0] == 135, rows


@test
def what_did_i_do_names_programs_documents_and_sites():
    fresh()
    t = at(1, 20)
    W.record("Word", "Эссе про климат.docx - Word", t)
    W.record("Word", "Эссе про климат.docx - Word", t + 40 * 60 - 60)  # разрыв > 90 с — новая запись
    for i in range(1, 160):
        W.record("Word", "Эссе про климат.docx - Word", t + i * 15)
    W.record("VS Code", "● main.py - atlas - Visual Studio Code", t + 3000)
    hist = lambda t0, t1: [(t + 100, "Chrome", "Linear equations | Khan Academy", "https://www.khanacademy.org/math/x"),
                           (t + 200, "Chrome", "lofi", "https://www.youtube.com/watch?v=1")]
    text = W.summary("yesterday", history=hist)
    assert text.startswith("Вчера: за компьютером") and "Word —" in text, text
    assert "Эссе про климат.docx (Word" in text and "khanacademy.org" in text and "youtube.com" in text, text
    assert "Nothing recorded" in W.summary("2020-01-01", history=lambda a, b: [])


@test
def periods_understand_weekdays_and_dates():
    now = datetime(2026, 10, 9, 15, 0)                       # пятница
    t0, t1, label = W.period_range("среда", now)
    assert datetime.fromtimestamp(t0).strftime("%Y-%m-%d") == "2026-10-07" and t1 - t0 == 86400
    t0, _, label = W.period_range("2026-10-01", now)
    assert label == "01.10"
    t0, _, label = W.period_range("вчера", now)
    assert datetime.fromtimestamp(t0).day == 8 and label == "вчера"
    assert W.period_range("пятница", now)[2] == "02.10", "сегодня пятница — значит, прошлая"


@test
def finds_documents_and_sites_by_words():
    fresh()
    t = at(2, 18)
    for i in range(20):
        W.record("Word", "Эссе про климат.docx - Word", t + i * 15)
    W.record("Chrome", "Новости - Google Chrome", t + 400)
    hist = lambda t0, t1: [(t + 500, "Chrome", "Climate change essay examples", "https://essays.example.com/climate"),
                           (t + 600, "Chrome", "Linear equations | Khan Academy", "https://www.khanacademy.org/x")]
    items = W.search("эссе про климат", history=hist)
    assert items[0]["kind"] == "doc" and items[0]["doc"] == "Эссе про климат.docx", items
    items = W.search("сайт про linear equations", history=hist)
    assert items[0]["kind"] == "site" and items[0]["url"] == "https://www.khanacademy.org/x", items
    assert "Nothing about" in W.search_text("квантовая гравитация", history=hist)


@test
def reopen_opens_the_document_or_the_site():
    fresh()
    t = at(1, 11)
    for i in range(20):
        W.record("Word", "Эссе про климат.docx - Word", t + i * 15)
    opened = []
    hist = lambda t0, t1: [(t + 900, "Chrome", "Linear equations | Khan Academy", "https://www.khanacademy.org/x")]
    r = W.reopen("эссе климат", history=hist, opener=lambda x: (opened.append(x), "C:/Users/me/Эссе про климат.docx")[1])
    assert opened == ["Эссе про климат.docx"] and r.startswith("Opened Эссе про климат.docx"), r
    r = W.reopen("linear equations", history=hist, opener=lambda x: opened.append(x))
    assert opened[-1] == "https://www.khanacademy.org/x" and "Khan Academy" in r, r
    r = W.reopen("эссе климат", history=hist, opener=lambda x: "")
    assert "not where it was" in r, "файл переместили — честно говорим"


@test
def remembers_where_you_left_off():
    fresh()
    t = at(1, 22)
    for i in range(20):
        W.record("Word", "Эссе про климат.docx - Word", t + i * 15)
    W.record("Chrome", "YouTube - Google Chrome", t + 400)
    w = W.last_work(before=t + 1000)
    assert w["doc"] == "Эссе про климат.docx" and w["app"] == "Word" and w["when"].startswith("вчера"), w
    fresh()
    W.record("Word", "мельком.docx - Word", t)
    assert W.last_work(before=t + 100) == {}, "мелькнул на 0 секунд — не считается"


@test
def forget_and_cleanup_remove_records():
    fresh()
    W.record("Word", "a.docx - Word", time.time() - 60)
    W.record("Word", "old.docx - Word", time.time() - 40 * 86400)
    assert W.cleanup() == 1, "старше 30 дней — удаляется"
    assert W.forget("today") == 1 and W._rows(0, time.time() + 1) == []


@test
def reads_chromium_and_firefox_history_copies():
    t = time.time() - 3600
    chrome = os.path.join(TMP, "History")
    c = sqlite3.connect(chrome)
    c.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    c.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER)")
    c.execute("INSERT INTO urls VALUES (1, 'https://www.khanacademy.org/x', 'Linear equations')")
    c.execute("INSERT INTO urls VALUES (2, 'https://online.mbank.kg/login', 'MBank')")
    c.execute("INSERT INTO visits (url, visit_time) VALUES (1, ?)", (int(t * 1e6) + 11644473600 * 1_000_000,))
    c.execute("INSERT INTO visits (url, visit_time) VALUES (2, ?)", (int(t * 1e6) + 11644473600 * 1_000_000,))
    c.commit()
    c.close()
    ff = os.path.join(TMP, "places.sqlite")
    c = sqlite3.connect(ff)
    c.execute("CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    c.execute("CREATE TABLE moz_historyvisits (id INTEGER PRIMARY KEY, place_id INTEGER, visit_date INTEGER)")
    c.execute("INSERT INTO moz_places VALUES (1, 'https://habr.com/ru/articles/1', 'Статья про Python')")
    c.execute("INSERT INTO moz_historyvisits (place_id, visit_date) VALUES (1, ?)", (int((t + 60) * 1e6),))
    c.commit()
    c.close()
    rows = W.browser_history(t - 10, t + 100, dbs=[("Chrome", chrome, "chromium"), ("Firefox", ff, "firefox")])
    assert [(r[1], r[3]) for r in rows] == [("Firefox", "https://habr.com/ru/articles/1"),
                                             ("Chrome", "https://www.khanacademy.org/x")], rows
    assert abs(rows[1][0] - t) < 1, "время Chrome переведено правильно"
    assert os.path.exists(chrome), "сам файл браузера не тронут"


@test
def workspace_saves_programs_documents_and_pages_and_brings_them_back():
    from core import workspaces as WS
    db = fresh()
    now = time.time()
    hist = lambda t0, t1: [(now - 60, "Chrome", "Linear equations | Khan Academy", "https://www.khanacademy.org/x")]
    wins = [{"exe": "C:/Program Files/Google/Chrome/chrome.exe", "app": "chrome.exe",
             "title": "Linear equations | Khan Academy - Google Chrome"},
            {"exe": "C:/Office/WINWORD.EXE", "app": "WINWORD.EXE", "title": "Эссе про климат.docx - Word"},
            {"exe": "C:/Users/me/AppData/Local/Programs/Spotify/Spotify.exe", "app": "Spotify.exe", "title": "Spotify Premium"},
            {"exe": "C:/Windows/explorer.exe", "app": "explorer.exe", "title": "Загрузки"},
            {"exe": "C:/Program Files/Google/Chrome/chrome.exe", "app": "chrome.exe", "title": "MBank - Google Chrome"},
            {"exe": "C:/Python/python.exe", "app": "python.exe", "title": "ATLAS"}]
    r = WS.save("Учёба", wins, history=hist)
    assert r.startswith("Saved workspace 'Учёба'") and "Эссе про климат.docx" in r and "Spotify" in r, r
    assert "MBank" not in r and "Загрузки" not in r, "банк и проводник не сохраняются"
    opened = []
    r = WS.open_("учеба", launcher=lambda it: (opened.append(it["kind"]), True)[1],
                 running={"c:/users/me/appdata/local/programs/spotify/spotify.exe"})
    assert opened == ["url", "doc"] and "Spotify" in r, (opened, r)  # Spotify уже открыт — не дублируем
    assert "No workspace 'игры'" in WS.open_("игры") and "учеба" in WS.open_("игры")
    assert WS.names() == ["учеба"] and "Say what to call" in WS.save("  ", wins)


@test
def tools_are_registered_for_the_brain():
    from core.skills import REGISTRY
    import skills.worklog  # noqa: F401
    for name in ("what_did_i_do", "find_past_activity", "reopen_from_history", "continue_last_work", "forget_activity",
                 "save_workspace", "open_workspace", "list_workspaces"):
        assert name in REGISTRY and REGISTRY[name]["group"] == "worklog", name
    import tool_router
    assert "worklog" in tool_router._detect("что я делал вчера вечером"), "вопрос находит память компьютера"
    assert "worklog" in tool_router._detect("где тот сайт про уравнения")


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
