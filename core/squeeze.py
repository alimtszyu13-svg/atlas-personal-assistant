"""
«Сожми выделенное»: выдели текст в любом окне (браузер, Word, PDF, Telegram) и
    • нажми Ctrl+Alt+S — или скажи «Атлас, сожми это»;
    • «объясни проще», «перескажи коротко», «сделай по пунктам» — то же, другим стилем.
Результат — в чате Atlas (коротко — вслух) и в буфере обмена: Ctrl+V — и вставил куда нужно.
Длинный текст (хоть целая глава) сжимается по частям, как конспект документа.
"""
import re
import time

_STYLES = (
    (re.compile(r"объясни|простыми словами|проще|для чайника|explain|simpler", re.I), "simple"),
    (re.compile(r"по пунктам|тезис|списком|bullet|points", re.I), "points"),
    (re.compile(r"сожми|сократи|коротко|кратко|главное|суть|tl;?dr|summar|shorten", re.I), "short"),
)
_ABOUT = re.compile(r"\b(?:это|этот|эту|выделенн\w*|текст\w*|скопированн\w*|абзац\w*|selected|this)\b", re.I)
_VERB = re.compile(r"^(?:сожми|сократи|объясни|перескажи|сделай|расскажи|выдели|дай|summarize|explain|shorten|"
                   r"tl;?dr|о ч[её]м)", re.I)


def intent(text: str):
    """→ стиль (short | simple | points) или None."""
    t = re.sub(r"^(?:атлас|atlas)[,\s]+", "", (text or "").strip(), flags=re.I).strip(" .!?")
    if not _VERB.match(t) or len(t.split()) > 8:
        return None
    if re.search(r"ролик|видео|страниц|стать|вкладк|сайт|ссылк|документ|файл|книг|youtube", t, re.I):
        return None                                        # это уже про ссылку или документ
    if not _ABOUT.search(t) and not re.match(r"^(?:сожми|сократи)$", t, re.I):
        return None
    for rx, style in _STYLES:
        if rx.search(t):
            return style
    return "short"


def selected_text(io=None, wait: float = 0.6) -> str:
    """Выделенное в окне, где курсор: Ctrl+C от твоего имени (если это не окно Atlas), иначе — что в буфере."""
    import ctypes
    import pyperclip
    from core import win_input
    io = io or win_input
    h = io.foreground()
    if h and not io.is_atlas(h):
        try:
            seq = ctypes.windll.user32.GetClipboardSequenceNumber()
        except Exception:
            seq = None
        io.keys("ctrl+c")
        deadline = time.time() + wait
        while time.time() < deadline:
            try:
                if seq is None or ctypes.windll.user32.GetClipboardSequenceNumber() != seq:
                    break
            except Exception:
                break
            time.sleep(0.03)
    try:
        return (pyperclip.paste() or "").strip()
    except Exception:
        return ""


def squeeze(style: str = "short", text: str = None, ask=None, copy=True) -> str:
    from core import text_notes
    text = (selected_text() if text is None else text).strip()
    if len(text) < 40:
        return ("Не вижу выделенного текста. Выдели его мышкой и скажи ещё раз (или нажми Ctrl+Alt+S)."
                if not text else "Тут и так коротко.")
    out = text_notes.notes(text, style, ask=ask)
    if copy and out:
        try:
            import pyperclip
            pyperclip.copy(out)
        except Exception:
            pass
    return out


def spoken(result: str, limit: int = 260) -> str:
    """Вслух — только начало; всё целиком — в чате и в буфере."""
    plain = re.sub(r"[#*_>`]|^\s*[-•\d.]+\s*", "", result, flags=re.M)
    plain = re.sub(r"\s+", " ", plain).strip()
    if len(plain) <= limit:
        return plain
    cut = plain[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[:end + 1] if end > 80 else cut.rstrip() + "…") + " Полностью — в чате и в буфере обмена."
