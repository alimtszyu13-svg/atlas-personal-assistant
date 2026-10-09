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
def speechy(seconds: float, pause_at=()):
    """«Речь»: громкий сигнал с тихими паузами в указанных секундах."""
    sr = 16000
    a = (np.sin(np.arange(int(seconds * sr)) / 3) * 0.3).astype(np.float32)
    for p in pause_at:
        a[int(p * sr):int((p + 0.8) * sr)] = 0
    return a


@test
def meeting_is_recorded_transcribed_and_summarized():
    from core import meeting_notes as M
    M.folder = lambda: TMP
    M.CHUNK_S, M.CHUNK_MAX_S = 4, 6                               # короткие куски — быстрый тест
    seen = []

    def recorder(emit, stop, with_mic):
        emit(np.zeros(16000 * 2, dtype=np.float32))               # тишина в начале — не распознаётся
        for _ in range(3):
            emit(speechy(3, pause_at=(1.5,)))
        stop.wait(3)
    texts = iter([[(0.0, "Дедлайн по проекту — пятница."), (1.0, "Маша делает слайды.")],
                  [(0.5, "Маша делает слайды."), (1.0, "Созвон в понедельник в десять.")],
                  [(0.0, "Продолжение следует...")], [], [], []])

    def transcribe(audio, path, prompt):
        seen.append(prompt)
        return [p for p in next(texts) if not M._junk(p[1])]
    changes = []
    M.on_change(changes.append)
    assert "Recording started" in M.start_recording("Созвон с командой", True, recorder=recorder, transcribe=transcribe)
    assert M.active() and "Already recording" in M.start_recording("ещё") and M.live()["phase"] == "recording"
    time.sleep(0.5)
    notes = "## Коротко\nОбсудили проект, дедлайн в пятницу [00:02].\n\n## Задачи\n- [ ] Маша — слайды — пятница"
    r = M.stop_recording(ask=lambda system, text, **k: notes)
    assert "Созвон с командой" in r and "дедлайн в пятницу" in r, r
    assert changes[:3] == ["recording", "processing", "idle"], changes
    assert M.last_summary() == "Обсудили проект, дедлайн в пятницу.", M.last_summary()
    it = M.get(M.recordings()[-1]["id"])
    lines = it["transcript"].splitlines()
    assert [l.split("] ", 1)[1] for l in lines] == ["Дедлайн по проекту — пятница.", "Маша делает слайды.",
                                                    "Созвон в понедельник в десять."], lines
    assert lines[0].startswith("[00:0"), "время от начала записи"
    assert "Маша делает слайды" in seen[1], "следующий кусок получает подсказку из предыдущего"
    assert "- [ ] Маша" in it["notes"] and it["title"] == "Созвон с командой" and it["mic"] is True
    got = {}
    ans = M.ask_recording("что сказали про дедлайн?", ask=lambda s, t, **k: (got.update(t=t), "В пятницу.")[1])
    assert ans == "В пятницу." and "Дедлайн" in got["t"] and "Созвон с командой" in M.status()
    assert M.notes_file("созвон") == it["md"] and not M.active()
    assert M.stop_recording() == "Nothing is being recorded right now."
    assert M.rename(it["id"], "Планёрка") and M.get(it["id"])["title"] == "Планёрка"
    assert M.delete(it["id"]) and not M.get(it["id"]) and not os.path.exists(it["txt"])
    M.CHUNK_S, M.CHUNK_MAX_S = 90, 120


@test
def silent_recording_keeps_no_fake_notes():
    from core import meeting_notes as M
    M.folder = lambda: TMP

    def recorder(emit, stop, with_mic):
        emit(np.zeros(16000 * 3, dtype=np.float32))
        stop.wait(3)
    M.start_recording("Тихое видео", False, recorder=recorder, transcribe=lambda *a: [(0, "не должно")],
                      with_screen=False)
    time.sleep(0.2)
    r = M.stop_recording(ask=lambda *a, **k: "конспект")
    assert "no speech was recognized" in r and not M.last_summary() == "конспект", r


@test
def chunks_are_cut_in_pauses_not_mid_word():
    from core import meeting_notes as M
    got = []
    ch = M.Chunker(lambda a, start: got.append((len(a) / 16000, start)), target=10, most=14)
    ch.add(speechy(12, pause_at=(8.0,)))                          # пауза на 8-й секунде
    ch.flush()
    assert abs(got[0][0] - 8.4) < 0.3 and got[0][1] == 0, got     # разрез — посреди паузы
    assert abs(got[1][1] - got[0][0]) < 1e-6 and abs(sum(g[0] for g in got) - 12) < 1e-6, got
    got.clear()
    ch = M.Chunker(lambda a, start: got.append(len(a) / 16000), target=10, most=11)
    ch.add(speechy(11.5))                                          # без пауз — режем по максимуму
    assert got and got[0] <= 11.0, got


