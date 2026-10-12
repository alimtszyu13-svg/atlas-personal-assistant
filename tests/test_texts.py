"""
Тексты: конспект документов и больших текстов, по ссылке, «сожми выделенное», сравнение, перевод.
Документы — настоящие файлы из tests/fixtures. Модель и интернет подставные.

    python tests/test_texts.py
"""
import json
import os
import re
import sys
import tempfile
import threading
import time
import traceback
import types
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FX = os.path.join(ROOT, "tests", "fixtures")
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TMP = tempfile.mkdtemp(prefix="atlas_texts_")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def setup():
    from core import meeting_notes as M
    M.folder = lambda: TMP
    for name in ("pygame", "groq", "edge_tts", "elevenlabs", "elevenlabs.client", "sounddevice"):
        sys.modules.setdefault(name, types.ModuleType(name))


class FakeModel:
    """Как quick_llm.ask: считает вызовы, отвечает по типу запроса."""

    def __init__(self):
        self.calls, self.lock = [], threading.Lock()

    def __call__(self, system, text, max_tokens=800, **k):
        with self.lock:
            self.calls.append((system, text))
        if "JSON array" in system:
            items = json.loads(text)
            return json.dumps([f"EN<{x}>" for x in items], ensure_ascii=False)
        if "part" in system and "of a long text" in system:
            return "- заметки части: " + text[:30].replace("\n", " ")
        if "compare two versions" in system:
            return "## Коротко\nПоменяли срок оплаты."
        return "## Коротко\nГлавное по тексту.\n\n## Главное\n- пункт"


# --- чтение документов ----------------------------------------------------------
@test
def reads_real_documents_with_structure():
    from core import doc_text as D
    pdf = D.read(os.path.join(FX, "doc_sample.pdf"), ocr=lambda i: "Отсканированная страница про резисторы.")
    assert pdf["pages"] == 3 and "[стр. 1]" in pdf["text"] and "[стр. 3]" in pdf["text"]
    assert "направленное движение заряженных частиц" in pdf["text"], "перенос «движе-ние» склеен"
    assert "Отсканированная страница" in pdf["text"], "пустая страница (скан) распознана"
    docx = D.read(os.path.join(FX, "doc_sample.docx"))["text"]
    assert "# Отчёт о проекте Atlas" in docx and "## Итоги" in docx, docx
    pptx = D.read(os.path.join(FX, "doc_sample.pptx"))
    assert pptx["pages"] == 2 and "## Слайд 2" in pptx["text"] and "Заметки: Спросить у класса" in pptx["text"]
    epub = D.read(os.path.join(FX, "doc_sample.epub"))["text"]
    assert "# Первая глава" in epub and "Они побежали по проводу." in epub and "xhtml" not in epub
    page = D.html_text("<html><head><title>x</title></head><body><nav>Меню Войти</nav><article><h1>Заголовок</h1>"
                       "<p>Первый абзац статьи.</p><ul><li>пункт</li></ul></article><footer>©</footer></body></html>")
    assert page.startswith("# Заголовок") and "Меню" not in page and "• пункт" in page and "©" not in page
    rtf = os.path.join(TMP, "a.rtf")
    open(rtf, "w", encoding="cp1251").write(r"{\rtf1\ansi Привет\par мир}")
    assert "Привет" in D.read(rtf)["text"] and "мир" in D.read(rtf)["text"]
    assert D.is_doc("x.PDF") and D.is_doc("книга.epub") and not D.is_doc("a.mp4")


# --- конспект большого текста ---------------------------------------------------
@test
def big_text_is_summarized_in_parallel_parts():
    from core import text_notes as T
    para = "Электроны движутся по проводнику, создавая ток. " * 40       # ~2000 знаков
    text = "\n\n".join(f"[стр. {i}]\n\n{para}" for i in range(1, 41))    # ~80 000 знаков — «книга»
    parts = T.split(text)
    assert len(parts) >= 6 and all(len(p) <= T.PART for p in parts) and "".join(parts).count("[стр.") == 40
    m = FakeModel()
    seen = []
    out = T.notes(text, "notes", ask=m, progress=seen.append, lang="Russian")
    part_calls = [c for c in m.calls if "of a long text" in c[0]]
    assert len(part_calls) == len(parts), (len(part_calls), len(parts))
    assert "merge them into one whole" in m.calls[-1][0] and out.startswith("## Коротко")
    assert seen and max(seen) >= 80
    m2 = FakeModel()
    assert T.notes("Короткий текст про ток.", "short", ask=m2) and len(m2.calls) == 1, "короткий — одним запросом"
    long_para = "Очень длинное предложение без абзацев. " * 900
    assert all(len(p) <= T.PART for p in T.split(long_para)), "простыня без абзацев режется по предложениям"


