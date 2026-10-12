"""
«Скажи, когда…» — Atlas следит за компьютером сам и говорит, когда случилось.

    «скажи, когда скачается файл»                   → загрузка в Загрузках закончилась
    «сообщи, когда закончится рендер / установка»   → самая загруженная программа освободила процессор
    «скажи, когда закроется Premiere»                → программа закрылась
    «скажи, когда на этой странице появится "в наличии"» → страница проверяется раз в 2 минуты
    «дай знать, когда страница изменится»
    «скажи, когда в Загрузках появится файл»        → новый файл в папке
    «скажи, когда вернётся интернет»
    «скажи, когда ноутбук зарядится (до 80%)»

Сообщение — голосом и, если ты отошёл от компьютера (или просил «на телефон»), уведомлением на телефон.
Наблюдение само заканчивается через 6 часов (страницы — через сутки). Список лежит в atlas_watchers.json,
только на этом компьютере; после перезапуска Atlas страницы, папки, интернет и зарядка продолжают отслеживаться.

    intent(text) → ("add", dict) | ("list",) | ("cancel", слова) | None
    add(w) / listing() / cancel(q) / check_all(env) / start(announce)
"""
import hashlib
import json
import os
import re
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "atlas_watchers.json")
TICK = 5
WEB_EVERY = 120
TTL = 6 * 3600
WEB_TTL = 24 * 3600
BUSY_CPU = 10.0         # % одного ядра — программа «освободилась», если ниже…
BUSY_HOLD = 30          # …столько секунд подряд
_PARTIAL = (".crdownload", ".part", ".tmp", ".download", ".partial", ".opdownload")
_PERSIST = {"web", "folder", "internet", "battery", "download", "price"}
_PRICE = re.compile(r"(\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,](\d{1,2}))?\s*(сом\w*|kgs|₽|руб\w*|р\.|\$|usd|€|eur|"
                    r"тг|₸|тенге|сум|uzs|₴|грн)|(\$|€|₽)\s*(\d{1,3}(?:[ ,]\d{3})+|\d+)(?:\.(\d{1,2}))?", re.I)


def prices(text: str) -> list:
    """Цены на странице по порядку: [(число, валюта)]."""
    out = []
    for m in _PRICE.finditer(text or ""):
        if m.group(1):
            n = float(re.sub(r"[ \u00a0\u202f]", "", m.group(1)) + ("." + m.group(2) if m.group(2) else ""))
            cur = m.group(3).lower()
        else:
            n = float(re.sub(r"[ ,]", "", m.group(5)) + ("." + m.group(6) if m.group(6) else ""))
            cur = m.group(4)
        if 0 < n < 1e9:
            out.append((n, cur))
    return out


def main_price(text: str):
    """Цена товара — первая цена на странице (обычно это она; рекомендации и «похожие» — ниже)."""
    p = prices(text)
    return p[0] if p else None
_lock = threading.Lock()
_s = {"items": None, "announce": None, "thread": None}


def _ru(text: str) -> bool:
    return bool(re.search(r"[а-яё]", text or "", re.I))


# --- хранение --------------------------------------------------------------------
def _load() -> list:
    if _s["items"] is None:
        try:
            with open(FILE, encoding="utf-8") as f:
                items = json.load(f)
            _s["items"] = [w for w in items if w.get("kind") in _PERSIST]    # просроченные уберёт check_all
        except Exception:
            _s["items"] = []
    return _s["items"]


def _save() -> None:
    try:
        tmp = FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([w for w in _s["items"] or [] if w.get("kind") in _PERSIST], f, ensure_ascii=False, indent=1)
        os.replace(tmp, FILE)
    except Exception as e:
        print(f"[слежу] не сохранил список: {e}")


