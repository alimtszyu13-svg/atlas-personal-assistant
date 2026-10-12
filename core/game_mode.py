"""
Игровой режим: «Атлас, игровой режим» — компьютер отдаёт всё игре, после игры всё как было.

Что делает:
  • план электропитания → «Максимальная производительность» (или «Высокая»), после — твой прежний;
  • уведомления Windows — выкл., после — как было;
  • закрывает фоновые программы, которые ничего не теряют и сами запускаются заново: OneDrive, Teams, Skype,
    обновлялки Adobe / Google / Microsoft Edge, «Связь с телефоном», Cortana… — после игры открывает их обратно;
  • говорит, что ещё ест память (Chrome 2,1 ГБ…), но сам это не закрывает — там может быть твоя работа;
  • сам Atlas молчит (кроме будильников и звонков);
  • FPS: если установлен PresentMon (бесплатно, от Intel), в конце скажет средний FPS игры. Без него — время
    игры и среднюю загрузку процессора и памяти.

    «игровой режим» / «включи игровой режим» → «выключи игровой режим» / «я наигрался» → отчёт
"""
import os
import re
import shutil
import subprocess
import threading
import time

SAFE_CLOSE = {"onedrive.exe": "OneDrive", "teams.exe": "Teams", "ms-teams.exe": "Teams", "skype.exe": "Skype",
              "adobearm.exe": "обновление Adobe", "ccxprocess.exe": "Adobe CC", "creative cloud.exe": "Adobe CC",
              "adobe desktop service.exe": "Adobe CC", "googleupdate.exe": "обновление Google",
              "msedgeupdate.exe": "обновление Edge", "phoneexperiencehost.exe": "Связь с телефоном",
              "yourphone.exe": "Связь с телефоном", "cortana.exe": "Cortana", "searchapp.exe": "Поиск Windows",
              "widgets.exe": "Виджеты", "dropbox.exe": "Dropbox", "googledrivefs.exe": "Google Диск",
              "epicgameslauncher.exe": "Epic Games (лаунчер)", "eadesktop.exe": "EA App"}
NO_RESTART = {"adobearm.exe", "googleupdate.exe", "msedgeupdate.exe", "searchapp.exe", "cortana.exe"}
HEAVY_MB = 700
ULTIMATE = "e9a42b02-d5df-448d-aa00-03f14749eb61"
HIGH = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
_s = {"on": False, "since": 0, "plan": None, "closed": [], "toasts": None, "samples": [], "thread": None,
      "stop": None, "fps": None, "game": ""}


def _ru(text: str) -> bool:
    return bool(re.search(r"[а-яё]", text or "", re.I))


