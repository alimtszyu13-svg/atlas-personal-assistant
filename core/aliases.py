"""
Свои названия команд: Atlas понимает твои слова для того, что он уже умеет.

    «Атлас, когда я говорю „кино“ — включай резку»     → «кино» = «включи резку»
    «запомни команду: учёба — это фокус на 50 минут»
    «запись — это запиши экран»
    «какие у меня команды?»,  «забудь команду кино»

Своё слово срабатывает целиком («кино») и с продолжением («кино интерстеллар» → «включи резку интерстеллар»).
Хранится только здесь (atlas_aliases.json, в git не попадает). Через окно Atlas, голосом и в чате — одинаково.
"""
import json
import os
import re
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "atlas_aliases.json")
_lock = threading.Lock()

_Q = r"[«»\"'„“”]"
_PREFIX = re.compile(r"^(?:(?:когда|если)\s+я\s+(?:говорю|скажу|произношу)|(?:запомни|добавь|создай)\s+(?:новую\s+)?"
                     r"(?:команду|слово)|when\s+i\s+say|new\s+command)\s*:?\s*", re.I)
_RENAME = re.compile(r"^(?:назови|переименуй)\s+(?:команду\s+)?(?P<b>.+?)\s+(?:как|в)\s+(?P<a>.+)$", re.I)
_LEAD = re.compile(r"^(?:то|это|значит|это значит|означает|будет|делай|сделай|выполняй|it means|means|do|run)\s+", re.I)
_IMPER = {"включай": "включи", "запускай": "запусти", "открывай": "открой", "ставь": "поставь", "показывай": "покажи",
          "пиши": "пиши", "ищи": "найди"}
_SEP = re.compile(r"\s*(?:,|—|–|\s-\s|:|=)\s*|\s+(?=(?:то|это|значит|означает|делай|сделай|выполняй|включай|запускай|"
                  r"открывай|ставь|it means|do|run|play|open)\s)", re.I)


def parse_add(raw: str):
    """Фраза → (своё слово, что делать) или None."""
    t = re.sub(r"^(?:атлас|atlas)[,\s]+", "", (raw or "").strip(), flags=re.I).strip(" .!?")
    m = _RENAME.match(t)
    # «назови три слова в русском языке…» — просьба, а не переименование (лог 23.10): новое имя короткое, старое — команда
    if m and len(m.group("a").split()) <= 3 and (re.match(r"^(?:назови|переименуй)\s+команду\s", t, re.I)
                                                 or _looks_like_command(m.group("b"))):
        return m.group("a"), m.group("b")
    m = _PREFIX.match(t)
    if m:
        rest = t[m.end():]
        q = re.match(r"^[«\"'„“](.+?)[»\"'“”]\s*(.*)$", rest)
        if q:
            a, b = q.group(1), q.group(2)
        else:
            sm = _SEP.search(rest)
            if not sm:
                return None
            a, b = rest[:sm.start()], rest[sm.end():]
        b = b.strip(" ,—–-:=")
        while _LEAD.match(b):
            b = _LEAD.sub("", b, count=1)
        first = b.split(" ", 1)
        if first and first[0].lower() in _IMPER:                  # «включай резку» → «включи резку»
            b = _IMPER[first[0].lower()] + (" " + first[1] if len(first) > 1 else "")
        return (a.strip(), b.strip()) if a.strip() and b.strip() else None
    m = re.match(r"^(?P<a>[\w-]+(?:\s+[\w-]+){0,2})\s+(?:теперь\s+)?(?:это|значит|означает)\s+(?:команда\s+)?(?P<b>.+)$",
                 t, re.I)
    if m and _looks_like_command(m.group("b")):                   # «погода это интересно» — не про команды
        return m.group("a"), m.group("b")
    return None


_DEL = re.compile(r"^(?:забудь|удали|убери|отмени)\s+(?:мою\s+)?(?:команду|слово)\s+(?P<a>.+)$|^forget\s+the\s+command\s+(?P<e>.+)$")
_LIST = re.compile(r"^(?:какие|покажи|список)\s+(?:у меня\s+)?(?:мои\s+|свои\s+)?(?:команды|слова|названия команд)|"
                   r"^мои\s+команды$|^(?:list|show)\s+my\s+commands$")
# короткие слова, которые нельзя занимать: ими Atlas управляется
_RESERVED = {"стоп", "хватит", "отмена", "выключись", "да", "нет", "атлас", "atlas", "stop", "yes", "no"}


def norm(text: str) -> str:
    t = re.sub(_Q, "", (text or "").lower().replace("ё", "е"))
    t = re.sub(r"^(?:атлас|atlas)[,\s]+", "", t.strip())
    t = re.sub(r"[^\w\s-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _load() -> dict:
    try:
        with open(FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, FILE)


def add(phrase: str, action: str) -> str:
    a, b = norm(phrase), (action or "").strip().strip(" .!").strip("«»\"'")
    if not a or not b:
        return "Не понял, какое слово и что делать."
    if a in _RESERVED:
        return f"Слово «{a}» занято — им Atlas управляется. Выбери другое."
    if norm(b) == a:
        return "Команда не может означать саму себя."
    with _lock:
        data = _load()
        data[a] = b
        _save(data)
    return f"Запомнил: «{a}» — это «{b}»."


def remove(phrase: str) -> str:
    a = norm(phrase)
    with _lock:
        data = _load()
        if a not in data:
            close = [k for k in data if a and (a in k or k in a)]
            if len(close) != 1:
                return f"Команды «{a}» нет."
            a = close[0]
        del data[a]
        _save(data)
    return f"Забыл команду «{a}»."


def listing() -> str:
    data = _load()
    if not data:
        return "Своих команд пока нет. Скажи, например: «когда я говорю „кино“ — включай резку»."
    return "Твои команды: " + "; ".join(f"«{k}» — {v}" for k, v in sorted(data.items()))


def expand(text: str) -> str:
    """Твоё слово → настоящая команда (если совпало), иначе текст как есть."""
    data = _load()
    if not data:
        return text
    t = norm(text)
    if t in data:
        return data[t]
    raw = re.sub(r"^(?:атлас|atlas)[,\s]+", "", (text or "").strip(), flags=re.I).split()
    for k in sorted(data, key=len, reverse=True):                  # «кино Интерстеллар» → «включи резку Интерстеллар»
        if t.startswith(k + " "):
            rest = " ".join(raw[len(k.split()):]).strip(" .!?")
            return f"{data[k]} {rest}".strip()
    return text


def handle(text: str):
    """Фраза про свои команды → ответ, иначе None (не про это)."""
    t = norm(text)
    raw = re.sub(r"^(?:атлас|atlas)[,\s]+", "", (text or "").strip(), flags=re.I).strip(" .!?")
    if _LIST.search(t):
        return listing()
    m = _DEL.match(t)
    if m:
        return remove(m.group("a") or m.group("e"))
    pair = parse_add(text)
    if pair:
        return add(*pair)
    return None


_VERBS = re.compile(r"^(?:включи|выключи|открой|закрой|запусти|поставь|сделай|покажи|найди|запиши|прекрати|"
                    r"фокус|режим|пиши|громче|тише|пауза|дальше|следующ|напомни|скажи|расскажи|отправь|пришли|"
                    r"play|open|start|stop|turn|show|record|focus)")


def _looks_like_command(b: str) -> bool:
    return bool(_VERBS.match(norm(b)))
