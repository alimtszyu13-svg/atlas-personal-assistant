"""
Перевод документа целиком: «Атлас, переведи документ про проект на английский», «переведи эссе на русский»
— или «Перевести» в окне «Записи».

Результат — новый файл рядом с оригиналом: «Эссе (EN).docx». Оригинал не меняется.
  • Word (.docx) и PowerPoint (.pptx): переводится каждый абзац на месте — заголовки, списки, таблицы,
    шрифты и картинки остаются (оформление абзаца берётся от первого фрагмента);
  • TXT / MD: построчно, разметка остаётся;
  • PDF, EPUB и прочее: текст с заголовками → новый документ Word.
Переводит модель кусками по ~40 абзацев параллельно; если модель недоступна — Google Переводчик.
"""
import html
import json
import os
import re
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

LANGS = {"en": ("английский", "English", "EN"), "ru": ("русский", "Russian", "RU"), "ky": ("кыргызский", "Kyrgyz", "KY"),
         "kk": ("казахский", "Kazakh", "KK"), "de": ("немецкий", "German", "DE"), "fr": ("французский", "French", "FR"),
         "es": ("испанский", "Spanish", "ES"), "zh": ("китайский", "Chinese", "ZH"), "tr": ("турецкий", "Turkish", "TR"),
         "uk": ("украинский", "Ukrainian", "UK"), "ko": ("корейский", "Korean", "KO"), "ja": ("японский", "Japanese", "JA"),
         "it": ("итальянский", "Italian", "IT"), "ar": ("арабский", "Arabic", "AR")}
BATCH = 40
BATCH_CHARS = 6000
WORKERS = 4


def lang_code(words: str) -> str:
    w = (words or "").lower()
    for code, (ru, en, _) in LANGS.items():
        if ru[:5] in w or en.lower()[:5] in w or re.search(rf"\b{code}\b", w):
            return code
    return ""


# ---------------------------------------------------------------------------
# Перевод списка фрагментов
# ---------------------------------------------------------------------------
def _batches(items: list) -> list:
    out, cur, size = [], [], 0
    for i, t in enumerate(items):
        if cur and (len(cur) >= BATCH or size + len(t) > BATCH_CHARS):
            out.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(t)
    if cur:
        out.append(cur)
    return out


def _google(texts: list, lang: str) -> list:
    from deep_translator import GoogleTranslator
    tr = GoogleTranslator(source="auto", target="zh-CN" if lang == "zh" else lang)
    return [tr.translate(t) if t.strip() else t for t in texts]


def translate_list(texts: list, lang: str, ask=None, progress=None, fallback=None) -> list:
    """[текст] → [перевод] той же длины. Пустые и «не текст» (числа, коды) не трогаем."""
    from core import quick_llm
    ask = ask or quick_llm.ask
    name = LANGS.get(lang, ("", lang, ""))[1]
    out = list(texts)
    todo = [i for i, t in enumerate(texts) if re.search(r"[^\W\d_]", t or "")]
    groups = _batches([texts[i] for i in todo])
    done = {"n": 0}
    rules = (f"Translate each item of the JSON array into {name}. Natural, accurate, keep names, numbers, "
             f"formatting marks and line breaks. Return ONLY a JSON array of strings of the SAME length, same order.")

    def one(group):
        idx = [todo[k] for k in group]
        src = [texts[i] for i in idx]
        res = None
        for _ in range(2):
            try:
                raw = ask(rules, json.dumps(src, ensure_ascii=False), max_tokens=min(8000, sum(map(len, src)) + 800))
                raw = raw[raw.find("["):raw.rfind("]") + 1]
                got = json.loads(raw)
                if isinstance(got, list) and len(got) == len(src):
                    res = [str(x) for x in got]
                    break
            except Exception:
                continue
        if res is None:
            try:
                res = (fallback or _google)(src, lang)
            except Exception as e:
                print(f"[перевод] кусок оставлен как есть: {e}")
                res = src
        for i, t in zip(idx, res):
            out[i] = t
        done["n"] += 1
        if progress:
            progress(int(90 * done["n"] / max(1, len(groups))))
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(one, groups))
    return out


# ---------------------------------------------------------------------------
# Форматы
# ---------------------------------------------------------------------------
_W_P = re.compile(r"<w:p[ >].*?</w:p>", re.S)
_W_T = re.compile(r"(<w:t(?: [^>]*)?>)(.*?)(</w:t>)", re.S)
_A_P = re.compile(r"<a:p>.*?</a:p>", re.S)
_A_T = re.compile(r"(<a:t>)(.*?)(</a:t>)", re.S)


