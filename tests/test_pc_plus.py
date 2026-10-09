"""
Диктовка, история буфера, фокус, конспекты, напоминания по ситуации — без окон, микрофона и сети.

    python tests/test_pc_plus.py
"""
import os
import sys
import tempfile
import time
import traceback
import types
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np  # noqa: E402

TMP = tempfile.mkdtemp(prefix="atlas_pcplus_")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class FakeWin:
    """Окно, в которое «печатает» диктовка."""

    def __init__(self, hwnd=42, title="Документ1 - Word"):
        self.hwnd, self.title, self.text, self.fg, self.log = hwnd, title, "", hwnd, []

    def foreground(self):
        return self.fg

    def is_atlas(self, h):
        return h == 7

    def title_of(self, h):
        return self.title

    def focus(self, h):
        self.fg = h
        return h == self.hwnd

    def paste(self, t):
        self.text += t

    def keys(self, combo, times=1):
        self.log.append((combo, times))
        for _ in range(times):
            if combo == "backspace":
                self.text = self.text[:-1]
            elif combo == "shift+enter":
                self.text += "\n"


# --- диктовка ---------------------------------------------------------------
@test
def dictation_types_fixes_and_stops():
    from core import dictation as D
    w = FakeWin()
    assert D.is_start("Атлас, пиши за мной") and D.is_start("Начни диктовку") and not D.is_start("пиши эссе")
    assert "Документ1" in D.start(io=w, lang="ru") and D.active()
    assert D.handle("Привет, Маша.") is None
    assert D.handle("Завтра встречаемся в пять") is None
    assert D.handle("и не опаздывай.") is None
    assert w.text == "Привет, Маша. Завтра встречаемся в пять и не опаздывай.", repr(w.text)
    assert D.handle("Сотри последнее.") is None
    assert w.text == "Привет, Маша. Завтра встречаемся в пять", repr(w.text)
    D.handle("Новая строка")
    D.handle("Пока.")
    assert w.text == "Привет, Маша. Завтра встречаемся в пять\nПока.", repr(w.text)
    from core import quick_llm
    saved = quick_llm.ask
    quick_llm.ask = lambda system, text, **k: "Здравствуйте, Мария! Завтра встречаемся в пять.\nДо свидания."
    try:
        assert D.handle("Сделай вежливее") == "Переписал."
    finally:
        quick_llm.ask = saved
    assert w.text == "Здравствуйте, Мария! Завтра встречаемся в пять.\nДо свидания.", repr(w.text)
    assert D.handle("Хватит").startswith("Диктовка закончена") and not D.active()


@test
def dictation_send_presses_enter_and_needs_a_real_window():
    from core import dictation as D
    w = FakeWin()
    D.start(io=w, lang="ru")
    D.handle("Скоро буду.")
    assert D.handle("Отправь") == "Отправил." and w.log[-1] == ("enter", 1) and not D.active()
    w.fg = 7                                                  # курсор в окне самого Atlas
    D._s["hwnd"] = 0
    assert "Поставь курсор" in D.start(io=w, lang="ru") and not D.active()
    w.fg = 42
    D.start(io=w, lang="ru")
    w.hwnd, w.fg = 99, 100                                    # окно закрыли — вернуться некуда
    assert "закончил" in D.handle("ещё текст") and not D.active()


# --- буфер обмена -----------------------------------------------------------
@test
def clipboard_keeps_text_but_not_passwords():
    from core import clipboard_history as C
    db = os.path.join(TMP, "clip1.db")
    assert C.add("https://khanacademy.org/math/algebra", "Chrome", db=db)
    assert not C.add("https://khanacademy.org/math/algebra", "Chrome", db=db), "повтор подряд не пишем"
    assert not C.add("Xk9$mP2qL!vw", "Chrome", db=db), "похоже на пароль"
    assert not C.add("sk-proj-abcdefghijklmnop123456", "Code", db=db), "ключ API"
    assert not C.add("любой текст", "KeePassXC", db=db), "из менеджера паролей"
    C.ignore("текст диктовки")
    assert not C.add("текст диктовки", "Word", db=db), "вставка самого Atlas"
    assert C.add("Эссе про климат: вступление", "Word", db=db)
    assert C.add("+996 555 123 456", "Telegram", db=db)
    assert C.kind_of("+996 555 123 456") == "phone" and C.kind_of("a@b.kg") == "email"