# --- окружение (в тестах подменяется) ------------------------------------------------
class Env:
    def __init__(self):
        self.home = os.path.expanduser("~")

    def folder(self, name: str) -> str:
        names = {"downloads": "Downloads", "desktop": "Desktop", "documents": "Documents", "pictures": "Pictures",
                 "videos": "Videos", "music": "Music"}
        if os.path.isabs(name or ""):
            return name
        return os.path.join(self.home, names.get(name, name or "Downloads"))

    def listdir(self, path: str) -> list:
        try:
            return os.listdir(path)
        except Exception:
            return []

    _last = 0.0

    def procs(self) -> list:
        """[{pid, name, cpu}] — cpu в процентах одного ядра (100 = одно ядро занято полностью)."""
        import psutil
        if time.time() - self._last > 3:              # давно не мерили — замер за последнюю секунду, а не за час
            for p in psutil.process_iter():
                try:
                    p.cpu_percent(None)
                except Exception:
                    pass
            time.sleep(1.0)
        self._last = time.time()
        out = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                out.append({"pid": p.info["pid"], "name": p.info["name"] or "", "cpu": p.cpu_percent(None)})
            except Exception:
                pass
        return out

    def fetch(self, url: str) -> str:
        from core import link_notes
        raw = link_notes.fetch(url, timeout=20)
        try:
            from core import doc_text
            return doc_text.html_text(raw)
        except Exception:
            return re.sub(r"<[^>]+>", " ", raw)

    def online(self) -> bool:
        import socket
        for host in (("1.1.1.1", 53), ("8.8.8.8", 53)):
            try:
                socket.create_connection(host, timeout=2).close()
                return True
            except Exception:
                continue
        return False

    def battery(self):
        import psutil
        b = psutil.sensors_battery()
        return (b.percent, b.power_plugged) if b else None

    def idle(self) -> float:
        try:
            from core import focus
            return focus._idle()
        except Exception:
            return 0.0

    def current_page(self):
        from core import link_notes
        return link_notes.current_page()

    def now(self) -> float:
        return time.time()


_env = None


def _get_env():
    global _env
    if _env is None:
        _env = Env()
    return _env


# --- разбор фразы ----------------------------------------------------------------------
_ASK = re.compile(r"(?:скажи|сообщи|напиши|уведоми|предупреди|дай(?:те)? знать|маякни|позови|скажешь|крикни|"
                  r"пришли|отправь уведомление|tell me|let me know|notify me|ping me|alert me|warn me)"
                  r"[^.?!]{0,40}?\b(?:когда|как только|если|when|once|as soon as|if)\b\s*(.*)", re.I)
_LIST = re.compile(r"(?:за чем|что) (?:ты )?(?:следишь|отслеживаешь|ждешь|ждёшь)|мои (?:слежки|наблюдения)|"
                   r"what are you (?:watching|waiting for)", re.I)
_CANCEL = re.compile(r"(?:не (?:надо |нужно )?(?:следить|ждать|сообщать)|перестань (?:следить|ждать)|отмени (?:слежк|наблюдени|ожидани)\w*|"
                     r"хватит следить|stop watching|cancel (?:the )?watch\w*)\s*(?:за\s+)?(.*)", re.I)
_QUOTE = re.compile(r"[«\"“'']([^»\"”'']{1,80})[»\"”'']")
_URL = re.compile(r"https?://\S+|\b(?:[\w-]+\.)+(?:ru|com|kg|kz|org|net|io|me|by|ua|uz|app|dev|shop|store)(?:/\S*)?", re.I)
_FOLDERS = {"загрузк": "downloads", "downloads": "downloads", "рабоч": "desktop", "desktop": "desktop",
            "документ": "documents", "documents": "documents", "картинк": "pictures", "изображен": "pictures",
            "pictures": "pictures", "видео": "videos", "videos": "videos", "музык": "music"}
_BUSY = re.compile(r"рендер|экспорт|установ|инстал|компил|сборк|собер|обработ|конверт|архивац|архивир|"
                   r"распаков|кодир|копирован|скопиру|выгруз|обнов|процессор освобод|компьютер освобод|"
                   r"render|export|install|compil|build|encod|convert|copy|unzip|extract|update|cpu", re.I)