def _xml_paragraphs(xml: str, p_rx, t_rx):
    """Абзацы XML → ([текст абзаца], функция «собрать обратно с переводом»)."""
    paras = list(p_rx.finditer(xml))
    texts = ["".join(html.unescape(m.group(2)) for m in t_rx.finditer(p.group(0))) for p in paras]

    def rebuild(translated: list) -> str:
        out, last = [], 0
        for p, new in zip(paras, translated):
            out.append(xml[last:p.start()])
            body, first = p.group(0), [True]

            def put(m):
                if first[0]:
                    first[0] = False
                    opening = m.group(1)
                    if "xml:space" not in opening and opening.startswith("<w:t"):
                        opening = opening[:-1] + ' xml:space="preserve">'
                    return opening + html.escape(new, quote=False) + m.group(3)
                return m.group(1) + m.group(3)
            out.append(t_rx.sub(put, body) if new != "" else body)
            last = p.end()
        out.append(xml[last:])
        return "".join(out)
    return texts, rebuild


def _zip_translate(src: str, dst: str, parts: list, p_rx, t_rx, lang, ask, progress, fallback):
    with zipfile.ZipFile(src) as z:
        files = {n: z.read(n) for n in z.namelist()}
    jobs = []
    for n in parts:
        xml = files[n].decode("utf-8")
        texts, rebuild = _xml_paragraphs(xml, p_rx, t_rx)
        jobs.append((n, texts, rebuild))
    flat = [t for _, texts, _ in jobs for t in texts]
    tr = translate_list(flat, lang, ask=ask, progress=progress, fallback=fallback)
    k = 0
    for n, texts, rebuild in jobs:
        files[n] = rebuild(tr[k:k + len(texts)]).encode("utf-8")
        k += len(texts)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in files.items():
            z.writestr(n, data)


def write_docx(path: str, text: str) -> None:
    """Простой Word-документ из текста: «# » — заголовок 1, «## » — заголовок 2, остальное — абзацы."""
    def para(t):
        style = ""
        m = re.match(r"^(#{1,3})\s+(.*)$", t)
        if m:
            style = f'<w:pPr><w:pStyle w:val="Heading{len(m.group(1))}"/></w:pPr>'
            t = m.group(2)
        runs = "".join(f'<w:r><w:t xml:space="preserve">{html.escape(line, quote=False)}</w:t></w:r>'
                       + ("<w:r><w:br/></w:r>" if i < len(t.split("\n")) - 1 else "")
                       for i, line in enumerate(t.split("\n")))
        return f"<w:p>{style}{runs}</w:p>"
    body = "".join(para(p) for p in re.split(r"\n\s*\n", text) if p.strip())
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document '
           'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body +
           '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="850" w:bottom="1134" '
           'w:left="1701"/></w:sectPr></w:body></w:document>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles '
              'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr>'
              '<w:spacing w:after="160" w:line="276" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri" '
              'w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="24"/></w:rPr></w:style>' +
              "".join(f'<w:style w:type="paragraph" w:styleId="Heading{i}"><w:name w:val="heading {i}"/>'
                      f'<w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="240"/><w:outlineLvl '
                      f'w:val="{i - 1}"/></w:pPr><w:rPr><w:b/><w:sz w:val="{36 - i * 4}"/></w:rPr></w:style>'
                      for i in (1, 2, 3)) + "</w:styles>")
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types '
          'xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" '
          'ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" '
          'ContentType="application/xml"/><Override PartName="/word/document.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/word/styles.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships '
                'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
                'Target="styles.xml"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/_rels/document.xml.rels", doc_rels)


def out_path(src: str, lang: str) -> str:
    stem, ext = os.path.splitext(src)
    ext = ext.lower() if ext.lower() in (".docx", ".pptx", ".txt", ".md") else ".docx"
    tag = LANGS.get(lang, ("", "", lang.upper()))[2]
    p = f"{stem} ({tag}){ext}"
    n = 2
    while os.path.exists(p):
        p = f"{stem} ({tag} {n}){ext}"
        n += 1
    return p


