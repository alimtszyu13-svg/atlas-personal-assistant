"""
Окна голосом: «поставь хром слева, а ворд справа», «разверни телеграм», «перенеси хром на второй монитор»,
«раздели экран между кодом и браузером», «сверни всё, кроме кода», «верни окна как было».

Всё через Windows API (ctypes), без мыши: окна ставятся точно в половину / четверть / треть рабочей
области монитора (без панели задач), с учётом невидимых рамок Windows 10/11 — без щелей между окнами.
Перед каждым изменением запоминается, где окна стояли, — «верни как было» ставит их обратно.

    intent(text)      → разобранная команда или None (быстрый путь, без модели)
    run(cmd)          → что сделано (текст для ответа)
    arrange(spec)     → для модели: "chrome:left; word:right"
"""
import re
import threading

# --- программы по-человечески ---------------------------------------------------
# ключ → (корни слов в речи, exe без .exe)
APPS = {
    "chrome": (r"хром|chrome|гугл хром", ("chrome",)),
    "edge": (r"edge|эдж|едж", ("msedge",)),
    "yandex": (r"яндекс(?:\s*браузер)?|yandex", ("browser",)),
    "firefox": (r"фаерфокс|файрфокс|firefox|мозил", ("firefox",)),
    "opera": (r"опер[аеуы]|opera", ("opera", "opera_gx")),
    "brave": (r"брейв|brave", ("brave",)),
    "browser": (r"браузер|browser", ("chrome", "msedge", "browser", "firefox", "opera", "opera_gx", "brave", "vivaldi")),
    "word": (r"ворд|word", ("winword",)),
    "excel": (r"эксел|excel|таблиц", ("excel",)),
    "powerpoint": (r"пауэр ?поинт|повер ?поинт|powerpoint|презентаци", ("powerpnt",)),
    "code": (r"vs ?code|вс ?код|визуал студио код|vscode|код|редактор кода|cursor|курсор", ("code", "cursor")),
    "pycharm": (r"пайчарм|pycharm", ("pycharm64", "pycharm")),
    "telegram": (r"телеграм|телега|telegram", ("telegram",)),
    "discord": (r"дискорд|discord", ("discord",)),
    "whatsapp": (r"ватсап|вотсап|whatsapp", ("whatsapp",)),
    "spotify": (r"спотифай|spotify", ("spotify",)),
    "explorer": (r"проводник|папк[аиуе]|explorer", ("explorer",)),
    "notepad": (r"блокнот|notepad", ("notepad", "notepad++")),
    "figma": (r"фигм|figma", ("figma",)),
    "zoom": (r"зум|zoom", ("zoom",)),
    "teams": (r"тимс|teams", ("ms-teams", "teams")),
    "obs": (r"обс\b|obs", ("obs64", "obs")),
    "steam": (r"стим|steam", ("steam", "steamwebhelper")),
    "terminal": (r"терминал|консол|powershell|cmd", ("windowsterminal", "powershell", "cmd")),
    "outlook": (r"аутлук|outlook", ("outlook", "olk")),
    "notion": (r"ноушн|notion", ("notion",)),
    "obsidian": (r"обсидиан|obsidian", ("obsidian",)),
    "vlc": (r"vlc|влц|плеер", ("vlc", "potplayermini64", "mpc-hc64", "wmplayer")),
}
_APP_RX = [(k, re.compile(r"(?<![\w])(?:" + rx + r")\w*", re.I)) for k, (rx, _) in APPS.items()]

