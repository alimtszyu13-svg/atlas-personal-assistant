"""
Диктовка в любое окно: «Атлас, пиши за мной» — дальше всё сказанное печатается туда, где стоит курсор
(Word, Telegram, браузер, блокнот), с точками и запятыми. Имя звать не нужно, пока диктовка идёт.

Голосом во время диктовки:
    «сотри последнее»            — убрать последнюю фразу
    «новая строка» / «абзац»     — перенос строки (в мессенджерах не отправляет)
    «исправь ошибки», «сделай вежливее / короче / официальнее / проще», «переведи на английский»
                                 — переписать всё надиктованное и заменить в окне
    «прочитай, что написал»      — прочитать вслух
    «отправь»                    — Enter (в мессенджере — отправить) и закончить
    «хватит» / «готово» / «закончи диктовку» — закончить
"""
import re

from core import win_input

START = re.compile(
    r"(?:пиши|печатай|записывай|набирай)\s+(?:за\s+мной|под\s+диктовку|что\s+я\s+говорю)|"
    r"(?:режим|начни|начать|включи)\s+диктовк|^диктовка$|^диктуй$|"
    r"\b(?:dictation\s+mode|start\s+dictation|type\s+what\s+i\s+say|take\s+dictation)\b")

_STOP = {"хватит", "стоп", "готово", "всё", "все", "закончи диктовку", "закончить диктовку", "конец диктовки",
         "останови диктовку", "выключи диктовку", "хватит диктовки", "диктовка всё", "закончили",
         "stop", "stop dictation", "done", "end dictation", "that's it", "that is it"}
_UNDO = {"сотри последнее", "сотри", "удали последнее", "удали последнюю фразу", "сотри последнюю фразу",
         "отмени", "отмена", "убери последнее", "undo", "delete that", "scratch that", "delete last"}
_NEWLINE = {"новая строка", "с новой строки", "перенос строки", "new line", "next line"}
_PARAGRAPH = {"абзац", "новый абзац", "new paragraph"}
_SEND = {"отправь", "отправить", "отправляй", "send", "send it"}
_READ = {"прочитай что написал", "прочитай что я написал", "прочитай", "что я написал", "read it back",
         "read what i wrote"}
_REWRITE = (
    (re.compile(r"исправь (?:ошибки|опечатки|текст)|проверь (?:ошибки|текст)|fix (?:the )?(?:mistakes|errors|typos)"),
     "Fix spelling, grammar and punctuation. Keep the wording, language and meaning."),
    (re.compile(r"(?:сделай|перепиши) (?:по)?вежлив|more polite|politer"),
     "Rewrite it more politely and warmly. Same language, same meaning."),
    (re.compile(r"(?:сделай|перепиши) (?:по)?короче|сократи|shorter|shorten"),
     "Make it shorter and clearer, keep the key points. Same language."),
    (re.compile(r"(?:сделай|перепиши) (?:по)?официальн|деловым|formal"),
     "Rewrite it in a formal, business tone. Same language."),
    (re.compile(r"(?:сделай|перепиши) (?:по)?проще|простыми словами|simpler"),
     "Rewrite it in simpler words. Same language."),
    (re.compile(r"переведи на английский|по-английски|translate (?:it )?(?:to|into) english"),
     "Translate it into natural English."),
    (re.compile(r"переведи на русский|по-русски|translate (?:it )?(?:to|into) russian"),
     "Translate it into natural Russian."),
)

_s = {"on": False, "hwnd": 0, "title": "", "chunks": [], "io": None, "lang": "ru"}


def _io():
    return _s["io"] or win_input


def _ru() -> bool:
    return _s["lang"] == "ru"


def norm(text: str) -> str:
    t = (text or "").lower().replace("ё", "е").strip()
    t = re.sub(r"^(?:атлас|atlas)[,\s]+", "", t)
    return re.sub(r"[^\w\s'-]", "", t).strip()


def is_start(text: str) -> bool:
    return bool(START.search(norm(text)))


def active() -> bool:
    return _s["on"]


def typed() -> str:
    return "".join(_s["chunks"])