@test
def document_goes_into_records_and_original_stays():
    setup()
    from core import doc_notes, meeting_notes as M, media_import
    src = os.path.join(TMP, "Отчёт.docx")
    with open(os.path.join(FX, "doc_sample.docx"), "rb") as a, open(src, "wb") as b:
        b.write(a.read())
    said = []
    media_import.import_file(src, announce=said.append, ask=FakeModel(), wait=True)    # документ — через doc_notes
    it = M.recordings()[-1]
    assert it["kind"] == "doc" and it["source"] == src and it["title"] == "Отчёт", it
    got = M.get(it["id"])
    assert "# Отчёт о проекте Atlas" in got["transcript"] and "Главное по тексту" in got["notes"]
    assert said and "готов" in said[-1]
    ans = M.ask_recording("что сделано?", it["id"], ask=lambda s, t, **k: "Окно записей.")
    assert ans == "Окно записей."
    assert M.delete(it["id"]) and os.path.exists(src), "удалили запись — документ остался"
    doc_notes.import_doc(os.path.join(TMP, "нет.pdf"), announce=said.append, ask=FakeModel(), wait=True)
    assert "Не получилось" in said[-1]


@test
def documents_found_by_words_type_and_date():
    from core import media_import as I
    from datetime import datetime, timedelta
    d = tempfile.mkdtemp(dir=TMP)
    now = datetime(2026, 10, 12, 13, 0)
    files = []
    for name, days in (("Реферат по физике.docx", 2), ("Scan_0012.pdf", 0), ("Лекция физика.mp4", 1)):
        p = os.path.join(d, name)
        open(p, "wb").write(b"x")
        ts = (now - timedelta(days=days, hours=1)).timestamp()
        os.utime(p, (ts, ts))
        files.append(p)
    assert I.find("реферат по физике", now=now, files=list(files))[0] == files[0]
    assert I.find("сегодняшний pdf", now=now, files=list(files)) == [files[1]], "«коварное» имя — по дате и типу"
    assert I.find("документ по физике", now=now, files=list(files))[0] == files[0], "документ, а не видео"
    assert I.intent("сделай конспект книги про электричество") == ("import", "книги про электричество")
    assert I.intent("сделай конспект документа Scan_0012") == ("import", "документа Scan_0012")


# --- по ссылке ----------------------------------------------------------------
VTT = """WEBVTT

00:00:01.000 --> 00:00:03.000
Привет, сегодня про ток

00:00:03.000 --> 00:00:05.000
Привет, сегодня про ток
и закон Ома.

00:01:10.500 --> 00:01:13.000
<c>Мощность</c> равна U умножить на I.
"""


class FakeYDL:
    """Как yt_dlp.YoutubeDL: пишет субтитры в папку."""
    with_subs = True

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def extract_info(self, url, download=True):
        out = self.opts["outtmpl"].replace("%(id)s", "abcdefghijk")
        if self.opts.get("skip_download"):
            if FakeYDL.with_subs:
                open(out.replace("%(ext)s", "ru.vtt"), "w", encoding="utf-8").write(VTT)
        else:
            open(out.replace("%(ext)s", "m4a"), "wb").write(b"audio")
        return {"title": "Закон Ома за 5 минут", "duration": 300, "language": "ru", "id": "abcdefghijk", "ext": "m4a"}

    def prepare_filename(self, info):
        return self.opts["outtmpl"].replace("%(id)s", "abcdefghijk").replace("%(ext)s", "m4a")


@test
def link_phrases_and_youtube_subtitles():
    setup()
    from core import link_notes as L, meeting_notes as M
    assert L.intent("Атлас, сделай конспект этого ролика") == ("link", "")
    assert L.intent("законспектируй эту статью") == ("link", "")
    assert L.intent("конспект по ссылке") == ("link", "")
    assert L.intent("сделай конспект https://youtu.be/abcdefghijk пожалуйста") == ("link", "https://youtu.be/abcdefghijk")
    assert L.intent("сделай конспект видео про ток") is None, "это файл на компьютере, не ссылка"
    assert L.youtube_id("https://www.youtube.com/watch?v=abcdefghijk&t=5") == "abcdefghijk"
    segs = L.parse_vtt(VTT)
    assert segs[0] == (1.0, "Привет, сегодня про ток и закон Ома.") and segs[1][0] == 70.0, segs
    said = []
    r = L.summarize("https://www.youtube.com/watch?v=abcdefghijk", announce=said.append, ask=FakeModel(),
                    wait=True, ytdlp=types.SimpleNamespace(YoutubeDL=FakeYDL))
    assert "ролика" in r and said and "готов" in said[-1], (r, said)
    it = M.recordings()[-1]
    assert it["kind"] == "yt" and it["url"].endswith("abcdefghijk") and it["title"] == "Закон Ома за 5 минут"
    assert "[01:10] Мощность равна U умножить на I." in M.get(it["id"])["transcript"]