# --- где поставить ----------------------------------------------------------------
_POS = [   # порядок важен: сначала углы и трети, потом половины
    ("tl", r"(?:лев\w* верх\w*|верх\w* лев\w*)(?: угол| углу)?|top[- ]left"),
    ("tr", r"(?:прав\w* верх\w*|верх\w* прав\w*)(?: угол| углу)?|top[- ]right"),
    ("bl", r"(?:лев\w* ниж\w*|ниж\w* лев\w*)(?: угол| углу)?|bottom[- ]left"),
    ("br", r"(?:прав\w* ниж\w*|ниж\w* прав\w*)(?: угол| углу)?|bottom[- ]right"),
    ("l3", r"(?:в |на )?левую треть|left third"),
    ("c3", r"(?:в |на )?(?:среднюю|центральную) треть|middle third"),
    ("r3", r"(?:в |на )?правую треть|right third"),
    ("max", r"на весь экран|во весь экран|на полный экран|полноэкран\w*|full ?screen|maximi[sz]e"),
    ("left", r"слева|влево|налево|в лев\w*|на лев\w*(?: половин\w*| сторон\w*| част\w*)?|лев\w* половин\w*|left"),
    ("right", r"справа|вправо|направо|в прав\w*|на прав\w*(?: половин\w*| сторон\w*| част\w*)?|прав\w* половин\w*|right"),
    ("top", r"сверху|наверх\w*|вверх\w*|верхн\w*(?: половин\w*)?|top"),
    ("bottom", r"снизу|вниз\w*|нижн\w*(?: половин\w*)?|bottom"),
    ("center", r"по центру|в центр\w*|посередин\w*|в середин\w*|center|centre|middle"),
]
_POS_RX = [(k, re.compile(r"(?<![\w])(?:" + rx + r")(?![\w])", re.I)) for k, rx in _POS]
_MON = re.compile(r"(?:на|в|во|to|on)\s+(?:the\s+)?(втор\w*|друг\w*|соседн\w*|следующ\w*|прав\w*|лев\w*|перв\w*|"
                  r"главн\w*|основн\w*|трет\w*|second|other|next|main|primary|first|left|right|third|\d)\s*"
                  r"(?:монитор\w*|экран\w*|дисплей\w*|monitor|screen|display)", re.I)
_VERB = re.compile(r"(?<![\w])(?:постав|размест|располож|перенес|перенос|подвин|сдвин|передвин|перемест|отправ|кинь|закин|"
                   r"разлож|раздели|разбей|разверн|сверн|спрячь|убери|верни|покажи рядом|put|move|snap|place|tile|"
                   r"сдела|split|arrange|maximi|minimi|restore|send)\w*", re.I)
_UNDO = re.compile(r"(?:верни|восстанови|отмени|put|restore|undo)\w*\s+(?:\w+\s+)?(?:окна|окно|windows|layout)?\s*"
                   r"(?:как было|назад|обратно|на место|back)|undo (?:the )?(?:window|layout)", re.I)
_MIN_ALL = re.compile(r"(?:сверни|спрячь|убери|minimi[sz]e|hide)\w*\s+(?:все|всё|all)(?:\s+(?:окна|windows))?"
                      r"(?:\s*,?\s*(?:кроме|except|but)\s+(.+))?", re.I)
_TILE = re.compile(r"(?:раздели|разбей|раздели\w*|split|tile)\w*\s+(?:экран|окна|screen|windows)?"
                   r"(?:\s+(?:между|на|for|between|with))?\s*(.*)|(?:разлож|arrange)\w*\s+(?:все\s+|всё\s+)?(?:окна|windows)"
                   r"(?:\s+(?:рядом|по экрану|side by side))?\s*(.*)|(?:покажи|постав\w*)\s+(.+?)\s+рядом", re.I)
_FILLER = re.compile(r"(?<![\w])(?:окн\w*|window\w*|пожалуйста|please|мне|его|её|ее|это|этот|эту|на|в|во|и|а|the|to|"
                     r"половин\w*|сторон\w*|част\w*|экран\w*|screen|half|side|part|сейчас|тоже|ещё|еще|сюда|туда|"
                     r"программ\w*|приложени\w*|app)(?![\w])", re.I)


def _ru(text: str) -> bool:
    return bool(re.search(r"[а-яё]", text or "", re.I))


