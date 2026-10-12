"""
Уборка диска голосом.

    «что занимает место» / «сколько места на диске»  → диски, большие папки и самые большие файлы
    «почисти компьютер» / «освободи место»          → план: что убрать и сколько ГБ освободится
    «да» (в течение 10 минут)                        → уборка в фоне, потом отчёт

Что убирается:
  • временные файлы Windows и программ старше суток (%TEMP%) — удаляются, они никому не нужны;
  • кэш браузеров (Chrome, Edge, Яндекс, Brave, Opera, Vivaldi) — удаляется; вкладки, пароли,
    история и закладки не трогаются, браузер просто заново скачает картинки сайтов;
  • установщики и архивы в Загрузках старше 30 дней (.exe .msi .zip .rar .7z .iso) — в КОРЗИНУ,
    их можно вернуть;
  • Корзина — только если попросишь отдельно («и корзину тоже»): это навсегда.
Документы, фото, видео, музыка, рабочий стол — никогда. Занятые файлы пропускаются.
"""
import os
import re
import threading
import time

PLAN_TTL = 600
OLD_TEMP_S = 24 * 3600
OLD_INSTALLER_DAYS = 30
SCAN_BUDGET_S = 12.0
_INSTALLERS = (".exe", ".msi", ".zip", ".rar", ".7z", ".iso", ".msix", ".appx", ".tar", ".gz")
_BROWSERS = {"Chrome": r"Google\Chrome\User Data", "Edge": r"Microsoft\Edge\User Data",
             "Яндекс": r"Yandex\YandexBrowser\User Data", "Brave": r"BraveSoftware\Brave-Browser\User Data",
             "Vivaldi": r"Vivaldi\User Data", "Opera": r"..\Roaming\Opera Software\Opera Stable",
             "Opera GX": r"..\Roaming\Opera Software\Opera GX Stable"}
_CACHE_DIRS = ("Cache", "Code Cache", "GPUCache", "Service Worker\\CacheStorage", "DawnCache", "GrShaderCache")
_s = {"plan": None, "busy": False}
_lock = threading.Lock()


def _ru(text: str) -> bool:
    return bool(re.search(r"[а-яё]", text or "", re.I))


def gb(n: float) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} ГБ".replace(".0 ", " ")
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f} МБ"
    return f"{max(1, n / 1024):.0f} КБ" if n else "0 МБ"


def _gb_en(n: float) -> str:
    return gb(n).replace("ГБ", "GB").replace("МБ", "MB").replace("КБ", "KB")


# --- окружение (в тестах подменяется) ---------------------------------------------------
class Env:
    def __init__(self):
        self.home = os.path.expanduser("~")
        self.local = os.environ.get("LOCALAPPDATA") or os.path.join(self.home, "AppData", "Local")
        self.temp = os.environ.get("TEMP") or os.path.join(self.local, "Temp")

    def drives(self) -> list:
        import psutil
        out = []
        for p in psutil.disk_partitions(all=False):
            if "cdrom" in (p.opts or "") or not p.fstype:
                continue
            try:
                u = psutil.disk_usage(p.mountpoint)
                out.append({"name": p.mountpoint.rstrip("\\"), "total": u.total, "free": u.free})
            except Exception:
                pass
        return out

    def folders(self) -> dict:
        h = self.home
        return {"Загрузки": os.path.join(h, "Downloads"), "Документы": os.path.join(h, "Documents"),
                "Рабочий стол": os.path.join(h, "Desktop"), "Видео": os.path.join(h, "Videos"),
                "Изображения": os.path.join(h, "Pictures"), "Музыка": os.path.join(h, "Music"),
                "OneDrive": os.path.join(h, "OneDrive")}

    def temp_dirs(self) -> list:
        return [self.temp, os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp")]

    def browser_caches(self) -> list:
        """[(браузер, папка кэша)] для всех профилей."""
        out = []
        for name, rel in _BROWSERS.items():
            root = os.path.normpath(os.path.join(self.local, rel))
            if not os.path.isdir(root):
                continue
            profiles = [root] if "Opera" in name else \
                [os.path.join(root, d) for d in _listdir(root) if d == "Default" or d.startswith("Profile ")]
            for prof in profiles:
                for c in _CACHE_DIRS:
                    p = os.path.join(prof, c)
                    if os.path.isdir(p):
                        out.append((name, p))
            if "Opera" in name:
                lc = os.path.join(self.local, "Opera Software", os.path.basename(root), "Cache")
                if os.path.isdir(lc):
                    out.append((name, lc))
        return out

    def recycle_bin(self) -> int:
        try:
            import ctypes

            class Q(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_ulong), ("i64Size", ctypes.c_longlong),
                            ("i64NumItems", ctypes.c_longlong)]
            q = Q()
            q.cbSize = ctypes.sizeof(Q)
            ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(q))
            return int(q.i64Size)
        except Exception:
            return 0

    def empty_bin(self) -> bool:
        try:
            import ctypes
            return ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x7) in (0, -2147418113)
        except Exception:
            return False

    def to_bin(self, paths: list) -> int:
        """В Корзину (можно вернуть). Возвращает, сколько ушло."""
        if not paths:
            return 0
        try:
            import ctypes
            from ctypes import wintypes as wt

            class OP(ctypes.Structure):
                _fields_ = [("hwnd", wt.HWND), ("wFunc", wt.UINT), ("pFrom", wt.LPCWSTR), ("pTo", wt.LPCWSTR),
                            ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wt.BOOL),
                            ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wt.LPCWSTR)]
            op = OP()
            op.wFunc = 3                                    # FO_DELETE
            op.pFrom = "\0".join(paths) + "\0\0"
            op.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400   # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
            ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        except Exception as e:
            print(f"[уборка] в корзину не ушло: {e}")
        return sum(1 for p in paths if not os.path.exists(p))

    def remove(self, path: str) -> bool:
        try:
            os.remove(path)
            return True
        except Exception:
            return False

    def now(self) -> float:
        return time.time()