_EXIT = re.compile(r"закро|закрыл|выключ|заверш|выйд|вылет|упад|close|exit|quit|stop", re.I)
_PROC_NAMES = {      # как называют → имя процесса
    "премьер": "adobe premiere pro", "premiere": "adobe premiere pro", "афтер": "afterfx", "after effects": "afterfx",
    "давинчи": "resolve", "davinci": "resolve", "резолв": "resolve", "блендер": "blender", "blender": "blender",
    "обс": "obs64", "obs": "obs64", "стим": "steam", "steam": "steam", "хром": "chrome", "chrome": "chrome",
    "ворд": "winword", "word": "winword", "эксел": "excel", "excel": "excel", "фотошоп": "photoshop",
    "photoshop": "photoshop", "хендбрейк": "handbrake", "handbrake": "handbrake", "винрар": "winrar",
    "winrar": "winrar", "7-zip": "7z", "7zip": "7z", "архиватор": "7z", "торрент": "qbittorrent",
    "qbittorrent": "qbittorrent", "utorrent": "utorrent", "телеграм": "telegram", "telegram": "telegram",
    "дискорд": "discord", "discord": "discord", "зум": "zoom", "zoom": "zoom", "код": "code", "vs code": "code",
    "игр": "", "установщик": "setup", "setup": "setup", "msiexec": "msiexec", "unity": "unity", "юнити": "unity",
    "unreal": "unrealeditor", "анрил": "unrealeditor", "visual studio": "devenv", "android studio": "studio64",
}


def _proc_word(text: str) -> str:
    low = text.lower()
    for k, v in _PROC_NAMES.items():
        if re.search(r"(?<![\w])" + re.escape(k), low) and v:
            return v
    m = re.search(r"\b([A-Za-z][\w.+-]{2,})\b", text)             # латиницей — как процесс
    return m.group(1).lower() if m else ""


def _phone(text: str) -> bool:
    return bool(re.search(r"телефон|смартфон|phone|push|пуш", text or "", re.I))


def parse(rest: str, full: str) -> dict:
    """Что после «когда…» → наблюдение (kind, target, text) или {} — не поняли."""
    r = " ".join((rest or "").replace("ё", "е").split())
    low = r.lower()
    ru = _ru(full)
    base = {"ru": ru, "phone": _phone(full), "said": full.strip()}
    if re.search(r"интернет|сеть|wi-?fi|вай-?фай|связь|онлайн|internet|online|connection", low) and \
            re.search(r"появ|верн|зараб|подключ|будет|back|return|work|up\b", low):
        return dict(base, kind="internet")
    if re.search(r"заряд|батаре|аккумулятор|charge|battery", low):
        m = re.search(r"(\d{1,3})\s*(?:%|процент|percent)", low)
        return dict(base, kind="battery", target=min(100, int(m.group(1))) if m else 100)
    folder = next((v for k, v in _FOLDERS.items() if re.search(r"(?<![\w])" + k, low)), None)
    q = _QUOTE.search(full)
    url = _URL.search(r)
    if re.search(r"подешев\w*|станет дешевле|цена (?:упад|снизит|опустит|будет ниже)|скидк|дешевле|price drops|cheaper|on sale", low):
        target = url.group(0) if url else "current"
        if url and not url.group(0).startswith("http"):
            target = "https://" + url.group(0)
        m = re.search(r"(?:ниже|до|меньше|below|under|to)\s+(\d[\d\s]*)", r)
        limit = float(re.sub(r"\s", "", m.group(1))) if m else 0
        return dict(base, kind="price", target=target, limit=limit)
    if re.search(r"страниц|сайт|вкладк|page|site|tab", low) or url:
        target = url.group(0) if url else "current"
        if url and not url.group(0).startswith("http"):
            target = "https://" + url.group(0)
        word = q.group(1) if q else ""
        if not word:
            m = re.search(r"(?:появится|появятся|будет|станет|напишут|appears?|says?|shows?)\s+"
                          r"(?:слово|надпись|текст|фраза|кнопка|word|text)?\s*(.+?)(?:\s+(?:на|в|on)\s+"
                          r"(?:этой|этом|той|сайте|странице|вкладке|this|the)\b.*)?$", r, re.I)
            if m:
                word = m.group(1).strip(" .!?»«\"")
                if _URL.fullmatch(word or ""):
                    word = ""
        if word:
            return dict(base, kind="web", target=target, text=word[:80])
        if re.search(r"изменит|обновит|поменяет|change|update|new", low):
            return dict(base, kind="web", target=target, text="")
        return {}
    if folder and re.search(r"появ|придет|придёт|упадет|сохранит|appear|lands|new file|arrive", low):
        return dict(base, kind="folder", target=folder, text=q.group(1) if q else "")
    if re.search(r"скача|загрузит|загрузк|докача|закача|download", low) and \
            not re.search(r"страниц|page", low):
        return dict(base, kind="download")
    name = _proc_word(r)
    if _BUSY.search(low):
        return dict(base, kind="busy", target=name)
    if _EXIT.search(low) and name:
        return dict(base, kind="exit", target=name)
    if re.search(r"закончит|закончится|доделает|освобод|finish|done|complete", low):
        return dict(base, kind="busy", target=name)
    return {}