# --- Windows -----------------------------------------------------------------------
class Win:
    """Настоящие окна Windows. В тестах подменяется."""

    def __init__(self):
        import ctypes
        import ctypes.wintypes as wt
        self.c, self.wt = ctypes, wt
        self.u = ctypes.windll.user32
        try:
            self.u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))     # координаты в настоящих пикселях
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass
        try:
            self.dwm = ctypes.windll.dwmapi
        except Exception:
            self.dwm = None

    def _rect(self, h):
        r = self.wt.RECT()
        self.u.GetWindowRect(h, self.c.byref(r))
        return (r.left, r.top, r.right, r.bottom)

    def _frame(self, h):
        """Видимая рамка окна (без прозрачной тени Windows 10/11)."""
        if self.dwm:
            r = self.wt.RECT()
            if self.dwm.DwmGetWindowAttribute(h, 9, self.c.byref(r), self.c.sizeof(r)) == 0:
                return (r.left, r.top, r.right, r.bottom)
        return self._rect(h)

    def _cloaked(self, h) -> bool:
        if not self.dwm:
            return False
        v = self.c.c_int(0)
        self.dwm.DwmGetWindowAttribute(h, 14, self.c.byref(v), self.c.sizeof(v))
        return bool(v.value)

    def windows(self) -> list:
        """Окна программ сверху вниз (как лежат на экране): hwnd, app, title, min, max, rect."""
        import psutil
        from core import win_input
        out, u, wt = [], self.u, self.wt

        def cb(h, _):
            try:
                if not u.IsWindowVisible(h) or u.GetWindow(h, 4) or self._cloaked(h):
                    return True
                n = u.GetWindowTextLengthW(h)
                if not n:
                    return True
                buf = self.c.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, buf, n + 1)
                title = buf.value
                if title in ("Program Manager", "Пуск", "Start") or win_input.is_atlas(h):
                    return True
                ex = u.GetWindowLongW(h, -20)                 # GWL_EXSTYLE
                if ex & 0x80:                                 # WS_EX_TOOLWINDOW — служебные панельки
                    return True
                pid = wt.DWORD()
                u.GetWindowThreadProcessId(h, self.c.byref(pid))
                app = psutil.Process(pid.value).name()
                app = app[:-4] if app.lower().endswith(".exe") else app
                if app.lower() in ("textinputhost", "shellexperiencehost", "searchhost", "startmenuexperiencehost",
                                   "lockapp", "applicationframehost") and not title:
                    return True
                out.append({"hwnd": int(h), "app": app, "title": title, "min": bool(u.IsIconic(h)),
                            "max": bool(u.IsZoomed(h)), "rect": self._rect(h)})
            except Exception:
                pass
            return True
        u.EnumWindows(self.c.WINFUNCTYPE(self.c.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
        return out

    def monitors(self) -> list:
        """Рабочие области мониторов (без панели задач), слева направо: [{"work": (l,t,r,b), "primary": bool}]."""
        c, wt, u = self.c, self.wt, self.u
        mons = []

        class MI(c.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

        def cb(hm, _hdc, _r, _l):
            mi = MI()
            mi.cbSize = c.sizeof(MI)
            u.GetMonitorInfoW(hm, c.byref(mi))
            w = mi.rcWork
            mons.append({"work": (w.left, w.top, w.right, w.bottom), "primary": bool(mi.dwFlags & 1),
                         "handle": int(hm or 0)})
            return True
        proto = c.WINFUNCTYPE(c.c_bool, c.c_void_p, c.c_void_p, c.POINTER(wt.RECT), wt.LPARAM)
        u.EnumDisplayMonitors(None, None, proto(cb), 0)
        return sorted(mons, key=lambda m: (m["work"][0], m["work"][1]))

    def monitor_of(self, hwnd: int) -> int:
        """Номер монитора (в списке monitors()), где окно."""
        l, t, r, b = self._rect(hwnd)
        cx, cy = (l + r) // 2, (t + b) // 2
        for i, m in enumerate(self.monitors()):
            ml, mt, mr, mb = m["work"]
            if ml <= cx < mr and mt <= cy < mb:
                return i
        return 0

    def move(self, hwnd: int, rect) -> None:
        u = self.u
        if u.IsIconic(hwnd) or u.IsZoomed(hwnd):
            u.ShowWindow(hwnd, 9)                             # SW_RESTORE
        l, t, r, b = rect
        wr, fr = self._rect(hwnd), self._frame(hwnd)          # невидимые рамки — растянуть, чтобы не было щелей
        dl, dt, dr, db = fr[0] - wr[0], fr[1] - wr[1], wr[2] - fr[2], wr[3] - fr[3]
        if max(abs(dl), abs(dt), abs(dr), abs(db)) > 30:
            dl = dt = dr = db = 0
        u.SetWindowPos(hwnd, 0, l - dl, t - dt, (r - l) + dl + dr, (b - t) + dt + db, 0x0004 | 0x0040)

    def show(self, hwnd: int, how: str) -> None:
        self.u.ShowWindow(hwnd, {"max": 3, "min": 6, "restore": 9}[how])

    def focus(self, hwnd: int) -> None:
        from core import win_input
        win_input.focus(hwnd)


_win = None
_last = {"saved": None}
_lock = threading.Lock()


def _backend():
    global _win
    if _win is None:
        _win = Win()
    return _win


# --- разбор фразы -------------------------------------------------------------------
def _apps_in(text: str) -> list:
    """Программы в порядке упоминания: [(ключ, начало, конец)]."""
    found = []
    for k, rx in _APP_RX:
        for m in rx.finditer(text):
            found.append((m.start(), m.end(), k))
    found.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    out, end = [], -1
    for s, e, k in found:
        if s >= end:
            if k == "browser" and any(o[0] in ("chrome", "edge", "yandex", "firefox", "opera", "brave") and
                                      o[1] <= s < o[2] + 2 for o in out):
                continue
            out.append((k, s, e))
            end = e
    return out


def _pos_in(text: str):
    for k, rx in _POS_RX:
        m = rx.search(text)
        if m:
            return k, m
    return None, None


def _monitor_word(word: str) -> str:
    w = word.lower()
    if w.startswith(("перв", "главн", "основн")) or w in ("first", "main", "primary", "1"):
        return "1"
    if w.startswith("трет") or w in ("third", "3"):
        return "3"
    if w.startswith("лев") or w == "left":
        return "left"
    if w.startswith("прав") or w == "right":
        return "right"
    if w.isdigit():
        return w
    return "next"


def _target(clause: str, keep_pos: bool = True):
    """Кусок фразы → {"app": ключ | None, "words": остаток для поиска по заголовку}."""
    apps = _apps_in(clause)
    if apps:
        return {"app": apps[0][0]}
    rest = clause
    for _, rx in _POS_RX:
        rest = rx.sub(" ", rest)
    rest = _MON.sub(" ", rest)
    rest = _VERB.sub(" ", rest)
    rest = _FILLER.sub(" ", rest)
    words = [w for w in re.findall(r"[\w.+-]{3,}", rest) if not w.isdigit()]
    if not words and re.search(r"(?<![\w])(?:окн[оа]|window|его|это|текущ\w*|current)(?![\w])", clause, re.I):
        return {"app": None, "active": True}               # «разверни окно», «поставь его слева»
    return {"app": None, "words": words} if words else None


def intent(text: str):
    """Команда окнам → dict или None. Без глагола («поставь», «разверни»…) — не наше."""
    t = " ".join((text or "").replace("ё", "е").split())
    if not t or not _VERB.search(t):
        return None
    ru = _ru(t)
    if _UNDO.search(t):
        return {"do": "undo", "ru": ru}
    m = _MIN_ALL.search(t)
    if m:
        keep = [{"app": k} for k, _, _ in _apps_in(m.group(1) or "")]
        if m.group(1) and not keep:
            tg = _target(m.group(1))
            keep = [tg] if tg else []
        return {"do": "min_all", "keep": keep, "ru": ru}
    m = _TILE.search(t)
    if m:
        rest = next((g for g in m.groups() if g is not None), "") or ""
        apps = [{"app": k} for k, _, _ in _apps_in(rest)]
        if not apps and re.search(r"(?:раздели|разбей|split|tile|разлож|arrange)", t, re.I) and \
                re.search(r"(?<![\w])(?:экран\w*|окна|окон|screen|windows)(?![\w])", t, re.I) and \
                not re.search(r"\b(?:файл|текст|документ|список|file|text)\w*", t, re.I):
            return {"do": "tile", "targets": [], "ru": ru}
        if len(apps) >= 2:
            return {"do": "tile", "targets": apps[:4], "ru": ru}
    moves = []
    clauses = [c for c in re.split(r"\s*(?:,|;|\bа\b|\bи\b|\band\b|\bthen\b|\bзатем\b|\bпотом\b)\s*", t) if c.strip()]
    last_pos = None
    for c in clauses:
        mon = _MON.search(c)
        pos, _ = _pos_in(_MON.sub(" ", c))
        if re.search(r"(?<![\w])(?:разверн|maximi)\w*", c, re.I) and not pos:
            pos = "max"
        elif re.search(r"(?<![\w])(?:сверн|спрячь|minimi)\w*", c, re.I) and not pos:
            pos = "min"
        tg = _target(c)
        if not tg:
            continue
        if not pos and not mon:
            if last_pos is None:
                continue
            pos = last_pos
        last_pos = pos
        mv = dict(tg)
        mv["pos"] = pos
        if mon:
            mv["monitor"] = _monitor_word(mon.group(1))
        moves.append(mv)
    if not moves:
        return None
    if all(mv.get("app") is None for mv in moves) and len(t) > 90:
        return None                                        # длинная фраза без программ — пусть решает модель
    return {"do": "place", "moves": moves, "ru": ru}


# --- поиск окна -----------------------------------------------------------------------
def find(target: dict, wins: list, used=()):
    """Окно для цели: по программе (самое верхнее) или по словам заголовка."""
    if not target:
        return None
    if target.get("active"):                               # верхнее окно под Atlas — то, с которым работали
        return next((w for w in wins if w["hwnd"] not in used and not w["min"]), None)
    if target.get("app"):
        exes = APPS[target["app"]][1]
        for w in wins:
            if w["hwnd"] not in used and w["app"].lower() in exes:
                return w
        return None
    words = [w.lower() for w in target.get("words") or []]
    best, score = None, 0
    for w in wins:
        if w["hwnd"] in used:
            continue
        hay = (w["title"] + " " + w["app"]).lower().replace("ё", "е")
        s = sum(1 for x in words if x[:max(4, len(x) - 2)] in hay)
        if s > score:
            best, score = w, s
    return best


def _name(w: dict) -> str:
    title = w["title"]
    for k, (_, exes) in APPS.items():
        if w["app"].lower() in exes and k != "browser":
            pretty = {"code": "VS Code", "powerpoint": "PowerPoint", "explorer": "Проводник", "notepad": "Блокнот"}
            return pretty.get(k, k.capitalize())
    return (title.rsplit(" - ", 1)[-1] or w["app"])[:30]


# --- геометрия -------------------------------------------------------------------------
def rect_for(pos: str, work) -> tuple:
    l, t, r, b = work
    w, h = r - l, b - t
    mx, my = l + w // 2, t + h // 2
    return {
        "left": (l, t, mx, b), "right": (mx, t, r, b), "top": (l, t, r, my), "bottom": (l, my, r, b),
        "tl": (l, t, mx, my), "tr": (mx, t, r, my), "bl": (l, my, mx, b), "br": (mx, my, r, b),
        "l3": (l, t, l + w // 3, b), "c3": (l + w // 3, t, l + 2 * w // 3, b), "r3": (l + 2 * w // 3, t, r, b),
        "center": (l + w // 8, t + h // 10, r - w // 8, b - h // 10), "full": (l, t, r, b),
    }[pos]


def _pick_monitor(word: str, current: int, n: int) -> int:
    if n <= 1:
        return 0
    if word == "next":
        return (current + 1) % n
    if word == "left":
        return 0
    if word == "right":
        return n - 1
    if word.isdigit():
        return max(0, min(n - 1, int(word) - 1))
    return 0


def tile_rects(n: int, work) -> list:
    if n <= 1:
        return [rect_for("full", work)]
    if n == 2:
        return [rect_for("left", work), rect_for("right", work)]
    if n == 3:
        return [rect_for("left", work), rect_for("tr", work), rect_for("br", work)]
    return [rect_for(p, work) for p in ("tl", "tr", "bl", "br")]


_POS_RU = {"left": "слева", "right": "справа", "top": "сверху", "bottom": "снизу", "tl": "в левом верхнем углу",
           "tr": "в правом верхнем углу", "bl": "в левом нижнем углу", "br": "в правом нижнем углу",
           "l3": "в левой трети", "c3": "в средней трети", "r3": "в правой трети", "center": "по центру",
           "max": "на весь экран", "min": "свёрнут"}
_POS_EN = {"left": "on the left", "right": "on the right", "top": "on top", "bottom": "at the bottom",
           "tl": "top-left", "tr": "top-right", "bl": "bottom-left", "br": "bottom-right", "l3": "left third",
           "c3": "middle third", "r3": "right third", "center": "centered", "max": "maximized", "min": "minimized"}


# --- выполнение ---------------------------------------------------------------------------
def _save(win, wins: list) -> None:
    _last["saved"] = [(w["hwnd"], w["rect"], w["min"], w["max"]) for w in wins]


def run(cmd: dict, win=None) -> str:
    win = win or _backend()
    ru = cmd.get("ru", True)
    with _lock:
        try:
            wins = win.windows()
            mons = win.monitors() or [{"work": (0, 0, 1920, 1040), "primary": True}]
        except Exception as e:
            print(f"[окна] не получил список окон: {e}")
            return "Не получилось добраться до окон." if ru else "Couldn't reach the windows."
        do = cmd["do"]
        if do == "undo":
            saved = _last["saved"]
            if not saved:
                return "Я ещё не двигал окна." if ru else "I haven't moved any windows yet."
            alive = {w["hwnd"] for w in wins}
            n = 0
            for h, rect, was_min, was_max in saved:
                if h not in alive:
                    continue
                if was_min:
                    win.show(h, "min")
                elif was_max:
                    win.show(h, "max")
                else:
                    win.move(h, rect)
                n += 1
            _last["saved"] = None
            return (f"Вернул окна как было ({n})." if ru else f"Put {n} windows back.") if n else \
                ("Тех окон уже нет." if ru else "Those windows are gone.")

        if do == "min_all":
            keep = [find(k, wins) for k in cmd.get("keep") or []]
            keep_h = {k["hwnd"] for k in keep if k}
            if cmd.get("keep") and not keep_h:
                return "Не нашёл окно, которое оставить." if ru else "Couldn't find the window to keep."
            _save(win, wins)
            n = 0
            for w in wins:
                if w["hwnd"] not in keep_h and not w["min"]:
                    win.show(w["hwnd"], "min")
                    n += 1
            for k in keep:
                if k:
                    if k["min"]:
                        win.show(k["hwnd"], "restore")
                    win.focus(k["hwnd"])
            if keep_h:
                names = ", ".join(_name(k) for k in keep if k)
                return f"Свернул всё, кроме: {names}." if ru else f"Minimized everything except {names}."
            return f"Свернул все окна ({n})." if ru else f"Minimized {n} windows."

        if do == "tile":
            targets = cmd.get("targets") or []
            used, chosen = set(), []
            for tg in targets:
                w = find(tg, wins, used)
                if w:
                    chosen.append(w)
                    used.add(w["hwnd"])
            if not targets:
                chosen = [w for w in wins if not w["min"]][:2]
            if len(chosen) < 2:
                miss = [APPS.get(tg.get("app"), ("", ()))[1][:1] for tg in targets]
                return ("Для этого нужно хотя бы два открытых окна — нашёл " + str(len(chosen)) + "." if ru else
                        f"I need at least two open windows — found {len(chosen)}.") if not miss or chosen else \
                    ("Не нашёл эти окна." if ru else "Couldn't find those windows.")
            mi = win.monitor_of(chosen[0]["hwnd"]) if len(mons) > 1 else 0
            work = mons[min(mi, len(mons) - 1)]["work"]
            _save(win, wins)
            parts = []
            for w, rect, pos in zip(chosen, tile_rects(len(chosen), work), ("left", "tr", "br", "")):
                win.move(w["hwnd"], rect)
                parts.append(_name(w))
            for w in reversed(chosen):
                win.focus(w["hwnd"])
            return ("Разложил рядом: " if ru else "Side by side: ") + ", ".join(parts) + "."

        # place
        used, said, missing = set(), [], []
        plan = []
        for mv in cmd.get("moves") or []:
            w = find(mv, wins, used)
            if not w:
                missing.append(mv)
                continue
            used.add(w["hwnd"])
            plan.append((w, mv))
        if not plan and all(mv.get("app") is None and not mv.get("active") for mv in missing):
            return None                                    # «сделай отступ слева» — это не про окна
        if not plan:
            what = _miss_name(missing[0], ru) if missing else ""
            return (f"Не нашёл открытое окно {what}.".replace("  ", " ") if ru else
                    f"I don't see an open {what} window.".replace("  ", " "))
        _save(win, wins)
        for w, mv in plan:
            pos = mv.get("pos") or ("max" if mv.get("monitor") and w["max"] else
                                    "center" if mv.get("monitor") else "max")
            cur = win.monitor_of(w["hwnd"]) if len(mons) > 1 else 0
            mi = _pick_monitor(mv["monitor"], cur, len(mons)) if mv.get("monitor") else cur
            work = mons[min(mi, len(mons) - 1)]["work"]
            if pos == "min":
                win.show(w["hwnd"], "min")
            elif pos == "max":
                if mv.get("monitor"):
                    win.move(w["hwnd"], rect_for("center", work))
                win.show(w["hwnd"], "max")
            else:
                win.move(w["hwnd"], rect_for(pos, work))
            if pos != "min":
                win.focus(w["hwnd"])
            where = (_POS_RU if ru else _POS_EN).get(pos, "")
            if mv.get("monitor") and len(mons) > 1:
                where = (f"на мониторе {mi + 1}" if ru else f"on monitor {mi + 1}") + (f", {where}" if where else "")
            said.append(f"{_name(w)} {where}".strip())
        out = ", ".join(said)
        out = (out[0].upper() + out[1:] + ".") if out else ""
        if mons and len(mons) == 1 and any(mv.get("monitor") for _, mv in plan):
            out += " Монитор у тебя один." if ru else " You have one monitor."
        if missing:
            out += (" Не нашёл: " if ru else " Not found: ") + ", ".join(_miss_name(m, ru) for m in missing) + "."
        return out


def handle(text: str, win=None):
    """Быстрый путь: фраза про окна → ответ; None — не про окна (дальше разбирается как обычно)."""
    cmd = intent(text)
    if not cmd:
        return None
    try:
        return run(cmd, win)
    except Exception as e:
        print(f"[окна] {e}")
        return None


def _miss_name(mv: dict, ru: bool) -> str:
    if mv.get("app"):
        return mv["app"].capitalize() if mv["app"] != "code" else "VS Code"
    return " ".join(mv.get("words") or [])


# --- для модели ------------------------------------------------------------------------------
def arrange(spec: str, win=None) -> str:
    """'chrome:left; word:right' | 'tile: chrome, code' | 'minimize all except: code' | 'undo'."""
    s = (spec or "").strip()
    low = s.lower()
    if not s:
        return "Say which windows and where, e.g. 'chrome:left; word:right'."
    if low.startswith("undo"):
        return run({"do": "undo", "ru": False}, win)
    if low.startswith("tile"):
        names = re.split(r"[,;]", s.split(":", 1)[-1])
        return run({"do": "tile", "targets": [_target(n) for n in names if _target(n)], "ru": False}, win)
    if low.startswith("minimize all"):
        keep = s.split(":", 1)[1] if ":" in s else ""
        return run({"do": "min_all", "keep": [_target(n) for n in re.split(r"[,;]", keep) if _target(n)],
                    "ru": False}, win)
    moves = []
    for part in re.split(r"[;\n]", s):
        if ":" not in part:
            continue
        name, where = part.split(":", 1)
        tg = _target(name)
        where = where.strip().lower()
        mon = re.search(r"monitor\s*(\d|next|other|left|right)", where)
        pos = next((k for k in ("left", "right", "top", "bottom", "tl", "tr", "bl", "br", "l3", "c3", "r3",
                                "center", "max", "min", "full") if re.search(rf"\b{k}\b", where)), None)
        if tg:
            mv = dict(tg, pos=pos or "full")
            if mon:
                mv["monitor"] = _monitor_word(mon.group(1))
            moves.append(mv)
    if not moves:
        return "Couldn't understand the layout. Format: 'chrome:left; word:right'."
    return run({"do": "place", "moves": moves, "ru": False}, win)


if __name__ == "__main__":                                  # python -m core.window_layout check
    import sys
    for _st in (sys.stdout,):
        try:
            _st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    w = _backend()
    for i, m in enumerate(w.monitors(), 1):
        print(f"монитор {i}: {m['work']}{' (главный)' if m['primary'] else ''}")
    for x in w.windows()[:15]:
        print(f"  {x['app']:<18} {'свёрнуто ' if x['min'] else ''}{'развёрнуто ' if x['max'] else ''}{x['title'][:60]}")
    if len(sys.argv) > 2:
        print(handle(" ".join(sys.argv[2:])))