@test
def youtube_without_subtitles_falls_back_to_audio():
    setup()
    from core import link_notes as L, media_import
    FakeYDL.with_subs = False
    got = {}
    saved = media_import.import_file
    media_import.import_file = lambda path, **kw: got.update(path=path, **kw)
    try:
        L.summarize("https://youtu.be/abcdefghijk", ask=FakeModel(), wait=True,
                    ytdlp=types.SimpleNamespace(YoutubeDL=FakeYDL))
    finally:
        media_import.import_file = saved
        FakeYDL.with_subs = True
    assert got["path"].endswith("youtube_abcdefghijk.m4a") and os.path.exists(got["path"]), got
    assert got["url"] == "https://youtu.be/abcdefghijk" and got["title"] == "Закон Ома за 5 минут"


@test
def article_main_text_without_menus():
    setup()
    from core import link_notes as L, meeting_notes as M
    page = ("<html><head><title>Как работает ток | Наука</title><meta property='og:title' content='Как работает ток'>"
            "</head><body><header>Логотип Меню</header><nav>Главная</nav><div class='ads'>Купи</div><article>"
            "<h1>Как работает ток</h1>" + "".join(f"<p>Абзац {i}: электроны движутся от минуса к плюсу, и это "
                                                    f"важно для понимания цепей.</p>" for i in range(12)) +
            "</article><footer>© 2026</footer></body></html>")
    a = L.article(page)
    assert a["title"] == "Как работает ток" and "Абзац 11" in a["text"] and "Меню" not in a["text"], a
    said = []
    L.summarize("https://example.com/tok", announce=said.append, ask=FakeModel(), wait=True, fetcher=lambda u: page)
    it = M.recordings()[-1]
    assert it["kind"] == "web" and it["url"] == "https://example.com/tok" and "Абзац 3" in M.get(it["id"])["transcript"]
    L.summarize("https://example.com/empty", announce=said.append, wait=True, fetcher=lambda u: "<p>мало</p>")
    assert "почти нет текста" in said[-1]


# --- сожми выделенное ----------------------------------------------------------------
@test
def squeeze_selected_text():
    from core import squeeze as S
    assert S.intent("Атлас, сожми это") == "short" and S.intent("объясни это проще") == "simple"
    assert S.intent("сделай это по пунктам") == "points" and S.intent("сожми выделенное") == "short"
    assert S.intent("сожми") == "short"
    assert S.intent("объясни мне квантовую механику") is None, "это вопрос, а не про выделенный текст"
    assert S.intent("сожми этот документ про физику") is None and S.intent("сделай конспект этого ролика") is None
    m = FakeModel()
    text = "Закон Ома: сила тока прямо пропорциональна напряжению и обратно пропорциональна сопротивлению. " * 3
    out = S.squeeze("short", text=text, ask=m, copy=False)
    assert out and "Compress the text" in m.calls[0][0]
    assert "Не вижу" in S.squeeze("short", text="", ask=m, copy=False)
    long = "Первое предложение про ток. " * 30
    sp = S.spoken(long)
    assert len(sp) < 330 and sp.endswith("в буфере обмена."), sp


# --- сравнение ---------------------------------------------------------------------
OLD = """# Договор аренды

Арендатор платит 30 000 сомов в месяц.

Оплата до 5 числа каждого месяца.

Залог — один месяц."""
NEW = """# Договор аренды

Арендатор платит 35 000 сомов в месяц.

Оплата до 5 числа каждого месяца.

Штраф за просрочку — 1% в день."""


