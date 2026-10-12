"""
Полный текст документа со структурой — для конспектов, вопросов, сравнения и перевода.

    read(path) → {"text": "…", "pages": N, "kind": "pdf"}

Абзацы разделены пустой строкой, заголовки — «# …», страницы PDF — «[стр. 5]», слайды — «## Слайд 3».
Поддержка: PDF (сканы — распознаванием текста Windows, постранично), Word (.docx), PowerPoint (.pptx),
Excel (.xlsx — как таблица строк), EPUB, FB2, ODT, TXT/MD/CSV, RTF, HTML, картинки (OCR).
Без ограничений на размер: книга на 500 страниц читается целиком (сканы — до SCAN_PAGES страниц).
"""
import html
import os
import re
import zipfile

DOCS = (".pdf", ".docx", ".pptx", ".xlsx", ".epub", ".fb2", ".odt", ".txt", ".md", ".rtf", ".html", ".htm",
        ".csv", ".log", ".json", ".xml")
IMAGES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")
SCAN_PAGES = int(os.getenv("SCAN_PAGES") or 300)


def is_doc(path: str) -> bool:
    return os.path.splitext(path or "")[1].lower() in DOCS + IMAGES


def _clean(text: str) -> str:
    text = text.replace("\r", "").replace("\u00a0", " ").replace("\u00ad", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
_HEAD = re.compile(r"^(?:(?:глава|раздел|часть|тема|лекция|параграф|§|chapter|section|part|lesson)\s*[\dIVXLC]+\b|"
                   r"\d{1,2}(?:\.\d{1,2}){0,2}\.?\s+[А-ЯЁA-Z])", re.I)


def _heading(line: str) -> bool:
    """«Глава 1. Электрический ток», «2.3 Закон Ома», «ВВЕДЕНИЕ» — заголовок, а не абзац."""
    t = line.strip()
    if not (3 <= len(t) <= 90) or t.endswith((",", ";", ":")):
        return False
    if _HEAD.match(t) and not re.search(r"[.!?]\s+\S", t[t.find(".") + 1:] if "." in t[:12] else t):
        return True
    letters = [c for c in t if c.isalpha()]
    return len(letters) >= 5 and all(c.isupper() for c in letters) and len(t.split()) <= 8


def _join_lines(page: str) -> str:
    """Строки PDF → абзацы: перенос внутри предложения склеиваем, «кон-\nец» → «конец»."""
    lines = [ln.strip() for ln in page.split("\n")]
    out, cur = [], ""
    for ln in lines:
        if ln and _heading(ln):
            if cur:
                out.append(cur)
                cur = ""
            out.append("## " + ln)
            continue
        if not ln:
            if cur:
                out.append(cur)
                cur = ""
            continue
        if cur.endswith("-") and ln[:1].islower():
            cur = cur[:-1] + ln
        elif cur and not re.search(r"[.!?:;»\"]$", cur) and (ln[:1].islower() or len(cur) > 60):
            cur += " " + ln
        else:
            if cur:
                out.append(cur)
            cur = ln
    if cur:
        out.append(cur)
    return "\n\n".join(out)


def _ocr_page(pdf, i: int) -> str:
    try:
        import file_search
        return file_search._ocr_pil(pdf[i].render(scale=2).to_pil())
    except Exception:
        return ""


def _pdf(path: str, ocr=None):
    from pypdf import PdfReader
    reader = PdfReader(path)
    n = len(reader.pages)
    pages, scanned = [], []
    for i, page in enumerate(reader.pages):
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        pages.append(t)
        if len(t.strip()) < 30:
            scanned.append(i)
    if scanned and (ocr or len(scanned) > 0):
        pdf = None
        try:
            if ocr is None:
                import pypdfium2 as pdfium
                pdf = pdfium.PdfDocument(path)
            for i in scanned[:SCAN_PAGES]:
                pages[i] = (ocr(i) if ocr else _ocr_page(pdf, i)) or pages[i]
        except Exception as e:
            print(f"[документ] сканы не распознаны: {e}")
        finally:
            if pdf is not None:
                pdf.close()
    parts = [f"[стр. {i + 1}]\n\n{_join_lines(t)}" for i, t in enumerate(pages) if t.strip()]
    return "\n\n".join(parts), n


# ---------------------------------------------------------------------------
# Office и прочие zip-форматы
# ---------------------------------------------------------------------------
def _xml_text(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s))


