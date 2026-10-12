"""
Конспект большого текста (книга, учебник, статья, договор) — по частям, параллельно, потом общий.

    notes(text, style="notes")   →  markdown
    styles: notes   — полный конспект с разделами (как у записей)
            short   — «сожми»: главное в 3–6 пунктах
            simple  — «объясни проще»: простыми словами, с примером
            points  — «по пунктам»: тезисы

Как это работает с огромным текстом: текст режется на части ~12 000 знаков по абзацам (не посреди фразы),
каждая часть конспектируется отдельно и параллельно (4 сразу), потом конспекты частей сводятся в один.
Если и их слишком много — сводятся ещё раз. Ссылки на страницы «[стр. 12]» сохраняются.
"""
import re
from concurrent.futures import ThreadPoolExecutor

PART = 12000
REDUCE_MAX = 40000
WORKERS = 4

RULES = {
    "notes": ("You make study notes from a long text (book, textbook, article, document). Write in {lang}. "
              "Markdown sections, skip empty ones: '## Коротко' (3–5 sentences: what it is about and the main "
              "conclusion), '## Главное' (key ideas as bullets), '## Термины и определения', '## Цифры, даты, "
              "имена', '## Выводы', '## Вопросы для себя' (3–5). Section titles in {lang}. Keep page references "
              "like [стр. 12] next to the points they support. Be concrete, no filler, never invent facts."),
    "short": ("Compress the text to the essentials in {lang}: one sentence of gist, then 3–6 short bullets. "
              "No intro, no filler."),
    "simple": ("Explain the text in simple words in {lang}, as to a smart 15-year-old: what it says, why it "
               "matters, one everyday example. Short paragraphs. No filler."),
    "points": "Turn the text into clear numbered points (theses) in {lang}. One idea per point, no filler.",
}
PART_RULES = ("This is part {i} of {n} of a long text. Make compact notes of THIS part in {lang}: key ideas, terms, "
              "numbers, names, conclusions as bullets; keep page references like [стр. 12]. No intro.")


def _lang(text: str) -> str:
    try:
        from voice import get_response_language
        if get_response_language() == "en":
            return "English"
    except Exception:
        pass
    return "Russian"


def split(text: str, size: int = PART) -> list:
    """Текст → части не больше size знаков, по границам абзацев (длинный абзац — по предложениям)."""
    paras = [p for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    parts, cur = [], ""
    for p in paras:
        while len(p) > size:                              # абзац-простыня — режем по предложениям
            cut = max(p.rfind(". ", 0, size), p.rfind("\n", 0, size))
            cut = cut + 1 if cut > size // 3 else size
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(p[:cut].strip())
            p = p[cut:].strip()
        if len(cur) + len(p) + 2 > size and cur:
            parts.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur.strip():
        parts.append(cur)
    return parts


def notes(text: str, style: str = "notes", ask=None, progress=None, lang: str = None) -> str:
    from core import quick_llm
    ask = ask or quick_llm.ask
    lang = lang or _lang(text)
    rules = RULES.get(style, RULES["notes"]).replace("{lang}", lang)
    text = (text or "").strip()
    if not text:
        return ""
    if len(text) <= PART * 1.3:
        if progress:
            progress(90)
        return ask(rules, text, max_tokens=1800 if style == "notes" else 700).strip()
    parts = split(text)
    done = {"n": 0}

    def one(args):
        i, part = args
        r = ask(PART_RULES.format(i=i + 1, n=len(parts), lang=lang), part, max_tokens=900)
        done["n"] += 1
        if progress:
            progress(int(80 * done["n"] / len(parts)))
        return r
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        partial = list(ex.map(one, enumerate(parts)))
    joined = "\n\n".join(f"### Часть {i + 1}\n{p}" for i, p in enumerate(partial))
    while len(joined) > REDUCE_MAX:                      # конспектов частей слишком много — сводим ещё раз
        groups = split(joined, REDUCE_MAX // 2)
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            joined = "\n\n".join(ex.map(lambda g: ask(PART_RULES.format(i=1, n=1, lang=lang), g, max_tokens=1200),
                                        groups))
    if progress:
        progress(90)
    return ask(rules + " The input is notes of consecutive parts of one text — merge them into one whole.",
               joined, max_tokens=2200 if style == "notes" else 800).strip()