def _listdir(p: str) -> list:
    try:
        return os.listdir(p)
    except Exception:
        return []


_env = None


def _get_env():
    global _env
    if _env is None:
        _env = Env()
    return _env


# --- подсчёт ------------------------------------------------------------------------------
def walk_files(root: str, deadline: float = None, older_than: float = None, now: float = None):
    """(путь, размер, время изменения) всех файлов в папке; останавливается по времени."""
    now = now or time.time()
    stack = [root]
    while stack:
        if deadline and time.time() > deadline:
            return
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            st = e.stat(follow_symlinks=False)
                            if older_than is None or now - st.st_mtime > older_than:
                                yield e.path, st.st_size, st.st_mtime
                    except Exception:
                        continue
        except Exception:
            continue


def folder_size(root: str, deadline: float = None) -> int:
    return sum(sz for _, sz, _ in walk_files(root, deadline))


def usage(env=None, budget: float = SCAN_BUDGET_S) -> dict:
    """Диски + большие папки + самые большие файлы (в пределах времени)."""
    env = env or _get_env()
    deadline = time.time() + budget
    folders, big = [], []
    for name, path in env.folders().items():
        if not os.path.isdir(path):
            continue
        total = 0
        for p, sz, _ in walk_files(path, deadline):
            total += sz
            if sz >= 200 * 1024 ** 2:
                big.append((sz, p))
        folders.append((total, name))
    tmp = sum(folder_size(d, deadline) for d in env.temp_dirs() if os.path.isdir(d))
    cache = sum(folder_size(p, deadline) for _, p in env.browser_caches())
    big.sort(reverse=True)
    return {"drives": env.drives(), "folders": sorted(folders, reverse=True), "big": big[:5], "temp": tmp,
            "cache": cache, "bin": env.recycle_bin(), "partial": time.time() > deadline}


def describe_usage(u: dict, ru: bool = True) -> str:
    G = gb if ru else _gb_en
    parts = []
    for d in u["drives"][:4]:
        used = round(100 - d["free"] * 100 / max(1, d["total"]))
        parts.append((f"Диск {d['name']} свободно {G(d['free'])} из {G(d['total'])} (занято {used}%)" if ru else
                      f"Drive {d['name']} {G(d['free'])} free of {G(d['total'])} ({used}% used)"))
    top = [f"{n} — {G(sz)}" for sz, n in u["folders"][:4] if sz > 50 * 1024 ** 2]
    if top:
        parts.append(("Больше всего: " if ru else "Biggest folders: ") + ", ".join(top))
    if u["big"]:
        files = [f"{os.path.basename(p)} ({G(sz)})" for sz, p in u["big"][:3]]
        parts.append(("Самые большие файлы: " if ru else "Largest files: ") + ", ".join(files))
    junk = u["temp"] + u["cache"]
    if junk > 100 * 1024 ** 2 or u["bin"] > 100 * 1024 ** 2:
        j = (f"Мусора примерно {G(junk)} (временные файлы и кэш браузеров)" if ru else
             f"About {G(junk)} of junk (temp files and browser cache)")
        if u["bin"] > 100 * 1024 ** 2:
            j += (f", в Корзине {G(u['bin'])}" if ru else f", {G(u['bin'])} in the Recycle Bin")
        parts.append(j + (" — скажи «почисти компьютер»." if ru else " — say “clean up my PC”."))
    if u.get("partial"):
        parts.append("Посчитал не всё — папки очень большие." if ru else "Couldn't count everything in time.")
    return ". ".join(parts) + "." if parts else ("Не получилось посчитать." if ru else "Couldn't measure.")