@test
def compare_finds_exact_changes():
    setup()
    from core import doc_compare as C, meeting_notes as M
    d = C.diff(OLD, NEW)
    assert d["same"] == 2 and d["changed"] == 1 and d["removed"] == 1 and d["added"] == 1, d
    assert any("[−30" in ln and "{+35" in ln for ln in d["lines"]), d["lines"]
    assert any(ln.startswith("+ Добавлено: Штраф") for ln in d["lines"])
    assert any(ln.startswith("− Убрано: Залог") for ln in d["lines"])
    assert C.diff(OLD, OLD)["lines"] == []
    texts = {"a.docx": OLD, "b.docx": NEW}
    m = FakeModel()
    said = []
    C.compare("a.docx", "b.docx", announce=said.append, ask=m, wait=True,
              reader=lambda p: {"text": texts[os.path.basename(p)]})
    it = M.recordings()[-1]
    assert it["kind"] == "compare" and "Поменяли срок оплаты" in M.get(it["id"])["notes"]
    assert "[−30" in M.get(it["id"])["transcript"] and said and "готов" in said[-1]
    assert C.intent("сравни эссе и эссе v2") == ("compare", "эссе", "эссе v2")
    assert C.intent("чем отличается новый договор от старого") == ("compare", "старого", "новый договор")
    assert C.intent("сравни две последние версии реферата") == ("versions", "реферата")
    assert C.intent("сравни цены") is None


# --- перевод ---------------------------------------------------------------------
@test
def translate_word_keeps_structure():
    from core import doc_translate as T, doc_text as D
    src = os.path.join(TMP, "Отчёт.docx")
    with open(os.path.join(FX, "doc_sample.docx"), "rb") as a, open(src, "wb") as b:
        b.write(a.read())
    m = FakeModel()
    out = T.translate_file(src, "en", ask=m)
    assert out.endswith("Отчёт (EN).docx") and os.path.exists(out)
    text = D.read(out)["text"]
    assert "# EN<Отчёт о проекте Atlas>" in text and "## EN<Итоги>" in text, "заголовки остались заголовками"
    with zipfile.ZipFile(src) as z1, zipfile.ZipFile(out) as z2:
        assert set(z1.namelist()) == set(z2.namelist()), "все части документа на месте"
    assert T.out_path(src, "en").endswith("(EN 2).docx"), "второй перевод не затирает первый"
    try:
        import docx                                               # проверка, что Word-файл читается (если есть)
        assert any("EN<" in p.text for p in docx.Document(out).paragraphs)
    except ImportError:
        pass


@test
def translate_pptx_pdf_txt_and_fallback():
    from core import doc_translate as T, doc_text as D
    m = FakeModel()
    p = T.translate_file(os.path.join(FX, "doc_sample.pptx"), "en", ask=m)
    os.replace(p, os.path.join(TMP, "slides (EN).pptx"))
    assert "EN<Домашнее задание>" in D.read(os.path.join(TMP, "slides (EN).pptx"))["text"]
    pdf = os.path.join(TMP, "lecture.pdf")
    with open(os.path.join(FX, "doc_sample.pdf"), "rb") as a, open(pdf, "wb") as b:
        b.write(a.read())
    saved = D.read
    D.read = lambda path, ocr=None: saved(path, ocr=lambda i: "Скан.") if path.endswith(".pdf") else saved(path)
    try:
        out = T.translate_file(pdf, "en", ask=m)
    finally:
        D.read = saved
    assert out.endswith("lecture (EN).docx") and "EN<Глава 2. Мощность>" in D.read(out)["text"], "PDF → Word"
    txt = os.path.join(TMP, "note.txt")
    open(txt, "w", encoding="utf-8").write("Привет\n\n123\nПока")
    out = T.translate_file(txt, "en", ask=m)
    assert open(out, encoding="utf-8").read() == "EN<Привет>\n\n123\nEN<Пока>", "числа и пустые строки не трогаем"
    broken = lambda s, t, **k: "не JSON"
    res = T.translate_list(["раз", "два"], "en", ask=broken, fallback=lambda xs, lang: [x.upper() for x in xs])
    assert res == ["РАЗ", "ДВА"], "модель сломалась — запасной переводчик"
    assert T.intent("переведи документ про проект на английский") == ("документ про проект", "en")
    assert T.intent("переведи эссе на кыргызский язык") == ("эссе", "ky")
    assert T.intent("переведи привет на английский") is None, "это не файл — обычный перевод фразы"
    said = []
    T.start(txt, "de", announce=said.append, ask=m, wait=True)
    assert said and "немецкий" in said[-1]


@test
def skills_registered_and_hidden_in_cloud():
    setup()
    from core.skills import REGISTRY
    import skills.pc_plus  # noqa: F401
    names = {"summarize_link", "squeeze_selection", "compare_documents", "translate_document", "summarize_media_file"}
    assert names <= set(REGISTRY), names - set(REGISTRY)
    src = open(os.path.join(ROOT, "cloud", "atlas_cloud.py"), encoding="utf-8").read()
    assert all(f'"{n}"' in src for n in names)


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