class Env:
    def _run(self, args, timeout=15) -> str:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout or ""

    def power_plan(self) -> str:
        m = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27})", self._run(["powercfg", "/getactivescheme"]), re.I)
        return m.group(1) if m else ""

    def plans(self) -> list:
        return re.findall(r"([0-9a-f]{8}-[0-9a-f-]{27})", self._run(["powercfg", "/list"]), re.I)

    def set_plan(self, guid: str) -> bool:
        self._run(["powercfg", "/setactive", guid])
        return self.power_plan().lower() == guid.lower()

    def add_ultimate(self) -> str:
        m = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27})", self._run(["powercfg", "-duplicatescheme", ULTIMATE]), re.I)
        return m.group(1) if m else ""

    def toasts(self, on=None):
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\PushNotifications"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
            try:
                cur = winreg.QueryValueEx(k, "ToastEnabled")[0]
            except OSError:
                cur = 1
            if on is not None:
                winreg.SetValueEx(k, "ToastEnabled", 0, winreg.REG_DWORD, 1 if on else 0)
            return bool(cur)

    def procs(self) -> list:
        import psutil
        out = []
        for p in psutil.process_iter(["pid", "name", "exe", "memory_info"]):
            try:
                out.append({"pid": p.info["pid"], "name": (p.info["name"] or ""), "exe": p.info["exe"] or "",
                            "mb": (p.info["memory_info"].rss if p.info["memory_info"] else 0) / 1048576})
            except Exception:
                pass
        return out

    def kill(self, pid: int) -> bool:
        import psutil
        try:
            p = psutil.Process(pid)
            p.terminate()
            p.wait(3)
            return True
        except Exception:
            return False

    def launch(self, exe: str) -> None:
        try:
            subprocess.Popen([exe], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        except Exception as e:
            print(f"[игровой режим] не запустил {exe}: {e}")

    def load(self):
        import psutil
        return psutil.cpu_percent(None), psutil.virtual_memory().percent

    def foreground_exe(self) -> str:
        from core import worklog
        app, _t, _ = worklog._foreground()
        return app or ""

    def presentmon(self) -> str:
        for name in ("PresentMon.exe", "presentmon.exe", "PresentMon-x64.exe"):
            p = shutil.which(name)
            if p:
                return p
        for base in (os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Intel", "PresentMon"),
                     os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")):
            if os.path.isdir(base):
                for d, _, fs in os.walk(base):
                    for f in fs:
                        if f.lower().startswith("presentmon") and f.lower().endswith(".exe"):
                            return os.path.join(d, f)
        return ""

    def now(self) -> float:
        return time.time()


_env = None


def _get_env():
    global _env
    if _env is None:
        _env = Env()
    return _env


def active() -> bool:
    return _s["on"]


def start(env=None, ru: bool = True, watch: bool = True) -> str:
    env = env or _get_env()
    if _s["on"]:
        return "Игровой режим уже включён." if ru else "Game mode is already on."
    did = []
    try:
        prev = env.power_plan()
        target = next((g for g in env.plans() if g.lower() == ULTIMATE), None)
        if not target:
            target = env.add_ultimate() or (HIGH if HIGH in [g.lower() for g in env.plans()] else "")
        if target and prev.lower() != target.lower() and env.set_plan(target):
            _s["plan"] = prev
            did.append("максимальная производительность" if ru else "max performance power plan")
    except Exception as e:
        print(f"[игровой режим] питание: {e}")
    try:
        was = env.toasts()
        if was:
            env.toasts(False)
            _s["toasts"] = True
            did.append("уведомления выключены" if ru else "notifications off")
    except Exception as e:
        print(f"[игровой режим] уведомления: {e}")
    closed, by = [], {}
    for p in env.procs():
        n = p["name"].lower()
        if n in SAFE_CLOSE:
            if env.kill(p["pid"]):
                closed.append({"name": n, "exe": p["exe"], "label": SAFE_CLOSE[n]})
        elif n not in ("system", "memory compression", "python.exe", "pythonw.exe", "explorer.exe", "dwm.exe",
                       "msmpeng.exe", "vmmem", "registry", "svchost.exe"):
            by[p["name"]] = by.get(p["name"], 0) + p["mb"]
    heavy = [(mb, name) for name, mb in by.items() if mb >= HEAVY_MB]
    _s["closed"] = closed
    labels = sorted({c["label"] for c in closed})
    if labels:
        did.append(("закрыл: " if ru else "closed: ") + ", ".join(labels))
    _s.update(on=True, since=env.now(), samples=[], fps=None, game="")
    if watch:
        _watch(env)
    msg = ("Игровой режим включён" if ru else "Game mode on") + (": " + "; ".join(did) if did else "") + "."
    if heavy:
        top = sorted(heavy, reverse=True)[:3]
        gb = (lambda mb: f"{mb / 1024:.1f}".replace(".", ",") + " ГБ") if ru else (lambda mb: f"{mb / 1024:.1f} GB")
        msg += (" Много памяти ещё занимают: " if ru else " Still using lots of memory: ") + \
            ", ".join(f"{n.replace('.exe', '')} {gb(mb)}" for mb, n in top) + \
            (" — закрой, если не нужны." if ru else " — close them if you don't need them.")
    if not env.presentmon():
        msg += (" Для FPS поставь бесплатный PresentMon от Intel — тогда в конце скажу средний FPS." if ru else "")
    return msg


def _watch(env) -> None:
    stop = threading.Event()
    _s["stop"] = stop

    def loop():
        pm = env.presentmon()
        csv_path = os.path.join(os.environ.get("TEMP", "."), "atlas_presentmon.csv")
        proc = None
        while not stop.is_set():
            try:
                cpu, mem = env.load()
                _s["samples"].append((cpu, mem))
                fg = env.foreground_exe()
                if fg and fg.lower() not in ("explorer.exe", "python.exe", "pythonw.exe", "atlas"):
                    _s["game"] = fg
                if pm and proc is None and _s["game"]:
                    try:
                        if os.path.exists(csv_path):
                            os.remove(csv_path)
                        proc = subprocess.Popen([pm, "--process_name", _s["game"], "--output_file", csv_path,
                                                 "--terminate_on_proc_exit", "--stop_existing_session"],
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    except Exception as e:
                        print(f"[игровой режим] PresentMon: {e}")
                        pm = ""
            except Exception:
                pass
            stop.wait(10)
        if proc:
            try:
                proc.terminate()
                proc.wait(5)
            except Exception:
                pass
            _s["fps"] = fps_from_csv(csv_path)
    _s["thread"] = threading.Thread(target=loop, daemon=True, name="game-mode")
    _s["thread"].start()


def fps_from_csv(path: str):
    """Средний FPS из лога PresentMon (колонка MsBetweenPresents / FrameTime)."""
    try:
        import csv
        with open(path, encoding="utf-8", errors="replace") as f:
            rows = list(csv.DictReader(f))
        col = next((c for c in ("MsBetweenPresents", "FrameTime", "msBetweenPresents") if rows and c in rows[0]), None)
        vals = [float(r[col]) for r in rows if col and r.get(col) not in (None, "", "NA")]
        vals = [v for v in vals if 0 < v < 1000]
        if len(vals) < 30:
            return None
        vals.sort()
        avg = 1000 / (sum(vals) / len(vals))
        low = 1000 / vals[int(len(vals) * 0.99) - 1]
        return round(avg), round(low)
    except Exception:
        return None


def stop(env=None, ru: bool = True) -> str:
    env = env or _get_env()
    if not _s["on"]:
        return "Игровой режим и так выключен." if ru else "Game mode is off."
    if _s.get("stop"):
        _s["stop"].set()
        if _s.get("thread"):
            _s["thread"].join(8)
    back = []
    if _s.get("plan"):
        try:
            env.set_plan(_s["plan"])
            back.append("питание")
        except Exception:
            pass
    if _s.get("toasts"):
        try:
            env.toasts(True)
            back.append("уведомления")
        except Exception:
            pass
    reopened = set()
    for c in _s["closed"]:
        if c["name"] not in NO_RESTART and c["exe"] and c["exe"].lower() not in reopened:
            env.launch(c["exe"])
            reopened.add(c["exe"].lower())
    if reopened:
        back.append("фоновые программы")
    mins = max(1, round((env.now() - _s["since"]) / 60))
    s = _s["samples"]
    cpu = round(sum(x for x, _ in s) / len(s)) if s else None
    mem = round(sum(y for _, y in s) / len(s)) if s else None
    game = (_s.get("game") or "").replace(".exe", "")
    out = (f"Игровой режим выключен: играл {mins} мин" if ru else f"Game mode off: {mins} min") + \
        (f" в {game}" if game and ru else (f" of {game}" if game else ""))
    if _s.get("fps"):
        out += (f", средний FPS {_s['fps'][0]}, в худший 1 % — {_s['fps'][1]}" if ru else
                f", average {_s['fps'][0]} FPS, 1% low {_s['fps'][1]}")
    elif cpu is not None:
        out += (f", процессор в среднем {cpu} %, память {mem} %" if ru else f", CPU avg {cpu}%, RAM {mem}%")
    out += "." + ((" Вернул как было: " + ", ".join(back) + ".") if back and ru else "")
    _s.update(on=False, plan=None, toasts=None, closed=[], stop=None)
    return out


_ON = re.compile(r"^(?:атлас[,\s]+)?(?:включи|запусти|активируй|врубай|давай|turn on|start|enable)?\s*"
                 r"(?:игровой режим|режим игры|game mode|gaming mode)\b|^(?:атлас[,\s]+)?(?:я\s+)?(?:буду|иду)\s+играть\s*$", re.I)
_OFF = re.compile(r"(?:выключи|отключи|останови|заверши|turn off|stop|disable)\s+(?:игровой режим|режим игры|game mode)|"
                  r"(?:я\s+)?наигрался|поиграл(?:и)?(?:\s+всё|\s+все)?$|game over", re.I)


def handle(text: str, env=None):
    t = " ".join((text or "").split()).strip(" .!")
    ru = _ru(t)
    if _OFF.search(t):
        return stop(env, ru)
    if _ON.search(t):
        return start(env, ru)
    return None