@test
def mixer_keeps_mic_going_while_speakers_are_silent():
    from core import meeting_notes as M
    t0 = 1000.0
    loop, mic = M.Source("компьютер"), M.Source("микрофон")
    mixer = M.Mixer([loop, mic], t0)
    mic.push(np.full(16000, 0.2, np.float32))
    mic.last = t0 + 1.0
    assert mixer.step(now=t0 + 0.4) is None, "компьютер ещё может прислать звук — ждём"
    mic.push(np.full(16000, 0.2, np.float32))
    mic.last = t0 + 2.0
    out = mixer.step(now=t0 + 2.0)                                 # в колонках тишина 2 с — добиваем нулями
    assert out is not None and len(out) >= 16000 and abs(float(out.mean()) - 0.2) < 1e-4, None if out is None else len(out)
    loop.push(np.full(8000, 0.1, np.float32))
    loop.last = mic.last = t0 + 2.5
    mic.push(np.full(8000, 0.2, np.float32))
    out2 = mixer.step(now=t0 + 2.5)
    assert out2 is None or float(out2.max()) <= 0.3 + 1e-6


@test
def capture_mixes_speakers_and_mic_in_real_time():
    from core import meeting_notes as M
    import threading as th
    clock = {}                                                    # время считаем от начала записи, а не теста

    class Rec:
        def __init__(self, kind, rate):
            self.kind, self.rate, self.sent = kind, rate, 0

        def __enter__(self):
            self.t0 = time.time()
            clock.setdefault("t0", self.t0)
            return self

        def __exit__(self, *a):
            pass

        def record(self, numframes=None):
            assert numframes is None, "не ждём звука — берём что есть"
            due = int((time.time() - self.t0) * self.rate)
            n, self.sent = due - self.sent, due
            el = time.time() - clock["t0"]
            if self.kind == "loop" and (el < 1.0 or 1.8 < el < 2.3):
                return np.zeros((0, 2), np.float32)                 # в колонках тишина — WASAPI ничего не отдаёт
            ch = 2 if self.kind == "loop" else 1
            return np.full((n, ch), 0.25 if self.kind == "loop" else 0.1, np.float32)

    class Dev:
        def __init__(self, kind, name):
            self.kind, self.name = kind, name

        def recorder(self, samplerate, channels=None, blocksize=None):
            return Rec(self.kind, samplerate)
    fake = types.SimpleNamespace(default_speaker=lambda: types.SimpleNamespace(name="Наушники"),
                                 get_microphone=lambda id, include_loopback=False: Dev("loop", id),
                                 default_microphone=lambda: Dev("mic", "Микрофон"))
    saved, saved_voice = M._soundcard, sys.modules.get("voice")
    M._soundcard = lambda: fake
    sys.modules["voice"] = types.SimpleNamespace(output_is_headphones=lambda: False)    # колонки → эхо приглушаем
    out, stop = [], th.Event()
    try:
        t = th.Thread(target=M._capture, args=(out.append, stop, True), daemon=True)
        t.start()
        for _ in range(200):
            if "t0" in clock:
                break
            time.sleep(0.02)
        time.sleep(3.2)
        stop.set()
        t_stop = time.time()
        t.join(3)
    finally:
        M._soundcard = saved
        if saved_voice is None:
            sys.modules.pop("voice", None)
        else:
            sys.modules["voice"] = saved_voice
    audio = np.concatenate(out)
    secs, expect = len(audio) / 16000, t_stop - M._s["audio_t0"]
    assert abs(secs - expect) < 0.7, f"звук идёт по часам, без дыр и задержек: {secs:.2f} с из {expect:.2f}"
    lag = clock["t0"] - M._s["audio_t0"]                          # устройства открылись чуть позже начала
    at = lambda sec: int((sec + lag) * 16000)
    first = float(audio[at(0.2):at(0.7)].mean())
    assert abs(first - 0.09) < 0.02, f"пока колонки молчат — только микрофон: {first}"
    last = float(audio[at(2.6):at(2.9)].mean())
    assert abs(last - (0.25 + 0.09 * 0.3)) < 0.03, f"потом — колонки + приглушённый микрофон (эхо): {last}"
    mid = float(audio[at(1.95):at(2.15)].mean())
    assert abs(mid - 0.09) < 0.02, f"пауза в колонках посередине — время не съехало: {mid}"