def intent(text: str):
    t = " ".join((text or "").split())
    if not t:
        return None
    if _LIST.search(t):
        return ("list",)
    m = _CANCEL.search(t)
    if m:
        return ("cancel", m.group(1) or "")
    m = _ASK.search(t)
    if not m:
        return None
    w = parse(m.group(1), t)
    return ("add", w) if w else None


# --- добавить ----------------------------------------------------------------------------
def _label(w: dict) -> str:
    ru, k = w.get("ru", True), w["kind"]
    if k == "download":
        return "загрузка" if ru else "the download"
    if k == "busy":
        return (f"{w.get('name') or w.get('target') or 'задача'} освободит процессор" if ru else
                f"{w.get('name') or w.get('target') or 'the task'} finishes")
    if k == "exit":
        return f"{w.get('name') or w['target']} закроется" if ru else f"{w.get('name') or w['target']} closes"
    if k == "web":
        page = w.get("title") or w.get("target", "")
        if w.get("text"):
            return (f"на «{page[:50]}» появится «{w['text']}»" if ru else f"“{w['text']}” appears on {page[:50]}")
        return f"страница «{page[:50]}» изменится" if ru else f"{page[:50]} changes"
    if k == "price":
        page = (w.get("title") or w.get("target", ""))[:50]
        lim = w.get("limit")
        return ((f"цена на «{page}» станет ниже {lim:g}" if lim else f"«{page}» подешевеет") if ru else
                (f"price on {page} drops below {lim:g}" if lim else f"{page} gets cheaper"))
    if k == "folder":
        return f"новый файл в папке {w['target']}" if ru else f"a new file in {w['target']}"
    if k == "internet":
        return "интернет вернётся" if ru else "the internet is back"
    if k == "battery":
        return f"заряд дойдёт до {w['target']}%" if ru else f"battery reaches {w['target']}%"
    return k