# --- план -----------------------------------------------------------------------------------
def plan(env=None, with_bin: bool = False, budget: float = SCAN_BUDGET_S) -> dict:
    env = env or _get_env()
    now = env.now()
    deadline = time.time() + budget
    temp = []
    for d in env.temp_dirs():
        if os.path.isdir(d):
            temp += [(p, sz) for p, sz, _ in walk_files(d, deadline, older_than=OLD_TEMP_S, now=now)]
    cache = {}
    for name, d in env.browser_caches():
        files = [(p, sz) for p, sz, _ in walk_files(d, deadline)]
        cache.setdefault(name, []).extend(files)
    old = []
    dl = env.folders().get("Загрузки")
    if dl and os.path.isdir(dl):
        for f in _listdir(dl):
            p = os.path.join(dl, f)
            try:
                st = os.stat(p)
            except Exception:
                continue
            if os.path.isfile(p) and f.lower().endswith(_INSTALLERS) and now - st.st_mtime > OLD_INSTALLER_DAYS * 86400:
                old.append((p, st.st_size))
    p = {"created": now, "temp": temp, "cache": cache, "installers": old,
         "bin": env.recycle_bin() if with_bin else 0, "with_bin": with_bin}
    p["total"] = sum(sz for _, sz in temp) + sum(sz for v in cache.values() for _, sz in v) + \
        sum(sz for _, sz in old) + p["bin"]
    return p


def describe_plan(p: dict, ru: bool = True) -> str:
    G = gb if ru else _gb_en
    lines = []
    t = sum(sz for _, sz in p["temp"])
    if t:
        lines.append(f"временные файлы — {G(t)}" if ru else f"temp files — {G(t)}")
    for name, files in sorted(p["cache"].items(), key=lambda kv: -sum(sz for _, sz in kv[1])):
        s = sum(sz for _, sz in files)
        if s > 5 * 1024 ** 2:
            lines.append(f"кэш {name} — {G(s)}" if ru else f"{name} cache — {G(s)}")
    if p["installers"]:
        s = sum(sz for _, sz in p["installers"])
        n = len(p["installers"])
        lines.append(f"старые установщики и архивы в Загрузках ({n}) — {G(s)}, в Корзину" if ru else
                     f"{n} old installers/archives in Downloads — {G(s)}, to the Recycle Bin")
    if p.get("with_bin") and p["bin"]:
        lines.append(f"очистить Корзину — {G(p['bin'])}, навсегда" if ru else
                     f"empty the Recycle Bin — {G(p['bin'])}, permanently")
    if not lines or p["total"] < 20 * 1024 ** 2:
        return ("Почти нечего убирать — компьютер и так чистый." if ru else "There's almost nothing to clean.")
    head = f"Могу освободить примерно {G(p['total'])}: " if ru else f"I can free about {G(p['total'])}: "
    tail = (". Документы, фото и рабочий стол не трогаю. Чистить?" if ru else
            ". Documents, photos and desktop stay untouched. Go ahead?")
    return head + "; ".join(lines) + tail


def propose(text: str = "", env=None) -> str:
    ru = _ru(text) or not text
    with_bin = bool(re.search(r"корзин|recycle|bin\b", text or "", re.I))
    p = plan(env, with_bin=with_bin)
    if p["total"] >= 20 * 1024 ** 2:
        with _lock:
            _s["plan"] = p
    return describe_plan(p, ru)


def execute(p: dict, env=None) -> dict:
    env = env or _get_env()
    freed, removed, skipped = 0, 0, 0
    for path, sz in p["temp"] + [x for v in p["cache"].values() for x in v]:
        if env.remove(path):
            freed += sz
            removed += 1
        else:
            skipped += 1
    inst = [x for x in p["installers"] if os.path.exists(x[0])]
    if inst:
        env.to_bin([x[0] for x in inst])
        for path, sz in inst:
            if not os.path.exists(path):
                freed += sz
                removed += 1
    bin_ok = False
    if p.get("with_bin") and p.get("bin"):
        bin_ok = env.empty_bin()
        if bin_ok:
            freed += p["bin"]
    return {"freed": freed, "removed": removed, "skipped": skipped, "bin": bin_ok, "to_bin": len(inst)}