@test
def video_recording_keeps_screen_and_sound_together():
    from core import meeting_notes as M
    M.folder = lambda: TMP
    made = {}

    class FakeScreen:                                             # как ScreenRecorder, без ffmpeg
        def __init__(self):
            self.out, self.t0, self.error = None, None, ""

        def start(self):
            self.out = os.path.join(TMP, M._s["id"] + ".screen.mp4")
            open(self.out, "wb").write(b"\0" * 5000)
            self.t0 = M._s["audio_t0"] + 0.7                       # картинка пошла на 0,7 с позже звука
            return True

        def stop(self):
            return True
    from core import screen_rec
    saved = screen_rec.mux
    screen_rec.mux = lambda v, a, out, off: (made.update(v=v, a=a, off=off), open(out, "wb").write(b"\1" * 6000))[1] > 0

    def recorder(emit, stop, with_mic):
        emit(speechy(2))
        stop.wait(3)

    class SF:                                                      # звук пишется (как soundfile на компьютере)
        def __init__(self, path, mode, **kw):
            open(path, "wb").write(b"OggS")

        def write(self, a):
            pass

        def close(self):
            pass
    had = sys.modules.get("soundfile")
    sys.modules["soundfile"] = types.SimpleNamespace(SoundFile=SF)
    try:
        M.start_recording("Видео", False, recorder=recorder, transcribe=lambda *a: [(0.0, "Сегодня про ток.")],
                          screen=FakeScreen())
        for _ in range(50):
            if M.live()["screen"]:
                break
            time.sleep(0.02)
        assert M.live()["screen"], "экран пишется"
        M.stop_recording(ask=lambda *a, **k: "## Коротко\nПро ток.")
    finally:
        screen_rec.mux = saved
        if had is None:
            del sys.modules["soundfile"]
        else:
            sys.modules["soundfile"] = had
    it = M.recordings()[-1]
    assert it["video"].endswith(".mp4") and not it["video"].endswith(".screen.mp4") and os.path.exists(it["video"])
    assert abs(made["off"] - 0.7) < 1e-6, "видео сдвинуто на задержку старта — звук и картинка совпадают"
    assert not os.path.exists(made["v"]), "черновик экрана после склейки удалён"
    assert M.delete(it["id"]) and not os.path.exists(it["video"])