def add(w: dict, env=None) -> str:
    env = env or _get_env()
    ru = w.get("ru", True)
    w = dict(w)
    now = env.now()
    w.setdefault("id", uuid.uuid4().hex[:8])
    w["created"] = now
    w["until"] = now + (WEB_TTL if w["kind"] == "web" else TTL)
    st = w.setdefault("state", {})
    k = w["kind"]
    if k == "download":
        path = env.folder("downloads")
        files = env.listdir(path)
        st["partial"] = sorted(f for f in files if f.lower().endswith(_PARTIAL))
        st["known"] = sorted(f for f in files if not f.lower().endswith(_PARTIAL))
        if not st["partial"]:
            msg = ("Сейчас ничего не скачивается — скажу, когда закончится следующая загрузка." if ru else
                   "Nothing is downloading right now — I'll tell you when the next download finishes.")
        else:
            n = len(st["partial"])
            msg = (f"Слежу за загрузкой ({n} {'файл' if n == 1 else 'файла' if n < 5 else 'файлов'}) — скажу, когда закончится."
                   if ru else f"Watching {n} download(s) — I'll tell you when done.")
    elif k in ("busy", "exit"):
        procs = env.procs()
        name = (w.get("target") or "").lower()
        mine = [p for p in procs if name and name in p["name"].lower()]
        if k == "busy" and not mine:
            skip = ("system idle", "idle", "system", "python", "pythonw", "atlas", "dwm", "memcompression", "registry")
            busy = sorted((p for p in procs if not any(s in p["name"].lower() for s in skip) and p["pid"] > 4),
                          key=lambda p: -p["cpu"])
            if name and not busy:
                return f"Не вижу, чтобы {name} работал." if ru else f"I don't see {name} running."
            if not busy or busy[0]["cpu"] < 25:
                if name:
                    return f"{name} сейчас не запущен." if ru else f"{name} isn't running."
                return ("Сейчас компьютер ничем не загружен — нечего ждать." if ru else
                        "Nothing is keeping the computer busy right now.")
            mine = [busy[0]]
        if not mine:
            return f"{w['target']} сейчас не запущен." if ru else f"{w['target']} isn't running."
        st["pids"] = sorted({p["pid"] for p in mine})
        nm = mine[0]["name"]
        w["name"] = nm[:-4] if nm.lower().endswith(".exe") else nm
        st["calm_since"] = 0
        msg = (f"Хорошо, скажу, когда {_label(w)}." if ru else f"OK, I'll tell you when {_label(w)}.")
    elif k == "web":
        if w.get("target") == "current":
            url, title = env.current_page()
            if not url or str(url).startswith("file:"):
                return ("Не понял, какая страница: открой её в браузере или скажи адрес." if ru else
                        "Which page? Open it in the browser or say the address.")
            w["target"], w["title"] = url, title
        try:
            txt = env.fetch(w["target"])
        except Exception as e:
            return (f"Не открывается страница: {e}" if ru else f"Couldn't open the page: {e}")[:200]
        if w.get("text") and present(txt, w["text"]):
            return (f"«{w['text']}» уже есть на странице." if ru else f"“{w['text']}” is already on the page.")
        st["hash"] = hashlib.sha1(_norm(txt).encode("utf-8")).hexdigest()
        st["next"] = now + WEB_EVERY
        msg = (f"Слежу: {_label(w)}. Проверяю раз в 2 минуты." if ru else
               f"Watching: {_label(w)}. Checking every 2 minutes.")
    elif k == "price":
        if w.get("target") == "current":
            url, title = env.current_page()
            if not url or str(url).startswith("file:"):
                return ("Открой страницу товара в браузере и скажи ещё раз." if ru else
                        "Open the product page in the browser and say it again.")
            w["target"], w["title"] = url, title
        try:
            txt = env.fetch(w["target"])
        except Exception as e:
            return (f"Не открывается страница: {e}" if ru else f"Couldn't open the page: {e}")[:200]
        mp = main_price(txt)
        if not mp:
            return "Не вижу цены на этой странице." if ru else "I can't find a price on this page."
        st["price"], st["cur"], st["next"] = mp[0], mp[1], now + WEB_EVERY * 5
        if w.get("limit") and mp[0] <= w["limit"]:
            return (f"Уже {mp[0]:g} {mp[1]} — дешевле, чем {w['limit']:g}." if ru else f"Already {mp[0]:g}.")
        w["until"] = now + 14 * 86400
        msg = (f"Сейчас {mp[0]:g} {mp[1]}. Проверяю раз в 10 минут, две недели — скажу, когда "
               + (f"станет ниже {w['limit']:g}." if w.get("limit") else "подешевеет.") if ru else
               f"Now {mp[0]:g} {mp[1]}. I'll tell you when it drops.")
    elif k == "folder":
        st["known"] = sorted(env.listdir(env.folder(w["target"])))
        msg = (f"Скажу, когда в папке появится новый файл." if ru else "I'll tell you when a new file appears.")
    elif k == "internet":
        if env.online():
            return "Интернет и так есть." if ru else "The internet is working already."
        msg = "Скажу, как только интернет вернётся." if ru else "I'll tell you when the internet is back."
    elif k == "battery":
        b = env.battery()
        if not b:
            return "У этого компьютера нет батареи." if ru else "This computer has no battery."
        if b[0] >= w["target"]:
            return (f"Уже {int(b[0])}%." if ru else f"Already at {int(b[0])}%.")
        msg = (f"Сейчас {int(b[0])}%. Скажу, когда будет {w['target']}%." +
               ("" if b[1] else " Зарядка не подключена!") if ru else
               f"Now {int(b[0])}%. I'll tell you at {w['target']}%." + ("" if b[1] else " The charger isn't plugged in!"))
    else:
        return "Не понял, за чем следить." if ru else "Not sure what to watch."
    with _lock:
        _load().append(w)
        _save()
    return msg


_NEG = re.compile(r"(?:\bнет|\bне|\bnot|\bno|out of|\bбез)[\s:—–-]*$", re.I)


def present(page: str, word: str) -> bool:
    """Есть ли слово на странице — не считая «Нет в наличии», «not available» и т.п."""
    low, w = (page or "").lower(), (word or "").lower().strip()
    if not w:
        return False
    i = low.find(w)
    while i >= 0:
        if not _NEG.search(low[max(0, i - 12):i]):
            return True
        i = low.find(w, i + 1)
    return False


