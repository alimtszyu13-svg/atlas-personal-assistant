"""
Ответы в мессенджерах голосом: Telegram, WhatsApp, Instagram (сайт), Discord — и их веб-версии в браузере.

    «ответь маме в телеграме, что опоздаю на 10 минут»
    «напиши Алиму в ватсапе: буду в 7»               → после двоеточия или в «кавычках» — дословно
    «спроси Дану в инсте, когда сдавать проект»
    «ответь ему, что согласен» / «ответь на это»       → в чате, который сейчас открыт
    «что написала мама в телеграме?»                    → коротко, что пишут
    «да» / «отправь» — отправить; «короче», «по-другому», «добавь, что…» — переписать; «нет» — отмена.

Как: Atlas открывает мессенджер, находит чат (в списке или через поиск), читает видимую переписку с
экрана (Windows OCR: свои сообщения — справа, собеседника — слева), пишет ответ твоим стилем и
ПОКАЗЫВАЕТ его. Отправляет только после твоего «да» — никогда сам.
Переписка читается локально; модели уходит только видимый кусок этого чата, чтобы написать ответ.
Где кто (мама → Telegram) запоминается в atlas_messengers.json, только на этом компьютере.
"""
import difflib
import json
import os
import re
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "atlas_messengers.json")
PENDING_TTL = 300

APPS = {
    "telegram": {"name": "Telegram", "exe": ("telegram", "ayugram", "64gram"), "web": ("telegram",),
                 "url": "https://web.telegram.org/", "launch": "tg://", "mode": "esc",
                 "rx": r"телеграм\w*|телеграмм\w*|телег[аеиуо]\w*|\bтг\b|telegram|\btg\b"},
    "whatsapp": {"name": "WhatsApp", "exe": ("whatsapp", "whatsapp.root"), "web": ("whatsapp",),
                 "url": "https://web.whatsapp.com/", "launch": "whatsapp:", "mode": "search",
                 "keys": "ctrl+f", "web_keys": "ctrl+alt+/",
                 "rx": r"ватсап\w*|вотсап\w*|вацап\w*|воцап\w*|whats ?app|ватс ап\w*"},
    "instagram": {"name": "Instagram", "exe": (), "web": ("instagram",),
                  "url": "https://www.instagram.com/direct/inbox/", "launch": "", "mode": "list",
                  "rx": r"инст[аеуы]\w*|инстаграм\w*|instagram|\bдирект(?:е|а|у|ом)?\b|\bdirect\b|\binsta\b"},
    "discord": {"name": "Discord", "exe": ("discord",), "web": ("discord",),
                "url": "https://discord.com/channels/@me", "launch": "discord://", "mode": "switcher",
                "keys": "ctrl+k", "rx": r"дискорд\w*|discord"},
}
_APP_RX = [(k, re.compile(r"(?:\s*\b(?:в|во|на|через|in|on|via)\s+)?(?:" + v["rx"] + r")", re.I)) for k, v in APPS.items()]
_BROWSERS = ("chrome", "msedge", "browser", "firefox", "opera", "opera_gx", "brave", "vivaldi")
_SEARCH_WORDS = re.compile(r"^(?:поиск|search|найти|search or start (?:a )?new chat|поиск или новый чат|"
                           r"искать|ищите|search messenger)\b", re.I)
_lock = threading.Lock()
_s = {"pending": None, "said": None}


def _ru(text: str) -> bool:
    return bool(re.search(r"[а-яё]", text or "", re.I))


