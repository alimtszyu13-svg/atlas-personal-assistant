"""
«Где я это сохранил?» — Atlas помнит, куда что скачивалось и сохранялось.

    «где реферат, который я вчера скачал?»   → «Реферат по истории.docx» — в Загрузках, вчера в 18:40. Открыть?
    «куда я сохранил презентацию?»
    «что я последним скачал?» / «последний скачанный файл»
    «открой реферат, который я вчера скачал»  → сразу открывает
    «да» / «открой» → открыть, «покажи в папке» → Проводник с выделенным файлом

Ищет по недавним файлам Windows (Недавние), Загрузкам, Рабочему столу, Документам, Картинкам, Видео —
по словам из названия и по времени («вчера», «утром», «на этой неделе»). Только на этом компьютере.
"""
import os
import re
import subprocess
import time
from datetime import datetime, timedelta

PENDING_TTL = 180
_s = {"last": None, "said": None}
_STOP = set("""где куда что какой какая какое какие который которая которое которые я мне мой моя мое мои мою
мы ты он она это этот эта эту тот та те файл файлы файлик документ документы недавно сегодня вчера позавчера
утром днём днем вечером ночью неделе неделю неделя прошлой этой на в во из с со и а но по за до у о об
скачал скачала скачивал скачивала скачали загрузил загрузила сохранил сохранила сохраняла положил положила
лежит находится находятся делся делась подевался подевалась открой открыть покажи найди найти последний
про последним последнее последняя последнюю скачанный скачанное скачанную сохраненный сохранённый тот самый
where did i save download downloaded saved put the a my file is it open show find last latest recently""".split())


def _ru(text: str) -> bool:
    return bool(re.search(r"[а-яё]", text or "", re.I))


class Env:
    home = os.path.expanduser("~")

    def roots(self) -> list:
        h = self.home
        return [(os.path.join(h, "Downloads"), "в Загрузках", "in Downloads", 1),
                (os.path.join(h, "Desktop"), "на рабочем столе", "on the Desktop", 2),
                (os.path.join(h, "Documents"), "в Документах", "in Documents", 3),
                (os.path.join(h, "Pictures"), "в Картинках", "in Pictures", 2),
                (os.path.join(h, "Videos"), "в Видео", "in Videos", 2),
                (os.path.join(h, "OneDrive"), "в OneDrive", "in OneDrive", 3)]

    def recent(self) -> list:
        """Недавние файлы Windows: [(путь, время)]."""
        d = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Recent")
        try:
            links = sorted((os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".lnk")),
                           key=os.path.getmtime, reverse=True)[:150]
        except Exception:
            return []
        if not links:
            return []
        script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;$s=New-Object -ComObject WScript.Shell;"
                  "foreach($l in $input){try{$t=$s.CreateShortcut($l).TargetPath;if($t){$t}else{'-'}}catch{'-'}}")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                               input="\n".join(links), capture_output=True, text=True, timeout=10, encoding="utf-8",
                               errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            targets = (r.stdout or "").splitlines()
        except Exception:
            return []
        out = []
        for lnk, tgt in zip(links, targets):
            tgt = tgt.strip()
            if tgt and tgt != "-" and os.path.isfile(tgt):
                out.append((tgt, os.path.getmtime(lnk)))
        return out

    def open(self, path: str) -> None:
        os.startfile(path)

    def reveal(self, path: str) -> None:
        subprocess.Popen(["explorer", "/select,", path])

    def now(self) -> float:
        return time.time()


_env = None


def _get_env():
    global _env
    if _env is None:
        _env = Env()
    return _env


def period(text: str, now: float):
    """Слова о времени → (от, до) или None."""
    t = (text or "").lower()
    d = datetime.fromtimestamp(now)
    day0 = d.replace(hour=0, minute=0, second=0, microsecond=0)
    if re.search(r"позавчера|day before yesterday", t):
        a = day0 - timedelta(days=2)
        return a.timestamp(), (a + timedelta(days=1)).timestamp()
    if re.search(r"вчера|yesterday", t):
        a = day0 - timedelta(days=1)
        lo, hi = a, day0
        if re.search(r"утр|morning", t):
            lo, hi = a + timedelta(hours=5), a + timedelta(hours=12)
        elif re.search(r"вечер|evening", t):
            lo, hi = a + timedelta(hours=17), day0
        return lo.timestamp(), hi.timestamp()
    if re.search(r"сегодня|today|утром|this morning", t):
        lo = day0 + (timedelta(hours=5) if re.search(r"утр|morning", t) else timedelta())
        return lo.timestamp(), now + 60
    m = re.search(r"(\d+|час|полчаса)\s*(?:час\w*|минут\w*)?\s*назад|(?:an?|\d+) (?:hour|minute)s? ago", t)
    if m:
        w = m.group(0)
        n = 30 if "полчаса" in w else int(re.search(r"\d+", w).group(0)) if re.search(r"\d+", w) else 1
        sec = n * (60 if re.search(r"минут|minute", w) else 3600)
        return now - sec * 2, now + 60
    if re.search(r"(?:на )?(?:этой|прошлой) неделе|this week|last week", t):
        back = 14 if "прошл" in t or "last" in t else 7
        return now - back * 86400, now + 60
    return None