def _norm(text: str) -> str:
    t = re.sub(r"\d{1,2}:\d{2}(?::\d{2})?|\d+\s*(?:сек|мин|ч|час)\w*\s*назад|\d+\s*(?:sec|min|hour)s?\s*ago", " ", text or "")
    return " ".join(t.split()).lower()


# --- проверка ------------------------------------------------------------------------------
def check(w: dict, env, procs=None) -> str:
    """'' — ещё нет; иначе текст сообщения (наблюдение закончено)."""
    ru, k, st, now = w.get("ru", True), w["kind"], w.setdefault("state", {}), env.now()
    if now > w.get("until", now + 1):
        return "\x00expired"
    if k == "download":
        files = env.listdir(env.folder("downloads"))
        partial = {f for f in files if f.lower().endswith(_PARTIAL)}
        done = sorted(f for f in files if not f.lower().endswith(_PARTIAL) and f not in set(st.get("known", [])))
        if partial:
            st["seen_partial"] = True
            return ""
        if done and (st.get("partial") or st.get("seen_partial") or not st.get("partial")):
            names = ", ".join(done[:3]) + (f" и ещё {len(done) - 3}" if len(done) > 3 else "")
            return f"Загрузка закончилась: {names}." if ru else f"Download finished: {names}."
        if st.get("partial") and not done:
            return "Загрузка остановилась — файл не появился, возможно, её отменили." if ru else \
                "The download stopped — no file appeared; it may have been cancelled."
        return ""
    if k in ("busy", "exit"):
        procs = procs if procs is not None else env.procs()
        pids = set(st.get("pids") or [])
        alive = [p for p in procs if p["pid"] in pids]
        name = w.get("name") or w.get("target")
        if not alive:
            return (f"{name} закрылся." if k == "exit" else f"{name} закончил и закрылся.") if ru else \
                f"{name} has closed."
        if k == "exit":
            return ""
        cpu = sum(p["cpu"] for p in alive)
        if cpu < BUSY_CPU:
            st["calm_since"] = st.get("calm_since") or now
            if now - st["calm_since"] >= BUSY_HOLD:
                return f"{name} закончил — процессор свободен." if ru else f"{name} is done — the CPU is free."
        else:
            st["calm_since"] = 0
        return ""
    if k == "web":
        if now < st.get("next", 0):
            return ""
        st["next"] = now + WEB_EVERY
        try:
            txt = env.fetch(w["target"])
        except Exception as e:
            st["errors"] = st.get("errors", 0) + 1
            print(f"[слежу] страница не открылась: {e}")
            return ""
        page = w.get("title") or w["target"]
        if w.get("text"):
            if present(txt, w["text"]):
                return (f"На странице «{page[:60]}» появилось «{w['text']}»." if ru else
                        f"“{w['text']}” appeared on {page[:60]}.")
            return ""
        h = hashlib.sha1(_norm(txt).encode("utf-8")).hexdigest()
        if st.get("hash") and h != st["hash"]:
            return f"Страница «{page[:60]}» изменилась." if ru else f"{page[:60]} has changed."
        st["hash"] = h
        return ""
    if k == "price":
        if now < st.get("next", 0):
            return ""
        st["next"] = now + WEB_EVERY * 5
        try:
            mp = main_price(env.fetch(w["target"]))
        except Exception as e:
            print(f"[слежу] цена: {e}")
            return ""
        if not mp:
            return ""
        page = (w.get("title") or w["target"])[:60]
        was = st.get("price") or mp[0]
        if (w.get("limit") and mp[0] <= w["limit"]) or (not w.get("limit") and mp[0] < was * 0.995):
            return (f"Подешевело: «{page}» — {mp[0]:g} {mp[1]} (было {was:g})." if ru else
                    f"Price drop: {page} — {mp[0]:g} {mp[1]} (was {was:g}).")
        return ""
    if k == "folder":
        files = env.listdir(env.folder(w["target"]))
        new = [f for f in files if f not in set(st.get("known", [])) and not f.lower().endswith(_PARTIAL)
               and not f.startswith(("~$", "."))]
        if w.get("text"):
            new = [f for f in new if w["text"].lower() in f.lower()]
        if new:
            return (f"В папке появился файл: {', '.join(sorted(new)[:3])}." if ru else
                    f"New file: {', '.join(sorted(new)[:3])}.")
        return ""
    if k == "internet":
        return ("Интернет вернулся." if ru else "The internet is back.") if env.online() else ""
    if k == "battery":
        b = env.battery()
        if b and b[0] >= w["target"]:
            return (f"Заряд {int(b[0])}% — можно отключать." if ru else f"Battery at {int(b[0])}% — you can unplug.")
        return ""
    return ""