def docx_paragraphs(xml: str) -> list:
    """document.xml → [(стиль-заголовок или '', текст)]."""
    out = []
    for p in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, flags=re.S):
        style = re.search(r'<w:pStyle w:val="([^"]+)"', p)
        p2 = re.sub(r"<w:tab/>", "\t", p)
        p2 = re.sub(r"<w:br[^>]*/>", "\n", p2)
        text = "".join(_xml_text(t) for t in re.findall(r"<w:t[ >].*?</w:t>|<w:t>.*?</w:t>", p2, flags=re.S))
        st = (style.group(1) if style else "").lower()
        head = "#" if re.search(r"heading1|заголовок1|^title|^1$", st) else "##" if re.search(r"heading[2-9]|заголовок[2-9]", st) else ""
        out.append((head, text))
    return out


def _docx(path: str):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    paras = docx_paragraphs(xml)
    lines = [(f"{h} {t}" if h and t.strip() else t) for h, t in paras]
    return "\n\n".join(t for t in lines if t.strip()), max(1, len(xml) // 18000)


def _pptx(path: str):
    with zipfile.ZipFile(path) as z:
        names = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                       key=lambda n: int(re.search(r"(\d+)", n.split("/")[-1]).group(1)))
        out = []
        for i, n in enumerate(names, 1):
            xml = z.read(n).decode("utf-8", "ignore")
            paras = [_xml_text("".join(re.findall(r"<a:t>.*?</a:t>", p, flags=re.S)))
                     for p in re.findall(r"<a:p>.*?</a:p>", xml, flags=re.S)]
            note = n.replace("slides/slide", "notesSlides/notesSlide")
            notes = ""
            if note in z.namelist():
                nx = z.read(note).decode("utf-8", "ignore")
                notes = " ".join(_xml_text(t) for t in re.findall(r"<a:t>.*?</a:t>", nx, flags=re.S))
            body = "\n\n".join(p for p in paras if p.strip())
            out.append(f"## Слайд {i}\n\n{body}" + (f"\n\n(Заметки: {notes.strip()})" if notes.strip() else ""))
    return "\n\n".join(out), len(names)


def _xlsx(path: str):
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            sx = z.read("xl/sharedStrings.xml").decode("utf-8", "ignore")
            shared = [_xml_text(si) for si in re.findall(r"<si>.*?</si>", sx, flags=re.S)]
        out = []
        for n in sorted(x for x in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", x)):
            xml = z.read(n).decode("utf-8", "ignore")
            out.append(f"## Лист {re.search(r'(\d+)', n.split('/')[-1]).group(1)}")
            for row in re.findall(r"<row[^>]*>.*?</row>", xml, flags=re.S):
                cells = []
                for c in re.findall(r"<c [^>]*?(?:/>|>.*?</c>)", row, flags=re.S):
                    v = re.search(r"<v>(.*?)</v>", c, flags=re.S)
                    t = re.search(r'\bt="(\w+)"', c)
                    val = v.group(1) if v else _xml_text("".join(re.findall(r"<t[^>]*>.*?</t>", c, flags=re.S)))
                    if t and t.group(1) == "s" and v:
                        val = shared[int(val)] if int(val) < len(shared) else val
                    cells.append(val.strip())
                if any(cells):
                    out.append(" | ".join(cells))
    return "\n".join(out), 1


def html_text(raw: str) -> str:
    """HTML → текст с заголовками и абзацами (без меню, скриптов и подвалов)."""
    s = re.sub(r"(?is)<(head|script|style|noscript|svg|nav|footer|header|aside|form|iframe)[^>]*>.*?</\1>", " ", raw)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)
    s = re.sub(r"(?is)<h1[^>]*>(.*?)</h1>", lambda m: "\n\n# " + _xml_text(m.group(1)).strip() + "\n\n", s)
    s = re.sub(r"(?is)<h[2-4][^>]*>(.*?)</h[2-4]>", lambda m: "\n\n## " + _xml_text(m.group(1)).strip() + "\n\n", s)
    s = re.sub(r"(?is)<li[^>]*>", "\n\n• ", s)
    s = re.sub(r"(?is)</(p|div|li|tr|blockquote|section|article|h\d)>|<br\s*/?>", "\n\n", s)
    s = _clean(html.unescape(re.sub(r"<[^>]+>", " ", s)))
    return re.sub(r"(?m)^•\s*$\n?", "", s).strip()


