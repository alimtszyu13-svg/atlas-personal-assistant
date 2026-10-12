"""
«Проверь перед отправкой»: выдели текст (письмо, сообщение, эссе) и скажи «проверь» — Atlas найдёт ошибки,
опечатки, пропущенные запятые, повторы и неудачные места и предложит исправить прямо там.

    «проверь» / «проверь текст» / «проверь эссе перед отправкой» / «proofread this»
    → «Нашёл 3 ошибки: «извени» → «извини»; запятая перед «что»; повтор «очень». Исправить?»
    «да» → исправленный текст встаёт на место выделенного; в Word — по абзацам и с режимом исправлений
           (видно, что поменялось, можно принять или отклонить)
Ничего не выделено, а на экране Word — проверяет весь документ. Стиль и смысл автора не меняются.
"""
import json
import re
import time

PENDING_TTL = 300
MAX_CHARS = 24000
_s = {"pending": None, "said": None}


def _ru(text: str) -> bool:
    return bool(re.search(r"[а-яё]", text or "", re.I))


class Env:
    def selected(self) -> tuple:
        """(текст, окно) — выделенное в активном окне."""
        from core import squeeze, win_input
        hwnd = win_input.foreground()
        return squeeze.selected_text(), hwnd

    def word(self):
        """Открытый документ Word на переднем плане → объект документа или None."""
        try:
            from core import worklog
            app, _t, _ = worklog._foreground()
            if (app or "").lower() not in ("winword.exe", "winword"):
                return None
            import comtypes.client
            return comtypes.client.GetActiveObject("Word.Application").ActiveDocument
        except Exception as e:
            print(f"[проверка] Word: {e}")
            return None

    def paste(self, hwnd: int, text: str) -> bool:
        from core import win_input
        win_input.focus(hwnd)
        time.sleep(0.15)
        win_input.paste(text)
        return True


_env = None


def _get_env():
    global _env
    if _env is None:
        _env = Env()
    return _env