def report(r: dict, ru: bool = True) -> str:
    G = gb if ru else _gb_en
    s = (f"Уборка закончена: освободил {G(r['freed'])}." if ru else f"Clean-up done: freed {G(r['freed'])}.")
    if r["to_bin"]:
        s += (f" Установщики ({r['to_bin']}) — в Корзине, их можно вернуть." if ru else
              f" {r['to_bin']} installers are in the Recycle Bin if you need them.")
    if r["skipped"] > 20:
        s += (" Часть файлов была занята (обычно кэш открытого браузера) — их пропустил." if ru else
              " Some files were in use (usually the open browser's cache) — skipped them.")
    return s


def pending():
    with _lock:
        p = _s["plan"]
    if p and time.time() - p["created"] < PLAN_TTL:
        return p
    return None


def confirm(text: str = "yes", env=None, announce=None, wait: bool = False) -> str:
    """Запустить уборку по последнему плану."""
    ru = _ru(text)
    p = pending()
    if not p:
        return "Нечего подтверждать — сначала скажи «почисти компьютер»." if ru else \
            "There's no clean-up plan — say “clean up my PC” first."
    if re.search(r"корзин|recycle|bin\b", text or "", re.I) and not p.get("with_bin"):
        p["with_bin"], p["bin"] = True, (env or _get_env()).recycle_bin()
    with _lock:
        if _s["busy"]:
            return "Уже чищу." if ru else "Already cleaning."
        _s["busy"], _s["plan"] = True, None

    def run():
        try:
            msg = report(execute(p, env), ru)
        except Exception as e:
            msg = (f"Уборка прервалась: {e}" if ru else f"Clean-up stopped: {e}")
        finally:
            _s["busy"] = False
        print(f"[уборка] {msg}")
        if announce:
            announce(msg)
        return msg
    if wait:
        return run()
    threading.Thread(target=run, daemon=True, name="disk-clean").start()
    return "Чищу — скажу, когда закончу." if ru else "Cleaning — I'll tell you when it's done."


def cancel_plan() -> bool:
    with _lock:
        had = _s["plan"] is not None
        _s["plan"] = None
    return had


# --- фразы -----------------------------------------------------------------------------------
_USAGE = re.compile(r"что (?:у меня )?занимает (?:\w+ )?мест|сколько (?:\w+ )?(?:свободного )?места|"
                    r"(?:куда|на что) (?:делось|ушло|уходит) мест|чем (?:забит|занят) (?:\w+ )?(?:диск|компьютер|ноутбук)|"
                    r"место на (?:диске|компьютере|ноутбуке)|what(?:'s| is) (?:taking|using) (?:up )?(?:the )?(?:disk )?space|"
                    r"how much (?:disk |free )?space", re.I)
_CLEAN = re.compile(r"(?:почисти|очисти|прибери|прибери\w*|вычисти|освободи|cleanup|clean up|clean|free up)\w*\s*"
                    r"(?:\w+\s+)?(?:компьютер|комп|ноутбук|ноут|пк|диск|систем\w*|мест\w*|мусор|временн\w*|кэш|кеш|"
                    r"pc|computer|laptop|disk|space|junk|temp|cache)|убери мусор|удали мусор|"
                    r"(?:сделай|проведи|запусти) (?:уборку|очистку)", re.I)
_YES = re.compile(r"^(?:да|давай|чисти|почисти|удаляй|поехали|можно|го|ага|конечно|ок|окей|запускай|"
                  r"yes|yeah|yep|sure|go|ok|okay|do it|go ahead)\b", re.I)
_NO = re.compile(r"^(?:нет|не надо|не нужно|отмена|отмени|потом|no|nope|cancel|not now)\b", re.I)


def intent(text: str, last: str = None):
    """last — последний ответ Atlas: «да» считается согласием, только если он и был планом уборки."""
    t = " ".join((text or "").replace("ё", "е").split()).strip(" .!")
    if not t:
        return None
    if pending() and len(t) < 40 and (last is None or last == _s.get("said")):
        if _YES.search(t):
            return ("confirm",)
        if _NO.search(t):
            return ("cancel",)
    if _CLEAN.search(t) and not re.search(r"буфер|clipboard|истори|history|памят|memory|чат|chat", t, re.I):
        return ("plan",)
    if _USAGE.search(t):
        return ("usage",)
    return None


def handle(text: str, announce=None, env=None, last: str = None):
    it = intent(text, last)
    if not it:
        return None
    ru = _ru(text)
    if it[0] == "usage":
        return describe_usage(usage(env), ru)
    if it[0] == "plan":
        said = propose(text, env)
        _s["said"] = said
        return said
    if it[0] == "cancel":
        cancel_plan()
        return "Хорошо, ничего не трогаю." if ru else "OK, nothing touched."
    return confirm(text, env, announce)