def words(text: str) -> list:
    t = re.sub(r"\.(docx?|pdf|pptx?|xlsx?|zip|mp4|mp3|png|jpe?g|txt)\b", r" \1", (text or "").lower().replace("ё", "е"))
    out = []
    for w in re.findall(r"[\w-]+", t):
        if w in _STOP or len(w) < 3 or w.isdigit():
            continue
        out.append(w[:max(4, len(w) - 2)] if re.search(r"[а-я]", w) else w)
    return out


_KIND = {"презентац": (".pptx", ".ppt", ".key", ".pdf"), "таблиц": (".xlsx", ".xls", ".csv"),
         "фото": (".jpg", ".jpeg", ".png", ".heic", ".webp"), "картин": (".jpg", ".jpeg", ".png", ".webp", ".gif"),
         "видео": (".mp4", ".mkv", ".mov", ".avi", ".webm"), "музык": (".mp3", ".flac", ".wav", ".m4a"),
         "pdf": (".pdf",), "пдф": (".pdf",), "архив": (".zip", ".rar", ".7z"), "установщ": (".exe", ".msi"),
         "ворд": (".docx", ".doc"), "word": (".docx", ".doc")}


def candidates(env, since: float) -> list:
    """[(путь, время, где_ru, где_en)] — новые файлы в известных папках + недавние Windows."""
    seen, out = set(), []
    for root, ru, en, depth in env.roots():
        if not os.path.isdir(root):
            continue
        base = root.rstrip("\\/").count(os.sep)
        for d, dirs, files in os.walk(root):
            if d.count(os.sep) - base >= depth:
                dirs[:] = []
            dirs[:] = [x for x in dirs if not x.startswith((".", "$")) and x not in ("node_modules", "__pycache__")]
            for f in files:
                if f.startswith(("~$", ".")) or f.lower().endswith((".crdownload", ".part", ".tmp", ".ini", ".lnk")):
                    continue
                p = os.path.join(d, f)
                try:
                    st = os.stat(p)
                except Exception:
                    continue
                t = st.st_mtime                       # (st_ctime на Windows — создание; у копий оно «сейчас»)
                if t >= since and p.lower() not in seen:
                    seen.add(p.lower())
                    sub = os.path.relpath(d, root)
                    out.append((p, t, ru + (f" (папка «{sub}»)" if sub != "." else ""),
                                en + (f" (folder {sub})" if sub != "." else "")))
    try:
        for p, t in env.recent():
            if t >= since and p.lower() not in seen:
                seen.add(p.lower())
                out.append((p, t, f"в папке {os.path.dirname(p)}", f"in {os.path.dirname(p)}"))
    except Exception as e:
        print(f"[где файл] недавние: {e}")
    return out


def find(text: str, env=None) -> list:
    env = env or _get_env()
    now = env.now()
    per = period(text, now)
    lo, hi = per if per else (now - 30 * 86400, now + 60)
    ws = words(text)
    kinds = tuple(e for k, v in _KIND.items() if k in text.lower() for e in v)
    latest = bool(re.search(r"последн\w*|latest|last", text, re.I))
    scored = []
    for p, t, where_ru, where_en in candidates(env, lo):
        if t > hi:
            continue
        name = os.path.basename(p).lower().replace("ё", "е").replace("_", " ")
        hit = sum(1 for w in ws if w in name)
        if ws and not hit and not (latest and not ws):
            continue
        if kinds and not name.endswith(kinds):
            continue
        dl = "загрузк" in where_ru.lower()
        scored.append((hit, (1 if dl and re.search(r"скач|загруз|download", text, re.I) else 0), t, p, where_ru, where_en))
    scored.sort(key=lambda x: (-x[0], -x[1], -x[2]))
    return [{"path": s[3], "time": s[2], "where_ru": s[4], "where_en": s[5]} for s in scored[:5]]


def _when(t: float, now: float, ru: bool) -> str:
    d, n = datetime.fromtimestamp(t), datetime.fromtimestamp(now)
    days = (n.date() - d.date()).days
    hm = d.strftime("%H:%M")
    if ru:
        return f"сегодня в {hm}" if days == 0 else f"вчера в {hm}" if days == 1 else \
            f"позавчера в {hm}" if days == 2 else d.strftime("%d.%m в %H:%M")
    return f"today at {hm}" if days == 0 else f"yesterday at {hm}" if days == 1 else d.strftime("%b %d at %H:%M")