def check(text: str, ask=None) -> dict:
    """→ {"fixed": текст, "issues": [кратко, …]}."""
    lang = "Russian" if _ru(text) else "English"
    system = (
        "You are a careful proofreader. Fix ONLY real mistakes in the user's text: spelling, typos, grammar, "
        "punctuation (commas!), agreement, wrong word forms, accidental repeats, obvious awkwardness. Keep the author's "
        "style, tone, slang, emoji, line breaks and meaning; do not rewrite, do not make it formal, do not add content. "
        "Return strict JSON: {\"fixed\": \"the full corrected text\", \"issues\": [\"short note per fix, e.g. "
        "«извени» → «извини»\", ...]}. Notes in " + lang + ", at most 8, most important first. "
        "If there are no mistakes: {\"fixed\": <same text>, \"issues\": []}.")
    from core import quick_llm
    raw = (ask or quick_llm.ask)(system, text[:MAX_CHARS], max_tokens=min(6000, 400 + len(text) // 2))
    from core import memory
    data = memory.parse_json(raw) if hasattr(memory, "parse_json") else {}
    if not data:
        try:
            data = json.loads(re.search(r"\{.*\}", raw or "", re.S).group(0))
        except Exception:
            data = {}
    fixed = data.get("fixed") if isinstance(data.get("fixed"), str) else text
    issues = [str(i) for i in (data.get("issues") or []) if str(i).strip()][:8]
    if fixed.strip() == text.strip():
        issues = []
    return {"fixed": fixed, "issues": issues}


def _summary(issues: list, ru: bool) -> str:
    n = len(issues)
    if ru:
        w = "ошибку" if n % 10 == 1 and n % 100 != 11 else "ошибки" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "ошибок"
        return f"Нашёл {n} {w}: " + "; ".join(issues[:4]) + ("; и ещё " + str(n - 4) if n > 4 else "") + ". Исправить?"
    return f"Found {n} issue(s): " + "; ".join(issues[:4]) + ". Fix them?"


def start(env=None, ask=None) -> str:
    env = env or _get_env()
    text, hwnd = env.selected()
    text = (text or "").strip()
    doc = None
    if len(text) < 2:
        doc = env.word()
        if doc is None:
            return "Выдели текст, который проверить, и скажи «проверь» ещё раз."
        paras = [str(p.Range.Text) for p in doc.Paragraphs]
        text = "\n".join(p.rstrip("\r\x07") for p in paras)
    ru = _ru(text)
    if len(text) > MAX_CHARS:
        return (f"Текст большой ({len(text) // 1000} тыс. знаков) — выдели часть, до {MAX_CHARS // 1000} тысяч, и я проверю."
                if ru else "That's too long — select a part and ask again.")
    try:
        r = check(text, ask)
    except Exception as e:
        return (f"Не получилось проверить: {e}" if ru else f"Couldn't check: {e}")[:200]
    if not r["issues"]:
        _s["pending"] = None
        return "Ошибок не нашёл — можно отправлять." if ru else "No mistakes found — good to send."
    _s["pending"] = {"text": text, "fixed": r["fixed"], "issues": r["issues"], "hwnd": hwnd, "doc": doc,
                     "created": time.time(), "ru": ru}
    said = _summary(r["issues"], ru)
    _s["said"] = said
    return said


def apply(env=None) -> str:
    env = env or _get_env()
    p = _s["pending"]
    _s["pending"] = None
    if not p:
        return "Нечего исправлять."
    ru = p["ru"]
    if p["doc"] is not None:
        try:
            doc = p["doc"]
            doc.TrackRevisions = True
            old = p["text"].split("\n")
            new = p["fixed"].split("\n")
            if len(old) != len(new):
                return ("Исправления поменяли число абзацев — в Word вставлять не стал, чтобы не сломать оформление. "
                        "Выдели абзац и скажи «проверь»." if ru else "Paragraph count changed — select a paragraph instead.")
            n = 0
            for i, (a, b) in enumerate(zip(old, new)):
                if a != b:
                    rng = doc.Paragraphs(i + 1).Range
                    rng.MoveEnd(1, -1)                         # без знака конца абзаца
                    rng.Text = b
                    n += 1
            return (f"Исправил {n} абзац{'ев' if n >= 5 or n == 0 else 'а' if n > 1 else ''} — в режиме исправлений, "
                    "можно принять или отклонить." if ru else f"Fixed {n} paragraphs with track changes.")
        except Exception as e:
            return (f"Word не дал исправить: {e}" if ru else f"Word refused: {e}")[:200]
    env.paste(p["hwnd"], p["fixed"])
    return "Исправил." if ru else "Fixed."


_ASK = re.compile(r"^(?:атлас[,\s]+)?(?:проверь|проверка|вычитай|исправь ошибки|найди ошибки|proofread|check)"
                  r"(?:\s+(?:это|этот|эту|мой|моё|мое|мою|текст\w*|письмо|сообщени\w*|эссе|сочинени\w*|документ\w*|"
                  r"выделенн\w*|ошибки|орфографи\w*|грамматик\w*|this|my|the|text|message|email|essay|spelling))*"
                  r"(?:\s+(?:на ошибки|перед отправкой|before sending|for mistakes))?\s*[.!?]?$", re.I)
_YES = re.compile(r"^(?:да|давай|исправь|исправляй|ок|окей|ага|yes|fix|go)\b", re.I)
_NO = re.compile(r"^(?:нет|не надо|оставь|отмена|no|cancel)\b", re.I)


def handle(text: str, last: str = None, env=None, ask=None):
    t = " ".join((text or "").split()).strip()
    p = _s["pending"]
    if p and time.time() - p["created"] < PENDING_TTL and (last is None or last == _s.get("said")):
        if _YES.search(t) and len(t) < 30:
            return apply(env)
        if _NO.search(t) and len(t) < 30:
            _s["pending"] = None
            return "Оставил как есть." if p["ru"] else "Left as is."
    if _ASK.match(t) and not re.search(r"почт|погод|интернет|обновлен|диск|mail|weather", t, re.I):
        return start(env, ask)
    return None