def start(io=None, lang: str = None) -> str:
    if io is not None:
        _s["io"] = io
    try:
        from voice import get_response_language
        _s["lang"] = lang or get_response_language()
    except Exception:
        _s["lang"] = lang or "ru"
    io = _io()
    h = io.foreground()
    if not h or io.is_atlas(h):
        h = _s["hwnd"] if _s["hwnd"] and not io.is_atlas(_s["hwnd"]) else 0
    if not h:
        return ("Поставь курсор туда, где печатать — в документ или чат, — и скажи «пиши за мной» ещё раз."
                if _ru() else "Put the cursor where I should type — a document or a chat — and say it again.")
    _s.update(on=True, hwnd=h, title=io.title_of(h), chunks=[])
    where = _s["title"][:60] or ("это окно" if _ru() else "this window")
    return (f"Пишу в «{where}». Говорите — имя звать не нужно. «Хватит» — закончу."
            if _ru() else f"Typing into “{where}”. Just talk — say “done” to finish.")


def stop() -> str:
    n = len(typed().split())
    _s.update(on=False, chunks=[])
    return (f"Диктовка закончена: {n} {_words(n)}." if _ru() else f"Dictation finished: {n} words.")


def _words(n: int) -> str:
    n100, n10 = n % 100, n % 10
    return "слов" if 11 <= n100 <= 14 else "слово" if n10 == 1 else "слова" if 2 <= n10 <= 4 else "слов"


def _target() -> bool:
    io = _io()
    return io.foreground() == _s["hwnd"] or io.focus(_s["hwnd"])


def _type(text: str) -> None:
    prev = typed()
    lead = "" if not prev or prev.endswith(("\n", " ")) else " "
    t = text.strip()
    if prev and not prev.endswith("\n") and not prev.rstrip().endswith((".", "!", "?", "…", ":")) \
            and t[:1].isupper() and not t[:2].isupper():
        t = t[0].lower() + t[1:]                        # продолжение фразы после паузы — с маленькой буквы
    chunk = lead + t
    _io().paste(chunk)
    _s["chunks"].append(chunk)


def _erase(n: int) -> None:
    if n > 0:
        _io().keys("backspace", n)


def _rewrite(rule: str) -> str:
    text = typed().strip()
    if not text:
        return "Пока нечего переписывать." if _ru() else "Nothing to rewrite yet."
    from core import quick_llm
    try:
        new = quick_llm.ask("You edit text the user dictated. " + rule +
                            " Return ONLY the resulting text, no quotes, no comments.", text,
                            max_tokens=min(2000, len(text) // 2 + 300))
    except Exception as e:
        return (f"Не получилось переписать: {e}" if _ru() else f"Couldn't rewrite it: {e}")
    if not _target():
        return "Не могу вернуться в окно — оно закрыто?" if _ru() else "Can't get back to that window."
    _erase(len(typed()))
    _s["chunks"] = []
    _io().paste(new)
    _s["chunks"].append(new)
    return "Переписал." if _ru() else "Rewritten."


def handle(text: str):
    """Фраза во время диктовки → что сказать вслух (или None — просто напечатал)."""
    if not _s["on"]:
        return None
    cmd = norm(text)
    if not cmd:
        return None
    if cmd in _STOP:
        return stop()
    if not _target():
        _s["on"] = False
        return ("Окно, куда я печатал, закрыто или недоступно — диктовку закончил."
                if _ru() else "The window I was typing into is gone — dictation stopped.")
    short = len(cmd.split()) <= 5
    if short and cmd in _UNDO:
        if not _s["chunks"]:
            return "Стирать нечего." if _ru() else "Nothing to delete."
        _erase(len(_s["chunks"].pop()))
        return None
    if short and cmd in _NEWLINE:
        _io().keys("shift+enter")
        _s["chunks"].append("\n")
        return None
    if short and cmd in _PARAGRAPH:
        _io().keys("shift+enter", 2)
        _s["chunks"].append("\n\n")
        return None
    if short and cmd in _SEND:
        _io().keys("enter")
        _s.update(on=False, chunks=[])
        return "Отправил." if _ru() else "Sent."
    if short and cmd in _READ:
        return typed().strip() or ("Пока ничего." if _ru() else "Nothing yet.")
    if len(cmd.split()) <= 6:
        for rx, rule in _REWRITE:
            if rx.search(cmd):
                return _rewrite(rule)
    _type(text)
    return None