_ASK = re.compile(r"(?:где|куда)\s+(?:я\s+)?(?:[\w-]+,?\s+){0,6}?(?:сохранил\w*|скачал\w*|загрузил\w*|положил\w*|лежит|"
                  r"делся|делась|подевал\w*|файл)|(?:что|какой файл)\s+я\s+(?:последн\w+\s+)?(?:скачал|сохранил)\w*|"
                  r"последн\w+\s+(?:скачанн|сохранённ|сохраненн|загруженн)\w*|(?:открой|найди|покажи)\s+(?:\w+\s+){0,5}?"
                  r"(?:котор\w+|что)\s+я\s+(?:\w+\s+)?(?:скачал|сохранил|загрузил)\w*|"
                  r"where (?:did i (?:save|put|download)|is the file)|what did i (?:last )?download|"
                  r"(?:open|find|show) the \w+ i (?:downloaded|saved)", re.I)
_YES = re.compile(r"^(?:да|открой|открывай|давай|ага|yes|open|ok|окей|ок)\b", re.I)
_REVEAL = re.compile(r"(?:покажи|открой)\s+(?:в|его в)\s+(?:папк|проводник)\w*|show in folder|show it in explorer", re.I)
_NEXT = re.compile(r"^(?:не (?:тот|то|та)|другой|другую|следующ\w*|ещё|еще|not that|another|next)\b", re.I)


def intent(text: str, last: str = None):
    t = " ".join((text or "").split()).strip(" ?!.")
    if not t:
        return None
    if _s["last"] and time.time() - _s["last"]["ts"] < PENDING_TTL and (last is None or last == _s.get("said")):
        if _REVEAL.search(t):
            return ("reveal",)
        if _NEXT.search(t):
            return ("next",)
        if _YES.search(t) and len(t) < 25:
            return ("open",)
    if _ASK.search(t):
        return ("find", t)
    return None


def _say(hit: dict, now: float, ru: bool, ask_open: bool) -> str:
    name = os.path.basename(hit["path"])
    where = hit["where_ru"] if ru else hit["where_en"]
    s = (f"«{name}» — {where}, {_when(hit['time'], now, ru)}." if ru else
         f"“{name}” — {where}, {_when(hit['time'], now, ru)}.")
    if ask_open:
        s += " Открыть?" if ru else " Open it?"
    return s


def handle(text: str, env=None, last: str = None):
    env = env or _get_env()
    it = intent(text, last)
    if not it:
        return None
    ru = _ru(text) if it[0] == "find" else (_s["last"] or {}).get("ru", True)
    if it[0] in ("open", "reveal", "next"):
        st = _s["last"]
        if it[0] == "next":
            st["i"] += 1
            if st["i"] >= len(st["hits"]):
                _s["last"] = None
                return "Больше ничего похожего не нашёл." if ru else "Nothing else like that."
            said = _say(st["hits"][st["i"]], env.now(), ru, True)
            st["ts"] = time.time()
            _s["said"] = said
            return said
        hit = st["hits"][st["i"]]
        _s["last"] = None
        try:
            (env.reveal if it[0] == "reveal" else env.open)(hit["path"])
        except Exception as e:
            return (f"Не открылся: {e}" if ru else f"Couldn't open: {e}")[:200]
        return ("Показал в папке." if it[0] == "reveal" else "Открываю.") if ru else \
            ("Shown in Explorer." if it[0] == "reveal" else "Opening.")
    hits = find(it[1], env)
    strong = re.search(r"сохранил|скачал|загрузил|файл|download|save|последн", it[1], re.I)
    if not hits and not strong:
        return None                                       # «где лежит ключ?» — не про файлы
    if not hits:
        return ("Не нашёл такого среди недавних файлов. Скажи другое слово из названия или когда это было." if ru else
                "Nothing like that among recent files. Try another word from the name or when it was.")
    want_open = bool(re.match(r"^(?:атлас[,\s]+)?(?:открой|open)\b", it[1], re.I))
    if want_open:
        try:
            env.open(hits[0]["path"])
        except Exception as e:
            return f"Нашёл, но не открылся: {e}"[:200]
        return ("Открываю " if ru else "Opening ") + _say(hits[0], env.now(), ru, False)
    _s["last"] = {"hits": hits, "i": 0, "ts": time.time(), "ru": ru}
    said = _say(hits[0], env.now(), ru, True)
    if len(hits) > 1:
        said += " (Если не тот — скажи «другой».)" if ru else " (Say “another” if it's not it.)"
    _s["said"] = said
    return said