def check_all(env=None, notify=None) -> list:
    """Один проход: что случилось → [(наблюдение, текст)]; законченные убираются."""
    env = env or _get_env()
    with _lock:
        items = list(_load())
    if not items:
        return []
    procs = env.procs() if any(w["kind"] in ("busy", "exit") for w in items) else None
    fired, gone = [], set()
    for w in items:
        try:
            msg = check(w, env, procs)
        except Exception as e:
            print(f"[слежу] {w.get('kind')}: {e}")
            continue
        if msg == "\x00expired":
            gone.add(w["id"])
        elif msg:
            fired.append((w, msg))
            gone.add(w["id"])
    if gone or fired:
        with _lock:
            _s["items"] = [w for w in _load() if w["id"] not in gone]
            _save()
    for w, msg in fired:
        (notify or _notify)(w, msg, env)
    return fired


def _notify(w: dict, msg: str, env) -> None:
    print(f"[слежу] {msg}")
    f = _s.get("announce")
    if f:
        try:
            f(msg)
        except Exception:
            pass
    away = False
    try:
        away = env.idle() > 60
    except Exception:
        pass
    if w.get("phone") or away:
        try:
            from core import handoff
            handoff.notify_phone("Atlas", msg)
        except Exception as e:
            print(f"[слежу] на телефон не ушло: {e}")


# --- список и отмена ---------------------------------------------------------------------------
def listing(ru: bool = True) -> str:
    with _lock:
        items = list(_load())
    if not items:
        return "Сейчас ни за чем не слежу." if ru else "I'm not watching anything."
    parts = [f"{i}) {_label(w)}" for i, w in enumerate(items, 1)]
    return ("Слежу: " if ru else "Watching: ") + "; ".join(parts) + "."


def cancel(query: str = "", ru: bool = True) -> str:
    q = (query or "").lower().strip(" .")
    with _lock:
        items = _load()
        if not items:
            return "Я и так ни за чем не слежу." if ru else "I'm not watching anything."
        if not q or re.fullmatch(r"(?:все\w*|всё|all|everything|всем)", q):
            n = len(items)
            _s["items"] = []
            _save()
            return f"Перестал следить ({n})." if ru else f"Stopped watching ({n})."
        keep, gone = [], []
        for i, w in enumerate(items, 1):
            hay = (_label(w) + " " + w["kind"] + " " + str(w.get("target", ""))).lower()
            words = [x for x in re.findall(r"\w{3,}", q)]
            if q == str(i) or (words and any(x[:5] in hay for x in words)):
                gone.append(w)
            else:
                keep.append(w)
        if not gone:
            return "Не нашёл такого наблюдения." if ru else "No such watch."
        _s["items"] = keep
        _save()
    return ("Больше не слежу: " if ru else "Stopped: ") + "; ".join(_label(w) for w in gone) + "."


def handle(text: str, env=None):
    """Быстрый путь: фраза → ответ или None."""
    it = intent(text)
    if not it:
        return None
    ru = _ru(text)
    if it[0] == "list":
        return listing(ru)
    if it[0] == "cancel":
        return cancel(it[1], ru)
    return add(it[1], env)


# --- фон ---------------------------------------------------------------------------------------
def start(announce=None, every: float = TICK) -> threading.Thread:
    _s["announce"] = announce
    if _s["thread"] and _s["thread"].is_alive():
        return _s["thread"]

    def loop():
        try:
            _get_env().procs()                        # первый замер процессора — «разогрев» счётчиков
        except Exception:
            pass
        while True:
            time.sleep(every)
            try:
                check_all()
            except Exception as e:
                print(f"[слежу] {e}")
    _s["thread"] = threading.Thread(target=loop, daemon=True, name="watchers")
    _s["thread"].start()
    return _s["thread"]