def translate_file(src: str, lang: str, ask=None, progress=None, fallback=None) -> str:
    """Перевести файл → путь нового файла рядом с оригиналом."""
    ext = os.path.splitext(src)[1].lower()
    dst = out_path(src, lang)
    if ext == ".docx":
        with zipfile.ZipFile(src) as z:
            parts = [n for n in z.namelist() if re.match(r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$", n)]
        _zip_translate(src, dst, parts, _W_P, _W_T, lang, ask, progress, fallback)
    elif ext == ".pptx":
        with zipfile.ZipFile(src) as z:
            parts = [n for n in z.namelist() if re.match(r"ppt/(slides/slide|notesSlides/notesSlide)\d+\.xml$", n)]
        _zip_translate(src, dst, parts, _A_P, _A_T, lang, ask, progress, fallback)
    elif ext in (".txt", ".md"):
        from core import doc_text
        lines = doc_text._plain(src)[0].split("\n")
        tr = translate_list(lines, lang, ask=ask, progress=progress, fallback=fallback)
        with open(dst, "w", encoding="utf-8") as f:
            f.write("\n".join(tr))
    else:
        from core import doc_text
        text = doc_text.read(src)["text"]
        paras = [p for p in re.split(r"\n\s*\n", text) if p.strip() and not re.fullmatch(r"\[стр\. \d+\]", p.strip())]
        marks = [re.match(r"^(#{1,3}\s+|•\s+)?", p).group(0) or "" for p in paras]   # заголовки и пункты — не переводим
        bare = [p[len(m):] for p, m in zip(paras, marks)]
        tr = translate_list(bare, lang, ask=ask, progress=progress, fallback=fallback)
        write_docx(dst, "\n\n".join(m + t for m, t in zip(marks, tr)))
    return dst


def start(src: str, lang: str, announce=None, ask=None, wait: bool = False, fallback=None) -> str:
    """В фоне, с полоской в окне «Записи». → id задачи."""
    from core import media_import, meeting_notes as M
    jid = uuid.uuid4().hex[:8]
    name = LANGS.get(lang, (lang,))[0]
    media_import._jobs[jid] = {"title": f"Перевод: {os.path.basename(src)}"[:80], "pct": 2, "stage": "перевод",
                               "error": "", "path": src}
    M._changed()

    def run():
        job = media_import._jobs[jid]
        try:
            dst = translate_file(src, lang, ask=ask, progress=lambda p: job.update(pct=2 + p), fallback=fallback)
            job.update(stage="готово", pct=100, out=dst)
            M._changed()
            msg = f"Перевод на {name} готов: «{os.path.basename(dst)}» — рядом с оригиналом."
            print(f"[перевод] {dst}")
            if announce:
                announce(msg)
        except Exception as e:
            job.update(stage="ошибка", error=str(e)[:200])
            M._changed()
            if announce:
                announce(f"Не получилось перевести: {str(e)[:140]}")
        finally:
            threading.Timer(30, lambda: media_import._jobs.pop(jid, None)
                            if media_import._jobs.get(jid, {}).get("stage") in ("готово", "ошибка") else None).start()
    t = threading.Thread(target=run, daemon=True, name=f"translate-{jid}")
    t.start()
    if wait:
        t.join()
    return jid


# ---------------------------------------------------------------------------
# Голосом
# ---------------------------------------------------------------------------
_ASK = re.compile(r"^(?:переведи|перевести|translate)\s+(?:мне\s+)?(?P<q>.+?)\s+(?:на|into|to)\s+(?P<l>[\w-]+(?:\s+язык)?)$",
                  re.I)
_DOCWORD = re.compile(r"документ|файл|pdf|пдф|книг|стать|эссе|реферат|договор|презентац|ворд|word|docx|txt|отчёт|отчет|"
                      r"письм|резюме|сочинени|document|file|essay|report|paper|slides", re.I)


def intent(text: str):
    """→ (запрос файла, код языка) | None. «переведи это на английский» (без файла) сюда не относится."""
    t = re.sub(r"^(?:атлас|atlas)[,\s]+", "", (text or "").strip(), flags=re.I).strip(" .!?")
    m = _ASK.match(t)
    if not m or not _DOCWORD.search(m.group("q")):
        return None
    code = lang_code(m.group("l"))
    return (m.group("q").strip(), code) if code else None


def ask(query: str, lang: str, announce=None, finder=None) -> str:
    from core import doc_text, media_import
    hits = [p for p in (finder or media_import.find)(query) if doc_text.is_doc(p) and not media_import.is_media(p)]
    if not hits:
        return f"Не нашёл документ «{query}». Открой окно «Записи» → выбери документ → «Перевести»."
    start(hits[0], lang, announce=announce)
    return f"Перевожу «{os.path.basename(hits[0])}» на {LANGS[lang][0]}. Файл появится рядом с оригиналом."