@test
def clipboard_search_by_kind_words_and_time():
    from core import clipboard_history as C
    db = os.path.join(TMP, "clip2.db")
    now = datetime(2026, 10, 9, 15, 0)
    C.add("https://old.example.com", "Chrome", ts=(now - timedelta(days=1, hours=2)).timestamp(), db=db)
    C.add("https://desmos.com/calculator", "Chrome", ts=now.replace(hour=9).timestamp(), db=db)
    C.add("Встреча в 18:00 у фонтана", "Telegram", ts=(now - timedelta(minutes=55)).timestamp(), db=db)
    C.add("+996 700 111 222", "Telegram", ts=(now - timedelta(minutes=5)).timestamp(), db=db)
    r = C.search("ссылку, которую я копировал утром", db=db, now=now)
    assert [i["text"] for i in r] == ["https://desmos.com/calculator"], r
    r = C.search("что я копировал час назад", db=db, now=now)
    assert r and r[0]["text"].startswith("Встреча"), r
    r = C.search("ссылка", "вчера", db=db, now=now)
    assert [i["text"] for i in r] == ["https://old.example.com"], r
    r = C.search("фонтан", db=db, now=now)
    assert len(r) == 1 and "фонтана" in r[0]["text"]
    assert C.search("номер", db=db, now=now)[0]["text"] == "+996 700 111 222"
    assert "Telegram" in C.describe(r) and C.forget("", db=db) == 4 and C.search("", db=db, now=now) == []


# --- фокус -------------------------------------------------------------------
@test
def focus_blocks_distractions_but_respects_allowed():
    from core import focus as F
    F.start(30, "телеграм можно", run=False)
    assert F.check("Discord.exe", "Discord")[0] == "app"
    assert F.check("Telegram.exe", "Telegram")[0] is None, "разрешили"
    assert F.check("chrome.exe", "Котики - YouTube - Google Chrome") == ("site", "YouTube")
    assert F.check("chrome.exe", "Khan Academy - Google Chrome")[0] is None
    assert F.check("WINWORD.EXE", "Эссе.docx - Word")[0] is None
    done = []
    F._s["announce"] = None
    t = time.time()
    assert F.tick(fg=lambda: (5, "chrome.exe", "Shorts - YouTube"), idle=0, act=lambda w, h: done.append(w), now=t)
    assert done == ["site"] and F._s["blocked"] == {"YouTube": 1}
    for i in range(30):                                        # минута работы в Word
        F.tick(fg=lambda: (6, "WINWORD.EXE", "Эссе"), idle=1, act=lambda w, h: None, now=t + i * 2)
    F.tick(fg=lambda: (6, "WINWORD.EXE", "Эссе"), idle=600, act=None, now=t + 70)   # отошёл — не работа
    assert F.active() and abs(F._s["worked"] - 60) < 0.1, F._s["worked"]
    assert F.tick(fg=lambda: (6, "x", "y"), idle=0, act=None, now=F._s["until"] + 1) == "finished"
    assert not F.active() and "YouTube 1" in F._s["summary"], F._s["summary"]


@test
def focus_stop_and_limits():
    from core import focus as F
    assert "50 minutes" in F.start(0, run=False)
    assert "240 minutes" in F.start(999, run=False)
    assert F.stop().startswith("Focus stopped early") and not F.active()
    assert F.stop() == "No focus session is running."