def _epub(path: str):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        order = []
        opf = next((n for n in names if n.endswith(".opf")), None)
        if opf:
            ox = z.read(opf).decode("utf-8", "ignore")
            items = dict(re.findall(r'<item [^>]*?id="([^"]+)"[^>]*?href="([^"]+)"', ox))
            items.update({k: v for v, k in re.findall(r'<item [^>]*?href="([^"]+)"[^>]*?id="([^"]+)"', ox)})
            base = os.path.dirname(opf)
            for idref in re.findall(r'<itemref [^>]*?idref="([^"]+)"', ox):
                href = items.get(idref)
                if href:
                    order.append(os.path.normpath(os.path.join(base, href)).replace("\\", "/"))
        if not order:
            order = sorted(n for n in names if n.endswith((".xhtml", ".html", ".htm")))
        parts = [html_text(z.read(n).decode("utf-8", "ignore")) for n in order if n in names]
    return "\n\n".join(p for p in parts if p), len(parts)


def _fb2(path: str):
    raw = open(path, "rb").read().decode("utf-8", "ignore")
    raw = re.sub(r"(?is)<binary[^>]*>.*?</binary>", "", raw)
    raw = re.sub(r"(?is)<title>(.*?)</title>", lambda m: "\n\n# " + _xml_text(m.group(1)).strip() + "\n\n", raw)
    raw = re.sub(r"(?is)</p>|<empty-line/>", "\n\n", raw)
    return _clean(_xml_text(raw)), 1


def _odt(path: str):
    with zipfile.ZipFile(path) as z:
        xml = z.read("content.xml").decode("utf-8", "ignore")
    xml = re.sub(r"(?is)<text:h[^>]*>(.*?)</text:h>", lambda m: "\n\n# " + _xml_text(m.group(1)) + "\n\n", xml)
    xml = re.sub(r"(?is)</text:p>", "\n\n", xml)
    return _clean(_xml_text(xml)), 1


def _rtf(path: str):
    raw = open(path, "rb").read().decode("cp1251", "ignore")
    raw = re.sub(r"\\'([0-9a-f]{2})", lambda m: bytes.fromhex(m.group(1)).decode("cp1251", "ignore"), raw)
    raw = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), raw)
    raw = re.sub(r"\\par[d]?\b", "\n\n", raw)
    raw = re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", raw)
    return _clean(raw), 1


def _plain(path: str):
    data = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1251", "utf-16"):
        try:
            return _clean(data.decode(enc)), 1
        except UnicodeDecodeError:
            continue
    return _clean(data.decode("utf-8", "ignore")), 1


def _image(path: str):
    import file_search
    return file_search._read_image(path), 1


def read(path: str, ocr=None) -> dict:
    ext = os.path.splitext(path)[1].lower()
    fn = {".pdf": lambda p: _pdf(p, ocr), ".docx": _docx, ".pptx": _pptx, ".xlsx": _xlsx, ".epub": _epub,
          ".fb2": _fb2, ".odt": _odt, ".rtf": _rtf, ".html": lambda p: (html_text(_plain(p)[0]), 1),
          ".htm": lambda p: (html_text(_plain(p)[0]), 1)}.get(ext)
    if fn is None:
        fn = _image if ext in IMAGES else _plain
    text, pages = fn(path)
    return {"text": _clean(text), "pages": pages, "kind": ext.lstrip(".")}