def norm(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    return " ".join(re.findall(r"[\w@.+-]+", t))


_POSS = {"моя", "мой", "моё", "мое", "мои", "моей", "моему", "моего", "моим", "моих", "мою", "моем", "моём",
         "наша", "наш", "нашей", "нашему", "нашего", "нашим", "my", "our"}
_END2 = ("ой", "ей", "ом", "ем", "ою", "ею", "ам", "ям", "ах", "ях", "ым", "им", "ого", "его", "ому", "ему")


def stems(contact: str) -> list:
    """«Маме» → ['мам'], «Алиму Сыдыкову» → ['алим', 'сыдыков']: поиск в мессенджерах ищет по началу слова,
    так что падеж не мешает."""
    out = []
    for w in norm(contact).split():
        if w in _POSS:                                   # «моей маме» → как «маме» (раньше «мое» мешало найти «Мама»)
            continue
        if w in ("в", "во", "на", "чат", "чате", "группу", "группе", "беседу", "беседе", "to", "the", "chat"):
            if w in ("группу", "группе"):
                out.append("групп")
            continue
        if re.fullmatch(r"[a-z0-9@._+-]+", w) or len(w) <= 3:
            out.append(w)
            continue
        cut = w
        for e in _END2:
            if cut.endswith(e) and len(cut) - len(e) >= 3:
                cut = cut[:-len(e)]
                break
        else:
            if cut[-1] in "аеиоуыэюяй" and len(cut) >= 4:
                cut = cut[:-1]
        out.append(cut)
    return out[:3]


def line_matches(text: str, st: list) -> bool:
    words = norm(text).split()
    return bool(st) and all(any(w.startswith(s) for w in words) for s in st)


# слова в названии чата, которые не делают его «чужим»: «Моя мама ❤», «Мама родная», «Мама 2»
_NOISE_W = re.compile(r"^(?:моя|мой|мое|мои|любим\w*|родн\w*|дорог\w*|my|dear|\d{1,2})$")


def is_kin(contact: str) -> bool:
    """«маме», «папе», «брату» — родство без имени. «маме Данияра» — уже не просто родство."""
    words = [w for w in norm(contact).split() if w not in _POSS]
    return bool(words) and all(_KIN.match(w) for w in words)


def match_score(text: str, st: list, kin: bool = False) -> float:
    """Насколько название чата совпадает с тем, кого назвали.
    3 — точно («Мама», «Мама ❤», «Моя мама»); 1–2 — имя с фамилией («Алим» → «Алим Сыдыков»);
    0.5 — родство с чужим именем («маме» → «Мама Данияра» — это мама друга); 0 — не совпадает."""
    if not line_matches(text, st):
        return 0.0
    extra = [w for w in norm(text).split() if not any(w.startswith(s) for s in st) and not _NOISE_W.match(w)]
    if not extra:
        return 3.0
    if kin:
        return 0.5
    return max(1.0, 2.0 - 0.3 * len(extra))


# --- запоминание: кто в каком мессенджере ------------------------------------------------
def _load() -> dict:
    try:
        with open(FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _entry(v) -> dict:
    """Старый формат {"мам": "telegram"} и новый {"мам": {"app": …, "titles": {app: «как записан»}}}."""
    if isinstance(v, str):
        return {"app": v, "titles": {}}
    return v if isinstance(v, dict) else {"app": None, "titles": {}}


def _remember(contact: str, app: str = None, title: str = None) -> None:
    key = _key(contact)
    if not key:
        return
    with _lock:
        d = _load()
        e = _entry(d.get(key))
        if app:
            e["app"] = app
        if title:
            e.setdefault("titles", {})[app or "*"] = title.strip()
        d[key] = e
        try:
            with open(FILE, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
        except Exception as e2:
            print(f"[мессенджеры] не запомнил: {e2}")


def _key(contact: str) -> str:
    return "__self__" if is_self(contact or "") else " ".join(stems(contact))


def known_app(contact: str):
    return _entry(_load().get(_key(contact))).get("app")


def known_title(contact: str, app: str = None):
    """Как контакт записан в этом мессенджере (запомнили после отправки или со слов «мама записана как …»)."""
    t = _entry(_load().get(_key(contact))).get("titles") or {}
    return t.get(app) or t.get("*")


# --- Windows (в тестах подменяется) ----------------------------------------------------------
class UI:
    def __init__(self):
        import ctypes
        self.c = ctypes
        self.u = ctypes.windll.user32

    def _dpi(self):
        try:
            self.u.SetThreadDpiAwarenessContext(self.c.c_void_p(-4))     # пиксели как у снимка экрана
        except Exception:
            pass

    def windows(self) -> list:
        from core import window_layout
        self._dpi()
        return window_layout._backend().windows()

    def launch(self, app: str) -> None:
        a = APPS[app]
        try:
            if a["launch"]:
                os.startfile(a["launch"])
                return
        except Exception:
            pass
        import webbrowser
        webbrowser.open(a["url"])

    def open_url(self, url: str) -> None:
        if not url.lower().startswith(("http://", "https://")):     # whatsapp://send?phone=…, tg://resolve?phone=…
            try:
                os.startfile(url)
                return
            except Exception as e:
                print(f"[мессенджеры] ссылка {url.split('?')[0]} не открылась: {e}")
        import webbrowser
        webbrowser.open(url)

    def activate(self, hwnd: int) -> bool:
        from core import win_input
        try:
            if self.u.IsIconic(hwnd):
                self.u.ShowWindow(hwnd, 9)
        except Exception:
            pass
        return win_input.focus(hwnd)

    def foreground(self) -> int:
        from core import win_input
        return win_input.foreground()

    def same_process(self, hwnd: int) -> bool:
        """Впереди окно того же приложения (у WhatsApp/Telegram бывают всплывающие и дочерние окна)."""
        from ctypes import wintypes as wt
        try:
            a, b = wt.DWORD(), wt.DWORD()
            self.u.GetWindowThreadProcessId(self.foreground(), self.c.byref(a))
            self.u.GetWindowThreadProcessId(hwnd, self.c.byref(b))
            return bool(a.value) and a.value == b.value
        except Exception:
            return False

    def who_is_front(self) -> str:
        from core import win_input
        h = self.foreground()
        return f"«{win_input.title_of(h)[:40]}»{' (сам Atlas)' if win_input.is_atlas(h) else ''}"

    def rect(self, hwnd: int):
        from ctypes import wintypes as wt
        self._dpi()
        r = wt.RECT()
        self.u.GetWindowRect(hwnd, self.c.byref(r))
        return (r.left, r.top, r.right, r.bottom)

    def ocr(self, rect) -> dict:
        """Строки текста в окне: {"ru": [...], "en": [...]}, строка = {text, x, y, w, h} в пикселях экрана."""
        from PIL import ImageGrab
        self._dpi()
        l, t, r, b = rect
        l, t = max(0, int(l)), max(0, int(t))              # развёрнутое окно: рамка (-8,-8,…) — минусы ломают захват
        try:
            img = ImageGrab.grab(bbox=(l, t, int(r), int(b)), all_screens=True)
        except Exception as e:
            print(f"[мессенджеры] снимок экрана не вышел: {e}")
            return {"ru": [], "en": []}
        return _ocr_in_subprocess(img, l, t)

    def click(self, x: int, y: int) -> None:
        from ctypes import wintypes as wt
        self._dpi()
        pt = wt.POINT()
        self.u.GetCursorPos(self.c.byref(pt))
        self.u.SetCursorPos(int(x), int(y))
        time.sleep(0.05)
        self.u.mouse_event(0x0002, 0, 0, 0, 0)
        self.u.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.05)
        self.u.SetCursorPos(pt.x, pt.y)

    def keys(self, combo: str) -> None:
        from core import win_input
        win_input.keys(combo)

    def paste(self, text: str) -> None:
        from core import win_input
        win_input.paste(text)

    def focus_edit(self, hwnd: int, rx: str) -> bool:
        """Поле ввода по названию (Windows UI Automation) — только в приложениях, не в браузере."""
        try:
            import uiautomation as auto
            ctl = auto.ControlFromHandle(hwnd)
            pat = re.compile(rx, re.I)
            for c, _ in auto.WalkControl(ctl, maxDepth=25):
                if c.ControlTypeName == "EditControl" and pat.search(c.Name or "") and \
                        not re.search(r"address|адрес", c.Name or "", re.I):
                    c.SetFocus()
                    return True
        except Exception as e:
            print(f"[мессенджеры] UIA: {e}")
        return False

    def uia_lines(self, hwnd: int, budget: float = 2.5) -> list:
        """Тексты элементов окна по UI Automation (как их читает экранный диктор) — точные, в отличие от OCR.
        Строка = {text, x, y, w, h}; у элемента из нескольких строк («Мама ❤\n19:37\n?») — каждая отдельно."""
        out = []
        try:
            import uiautomation as auto
            self._dpi()
            t0 = time.time()
            ctl = auto.ControlFromHandle(hwnd)
            for c, _ in auto.WalkControl(ctl, maxDepth=45):
                if time.time() - t0 > budget:
                    break
                if c.ControlTypeName not in _UIA_TYPES:
                    continue
                name = (c.Name or "").strip()
                if not name or len(name) > 300:
                    continue
                r = c.BoundingRectangle
                if r.right - r.left < 4 or r.bottom - r.top < 4:
                    continue
                parts = [x.strip() for x in re.split(r"[\r\n]+", name) if x.strip()][:3]
                h = max(1, (r.bottom - r.top) // max(1, len(parts)))
                for i, part in enumerate(parts):
                    out.append({"text": part, "x": r.left, "y": r.top + i * h, "w": r.right - r.left, "h": h,
                                "uia": c.ControlTypeName})
        except Exception as e:
            print(f"[мессенджеры] UIA: {e}")
        try:
            rect = self.rect(hwnd)
            out = [ln for ln in out if rect[1] - 2 <= ln["y"] <= rect[3]]        # прокрученное за край — не на экране
            learn_divider(hwnd, out, rect)
        except Exception:
            pass
        return out

    def sleep(self, s: float) -> None:
        time.sleep(s)


_UIA_TYPES = ("ListItemControl", "DataItemControl", "TreeItemControl", "ButtonControl", "HyperlinkControl",
              "TextControl", "HeaderItemControl", "TabItemControl")


def _lines(res, ox: int, oy: int) -> list:
    out = []
    for ln in (res or {}).get("lines") or []:
        boxes = [w.get("bounding_rect") for w in ln.get("words") or [] if w.get("bounding_rect")]
        if not boxes or not (ln.get("text") or "").strip():
            continue
        x1 = min(b["x"] for b in boxes)
        y1 = min(b["y"] for b in boxes)
        x2 = max(b["x"] + b["width"] for b in boxes)
        y2 = max(b["y"] + b["height"] for b in boxes)
        out.append({"text": ln["text"].strip(), "x": ox + x1, "y": oy + y1, "w": x2 - x1, "h": y2 - y1})
    return out


_ocr_dead = {"since": 0.0}          # winocr рухнул недавно — не дёргаем его каждую секунду


def _ocr_in_subprocess(img, ox: int, oy: int, timeout: float = 25.0) -> dict:
    """winocr в отдельном процессе: если он падает/виснет — умирает процесс, а не Atlas."""
    import subprocess
    import tempfile
    if time.time() - _ocr_dead["since"] < 120:            # только что падал — 2 минуты не трогаем
        return {"ru": [], "en": []}
    tmp = os.path.join(tempfile.gettempdir(), f"atlas_ocr_{os.getpid()}_{threading.get_ident()}.png")
    scale = 1                       # снимок идёт в полном размере: воркер режет на куски, а не уменьшает
    try:
        img.save(tmp)
        from file_search import _ocr_lock
        with _ocr_lock:
            p = subprocess.run([sys.executable, "-m", "core._ocr_worker", tmp, "ru,en"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=ROOT)
        if p.returncode != 0 or not (p.stdout or "").strip():
            _ocr_dead["since"] = time.time()
            print(f"[мессенджеры] распознавание окна не работает (winocr упал, код {p.returncode}) — "
                  f"читать чаты не могу, отправлять могу")
            return {"ru": [], "en": []}
        data = json.loads(p.stdout)
        return {lang: [dict(ln, x=ln["x"] // scale + ox, y=ln["y"] // scale + oy, w=ln["w"] // scale,
                            h=ln["h"] // scale) for ln in data.get(lang, [])] for lang in ("ru", "en")}
    except subprocess.TimeoutExpired:
        _ocr_dead["since"] = time.time()
        print("[мессенджеры] распознавание окна зависло — пропускаю")
        return {"ru": [], "en": []}
    except Exception as e:
        print(f"[мессенджеры] распознавание: {e}")
        return {"ru": [], "en": []}
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


_ui = None


def _get_ui():
    global _ui
    if _ui is None:
        _ui = UI()
    return _ui


# --- окно мессенджера --------------------------------------------------------------------------
_STORE_TITLE = {"whatsapp": re.compile(r"\bwhatsapp\b", re.I), "telegram": re.compile(r"\btelegram\b", re.I),
                "discord": re.compile(r"\bdiscord\b", re.I)}


def find_window(app: str, wins: list):
    a = APPS[app]
    for w in wins:
        if w["app"].lower() in a["exe"]:
            return dict(w, web=False)
    rx = _STORE_TITLE.get(app)                                # WhatsApp из Store: окно под ApplicationFrameHost
    if rx:
        for w in wins:
            if w["app"].lower() not in _BROWSERS and rx.search(w["title"] or ""):
                return dict(w, web=False)
    for w in wins:
        if w["app"].lower() in _BROWSERS and any(k in w["title"].lower() for k in a["web"]):
            return dict(w, web=True)
    return None


def current_messenger(wins: list):
    """Самое верхнее окно под Atlas, если это мессенджер."""
    for w in wins[:3]:
        if w.get("min"):
            continue
        for k in APPS:
            f = find_window(k, [w])
            if f:
                return k, f
        return None
    return None


def _ensure(app: str, ui):
    wins = ui.windows()
    w = find_window(app, wins)
    if not w:
        ui.launch(app)
        for _ in range(20):
            ui.sleep(0.5)
            w = find_window(app, ui.windows())
            if w:
                ui.sleep(1.5 if not w.get("web") else 3.0)      # загрузка чатов
                break
    if w:
        ui.activate(w["hwnd"])
        ui.sleep(0.35)
    return w


def _pick(lines: dict, st: list) -> list:
    """Строки на нужном алфавите: кириллица — русский движок, латиница — английский."""
    cyr = any(re.search(r"[а-я]", s) for s in st)
    return lines.get("ru" if cyr else "en") or lines.get("en" if cyr else "ru") or []


_DIV = {}          # hwnd → x границы «список чатов | переписка» (узнали из структуры окна)


def learn_divider(hwnd, items: list, rect) -> int:
    """Где кончается список чатов: ручка «размер панели» или правый край элементов списка.
    WhatsApp: список всегда ~400 px, а не 45% окна — на широком экране доли промахиваются мимо заголовка."""
    l, t, r, b = rect
    mid = l + (r - l) * 0.6
    grip = [ln for ln in items if re.search(r"размер панели|resize|chat ?list|списка чатов", ln["text"], re.I)
            and l < ln["x"] < mid and ln["w"] < 60]
    if grip:
        d = int(grip[0]["x"] + grip[0]["w"] / 2)
    else:
        rows = [ln for ln in items if ln.get("uia") in ("DataItemControl", "ListItemControl", "TreeItemControl")
                and ln["w"] > 150 and ln["x"] < mid and ln["x"] + ln["w"] < mid]
        if len(rows) < 3:
            return 0
        d = int(max(ln["x"] + ln["w"] for ln in rows)) + 6
    if hwnd is not None:
        _DIV[hwnd] = d
    return d


def _regions(rect, web: bool, hwnd=None):
    l, t, r, b = rect
    W, H = r - l, b - t
    rg = {"list_x": l + int(W * (0.45 if not web else 0.42)), "top": t + int(H * (0.07 if not web else 0.12)),
          "chat_x": l + int(W * (0.30 if not web else 0.28)),
          "head_y": t + int(H * (0.16 if not web else 0.26)), "foot_y": b - int(H * 0.09), "W": W, "H": H}
    d = _DIV.get(hwnd) if hwnd is not None else None
    if d and l + 100 < d < l + W * 0.6:                          # настоящая граница панелей вместо долей окна
        rg["list_x"] = rg["chat_x"] = d + 4
    return rg


def _cx(ln):
    return ln["x"] + ln["w"] / 2


def _cy(ln):
    return ln["y"] + ln["h"] / 2


_HEAD_JUNK = re.compile(r"[\s,.]*(?:нажмите здесь|щелкните здесь|щёлкните здесь|click here|tap here)\b.*$", re.I)


def _header(lines: list, rg: dict) -> list:
    """Строки заголовка открытого чата. WhatsApp подписывает кнопку: «Назар нажмите здесь, чтобы посмотреть данные
    контакта» — подпись срезаем (живой тест 28.10)."""
    out = []
    for ln in lines:
        if _cx(ln) > rg["chat_x"] and ln["y"] < rg["head_y"]:
            t = _HEAD_JUNK.sub("", ln["text"]).strip()
            if t:
                out.append(dict(ln, text=t) if t != ln["text"] else ln)
    return out


def _choose(lines: list, rg: dict, st: list, kin: bool, title: str = None, query: str = ""):
    """Какую строку списка чатов нажать. → ("click", строка) | ("ambiguous", [названия]) | ("weak", [названия]) | None.
    Никогда не берём «верхний похожий» наугад: только точный, единственный или запомненный."""
    cand = [ln for ln in lines if _cx(ln) < rg["list_x"] and ln["y"] > rg["top"]]
    if query:
        # Текст в строке поиска — не чат. Но в структуре окна поля поиска нет, а найденная строка «Назар» = запросу «Назар»:
        # раньше она выбрасывалась вместе с полем, и точное имя не находилось никогда (лог 23.10: «Назар» → notfound).
        same = [ln for ln in cand if norm(ln["text"]) == norm(query) and not ln.get("uia")]
        box = [ln for ln in same if ln["y"] < rg["top"] + rg.get("H", 0) * 0.1]
        if not box and len(same) > 1:
            box = [min(same, key=lambda x: x["y"])]
        cand = [ln for ln in cand if not any(ln is b for b in box)]
    if title:                                                         # знаем, как записан, — только он
        exact = [ln for ln in cand if norm(ln["text"]) == norm(title)]
        if exact:
            return "click", min(exact, key=lambda x: x["y"])
    scored = [(match_score(ln["text"], st, kin), ln) for ln in cand]
    scored = [(sc, ln) for sc, ln in scored if sc > 0]
    if not scored:
        return None
    short = [(sc, ln) for sc, ln in scored if len(norm(ln["text"]).split()) <= 4]   # названия чатов короткие, превью — длинные
    if short:
        scored = short
    best = max(sc for sc, _ in scored)
    top = [ln for sc, ln in scored if sc == best]
    names = list(dict.fromkeys(ln["text"].strip() for ln in top))
    if best < 1:                                                      # «маме» → только «Мама Данияра»: это не она
        return "weak", list(dict.fromkeys(ln["text"].strip() for _, ln in scored))[:4]
    rows = []                                                         # «Назар» и «Назар 20:31» в одной строке списка — один чат
    for ln in sorted(top, key=_cy):
        if rows and abs(_cy(ln) - _cy(rows[-1][-1])) < 30:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    if len(rows) > 1 and len({norm(n) for n in names}) > 1:          # «Алим Сыдыков» и «Алим Касымов»
        return "ambiguous", names[:4]
    return "click", min(top, key=lambda x: x["y"])


def open_chat(app: str, contact: str, ui=None, title: str = None) -> dict:
    """Открыть чат → {"ok", "hwnd", "verified", "name", "why", "options"}."""
    ui = ui or _get_ui()
    t0 = time.time()
    r = _open_chat_logged(app, contact, ui, title)
    print(f"[мессенджеры] {app}: «{contact}»{f' (как «{title}»)' if title else ''} → "
          f"{'открыт «' + str(r.get('name')) + '»' + (' по номеру' if r.get('phone') else '') if r.get('ok') else r.get('why')}"
          f"{'' if r.get('verified', True) else ', не проверен'} за {time.time() - t0:.1f} с")
    return r


def _open_chat_logged(app: str, contact: str, ui, title: str = None) -> dict:
    remembered = None if title else known_title(contact, app)
    r = _open_chat(app, contact, ui, title or remembered)
    if not r.get("ok") and remembered and r.get("why") not in ("noapp",):   # запомненное имя устарело — ищем по слову
        r = _open_chat(app, contact, ui, None, avoid=remembered)
    return r


def _open_chat(app: str, contact: str, ui, title: str = None, avoid: str = "") -> dict:
    w = _ensure(app, ui)
    if not w:
        return {"ok": False, "why": "noapp"}
    hwnd, web = w["hwnd"], w.get("web", False)
    if is_self(contact) and not title:
        return _open_self(app, ui, hwnd, web)
    kin = is_kin(contact) and not title
    st = stems(title) if title else stems(contact)
    a = APPS[app]

    def look():
        rect = ui.rect(hwnd)
        lines = ui.ocr(rect)
        if not web and hasattr(ui, "uia_lines"):
            uia = ui.uia_lines(hwnd)
            if len(uia) < 25:                                          # окно ещё перерисовывается после поиска — ещё раз
                ui.sleep(0.6)
                uia = ui.uia_lines(hwnd) or uia
            lines = dict(lines, uia=uia)
        return rect, _regions(rect, web, hwnd), lines

    def seen(lines, rg):
        """Что было в списке чатов — для лога, когда не нашлось (как в _open_self)."""
        src = lines.get("uia") or _pick(lines, st)
        names = [ln["text"].strip() for ln in src if _cx(ln) < rg["list_x"] and ln["y"] > rg["top"]
                 and len(ln["text"].split()) <= 4]
        return f"{len(src)} строк, в списке: {list(dict.fromkeys(names))[:10]}"

    def by_phone():
        """Номер из телефонной книги → ссылка открывает именно этот чат. Поиск на экране не нужен (логи 23–24.10)."""
        if web or app not in ("whatsapp", "telegram"):
            return None
        try:
            from core import contacts
            hit = contacts.lookup(title or contact)
        except Exception as e:
            print(f"[мессенджеры] телефонная книга: {e}")
            return None
        if not hit or hit[0] != "one":
            if hit:
                print(f"[мессенджеры] в контактах несколько похожих: {hit[1]} — ищу на экране")
            return None
        _, pname, phone = hit
        pst, tail = stems(pname), phone[-9:]
        def quick():                                      # структура окна — доли секунды; снимок+OCR — только без неё
            rect = ui.rect(hwnd)
            uia = ui.uia_lines(hwnd) if hasattr(ui, "uia_lines") else []
            return rect, _regions(rect, web, hwnd), ({"uia": uia} if len(uia) >= 25 else dict(ui.ocr(rect), uia=uia))

        rect, rg, lines = quick()
        before = [norm(ln["text"]) for ln in _header(lines.get("uia") or [], rg) + _header(_pick(lines, pst), rg)]
        ui.open_url(f"whatsapp://send?phone={phone}" if app == "whatsapp" else f"tg://resolve?phone={phone}")
        head = []
        for wait in (0.8, 1.0, 1.5, 2.0):
            ui.sleep(wait)
            ui.activate(hwnd)
            rect, rg, lines = quick()
            head = [ln for ln in _header(lines.get("uia") or [], rg) + _header(_pick(lines, pst), rg)
                    if 1 < len(ln["text"]) < 60]
            for ln in head:                               # имя из книги или номер в заголовке — точно тот чат
                if match_score(ln["text"], pst) >= 1 or match_score(ln["text"], st, kin) >= 1 or \
                        (tail and tail in re.sub(r"\D", "", ln["text"])):
                    return {"ok": True, "hwnd": hwnd, "verified": True, "web": False, "name": ln["text"], "phone": phone}
            if head and [norm(ln["text"]) for ln in head] != before:   # открылся другой заголовок: так он записан в телефоне
                return {"ok": True, "hwnd": hwnd, "verified": True, "web": False, "name": head[0]["text"],
                        "phone": phone}
        print(f"[мессенджеры] {app}: открыл по номеру +{phone} ({pname}), но заголовок не сменился "
              f"({[ln['text'] for ln in head][:3]}) — ищу на экране")
        return None

    def head_match(lines, rg):
        """Строка заголовка открытого чата, если это именно тот, кто нужен (не «Мама Данияра» вместо «Мамы»)."""
        for ln in _header(lines.get("uia") or [], rg) + _header(_pick(lines, st), rg):
            if title and norm(ln["text"]).startswith(norm(title)[:24]):
                return ln
            if not title and match_score(ln["text"], st, kin) >= 1:
                return ln
        return None

    def verify(clicked=None):
        ui.sleep(0.9)
        rect, rg, lines = look()
        h = head_match(lines, rg)
        if h is None and clicked is not None:                         # заголовок обрезан — сверяем с тем, что нажали
            h = next((ln for ln in _header(lines.get("uia") or [], rg) + _header(_pick(lines, st), rg)
                      if norm(clicked["text"])[:16] and norm(ln["text"]).startswith(norm(clicked["text"])[:16])), None)
        return {"ok": True, "hwnd": hwnd, "verified": h is not None,
                "name": h["text"] if h is not None else (clicked["text"] if clicked else contact), "web": web}

    rect, rg, lines = look()
    h = head_match(lines, rg)
    if h is not None:                                                  # нужный чат уже открыт
        return {"ok": True, "hwnd": hwnd, "verified": True, "web": web, "name": h["text"]}
    r = by_phone()
    if r is not None:
        return r

    def try_list(lines, rg, query=""):
        query = query or avoid                                         # старый текст в строке поиска — не чат
        res = _choose(lines.get("uia") or [], rg, st, kin, title, query) if lines.get("uia") else None
        if res is None or res[0] != "click":                         # точные имена из структуры окна — первыми
            res2 = _choose(_pick(lines, st), rg, st, kin, title, query)
            res = res2 if res2 is not None and (res is None or res2[0] == "click") else res
        if res is None:
            return None
        if res[0] == "click":
            ln = res[1]
            ui.click(int(_cx(ln)), int(_cy(ln)))
            return verify(ln)
        return {"ok": False, "why": res[0], "options": res[1], "hwnd": hwnd}

    if a["mode"] != "switcher":                                      # чат уже виден в списке
        r = try_list(lines, rg)
        if r is not None:
            return r
    if a["mode"] == "list":                                          # Instagram: поиска в директе нет
        return {"ok": False, "why": "notinlist", "hwnd": hwnd}

    query = title or (" ".join(st) if all(len(s) >= 3 for s in st) else contact)
    if a["mode"] == "switcher":                                      # Discord: Ctrl+K → имя → Enter
        ui.keys(a["keys"])
        ui.sleep(0.4)
        ui.paste(query)
        ui.sleep(0.9)
        ui.keys("enter")
        return verify()
    ui.keys("esc")
    ui.sleep(0.15)
    if a["mode"] == "esc":                                           # Telegram: Esc — сразу в поиск
        ui.keys("esc")
        ui.sleep(0.25)
    else:
        focused = (not web) and ui.focus_edit(hwnd, r"search|поиск|найти")
        if not focused:
            ph = [ln for ln in (lines.get("ru") or []) + (lines.get("en") or [])
                  if _cx(ln) < rg["list_x"] and _SEARCH_WORDS.search(ln["text"])]
            if ph:
                ui.click(int(_cx(ph[0])), int(_cy(ph[0])))
            else:
                ui.keys(a.get("web_keys") if web else a.get("keys", "ctrl+f"))
        ui.sleep(0.3)
    ui.paste(query)
    saw_text = False
    for wait in (1.2, 1.5):
        ui.sleep(wait)
        rect, rg, lines = look()
        if lines.get("ru") or lines.get("en") or lines.get("uia"):
            saw_text = True
        r = try_list(lines, rg, query)
        if r is not None:
            if not r.get("ok"):
                ui.keys("esc")
            return r
    if saw_text:                                            # распознавание работает, а чат не нашёлся — не открываем наугад
        print(f"[мессенджеры] {app}: искал «{query}», не нашёл — видел {seen(lines, rg)}")
        ui.keys("esc")
        return {"ok": False, "why": "notfound", "hwnd": hwnd}
    # OCR ничего не вернул (распознавание окна не работает) — открываем верхний результат поиска с клавиатуры.
    # Без проверки по экрану: send() скажет «не уверен, что это тот чат — глянь», и ждёт «да».
    ui.keys("down")
    ui.sleep(0.35)
    ui.keys("enter")
    ui.sleep(0.6)
    v = verify()
    if v.get("verified"):
        return v
    return {"ok": True, "hwnd": hwnd, "web": web, "verified": False, "name": contact, "why": "unverified"}


def _open_self(app: str, ui, hwnd: int, web: bool) -> dict:
    """Чат с самим собой: WhatsApp — «+996 … (вы)», Telegram — «Избранное»."""
    def look():
        rect = ui.rect(hwnd)
        lines = ui.ocr(rect)
        uia = ui.uia_lines(hwnd) if (not web and hasattr(ui, "uia_lines")) else []
        rg = _regions(rect, web, hwnd)
        every = uia + (lines.get("ru") or []) + (lines.get("en") or [])
        return rg, every

    def find(rg, every, where):
        hits = [ln for ln in every if _SELF_CHAT.search(ln["text"].strip())
                and (_cx(ln) < rg["list_x"] if where == "list" else _cx(ln) > rg["chat_x"] and ln["y"] < rg["head_y"])]
        return min(hits, key=lambda x: x["y"]) if hits else None

    t0 = time.time()
    rg, every = look()
    if not find(rg, every, "head") and not find(rg, every, "list"):    # окно только что вывели вперёд — дорисовывается
        ui.sleep(0.8)
        rg, every = look()
    h = find(rg, every, "head")
    if h is not None:
        return {"ok": True, "hwnd": hwnd, "verified": True, "web": web, "name": h["text"]}
    ln = find(rg, every, "list")
    if ln is None and app == "telegram":
        ui.keys("esc")
        ui.sleep(0.2)
        ui.keys("esc")
        ui.sleep(0.25)
        ui.paste("Избранное")
        ui.sleep(1.2)
        rg, every = look()
        ln = find(rg, every, "list")
    if ln is None:
        sample = [x["text"] for x in every if _cx(x) < rg["list_x"]][:8]
        print(f"[мессенджеры] чат с собой не найден в {app}: элементов {len(every)}, граница {_DIV.get(hwnd)}, "
              f"слева: {sample} ({time.time() - t0:.1f} с)")
        return {"ok": False, "why": "notfound", "hwnd": hwnd}
    ui.click(int(_cx(ln)), int(_cy(ln)))
    ui.sleep(0.9)
    return {"ok": True, "hwnd": hwnd, "verified": True, "web": web, "name": ln["text"]}


def read_chat(hwnd: int, web: bool = False, ui=None, rect=None, lines=None) -> list:
    """Видимая переписка: [("me" | "them", текст)] сверху вниз (последние ~25)."""
    ui = ui or _get_ui()
    rect = rect or ui.rect(hwnd)
    if lines is None:
        lines = ui.ocr(rect)
    rg = _regions(rect, web, hwnd)
    ru, en = lines.get("ru") or [], lines.get("en") or []
    txt = " ".join(ln["text"] for ln in ru)
    letters = [c for c in txt if c.isalpha()]
    cyr = sum(1 for c in letters if "а" <= c.lower() <= "я") / max(1, len(letters))
    use = ru if cyr > 0.35 else (en or ru)
    left, right = rg["chat_x"], rect[2]
    mid = left + (right - left) * 0.48
    msgs = []
    for ln in sorted(use, key=lambda x: (x["y"], x["x"])):
        if _cx(ln) < left or ln["y"] < rg["head_y"] or ln["y"] > rg["foot_y"]:
            continue
        t = re.sub(r"\s*\b\d{1,2}:\d{2}\s*(?:[✓✔√]+|vv|w)?\s*$", "", ln["text"]).strip()
        if not t or re.fullmatch(r"(?:сегодня|вчера|today|yesterday|\d{1,2} \w+|[✓✔√ ]+)", t, re.I):
            continue
        pane = right - left
        who = "me" if ln["x"] > mid or (ln["x"] > left + pane * 0.25 and ln["x"] + ln["w"] > right - pane * 0.08) \
            else "them"
        if msgs and msgs[-1][0] == who:
            msgs[-1] = (who, msgs[-1][1] + " " + t)
        else:
            msgs.append((who, t))
    return msgs[-25:]


def _conv_text(conv: list, name: str) -> str:
    return "\n".join(("Я" if w == "me" else name) + ": " + t for w, t in conv) or "(переписки не видно)"


# --- текст ответа ------------------------------------------------------------------------------
_SHOP = re.compile(r"магазин\w*|продав\w*|поддержк\w*|support|shop|store|seller|бутик\w*|салон\w*|сервис\w*|"
                   r"доставк\w*|курьер\w*", re.I)


def is_shop(contact: str) -> bool:
    return bool(_SHOP.search(contact or ""))


def shop_contact(contact: str) -> str:
    """«магазину Mobi» → «Mobi»: в чатах магазин записан своим названием."""
    left = _SHOP.sub(" ", contact or "")
    left = re.sub(r"(?<![\w])(?:в|во|на|у|to|the)(?![\w])", " ", left, flags=re.I)
    return " ".join(left.split()) or contact


# --- грамотно и красиво ---------------------------------------------------------------------------
_RULES_RU = (
    "Write flawless, natural Russian, the way a literate person writes to someone close: "
    "обращение выделяй запятой («Мам, …», «Алим, привет!»); запятая перед «что», «чтобы», «но», «а», «потому что», "
    "между частями сложного предложения; вводные слова («кстати», «наверное», «кажется», «честно говоря») — "
    "запятыми; «-тся/-ться», «не» с глаголами раздельно, дефисы («как-нибудь», «всё-таки», «из-за»), ё где нужно; "
    "первая буква — заглавная; время и числа аккуратно («в 19:00», «через 10 минут»); тире — «—». "
    "Живой разговорный язык, без канцелярита («сообщаю», «в связи с», «данный»), без шаблонов нейросети "
    "(«Надеюсь, у тебя всё хорошо!», «Буду рад помочь»), без лишней вежливости и пафоса. "
    "Короткие ясные фразы, в каждой — одна мысль. В конце короткого бытового сообщения точку можно не ставить; "
    "после смайлика точку не ставь.")
_RULES_EN = (
    "Write flawless, natural English: correct spelling, grammar and punctuation, a capital first letter, "
    "contractions where natural, no corporate phrases or AI clichés, short clear sentences.")
_EXAMPLES_RU = (
    "Examples of the level expected (user's note → message):\n"
    "«скажи маме что опоздаю минут на 10 автобус сломался» → «Мам, опоздаю минут на 10 — автобус сломался»\n"
    "«спроси алима во сколько завтра тренировка» → «Алим, привет! Во сколько завтра тренировка?»\n"
    "«напиши дане что не смогу сегодня но могу в субботу» → «Дана, сегодня не получится, но в субботу могу. Удобно?»")


def _tidy(text: str, ru: bool) -> str:
    """Типографика без модели: пробелы, кавычки, тире, многоточие, заглавная буква."""
    t = " ".join((text or "").split())
    t = re.sub(r"\s+([,.!?;:…])", r"\1", t)                         # «привет ,» → «привет,»
    t = re.sub(r"([,!?;])(?=[A-Za-zА-Яа-яЁё])", r"\1 ", t)          # «привет,как» → «привет, как»
    t = re.sub(r"(?<![.])\.\.\.(?![.])", "…", t)
    if ru:
        t = re.sub(r"\s[-–]\s", " — ", t)
        t = re.sub(r'"([^"]+)"', r"«\1»", t)
    t = re.sub(r"([!?])\1{2,}", r"\1", t)                           # «!!!» → «!»
    m = re.search(r"[A-Za-zА-Яа-яЁё]", t)
    if m and m.start() <= 2:
        t = t[:m.start()] + t[m.start()].upper() + t[m.start() + 1:]
    return t


def _polish(text: str, ru: bool, ask) -> str:
    """Второй проход — корректор: только ошибки и пунктуация; слова, смысл и тон не трогает."""
    if not text or len(text) < 3:
        return text
    system = ("You are a meticulous proofreader. Fix ONLY spelling, grammar, punctuation and capitalization of this "
              f"chat message in {'Russian' if ru else 'its language'}. Keep every word, the meaning, the tone, slang, "
              "emoji and length. Do not add or remove ideas, greetings or a final period. "
              + (_RULES_RU if ru else _RULES_EN) + " Output only the corrected message.")
    try:
        out = (ask(system, text, max_tokens=300) or "").strip().strip("«»\"' \n")
    except Exception as e:
        print(f"[мессенджеры] корректор: {e}")
        return text
    import difflib
    if not out or difflib.SequenceMatcher(None, text.lower(), out.lower()).ratio() < 0.72 or \
            not (0.7 <= len(out) / max(1, len(text)) <= 1.35):
        return text                                                  # корректор «переписал» — не доверяем
    return out


def draft(gist: str, conv: list, name: str, app: str, ru: bool, ask=None, change: str = "", before: str = "",
          shop: bool = False) -> str:
    """Черновик: смысл пишет модель, затем корректор (в живой работе) и типографика."""
    out = _draft_raw(gist, conv, name, app, ru, ask, change, before, shop)
    if ask is None and out:
        from core import quick_llm
        out = _polish(out, ru, quick_llm.ask)
    return _tidy(out, ru)


def _draft_raw(gist: str, conv: list, name: str, app: str, ru: bool, ask=None, change: str = "", before: str = "",
          shop: bool = False) -> str:
    from core import quick_llm
    q = ask or quick_llm.ask
    if shop:
        system = (
            f"You write ONE message from the user to a shop / business «{name}» in {APPS[app]['name']}. Polite, "
            "literate, flawless spelling and punctuation, friendly-formal (на «вы» in Russian): a greeting "
            "(«Здравствуйте!»), the request stated clearly and concretely (product, size, colour, quantity, dates — "
            "only what the user gave), a short thank-you at the end. 1–4 sentences. Do not invent details, prices or "
            f"promises. Language: {'Russian' if ru else 'English'} unless the shop's chat is in another language. "
            "Output only the message.")
        user = (f"Chat on screen (OCR):\n{_conv_text(conv, name)}\n\nWhat the user wants to ask or say: {gist}")
        if change and before:
            user += f"\n\nPrevious draft: {before}\nChange it: {change}"
        return q(system, user, max_tokens=300).strip().strip("«»\"' \n")
    system = (
        f"You write ONE chat message that the user will send as themselves to «{name}» in {APPS[app]['name']}. "
        "First person, as the user. Language: the language of this conversation (if there is none — "
        f"{'Russian' if ru else 'English'}). Match the user's own manner from their messages (lines starting "
        "with 'Я:'): length, emoji or not, slang, warmth, ты/вы. Casual chat — casual text. "
        "Say exactly what the user wants and nothing more: do not invent facts, times, promises or excuses. "
        "No greeting unless it fits, no signature, no quotes around the text, no explanations. "
        "Never copy the user's typos, missing commas or lowercase — the message must be literate and read well. "
        + (_RULES_RU + " " + _EXAMPLES_RU if ru else _RULES_EN) + " Output only the message.")
    user = (f"Conversation on screen (OCR, may have small errors):\n{_conv_text(conv, name)}\n\n"
            f"What the user wants to say: {gist or 'a fitting reply to their last message'}")
    if change and before:
        user += f"\n\nPrevious draft: {before}\nChange it: {change}"
    out = q(system, user, max_tokens=300).strip()
    return out.strip("«»\"' \n")


def summarize(conv: list, name: str, ru: bool, ask=None) -> str:
    theirs = [t for w, t in conv if w == "them"]
    if not theirs:
        return (f"Новых сообщений от {name} на экране не вижу." if ru else f"I don't see messages from {name}.")
    from core import quick_llm
    q = ask or quick_llm.ask
    system = (f"Tell the user briefly what «{name}» wrote in this chat (the last messages matter most). "
              f"1–3 sentences in {'Russian' if ru else 'English'}. Quote short messages as they are. "
              "Lines starting with 'Я:' are the user's own. No filler.")
    try:
        return q(system, _conv_text(conv[-15:], name), max_tokens=250).strip()
    except Exception:
        return (f"{name}: " if ru else f"{name}: ") + " / ".join(theirs[-3:])


# --- фразы ----------------------------------------------------------------------------------------
_SEND = re.compile(r"^(?:атлас[,\s]+|atlas[,\s]+)?(?:пожалуйста\s+)?(ответь|напиши|отправь|скинь|передай|скажи|пожелай|поздравь|"
                   r"спроси|попроси|сообщи|предупреди|reply|text|message|write|tell|send|ask)\b\s*(.*)$", re.I)
_SEP = re.compile(r"\s*(?:[,:;—–]|\s-\s|\bчто\b|\bчтобы\b|«|\"|\bпро\b|\bнасчет\b|\bнасчёт\b|\bо том\b|\bthat\b|\bsaying\b|"
                  r"\basking\b|\bабout\b)\s*", re.I)
_CUR = re.compile(r"^(?:ему|ей|им|на (?:это|него|нее|неё|сообщение|последнее\w*)|в (?:этом|этот|текущ\w+) чат\w*|"
                  r"сюда|тут|здесь|him|her|them|to this|here)\b", re.I)
_READ = re.compile(r"(?:что|чего)\s+(?:мне\s+)?(?:написал[аио]?|пишет|пишут|ответил[аи]?|прислал[аи]?)\s+(.+)|"
                   r"(?:прочитай|прочти|покажи|read)\s+(?:мне\s+)?(?:последн\w+\s+)?(?:сообщени\w+|переписк\w+|чат|messages?)"
                   r"(?:\s+(?:от|с|from|with))?\s*(.*)|what did (.+?) (?:write|send|say)", re.I)
_THINGS = re.compile(r"^(?:письм\w*|письмецо|заметк\w*|сообщени\w*|текст\w*|пост\w*|коммент\w*|имейл\w*|емейл\w*|"
                     r"мейл\w*|эссе|сочинени\w*|файл\w*|код\b|note|letter|email|mail|post|comment|essay|file|code)\b", re.I)
_KIN = re.compile(   # точные формы слов-родства: раньше «баб\w*» и «мам\w*» ловили имена Бабур, Мамат, Мамед (живая проверка 22.10)
    r"^(?:мам(?:а|ы|е|у|ой|очк\w*|ул\w*|уль\w*|к[аиеу]|кой|еньк\w*|ик)?|пап(?:а|ы|е|у|ой|очк\w*|ул\w*|к[аиеу]|кой|еньк\w*|ик)?|"
    r"бат(?:я|ю|е|ей)|отц\w*|отец|брат(?:а|у|ом|е|ик\w*|ишк\w*|ан\w*|ец|цу|ца|ья|ьям)?|сестр\w*|сест[реу]\w*|"
    r"жен(?:а|ы|е|у|ой|ушк\w*)|муж(?:а|у|ем|е|ик|енёк|енек)?|сын(?:а|у|ом|е|ок|ку|ка|ишк\w*|уль\w*)?|"
    r"доч(?:ь|ери|ерью|к\w*|еньк\w*|урк\w*)|дяд(?:я|и|е|ю|ей|ечк\w*|ьк\w*)|т[её]т(?:я|и|е|ю|ей|ушк\w*|еньк\w*)|"
    r"баб(?:а|ы|е|у|ой|ушк\w*|ул\w*|уль\w*|уленьк\w*)|дед(?:а|у|ом|е|ушк\w*|ул\w*|уль\w*)?|"
    r"друг(?:а|у|ом|е|ан\w*)?|подруг\w*|любим\w*|девушк\w*|парн\w*|парень|"
    r"mom\w*|mum\w*|dad\w*|bro\b|sis\b|wife|husband|son|daughter|friend)$", re.I)
_MSG_ONLY = re.compile(r"^(?:в\s+|во\s+|на\s+|через\s+|in\s+|on\s+|via\s+)?(?:телеграм\w*|телег\w*|телег[еу]|ватсап\w*|"
                       r"вотсап\w*|whatsapp|инстаграм\w*|инст[ае]?\w*|instagram|дискорд\w*|discord)\s*\.?$", re.I)
_INC = re.compile(r"^(?:атлас[,\s]+|atlas[,\s]+)?(?:скажи|ответь|напиши|передай|reply|tell)(?:\s+(?:ей|ему|им|her|him|them))?"
                  r"\s*(?:[,:]\s*|\s+(?=что|чтобы|that\b|«|\"))(?:что\s+|чтобы\s+|that\s+)?(.+)$", re.I)
_TEACH = re.compile(r"^(?:атлас[,\s]+|atlas[,\s]+)?(?:запомни,?\s+)?(?:что\s+)?(.+?)\s+(?:у меня\s+)?(?:в\s+(\S+)\s+)?"
                    r"(?:записан[аоы]?|сохранен[аоы]?|сохранён[аоы]?|подписан[аоы]?)\s+(?:у меня\s+)?(?:в\s+(\S+)\s+)?"
                    r"(?:как|под именем)\s+[«\"“]?(.+?)[»\"”]?\s*[.!]?$", re.I)
_ORD = {"перв": 0, "1": 0, "втор": 1, "2": 1, "трет": 2, "3": 2, "четв": 3, "4": 3}
_OPEN = re.compile(r"^(?:атлас[,\s]+|atlas[,\s]+)?(?:открой|покажи|open|show)\s+(?:мне\s+)?"
                   r"(чат|переписк\w*|диалог\w*|фото\w*|фотк\w*|картинк\w*|видео\w*|голосов\w*|кружоч\w*|файл\w*|"
                   r"сообщени\w*|chat|photo|picture|video)\W*"
                   r"(?:(?:что|которое|которую|которые|который|от|с|со|with|from)\s+)?"
                   r"(?:(?:мне\s+)?(?:прислал|скинул|отправил|написал|sent)\w*\s+)?(.+)$", re.I)
_HERE = re.compile(r"^(?:я\s+)?(?:открыл\w*|открыт\w*|чат открыт\w*|он открыт|она открыт\w*|открыто|"
                   r"здесь|тут|сюда|в этот|в этом|в открытый|в открыт\w+|вот (?:он|она|этот|эта|тут)|готово|"
                   r"нашёл|нашел|нашла|i opened|opened|it'?s open|here|done)\b", re.I)
_YES = re.compile(r"^(?:да|давай|отправь|отправляй|шли|жми|го|ок|окей|ага|можно|верно|yes|yeah|yep|send|ok|okay|go)\b", re.I)
_NO = re.compile(r"^(?:нет|не надо|не отправляй|отмена|отмени|стоп|no|nope|cancel|don'?t)\b", re.I)
_EDIT = re.compile(r"короче|длиннее|подробнее|по-другому|по другому|иначе|вежлив|формальн|неформальн|проще|"
                   r"добавь|убери|измени|перепиши|без смайл|со смайл|смайлик|замени|вместо|теплее|мягче|строже|"
                   r"shorter|longer|rewrite|change|add|remove|polite|casual|emoji", re.I)


NEED_CHAT_TTL = 90         # «как записан контакт?» ждём не дольше полутора минут


def _answer_name(t: str):
    """Ответ на «Как контакт записан?» → имя чата или None, если это другая команда.
    «Мама» · «вы» · «он записан как Ана» · «записан в «вы»» · «не себе, а вы» · ««Кундуз»»."""
    t = " ".join(t.split()).strip(" .!")
    t = re.sub(r"^(?:атлас|atlas)[,\s]+", "", t, flags=re.I)
    q = re.findall(r"[«\"“]([^»\"”]{1,40})[»\"”]", t)
    m = re.search(r"(?:записан\w*|сохранен\w*|сохранён\w*|подписан\w*|называ\w+|saved as)[\s.,:;—–-]+(?:у меня\s+)?"
                  r"(?:как|в|под именем|as)?\s*[«\"“]?([^»\"”,.]{1,40})", t, re.I)
    if q:
        return q[-1].strip()
    if m:
        return m.group(1).strip()
    m = re.search(r"\bне\s+\S+[,\s]+а\s+([^,.]{1,30})", t, re.I)       # «не себе, а вы»
    if m:
        return m.group(1).strip()
    words = t.split()
    if not words or len(words) > 3 or any(re.search(r"\d{3}", w) for w in words):
        return None
    if _SEND.search(t) or _READ.search(t) or re.search(r"(?:^|\s)(?:погод\w*|выведи|покажи|открой|включи|"
                                                       r"поставь|сделай|выключи|найди|что|как|где|когда)\b", t, re.I):
        return None
    if all(w[:1].isupper() or _KIN.match(norm(w)) or norm(w) in ("вы", "себе", "я") or norm(w) in _POSS
           for w in words if norm(w)):
        return t
    return None


def _same(last: str, said: str) -> bool:
    """Последний ответ в чате — наш вопрос «Отправить?» (смайлики и разметка могли сняться при показе)
    или его пересказ («Надо подтвердить отправку…» — когда черновик готовил мозг)."""
    a, b = norm(last), norm(said)
    if bool(b) and (a == b or b[:40] in a or a[:40] in b):
        return True
    return bool(a) and bool(re.search(r"отправ|подтверд|\bsend\b|confirm", a)) and bool(re.search(r"\?|да|yes", last or ""))


_SELF_WORDS = re.compile(r"^(?:мне|себе|me|myself)(?:\s+(?:самому|самой|сам|же|лично))?$|^(?:самому|самой)\s+себе$|^вы$|"
                         r"^избранное$|^saved messages$", re.I)
_SELF_CHAT = re.compile(r"\((?:вы|you)\)|^(?:вы|you)$|^избранное$|^saved messages$|^заметки для себя$|"
                        r"^message yourself$|^написать себе$", re.I)


def is_self(contact: str) -> bool:
    """«мне самому», «себе», «вы» (так WhatsApp подписывает чат с собой) — чат с самим собой."""
    return bool(_SELF_WORDS.match(" ".join(norm(contact).split())))


def _app_in(text: str):
    for k, rx in _APP_RX:
        m = rx.search(text)
        if m:
            return k, m
    return None, None


_OPEN_THEN = re.compile(r"^(?:атлас[,\s]+|atlas[,\s]+)?(?:открой|зайди\s+в|запусти|open)\s+(\S+)\s*"
                        r"(?:,\s*(?:и\s+)?|\s+и\s+|\s+а\s+потом\s+|\s+and\s+)"
                        r"((?:напиши|ответь|скажи|отправь|передай|write|text|tell|send)\b.*)$", re.I)


_SERVICES = re.compile(r"ютуб\w*|youtube|spotify|спотиф\w*|гугл\w*|google|браузер\w*|сайт\w*|кинопоиск\w*|netflix|"
                       r"нетфликс\w*|твич\w*|twitch|вконтакте|\bвк\b|tiktok|тикток\w*|рутуб\w*|rutube", re.I)


def intent(text: str, last: str = None):
    t = " ".join((text or "").split()).strip()
    if not t:
        return None
    try:
        from core import contacts                         # «запомни номер Назара 0707…», «обнови контакты»
        if contacts.intent(t):
            return ("contacts",)
    except Exception:
        pass
    # «мне самому напиши привет» → «напиши мне самому привет» (лог 24.10: уходило в модель и печатало наугад)
    t = re.sub(r"^((?:(?:атлас|atlas)[,!\s]+)?)(мне|себе)(\s+(?:самому|самой|сам|же|лично))?\s+"
               r"(напиши|отправь|скинь|перешли)\b", lambda m: f"{m.group(1)}{m.group(4)} {m.group(2)}{m.group(3) or ''}",
               t, flags=re.I)
    t2 = re.sub(r"^(?:(?:атлас|atlas)[,!\s]+)?(?:(?:тогда|ну|давай|а|и|так|теперь|ещё|еще|пожалуйста|слушай|"
                r"можешь|ты можешь|можешь ли)[,\s]+)+", "", t, flags=re.I)
    if t2 != t and (_SEND.search(t2) or _OPEN_THEN.match(t2)):  # «Атлас, тогда напиши Назару» — та же команда
        t = t2
    m = _OPEN_THEN.match(t)                     # «открой WhatsApp и напиши маме…» → «напиши маме… в WhatsApp»
    if m and _app_in(m.group(1))[0]:
        t = f"{m.group(2).rstrip(' .!')} в {m.group(1)}"
    p = pending()
    if p and p.get("need_app") and (last is None or _same(last, _s.get("said"))) and len(t) < 40:
        t = re.sub(r"^(?:я\s+)?(?:имею\s+в\s*виду|имел\s+в\s*виду|в смысле|то есть|конечно|ну)[,\s]+", "", t, flags=re.I)
        if _MSG_ONLY.match(t):                          # «в телеграме» / «ватсап» — выбор мессенджера
            ak, _ = _app_in(t)
            if ak:
                return ("pick_app", ak)
        if _NO.search(t.strip(" .!").lower()):
            return ("cancel",)
    if p and p.get("need_chat") and time.time() - p["created"] < NEED_CHAT_TTL \
            and (last is None or _same(last, _s.get("said"))) and len(t) < 90:
        bare = t.strip(" .!").lower()
        if _NO.search(bare):
            return ("cancel",)
        h = _HERE.search(bare)
        if h:                                                       # «чат открыт, пиши тест 1» — пишем в открытый чат
            m2 = re.search(r"(?:^|[\s.,!])(?:пиши|напиши|пишите|отправь|отправляй|скажи|write|send)\b[\s,:]*(.*)$", t, re.I)
            more = (m2.group(1) if m2 else "").strip(" «»\"“”.!")
            return ("here", more)
        if p.get("here_only"):                                     # два раза не нашли — ждём только «открыл» / «отмена»
            opts = []
        else:
            opts = p.get("options") or []
        name = None if p.get("here_only") else _answer_name(t)
        if name and not opts:
            return ("pick_chat", name)
        m = re.search(r"\b(перв|втор|трет|четв|[1-4])\w*", bare)
        if m and _ORD[m.group(1)] < len(opts):
            return ("pick_chat", opts[_ORD[m.group(1)]])
        if len(opts) == 1 and re.match(r"^(?:да|он|она|этот|эта|это|верно|yes|that one|it is)\b", bare):
            return ("pick_chat", opts[0])
        st2 = stems(t)
        hit = max(opts, key=lambda o: match_score(o, st2)) if opts and st2 else None
        if hit and match_score(hit, st2) >= 1:
            return ("pick_chat", hit)
        if name:
            return ("pick_chat", name)
        # не похоже на имя чата («погода», «напиши Назару», «выведи голограмму») — не наше: пусть идёт дальше
        # (лог 23.10: ожидание имени глотало все фразы подряд и каждый раз искало чат «Погода»)
    if p and not p.get("need_app") and not p.get("need_chat") and (last is None or _same(last, _s.get("said"))) and len(t) < 120:
        bare = t.strip(" .!").lower()
        if _NO.search(bare):
            return ("cancel",)
        if _YES.search(bare) and len(bare) < 30:
            return ("send",)
        if _EDIT.search(bare):
            return ("edit", t)
    try:
        from core import msg_watch                        # «сообщай о сообщениях от Алима», «что я пропустил?»
        if msg_watch.intent(t):
            return ("watch",)
    except Exception:
        pass
    inc = _s.get("incoming")
    if inc and time.time() - inc["ts"] < 180 and not p:   # только что объявили сообщение — «скажи, что куплю»
        m = _INC.match(t)
        if m and not _app_in(t)[0]:
            body = m.group(1).strip()
            q = re.fullmatch(r"[«\"“]([^»\"”]+)[»\"”]", body)
            colon = re.match(r"^(?:атлас[,\s]+|atlas[,\s]+)?\w+(?:\s+(?:ей|ему|им|her|him|them))?\s*:", t, re.I)
            exact = q.group(1) if q else (body if colon else None)
            return ("reply_incoming", "" if exact else body, exact)
    m = _OPEN.match(t.strip(" .!?"))
    if m and _SERVICES.search(t) and not _app_in(t)[0]:   # «открой видео с Витей на YouTube» — это не чат (лог 24.10)
        m = None
    if m:
        who = (m.group(2) or "").strip(" .,!?«»")
        ak, am2 = _app_in(who)
        if am2:
            who = (who[:am2.start()] + who[am2.end():]).strip()
            who = re.sub(r"\s+(?:в|во|на)$", "", who).strip()
        ak = ak or _app_in(t)[0]
        first = (who.split() or [""])[0]
        if who and stems(who) and len(who.split()) <= 3 and (_KIN.match(first) or first[:1].isupper() or known_app(who)):
            return ("open", ak, who, (m.group(1) or "").lower())
    m = _TEACH.match(t)
    if m:
        who, title = m.group(1).strip(" ,"), m.group(4).strip()
        ak = None
        for g in (m.group(2), m.group(3)):
            if g:
                ak = _app_in(g)[0] or ak
        if stems(who) and title:
            return ("teach", ak, who, title)
    app, am = _app_in(t)
    m = _READ.search(t)
    if m:
        who = next((g for g in m.groups() if g), "") or ""
        who = (_APP_RX[[k for k, _ in _APP_RX].index(app)][1].sub(" ", who) if app else who).strip(" ?.!")
        who = re.sub(r"^(?:от|с|from|with)\s+", "", who, flags=re.I).strip()
        if who and re.fullmatch(r"(?:мне|нового|новенького|там|в чатах|in chats|me)", who, re.I):
            who = ""
        if app or (who and known_app(who)):
            return ("read", app or known_app(who), who)
    m = _SEND.search(t)
    if not m:
        return None
    verb, rest = m.group(1).lower(), m.group(2)
    if app:
        rest = _APP_RX[[k for k, _ in _APP_RX].index(app)][1].sub(" ", rest, count=1)
    rest = " ".join(rest.split())
    quote = re.search(r"[«\"“]([^»\"”]+)[»\"”]", rest)
    if quote:
        rest = " ".join((rest[:quote.start()] + " " + rest[quote.end():]).split()).strip(" ,:")
    rest = re.sub(r"^(?:контакт[уа]?|человеку|пользователю|абоненту|номеру)(?:\s+|$)", "", rest, flags=re.I).strip()   # «напиши контакту X»
    qtext = quote.group(1).strip() if quote else None
    if not rest and qtext and len(qtext.split()) >= 2 and qtext[:1].isupper():
        # «напиши контакту «Обэй привет»» — распознавание взяло в кавычки и имя, и текст
        rest, qtext = qtext.split(" ", 1)
    cur = _CUR.match(rest)
    if cur:
        contact, gist = None, rest[cur.end():].strip(" ,:")
        gist = re.sub(r"^(?:что|чтобы|that)\s+", "", gist, flags=re.I)
    else:
        rest2 = re.sub(r"^(?:в|во|to)\s+(?=чат|групп|бесед|chat|group)", "", rest, flags=re.I)
        rest2 = re.sub(r"^(?:у|в|во)\s+(?=магазин|продав|поддержк|салон|сервис|бутик)", "", rest2, flags=re.I)
        sp = _SEP.search(rest2)
        if sp and sp.start() > 0:
            contact, gist = rest2[:sp.start()], rest2[sp.end():]
            if len(contact.split()) > 3:
                contact, gist = contact.split()[0], " ".join(contact.split()[1:]) + " " + gist
        else:
            words = rest2.split()
            if not words:
                return None
            n = 2 if len(words) > 1 and norm(words[0]) in _POSS else 1     # «моей маме привет» — контакт из двух слов
            contact, gist = " ".join(words[:n]), " ".join(words[n:])
            last_w = words[-1].strip(" .,!?")
            if len(words) >= 2 and not words[0][:1].isupper() and not _KIN.match(norm(words[0])) \
                    and not _THINGS.match(words[0]) \
                    and (last_w[:1].isupper() or _KIN.match(norm(last_w))):
                contact, gist = last_w, " ".join(words[:-1])          # «напиши привет Назару» — адресат в конце
        contact = contact.strip(" ,.")
        gist = re.sub(r"^(?:что|чтобы|that)\s+", "", gist.strip(" ,:"), flags=re.I)
    exact = None
    if qtext:
        exact = qtext
    elif re.search(r":\s*\S", rest) and not cur:
        exact = rest.split(":", 1)[1].strip()
    if exact and not quote and contact and is_shop(contact):
        gist, exact = exact, None                          # магазину — вежливо и грамотно, не дословно
    if contact and is_shop(contact):
        m2 = re.match(r"^(?:магазин\w*|продав\w*|поддержк\w*|салон\w*|сервис\w*|бутик\w*)\s+(\S+)\s+(.+)$",
                      (contact + " " + gist).strip(), re.I)
        if m2 and len(contact.split()) == 1:
            contact, gist = contact + " " + m2.group(1), m2.group(2)
    if verb in ("спроси", "попроси", "пожелай", "поздравь", "предупреди", "ask") and not exact:
        gist = f"{verb} {gist}".strip()
    if contact is not None and re.fullmatch(r"мне|себе", contact, re.I):   # «напиши мне самому привет»
        gist = re.sub(r"^(?:самому|самой|сам|же|лично)\s+(?:себе\s+)?", "", gist.strip(), flags=re.I)
        return ("reply", app or known_app("себе"), "себе", gist.strip(" .,"), exact)
    if not app:
        if contact is None:
            return ("reply_here", None, gist, exact)
        app = known_app(contact)
        if not app:
            first = ([w for w in contact.split() if norm(w) not in _POSS] or [""])[0]
            person = bool(is_kin(contact) or (first[:1].isupper() and re.search(r"[A-Za-zА-Яа-яЁё]", first)))
            if not person or _THINGS.match(contact) or not stems(contact):
                return None                            # «напиши письмо», «скажи что-нибудь» — не мессенджер
            return ("reply", None, contact, gist.strip(" .,"), exact)   # «напиши маме/Алиму» — мессенджер выберем сами
    if contact is not None and not stems(contact):
        return None
    return ("reply", app, contact, gist.strip(" .,"), exact)


# --- сценарии ----------------------------------------------------------------------------------------
def pending():
    p = _s["pending"]
    if p and time.time() - p["created"] < PENDING_TTL:
        return p
    return None


def _ask_line(p: dict, ru: bool) -> str:
    app = APPS[p["app"]]["name"]
    if ru:
        head = f"Пишу в {app}, чат «{p['chat']}»" if p.get("verified") else \
            f"Открыл чат в {app}, но не уверен, что это {p['contact'] or 'нужный чат'} — глянь на экран. Текст"
        return f"{head}: «{p['text']}». Отправить?"
    head = f"{app}, chat “{p['chat']}”" if p.get("verified") else \
        f"Opened {app}, but not sure it's {p['contact'] or 'the right chat'} — check the screen. Text"
    return f"{head}: “{p['text']}”. Send?"


def _no_chat(r: dict, app: str, contact: str, ru: bool) -> str:
    name = APPS[app]["name"]
    opts = r.get("options") or []
    q = ", ".join(f"«{o}»" for o in opts)
    if r.get("why") == "ambiguous":
        return (f"В {name} несколько подходящих чатов: {q}. Кому писать? Скажи, как записан контакт, или «первому», «второму»." if ru else
                f"Several {name} chats match: {q}. Which one? Say the name as saved, or “the first”, “the second”.")
    if r.get("why") == "weak":
        who = _nominal(contact) if ru else contact
        return (f"Вижу только {q} — похоже, это не {who.lower() if ru else who}. Как контакт записан у тебя? "
                f"Если это он, скажи «да, этот»." if ru else
                f"I only see {q} — that doesn't look like {who}. How is the contact saved? If it is them, say “yes, that one”.")
    if r.get("why") == "noapp":
        return f"Не получилось открыть {name}." if ru else f"Couldn't open {name}."
    if r.get("why") == "notinlist":
        return (f"Не вижу чат «{_nominal(contact)}» в списке {name}. Открой его сам и скажи «открыл»." if ru else
                f"I don't see “{contact}” in the {name} list. Open the chat and say “reply to him that…”.")
    return (f"Не нашёл чат «{_nominal(contact)}» в {name}. Скажи, как контакт записан, или открой чат сам и скажи «открыл»." if ru
            else f"Couldn't find “{contact}” in {name}. Say the name as saved, or open the chat and say “reply here”.")


_NOMINAL = {"маме": "Мама", "мамы": "Мама", "маму": "Мама", "мам": "Мама", "папе": "Папа", "папы": "Папа",
            "папу": "Папа", "бате": "Батя", "брату": "Брат", "брата": "Брат", "сестре": "Сестра", "сестры": "Сестра",
            "сестру": "Сестра", "жене": "Жена", "жены": "Жена", "жену": "Жена", "мужу": "Муж", "сыну": "Сын",
            "дочери": "Дочь", "дочке": "Дочка", "бабушке": "Бабушка", "дедушке": "Дедушка", "другу": "Друг",
            "подруге": "Подруга", "подругу": "Подруга"}


def _nominal(name: str) -> str:
    """«маме» → «Мама» только для показа/озвучки; на поиск в мессенджере не влияет (там stems)."""
    return _NOMINAL.get((name or "").strip().lower(), name)


def _default_app():
    d = (os.getenv("ATLAS_DEFAULT_MESSENGER") or "").lower().strip()
    if not d:
        return None
    return next((k for k in APPS if k == d or re.search(APPS[k]["rx"], d, re.I)), None)


def _resolve_app(contact, ui):
    """«Напиши маме» без названия мессенджера: вспомнить / открытый / единственный открытый / по умолчанию."""
    if contact and known_app(contact):
        return known_app(contact)
    wins = ui.windows()
    found = current_messenger(wins)                                   # мессенджер уже на переднем плане
    if found:
        return found[0]
    open_apps = [k for k in APPS if find_window(k, wins)]
    if len(open_apps) == 1:                                           # открыт ровно один — он и есть
        return open_apps[0]
    return _default_app()


def prepare(app, contact, gist: str, exact: str = None, ru: bool = True, ui=None, ask=None, title: str = None,
            here: str = None) -> str:
    """Открыть чат, прочитать, написать черновик — и спросить «Отправить?».
    here="whatsapp": ты сам открыл чат — пишем в открытый чат этого мессенджера (контакт — для запоминания)."""
    ui = ui or _get_ui()
    if here:
        return _prepare_here(here, contact, gist, exact, ru, ui, ask)
    if app is None and contact is not None:                           # «напиши маме» — выбрать мессенджер сами
        app = _resolve_app(contact, ui)
        if app is None:
            _s["pending"] = {"need_app": True, "contact": contact, "gist": gist, "exact": exact,
                             "created": time.time(), "ru": ru}
            q = (f"В каком мессенджере написать {contact}? Telegram, WhatsApp, Instagram или Discord?" if ru else
                 f"Which messenger should I use for {contact}? Telegram, WhatsApp, Instagram or Discord?")
            _s["said"] = q
            return q
    if app is None:                                                   # «ответь ему» — в открытом чате
        found = current_messenger(ui.windows())
        if not found:
            return ("Открой чат в мессенджере и скажи ещё раз — или назови, кому и где." if ru else
                    "Open the chat first, or say who and where.")
        app, w = found
        rect = ui.rect(w["hwnd"])
        lines = ui.ocr(rect)
        rg = _regions(rect, w.get("web", False), w["hwnd"])
        head = _header(lines.get("ru") or lines.get("en") or [], rg)
        r = {"ok": True, "hwnd": w["hwnd"], "verified": True, "web": w.get("web", False),
             "name": head[0]["text"][:40] if head else ("собеседник" if ru else "them")}
        conv = read_chat(w["hwnd"], r["web"], ui, rect, lines)
    else:
        r = open_chat(app, shop_contact(contact) if is_shop(contact) else contact, ui, **({"title": title} if title else {}))
        if not r.get("ok"):
            if r.get("why") in ("ambiguous", "weak", "notfound", "notinlist"):   # ждём: «первому», «Мама», «чат открыт»
                _s["pending"] = {"need_chat": True, "app": app, "contact": contact, "gist": gist, "exact": exact,
                                 "options": r.get("options") or [], "created": time.time(), "ru": ru}
            said = _no_chat(r, app, contact, ru)
            _s["said"] = said
            return said
        conv = read_chat(r["hwnd"], r.get("web", False), ui)
        _remember(contact, app)
    chat = re.sub(r"\s+(?:онлайн|online|был\w*|печатает|typing|last seen).*$", "", r["name"], flags=re.I).strip()
    name = _nominal(chat)                                    # «маме» → «Мама» для ответа вслух (чат не распознан)
    try:
        text = _exact_clean(exact, ru, ask) if exact else \
            draft(gist, conv, name, app, ru, ask, shop=bool(contact and is_shop(contact)))
    except Exception as e:
        return (f"Чат открыл, но текст не получился: {e}" if ru else f"Opened the chat, but couldn't write: {e}")[:220]
    if not text:
        return "Не получилось написать ответ." if ru else "Couldn't write a reply."
    p = {"app": app, "hwnd": r["hwnd"], "contact": contact, "name": name, "chat": chat, "text": text, "verified": r["verified"],
         "created": time.time(), "gist": gist, "conv": conv, "ru": ru, "web": r.get("web", False),
         "shop": bool(contact and is_shop(contact))}
    _s["pending"] = p
    said = _ask_line(p, ru)
    _s["said"] = said
    return said


def _exact_clean(text: str, ru: bool, ask=None) -> str:
    """Продиктованный дословно текст: слова те же — только запятые, буквы и опечатки распознавания речи."""
    if ask is None:
        from core import quick_llm
        text = _polish(text, ru, quick_llm.ask)
    return _tidy(text, ru)


def redraft(change: str, ask=None) -> str:
    p = pending()
    if not p:
        return "Нечего переписывать." if _ru(change) else "Nothing to rewrite."
    try:
        p["text"] = draft(p["gist"], p["conv"], p["name"], p["app"], p["ru"], ask, change=change, before=p["text"],
                          shop=p.get("shop", False))
    except Exception as e:
        return f"Не получилось переписать: {e}"[:200]
    p["created"] = time.time()
    said = _ask_line(p, p["ru"])
    _s["said"] = said
    return said


def _prepare_here(app: str, contact, gist: str, exact, ru: bool, ui, ask) -> str:
    wins = ui.windows()
    w = find_window(app, wins)
    if not w:
        return (f"Не вижу окно {APPS[app]['name']} — открой чат и скажи ещё раз." if ru else
                f"I don't see {APPS[app]['name']} — open the chat and say it again.")
    rect = ui.rect(w["hwnd"])
    lines = ui.ocr(rect)
    uia = ui.uia_lines(w["hwnd"]) if (not w.get("web") and hasattr(ui, "uia_lines")) else []
    rg = _regions(rect, w.get("web", False), w["hwnd"])
    st = stems(contact) if contact else []
    head = _header(uia, rg) + _header(_pick(lines, st) if st else (lines.get("ru") or lines.get("en") or []), rg)
    chat = ""
    for ln in head:                                                 # имя чата: похожее на «маму», иначе первая строка
        if st and match_score(ln["text"], st, is_kin(contact)) >= 1:
            chat = ln["text"]
            break
    if not chat and head:
        chat = head[0]["text"]
    chat = re.sub(r"\s+(?:онлайн|online|был\w*|печатает|typing|last seen).*$", "", chat[:40], flags=re.I).strip()
    conv = read_chat(w["hwnd"], w.get("web", False), ui, rect, lines)
    name = chat or (_nominal(contact) if contact else ("собеседник" if ru else "them"))
    try:
        text = _exact_clean(exact, ru, ask) if exact else draft(gist, conv, name, app, ru, ask)
    except Exception as e:
        return (f"Текст не получился: {e}" if ru else f"Couldn't write: {e}")[:220]
    if not text:
        return "Не получилось написать ответ." if ru else "Couldn't write a reply."
    p = {"app": app, "hwnd": w["hwnd"], "contact": contact, "name": name, "chat": chat or name, "text": text,
         "verified": True, "created": time.time(), "gist": gist, "conv": conv, "ru": ru, "web": w.get("web", False),
         "learn": bool(chat)}              # ты сам открыл и подтвердил — запомним, как «мама» записана (устареет — найдём заново)
    _s["pending"] = p
    said = (f"Пишу в {APPS[app]['name']}, в открытый чат{f' «{chat}»' if chat else ''}: «{text}». Отправить?" if ru else
            f"{APPS[app]['name']}, the open chat{f' “{chat}”' if chat else ''}: “{text}”. Send?")
    _s["said"] = said
    return said


def _hold(ui, hwnd, tries: int = 4) -> bool:
    """Окно мессенджера впереди? Нет — возвращаем, несколько раз: интерфейс Atlas после ответа может
    на миг забрать фокус (живой тест 28.10: дважды «окно ушло с экрана», хотя WhatsApp был открыт)."""
    for i in range(tries):
        if ui.foreground() == hwnd or (hasattr(ui, "same_process") and ui.same_process(hwnd)):
            return True
        ui.activate(hwnd)
        ui.sleep(0.25 + 0.15 * i)
    return ui.foreground() == hwnd or (hasattr(ui, "same_process") and ui.same_process(hwnd))


def _front(ui) -> str:
    return ui.who_is_front() if hasattr(ui, "who_is_front") else str(ui.foreground())


def send(ui=None) -> str:
    """Отправить черновик после «да»."""
    p = pending()
    if not p:
        return "Нечего отправлять — черновик устарел или уже отправлен."
    ui = ui or _get_ui()
    ru = p["ru"]
    _s["pending"] = None
    if not _hold(ui, p["hwnd"]):
        print(f"[мессенджеры] не отдаёт фокус: впереди {_front(ui)}")
        return (f"Не получилось переключиться на {APPS[p['app']]['name']} — не отправил." if ru else
                f"Couldn't switch to {APPS[p['app']]['name']} — not sent.")
    ui.sleep(0.25)
    if p.get("web") or not ui.focus_edit(p["hwnd"], r"message|сообщени|написать|введите|type"):
        rect = ui.rect(p["hwnd"])
        rg = _regions(rect, p.get("web", False), p["hwnd"])
        ui.click(int(rg["chat_x"] + (rect[2] - rg["chat_x"]) * 0.45), int(rect[3] - rg["H"] * 0.045))
        ui.sleep(0.2)
    if not _hold(ui, p["hwnd"]):
        print(f"[мессенджеры] перед вставкой впереди {_front(ui)} — не отправил")
        return "Окно мессенджера ушло с экрана — не отправил." if ru else "The messenger window lost focus — not sent."
    if p.get("verified") and p.get("chat") and p.get("learn", True):   # пока ты думал, в окне могли открыть другой чат
        rect = ui.rect(p["hwnd"])
        rg = _regions(rect, p.get("web", False), p["hwnd"])
        cst = stems(p["chat"])
        uia = ui.uia_lines(p["hwnd"]) if (not p.get("web") and hasattr(ui, "uia_lines")) else []
        rg = _regions(rect, p.get("web", False), p["hwnd"])
        want = norm(p["chat"])[:16]
        hu = _header(uia, rg)
        head = hu if hu else _header(_pick(ui.ocr(rect), cst), rg)
        if head and not any(norm(ln["text"]).startswith(want) or want.startswith(norm(ln["text"])[:16] or "—")
                            for ln in head if norm(ln["text"])):
            return (f"Сейчас открыт другой чат, не «{p['chat']}» — не отправил. Скажи ещё раз, кому написать." if ru else
                    f"A different chat is open now, not “{p['chat']}” — not sent.")
    ui.paste(p["text"])
    ui.sleep(0.25)
    if not _hold(ui, p["hwnd"], 2):                       # Enter не должен уйти в чужое окно
        print(f"[мессенджеры] перед Enter впереди {_front(ui)} — не нажал")
        return ("Текст вставил, но окно ушло с экрана — Enter не нажал. Проверь и нажми сам." if ru else
                "Pasted, but the window lost focus — didn't press Enter.")
    ui.keys("enter")
    ui.sleep(1.0)
    conv = read_chat(p["hwnd"], p.get("web", False), ui)
    head = norm(p["text"])[:14]
    ok = any(w == "me" and head and head[:10] in norm(t) for w, t in conv[-4:])
    if p.get("verified") and p.get("contact") and p.get("chat") and p.get("learn", True):
        _remember(p["contact"], p["app"], p["chat"])
    _s["last_sent"] = {"t": time.time(), "app": p["app"], "contact": p.get("contact") or "", "chat": p.get("chat") or "",
                       "text": p["text"]}         # ты подтвердил — значит, это и есть «мама» в этом мессенджере
    if ok:
        return "Отправил." if ru else "Sent."
    return ("Вставил и нажал Enter — проверь, что ушло." if ru else "Pasted and pressed Enter — check it went through.")


def read(app, who: str, ru: bool = True, ui=None, ask=None) -> str:
    ui = ui or _get_ui()
    r = open_chat(app, who, ui)
    if not r.get("ok"):
        return _no_chat(r, app, who, ru)
    _remember(who, app)
    conv = read_chat(r["hwnd"], r.get("web", False), ui)
    return summarize(conv, r["name"] if r.get("verified") else who, ru, ask)


def overview(app: str, ru: bool = True, ui=None, ask=None) -> str:
    """«Что мне пишут в телеграме?» — сначала по уведомлениям (новое), иначе по списку чатов."""
    try:
        from core import msg_watch
        fresh = msg_watch.recent_line(app, ru)
        if fresh:
            return fresh
    except Exception as e:
        print(f"[мессенджеры] уведомления: {e}")
    ui = ui or _get_ui()
    w = _ensure(app, ui)
    if not w:
        return f"Не получилось открыть {APPS[app]['name']}." if ru else f"Couldn't open {APPS[app]['name']}."
    rect = ui.rect(w["hwnd"])
    lines = ui.ocr(rect)
    rg = _regions(rect, w.get("web", False), w["hwnd"])
    use = (lines.get("ru") or []) if sum(len(ln["text"]) for ln in lines.get("ru") or []) else (lines.get("en") or [])
    items = [ln["text"] for ln in sorted(use, key=lambda x: (x["y"], x["x"]))
             if _cx(ln) < rg["list_x"] and ln["y"] > rg["top"]][:40]
    if not items:
        return "Не вижу список чатов." if ru else "I can't see the chat list."
    from core import quick_llm
    q = ask or quick_llm.ask
    system = ("This is OCR of a messenger's chat list (each chat: name, last message preview, time, maybe an unread "
              f"counter). Tell the user in {'Russian' if ru else 'English'}, in 1–4 short sentences, who wrote "
              "recently and what — unread chats first. Skip channels and ads unless nothing else. No filler.")
    try:
        said = q(system, "\n".join(items), max_tokens=300).strip()
    except Exception:
        said = "; ".join(items[:6])
    return (("Новых уведомлений не приходило. В списке чатов сверху: " if ru else
             "No new notifications. At the top of the chat list: ") + said)


def cancel() -> bool:
    had = _s["pending"] is not None
    _s["pending"] = None
    return had


def handle(text: str, last: str = None, ui=None, ask=None):
    it = intent(text, last)
    if not it:
        return None
    p = pending()
    ru = _ru(text) or bool(p and p["ru"] and not re.search(r"[a-z]", text, re.I))
    k = it[0]
    if k == "contacts":
        from core import contacts
        return contacts.handle(text)
    if k == "cancel":
        cancel()
        return "Не отправляю." if ru else "Not sending."
    if k == "send":
        return send(ui)
    if k == "edit":
        return redraft(it[1], ask)
    if k == "read":
        return read(it[1], it[2], ru, ui, ask) if it[2] else overview(it[1], ru, ui, ask)
    if k == "reply_here":
        if not current_messenger((ui or _get_ui()).windows()):
            return None                                # «ответь на это» не в мессенджере — не наше
        return prepare(None, None, it[2], it[3], ru, ui, ask)
    if k == "watch":
        from core import msg_watch
        return msg_watch.handle(text)
    if k == "reply_incoming":
        inc = _s.get("incoming") or {}
        _s["incoming"] = None
        return prepare(inc.get("app"), inc.get("title"), it[1], it[2], ru, ui, ask, title=inc.get("title"))
    if k == "teach":
        _remember(it[2], it[1], it[3])
        where = f" в {APPS[it[1]]['name']}" if it[1] else ""
        return (f"Запомнил: {_nominal(it[2]).lower()}{where} — это «{it[3]}»." if ru else
                f"Got it: {it[2]}{where} is saved as “{it[3]}”.")
    if k == "pick_chat":
        np = _s.get("pending") or {}
        _s["pending"] = None
        out = prepare(np.get("app"), np.get("contact"), np.get("gist", ""), np.get("exact"),
                      np.get("ru", ru), ui, ask, title=it[1])
        again = _s.get("pending")
        if again and again.get("need_chat"):                      # опять не нашли — второй раз не спрашиваем по кругу
            again["tries"] = np.get("tries", 0) + 1
            if again["tries"] >= 2:
                again["here_only"] = True
                out = (f"Не нашёл «{it[1]}» в {APPS[np.get('app') or 'telegram']['name']}. Открой чат сам и скажи "
                       f"«открыл» — или «отмена»." if np.get("ru", ru) else
                       f"Couldn't find “{it[1]}”. Open the chat yourself and say “opened”, or “cancel”.")
                _s["said"] = out
        return out
    if k == "open":
        return open_for_user(it[1], it[2], it[3], ru, ui)
    if k == "here":
        np = _s.get("pending") or {}
        _s["pending"] = None
        gist, exact = np.get("gist", ""), np.get("exact")
        if it[1]:                                                   # «пиши тест 1» — новый текст вместо прежнего
            gist, exact = ("", it[1]) if exact else (it[1], None)
        return prepare(None, np.get("contact"), gist, exact, np.get("ru", ru), ui, ask, here=np.get("app"))
    if k == "pick_app":
        np = _s.get("pending") or {}
        _s["pending"] = None
        return prepare(it[1], np.get("contact"), np.get("gist", ""), np.get("exact"),
                       np.get("ru", ru), ui, ask)
    return prepare(it[1], it[2], it[3], it[4], ru, ui, ask)


def open_for_user(app, who: str, what: str = "", ru: bool = True, ui=None) -> str:
    """«Открой чат с папой» / «открой фото, что прислал папа» — открыть его чат на экране."""
    ui = ui or _get_ui()
    if not app:
        app = known_app(who)
    if not app:
        try:
            from core import msg_watch
            app = msg_watch.app_of(who)                    # кто недавно писал — тот мессенджер
        except Exception:
            app = None
    app = app or _resolve_app(who, ui)
    if not app:
        return (f"В каком мессенджере открыть чат с {_nominal(who).lower()}?" if ru else f"Which messenger for {who}?")
    r = open_chat(app, who, ui)
    if not r.get("ok"):
        if r.get("why") in ("ambiguous", "weak"):
            return _no_chat(r, app, who, ru)
        return (f"Не нашёл чат «{_nominal(who)}» в {APPS[app]['name']}. Как контакт записан?" if ru else
                f"Couldn't find “{who}” in {APPS[app]['name']}.")
    _remember(who, app, r["name"] if r.get("verified") else None)
    media = re.match(r"фото|фотк|картин|видео|кружоч|голосов|файл|photo|picture|video", what or "")
    if ru:
        return f"Открыл чат «{r['name']}» в {APPS[app]['name']}." + (" Последнее — внизу." if media else "")
    return f"Opened “{r['name']}” in {APPS[app]['name']}."


def tool_send_message(app: str, contact: str, message: str, exact: bool = False) -> str:
    """Для модели: черновик — явно «НЕ отправлено», ошибка — явно «не отправлено». Никаких «готово» без отправки."""
    ls = _s.get("last_sent")
    if ls and time.time() - ls["t"] < 60 and (not message or difflib.SequenceMatcher(
            None, norm(message), norm(ls["text"])).ratio() > 0.6):
        return (f"ALREADY SENT {int(time.time() - ls['t'])} s ago to “{ls['chat'] or ls['contact']}”: “{ls['text']}”. "
                "Do NOT draft or send it again; just tell the user it was sent.")
    out = tool_prepare(app, contact, message, bool(exact))
    if pending() and re.search(r"(?:Отправить|Send)\?\s*$", out or ""):
        return ("DRAFT READY — NOT SENT YET. Say this to the user word for word and wait for their yes "
                "(then confirm_message_send). Never say it was sent: " + out)
    return "NOT SENT (failed): " + (out or "")


def tool_confirm() -> str:
    out = send() or ""
    if out in ("Отправил.", "Sent."):
        return "SENT — the message is in the chat (checked on screen). " + out
    if "Enter" in out:
        return "PROBABLY SENT — pasted and pressed Enter, but could not confirm on screen; tell the user to check. " + out
    return "NOT SENT (failed) — tell the user exactly this, do not claim it was sent: " + out


def tool_prepare(app: str, contact: str, message: str, exact: bool = False) -> str:
    """Для модели."""
    a = (app or "").lower().strip()
    key = next((k for k, v in APPS.items() if a == k or re.search(v["rx"], a, re.I)), None) if a else None
    if a in ("", "current", "here", "this"):
        key = None
    elif not key:
        return f"Unknown messenger '{app}'. Supported: Telegram, WhatsApp, Instagram, Discord."
    ru = _ru(message) or _ru(contact)
    if contact and is_self(contact):
        contact = "себе"
    if key is None:
        return prepare(None, None, "" if exact else message, message if exact else None, ru)
    return prepare(key, contact, "" if exact else message, message if exact else None, ru)


if __name__ == "__main__":            # python -m core.messengers check telegram  — что Atlas видит в окне
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    app = (sys.argv[2] if len(sys.argv) > 2 else "telegram").lower()
    ui = _get_ui()
    wins = ui.windows()
    w = find_window(app, wins)
    if not w:
        print(f"Окно {APPS[app]['name']} не найдено — открой его и запусти ещё раз.")
        print("--- все видимые окна (app «заголовок»): ---")
        for x in wins:
            print(f"  {x['app']:<22} «{x['title']}»")
        sys.exit(1)
    print(f"окно: {w['app']} «{w['title']}»{' (браузер)' if w.get('web') else ''}", flush=True)
    if sys.argv[1:2] == ["probe"]:                 # python -m core.messengers probe whatsapp → logs/whatsapp_probe.txt
        ui.activate(w["hwnd"])
        time.sleep(0.8)
        rect = ui.rect(w["hwnd"])
        t0 = time.time()
        uia = ui.uia_lines(w["hwnd"], budget=8)
        t_uia = time.time() - t0
        rg = _regions(rect, w.get("web", False), w["hwnd"])
        t0 = time.time()
        lines = ui.ocr(rect)
        t_ocr = time.time() - t0
        out = [f"окно: {w['app']} «{w['title']}» рамка {rect}", f"области: {rg}",
               f"UIA: {len(uia)} строк за {t_uia:.1f} с; OCR: ru {len(lines.get('ru') or [])}, "
               f"en {len(lines.get('en') or [])} за {t_ocr:.1f} с", "", "=== UIA (тип, x, y, w, h, текст) ==="]
        out += [f"{ln.get('uia', ''):<18} {ln['x']:>5} {ln['y']:>5} {ln['w']:>5} {ln['h']:>4}  {ln['text']}" for ln in uia]
        for lang in ("ru", "en"):
            out += ["", f"=== OCR {lang} (x, y, w, h, текст) ==="]
            out += [f"{ln['x']:>5} {ln['y']:>5} {ln['w']:>5} {ln['h']:>4}  {ln['text']}" for ln in lines.get(lang) or []]
        out += ["", "список чатов (UIA): " + str([ln["text"] for ln in uia if _cx(ln) < rg["list_x"] and ln["y"] > rg["top"]][:25]),
                "заголовок (UIA): " + str([ln["text"] for ln in _header(uia, rg)][:8])]
        os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
        path = os.path.join(ROOT, "logs", f"{app}_probe.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        try:
            from PIL import ImageGrab
            ImageGrab.grab(bbox=(max(0, rect[0]), max(0, rect[1]), rect[2], rect[3]), all_screens=True).save(
                os.path.join(ROOT, "logs", f"{app}_probe.png"))
        except Exception as e:
            print(f"снимок: {e}")
        print("\n".join(out[:3]))
        print(f"готово → {path}")
        sys.exit(0)
    try:
        ui.activate(w["hwnd"])
        time.sleep(0.6)
        rect = ui.rect(w["hwnd"])
        print(f"рамка окна: {rect}", flush=True)
    except Exception as e:
        print(f"[ошибка] активация/рамка: {e!r}", flush=True)
        sys.exit(1)
    try:
        print("распознаю окно (OCR)…", flush=True)
        lines = ui.ocr(rect)
    except Exception as e:
        print(f"[ошибка] OCR (winocr): {e!r}", flush=True)
        print("  → распознавание текста окна не работает (нет winocr?) — отправка возможна, чтение чатов нет.", flush=True)
        sys.exit(1)
    rg = _regions(rect, w.get("web", False), w["hwnd"])
    print(f"строк OCR: ru {len(lines.get('ru') or [])}, en {len(lines.get('en') or [])}", flush=True)
    use = lines.get("ru") or lines.get("en") or []
    print("список чатов:", [ln["text"] for ln in use if _cx(ln) < rg["list_x"] and ln["y"] > rg["top"]][:12], flush=True)
    print("заголовок чата:", [ln["text"] for ln in _header(use, rg)], flush=True)
    try:
        for who, t in read_chat(w["hwnd"], w.get("web", False), ui, rect, lines)[-8:]:
            print(f"  {'я' if who == 'me' else 'он(а)'}: {t}", flush=True)
    except Exception as e:
        print(f"[ошибка] чтение переписки: {e!r}", flush=True)