# --- конспекты --------------------------------------------------------------
@test
def meeting_is_recorded_transcribed_and_summarized():
    from core import meeting_notes as M
    M.folder = lambda: TMP
    loud = (np.sin(np.arange(16000 * 3) / 3) * 0.3).astype(np.float32)
    silent = np.zeros(16000 * 3, dtype=np.float32)

    def recorder(on_chunk, stop, with_mic):
        on_chunk(silent, time.time())                            # тишина — не распознаём
        on_chunk(loud, M._s["started"] + 10)
        on_chunk(loud, M._s["started"] + 200)
        stop.wait(2)
    texts = iter([[(0.0, "Дедлайн по проекту — пятница."), (5.0, "Маша делает слайды.")],
                  [(1.0, "Созвон в понедельник в десять.")]])
    saved = M._transcribe
    M._transcribe = lambda audio, path: next(texts)
    try:
        assert "Recording started" in M.start_recording("Созвон с командой", True, recorder=recorder)
        assert M.active() and "Already recording" in M.start_recording("ещё")
        time.sleep(0.3)
        notes = "## Коротко\nОбсудили проект, дедлайн в пятницу.\n\n## Задачи\n- [ ] Маша — слайды — пятница"
        r = M.stop_recording(ask=lambda system, text, **k: notes)
    finally:
        M._transcribe = saved
    assert "Созвон с командой" in r and "дедлайн в пятницу" in r, r
    it = M.recordings()[-1]
    txt = open(it["txt"], encoding="utf-8").read()
    assert txt.splitlines() == ["[00:10] Дедлайн по проекту — пятница.", "[00:15] Маша делает слайды.",
                                "[03:21] Созвон в понедельник в десять."], txt
    assert "- [ ] Маша" in open(it["md"], encoding="utf-8").read()
    got = {}
    ans = M.ask_recording("что сказали про дедлайн?", ask=lambda s, t, **k: (got.update(t=t), "В пятницу.")[1])
    assert ans == "В пятницу." and "Дедлайн" in got["t"] and "Созвон с командой" in M.status()
    assert M.notes_file("созвон") == it["md"] and not M.active()
    assert M.stop_recording() == "Nothing is being recorded right now."


@test
def audio_helpers_resample_and_mix():
    from core import meeting_notes as M
    stereo = np.ones((48000, 2), dtype=np.float32) * 0.5
    mono = M.to_16k_mono(stereo, 48000)
    assert len(mono) == 16000 and abs(float(mono.mean()) - 0.5) < 1e-4
    assert np.all(M.mix(mono, mono) <= 1.0) and not M.loud_enough(np.zeros(100, dtype=np.float32))


# --- напоминания по ситуации -------------------------------------------------
@test
def context_reminders_fire_once_on_the_right_window():
    from core import context_reminders as R
    R.FILE = os.path.join(TMP, "ctx.json")
    assert "when когда открою Word opens" in R.add("проверить эссе", "когда открою Word")
    R.add("написать Маше", "когда зайду в телеграм")
    R.add("про SAT", "когда сяду за компьютер")
    assert R.due("Chrome", "Google", 0) == []
    fired = R.due("Word", "Эссе.docx - Word", 0)
    assert [f["text"] for f in fired] == ["проверить эссе"]
    assert R.due("Word", "Эссе.docx - Word", 0) == [], "один раз"
    assert R.due("Telegram", "Маша", 30)[0]["text"] == "написать Маше"
    assert R.due("Chrome", "x", 60) == [] and R.due("Chrome", "x", 15 * 60)[0]["text"] == "про SAT"
    R.add("купить хлеб", "когда открою Notion")
    assert "купить хлеб" in R.listing() and "Cancelled 1" in R.cancel("хлеб") and R.listing() == "No context reminders."


@test
def skills_are_registered_and_hidden_in_the_cloud():
    for name in ("pygame", "groq", "edge_tts", "elevenlabs", "elevenlabs.client", "sounddevice"):
        sys.modules.setdefault(name, types.ModuleType(name))
    from core.skills import REGISTRY
    import skills.pc_plus  # noqa: F401
    names = {"start_dictation", "clipboard_history_search", "paste_from_clipboard_history", "start_focus",
             "start_recording", "stop_recording", "ask_about_recording", "remind_when", "send_notes_to_phone"}
    assert names <= set(REGISTRY), names - set(REGISTRY)
    src = open(os.path.join(ROOT, "cloud", "atlas_cloud.py"), encoding="utf-8").read()
    assert all(f'"{n}"' in src for n in names), "в облаке этих инструментов нет — только через компьютер"


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