@test
def screen_and_sound_really_merge_with_ffmpeg():
    import shutil
    from core import screen_rec
    exe = shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            print("      (ffmpeg нет — проверка склейки пропущена)")
            return
    import subprocess
    import wave
    rec = screen_rec.ScreenRecorder(os.path.join(TMP, "scr.mp4"), exe=exe,
                                    sources=[("test", ["-re", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=15"])])
    t_audio = time.time()
    assert rec.start(), rec.error
    time.sleep(1.5)
    assert rec.stop() and rec.t0 >= t_audio - 0.1
    with wave.open(os.path.join(TMP, "snd.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((speechy(3) * 20000).astype(np.int16).tobytes())
    out = os.path.join(TMP, "all.mp4")
    assert screen_rec.mux(rec.out, os.path.join(TMP, "snd.wav"), out, rec.t0 - t_audio, exe=exe)
    probe = subprocess.run([exe, "-hide_banner", "-i", out], capture_output=True, text=True).stderr
    assert "Video: h264" in probe and "Audio: aac" in probe, probe[-400:]
    bad = screen_rec.ScreenRecorder(os.path.join(TMP, "bad.mp4"), exe=exe,
                                    sources=[("нет", ["-f", "lavfi", "-i", "nosuchsource"])])
    assert not bad.start(wait=3) and bad.error, "не получилось — честно говорим, запись идёт только звуком"


@test
def media_server_streams_with_seek():
    import urllib.request
    from core import media_server
    path = os.path.join(TMP, "a.ogg")
    open(path, "wb").write(bytes(range(256)) * 40)
    media_server.start(resolve=lambda rid, kind: path if rid == "rec 1" and kind == "audio" else "")
    u = media_server.url("rec 1", "audio")
    with urllib.request.urlopen(u) as r:
        assert r.status == 200 and len(r.read()) == 10240 and r.headers["Content-Type"] == "audio/ogg"
    req = urllib.request.Request(u, headers={"Range": "bytes=100-199"})
    with urllib.request.urlopen(req) as r:
        assert r.status == 206 and r.read() == (bytes(range(256)) * 40)[100:200]
    import urllib.error
    for bad in (u.replace("/audio/", "/video/"), u.replace(media_server._s["token"], "wrongtoken")):
        try:
            urllib.request.urlopen(bad)
            raise AssertionError("чужой ключ или файл не отдаём")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    media_server._s["resolve"] = None


@test
def voice_phrases_for_recording():
    from core import meeting_notes as M
    assert M.intent("Атлас, запиши это видео") == ("start", "Видео", False, True)
    assert M.intent("запиши созвон с командой") == ("start", "Созвон с командой", True, False)
    assert M.intent("Запиши лекцию по физике.") == ("start", "Лекция по физике", False, True)
    assert M.intent("запиши созвон с экраном") == ("start", "Созвон", True, True)
    assert M.intent("запиши видео без экрана") == ("start", "Видео", False, False)
    for t in ("прекрати запись", "Останови запись, пожалуйста", "хватит записывать", "stop recording"):
        assert M.intent(t) == ("stop",), t
    assert M.intent("открой записи") == ("open",) and M.intent("покажи мои конспекты") == ("open",)
    assert M.intent("запиши встречу в календарь") is None and M.intent("запиши в заметки хлеб") is None
    assert M.intent("какая погода") is None


@test
def audio_helpers_resample_and_save():
    from core import meeting_notes as M
    stereo = np.ones((48000, 2), dtype=np.float32) * 0.5
    mono = M.to_16k_mono(stereo, 48000)
    assert len(mono) == 16000 and abs(float(mono.mean()) - 0.5) < 1e-4
    assert not M.loud_enough(np.zeros(100, dtype=np.float32)) and M.loud_enough(speechy(1))
    written = []

    class SF:                                                      # как soundfile.SoundFile
        def __init__(self, path, mode, **kw):
            if kw["format"] == "OGG":
                raise RuntimeError("нет OGG")
            self.path = path
            open(path, "wb").close()

        def write(self, a):
            written.append(len(a))

        def close(self):
            pass
    sys.modules["soundfile"] = types.SimpleNamespace(SoundFile=SF)
    try:
        f = M.AudioFile(os.path.join(TMP, "звук"))
        f.write(mono)
        f.close()
    finally:
        del sys.modules["soundfile"]
    assert f.path.endswith(".flac") and written == [16000], "нет OGG — сохраняем во FLAC"


@test
def records_window_api():
    sys.modules.setdefault("webview", types.SimpleNamespace(create_window=lambda *a, **k: None))
    sys.modules.setdefault("psutil", types.ModuleType("psutil"))
    import web_gui
    from core import meeting_notes as M
    M.folder = lambda: TMP

    def recorder(emit, stop, with_mic):
        emit(speechy(2))
        stop.wait(3)
    api = web_gui.RecordsApi()
    saved, saved_cap = M._transcribe, M._capture
    M._capture = recorder
    M._transcribe = lambda audio, path, prompt="": [(0.0, "Тема урока — электричество.")]
    try:
        assert api.rec_start("Урок физики", False, False)["ok"]
        assert api.rec_start("ещё", False, False)["error"] == "Уже идёт запись."
        assert api.rec_live()["phase"] == "recording"
        from core import quick_llm
        saved_ask = quick_llm.ask
        quick_llm.ask = lambda *a, **k: "## Коротко\nУрок про электричество."
        try:
            assert api.rec_stop()
            for _ in range(100):
                if M.phase() == "idle":
                    break
                time.sleep(0.05)
        finally:
            quick_llm.ask = saved_ask
    finally:
        M._transcribe, M._capture = saved, saved_cap
    rows = api.rec_list()
    assert rows[0]["title"] == "Урок физики" and rows[0]["mic"] is False, rows
    r = api.rec_get(rows[0]["id"])
    assert "электричество" in r["notes"] and "электричество" in r["transcript"], r
    assert api.rec_rename(rows[0]["id"], "Физика") and api.rec_list()[0]["title"] == "Физика"
    assert api.rec_delete(rows[0]["id"]) and all(x["id"] != rows[0]["id"] for x in api.rec_list())
    page = open(os.path.join(ROOT, "atlas_records.html"), encoding="utf-8").read()
    for name in ("rec_list", "rec_get", "rec_live", "rec_start", "rec_stop", "rec_delete", "rec_rename",
                 "rec_open_audio", "rec_open_folder", "rec_to_phone", "rec_ask"):
        assert f'"{name}"' in page and hasattr(api, name), name


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
             "start_recording", "stop_recording", "ask_about_recording", "remind_when", "send_notes_to_phone",
             "open_recordings_window"}
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
