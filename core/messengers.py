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
import json
import os
import re
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
                  "rx": r"инст[аеуы]\w*|инстаграм\w*|instagram|\bдирект\w*|\bdirect\b|\binsta\b"},
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


_END2 = ("ой", "ей", "ом", "ем", "ою", "ею", "ам", "ям", "ах", "ях", "ым", "им", "ого", "его", "ому", "ему")


def stems(contact: str) -> list:
    """«Маме» → ['мам'], «Алиму Сыдыкову» → ['алим', 'сыдыков']: поиск в мессенджерах ищет по началу слова,
    так что падеж не мешает."""
    out = []
    for w in norm(contact).split():
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


# --- запоминание: кто в каком мессенджере ------------------------------------------------
def _load() -> dict:
    try:
        with open(FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _remember(contact: str, app: str) -> None:
    key = " ".join(stems(contact))
    if not key:
        return
    with _lock:
        d = _load()
        d[key] = app
        try:
            with open(FILE, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
        except Exception as e:
            print(f"[мессенджеры] не запомнил: {e}")


def known_app(contact: str):
    return _load().get(" ".join(stems(contact)))


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
        img = ImageGrab.grab(bbox=tuple(rect), all_screens=True)
        try:
            from winocr import recognize_pil_sync
        except ImportError:
            return {"ru": [], "en": []}
        from file_search import _ocr_lock
        out = {}
        for lang in ("ru", "en"):
            try:
                with _ocr_lock:
                    res = recognize_pil_sync(img, lang)
            except Exception:
                res = {}
            out[lang] = _lines(res, rect[0], rect[1])
        return out

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

    def sleep(self, s: float) -> None:
        time.sleep(s)


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


_ui = None


def _get_ui():
    global _ui
    if _ui is None:
        _ui = UI()
    return _ui


# --- окно мессенджера --------------------------------------------------------------------------
def find_window(app: str, wins: list):
    a = APPS[app]
    for w in wins:
        if w["app"].lower() in a["exe"]:
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


def _regions(rect, web: bool):
    l, t, r, b = rect
    W, H = r - l, b - t
    return {"list_x": l + int(W * (0.45 if not web else 0.42)), "top": t + int(H * (0.07 if not web else 0.12)),
            "chat_x": l + int(W * (0.30 if not web else 0.28)),
            "head_y": t + int(H * (0.16 if not web else 0.26)), "foot_y": b - int(H * 0.09), "W": W, "H": H}


def _cx(ln):
    return ln["x"] + ln["w"] / 2


def _cy(ln):
    return ln["y"] + ln["h"] / 2


def _header(lines: list, rg: dict) -> list:
    return [ln for ln in lines if _cx(ln) > rg["chat_x"] and ln["y"] < rg["head_y"]]


def open_chat(app: str, contact: str, ui=None) -> dict:
    """Открыть чат → {"ok", "hwnd", "verified", "name", "why"}."""
    ui = ui or _get_ui()
    w = _ensure(app, ui)
    if not w:
        return {"ok": False, "why": "noapp"}
    hwnd, web = w["hwnd"], w.get("web", False)
    st = stems(contact)
    a = APPS[app]

    def look():
        rect = ui.rect(hwnd)
        lines = ui.ocr(rect)
        return rect, _regions(rect, web), lines

    def verify():
        ui.sleep(0.9)
        rect, rg, lines = look()
        head = [ln for ln in _header(_pick(lines, st), rg) if line_matches(ln["text"], st)]
        return {"ok": True, "hwnd": hwnd, "verified": bool(head),
                "name": head[0]["text"] if head else contact, "web": web}

    rect, rg, lines = look()
    if _header([ln for ln in _pick(lines, st) if line_matches(ln["text"], st)], rg):
        return {"ok": True, "hwnd": hwnd, "verified": True, "web": web,
                "name": next(ln["text"] for ln in _header(_pick(lines, st), rg) if line_matches(ln["text"], st))}

    def click_in_list(lines, rg, query=""):
        cand = [ln for ln in _pick(lines, st) if _cx(ln) < rg["list_x"] and ln["y"] > rg["top"] and
                line_matches(ln["text"], st) and norm(ln["text"]) != norm(query)]
        if not cand:
            return False
        ln = min(cand, key=lambda x: x["y"])
        ui.click(int(_cx(ln)), int(_cy(ln)))
        return True

    if a["mode"] != "switcher" and click_in_list(lines, rg):        # чат уже виден в списке
        return verify()
    if a["mode"] == "list":                                          # Instagram: поиска в директе нет
        return {"ok": False, "why": "notinlist", "hwnd": hwnd}

    query = " ".join(st) if all(len(s) >= 3 for s in st) else contact
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
    for wait in (1.2, 1.5):
        ui.sleep(wait)
        rect, rg, lines = look()
        if click_in_list(lines, rg, query):
            return verify()
    ui.keys("esc")
    return {"ok": False, "why": "notfound", "hwnd": hwnd}


def read_chat(hwnd: int, web: bool = False, ui=None, rect=None, lines=None) -> list:
    """Видимая переписка: [("me" | "them", текст)] сверху вниз (последние ~25)."""
    ui = ui or _get_ui()
    rect = rect or ui.rect(hwnd)
    if lines is None:
        lines = ui.ocr(rect)
    rg = _regions(rect, web)
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


def draft(gist: str, conv: list, name: str, app: str, ru: bool, ask=None, change: str = "", before: str = "",
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
        f"{'Russian' if ru else 'English'}). Copy the user's own style from their messages (lines starting "
        "with 'Я:'): length, capital letters or not, emoji or not, punctuation, slang. Casual chat — casual text. "
        "Say exactly what the user wants and nothing more: do not invent facts, times, promises or excuses. "
        "No greeting unless it fits, no signature, no quotes around the text, no explanations. Output only the message.")
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
_YES = re.compile(r"^(?:да|давай|отправь|отправляй|шли|жми|го|ок|окей|ага|можно|верно|yes|yeah|yep|send|ok|okay|go)\b", re.I)
_NO = re.compile(r"^(?:нет|не надо|не отправляй|отмена|отмени|стоп|no|nope|cancel|don'?t)\b", re.I)
_EDIT = re.compile(r"короче|длиннее|подробнее|по-другому|по другому|иначе|вежлив|формальн|неформальн|проще|"
                   r"добавь|убери|измени|перепиши|без смайл|со смайл|смайлик|замени|вместо|теплее|мягче|строже|"
                   r"shorter|longer|rewrite|change|add|remove|polite|casual|emoji", re.I)


def _same(last: str, said: str) -> bool:
    """Последний ответ в чате — наш вопрос «Отправить?» (смайлики и разметка могли сняться при показе)."""
    a, b = norm(last), norm(said)
    return bool(b) and (a == b or b[:40] in a or a[:40] in b)


def _app_in(text: str):
    for k, rx in _APP_RX:
        m = rx.search(text)
        if m:
            return k, m
    return None, None


def intent(text: str, last: str = None):
    t = " ".join((text or "").split()).strip()
    if not t:
        return None
    p = pending()
    if p and (last is None or _same(last, _s.get("said"))) and len(t) < 120:
        bare = t.strip(" .!").lower()
        if _NO.search(bare):
            return ("cancel",)
        if _YES.search(bare) and len(bare) < 30:
            return ("send",)
        if _EDIT.search(bare):
            return ("edit", t)
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
            contact, gist = words[0], " ".join(words[1:])
        contact = contact.strip(" ,.")
        gist = re.sub(r"^(?:что|чтобы|that)\s+", "", gist.strip(" ,:"), flags=re.I)
    exact = None
    if quote:
        exact = quote.group(1).strip()
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
    if not app:
        if contact is None:
            return ("reply_here", None, gist, exact)
        app = known_app(contact)
        if not app:
            return None                                # «напиши маме…» без мессенджера — пусть решает модель
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
        head = f"Напишу {p['name']} в {app}" if p.get("verified") else \
            f"Открыл чат в {app}, но не уверен, что это {p['contact'] or 'нужный чат'} — глянь на экран. Текст"
        return f"{head}: «{p['text']}». Отправить?"
    head = f"To {p['name']} on {app}" if p.get("verified") else \
        f"Opened {app}, but not sure it's {p['contact'] or 'the right chat'} — check the screen. Text"
    return f"{head}: “{p['text']}”. Send?"


def _no_chat(r: dict, app: str, contact: str, ru: bool) -> str:
    name = APPS[app]["name"]
    if r.get("why") == "noapp":
        return f"Не получилось открыть {name}." if ru else f"Couldn't open {name}."
    if r.get("why") == "notinlist":
        return (f"Не вижу чат «{contact}» в списке {name}. Открой его сам и скажи «ответь ему, что…»." if ru else
                f"I don't see “{contact}” in the {name} list. Open the chat and say “reply to him that…”.")
    return (f"Не нашёл чат «{contact}» в {name}. Скажи имя, как оно записано, или открой чат и скажи «ответь ему»." if ru
            else f"Couldn't find “{contact}” in {name}. Say the name as saved, or open the chat and say “reply here”.")


def prepare(app, contact, gist: str, exact: str = None, ru: bool = True, ui=None, ask=None) -> str:
    """Открыть чат, прочитать, написать черновик — и спросить «Отправить?»."""
    ui = ui or _get_ui()
    if app is None:                                                    # «ответь ему» — в открытом чате
        found = current_messenger(ui.windows())
        if not found:
            return ("Открой чат в мессенджере и скажи ещё раз — или назови, кому и где." if ru else
                    "Open the chat first, or say who and where.")
        app, w = found
        rect = ui.rect(w["hwnd"])
        lines = ui.ocr(rect)
        rg = _regions(rect, w.get("web", False))
        head = _header(lines.get("ru") or lines.get("en") or [], rg)
        r = {"ok": True, "hwnd": w["hwnd"], "verified": True, "web": w.get("web", False),
             "name": head[0]["text"][:40] if head else ("собеседник" if ru else "them")}
        conv = read_chat(w["hwnd"], r["web"], ui, rect, lines)
    else:
        r = open_chat(app, shop_contact(contact) if is_shop(contact) else contact, ui)
        if not r.get("ok"):
            return _no_chat(r, app, contact, ru)
        conv = read_chat(r["hwnd"], r.get("web", False), ui)
        _remember(contact, app)
    name = re.sub(r"\s+(?:онлайн|online|был\w*|печатает|typing|last seen).*$", "", r["name"], flags=re.I).strip()
    try:
        text = exact or draft(gist, conv, name, app, ru, ask, shop=bool(contact and is_shop(contact)))
    except Exception as e:
        return (f"Чат открыл, но текст не получился: {e}" if ru else f"Opened the chat, but couldn't write: {e}")[:220]
    if not text:
        return "Не получилось написать ответ." if ru else "Couldn't write a reply."
    p = {"app": app, "hwnd": r["hwnd"], "contact": contact, "name": name, "text": text, "verified": r["verified"],
         "created": time.time(), "gist": gist, "conv": conv, "ru": ru, "web": r.get("web", False),
         "shop": bool(contact and is_shop(contact))}
    _s["pending"] = p
    said = _ask_line(p, ru)
    _s["said"] = said
    return said


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


def send(ui=None) -> str:
    """Отправить черновик после «да»."""
    ui = ui or _get_ui()
    p = pending()
    if not p:
        return "Нечего отправлять — черновик устарел или уже отправлен."
    ru = p["ru"]
    _s["pending"] = None
    if not ui.activate(p["hwnd"]) and ui.foreground() != p["hwnd"]:
        return (f"Не получилось переключиться на {APPS[p['app']]['name']} — не отправил." if ru else
                f"Couldn't switch to {APPS[p['app']]['name']} — not sent.")
    ui.sleep(0.25)
    if p.get("web") or not ui.focus_edit(p["hwnd"], r"message|сообщени|написать|введите|type"):
        rect = ui.rect(p["hwnd"])
        rg = _regions(rect, p.get("web", False))
        ui.click(int(rg["chat_x"] + (rect[2] - rg["chat_x"]) * 0.45), int(rect[3] - rg["H"] * 0.045))
        ui.sleep(0.2)
    if ui.foreground() != p["hwnd"]:
        return "Окно мессенджера ушло с экрана — не отправил." if ru else "The messenger window lost focus — not sent."
    ui.paste(p["text"])
    ui.sleep(0.25)
    ui.keys("enter")
    ui.sleep(1.0)
    conv = read_chat(p["hwnd"], p.get("web", False), ui)
    head = norm(p["text"])[:14]
    ok = any(w == "me" and head and head[:10] in norm(t) for w, t in conv[-4:])
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
    """«Что мне пишут в телеграме?» — по списку чатов: кто и что написал последним."""
    ui = ui or _get_ui()
    w = _ensure(app, ui)
    if not w:
        return f"Не получилось открыть {APPS[app]['name']}." if ru else f"Couldn't open {APPS[app]['name']}."
    rect = ui.rect(w["hwnd"])
    lines = ui.ocr(rect)
    rg = _regions(rect, w.get("web", False))
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
        return q(system, "\n".join(items), max_tokens=300).strip()
    except Exception:
        return "; ".join(items[:6])


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
    return prepare(it[1], it[2], it[3], it[4], ru, ui, ask)


def tool_prepare(app: str, contact: str, message: str, exact: bool = False) -> str:
    """Для модели."""
    a = (app or "").lower().strip()
    key = next((k for k, v in APPS.items() if a == k or re.search(v["rx"], a, re.I)), None) if a else None
    if a in ("", "current", "here", "this"):
        key = None
    elif not key:
        return f"Unknown messenger '{app}'. Supported: Telegram, WhatsApp, Instagram, Discord."
    ru = _ru(message) or _ru(contact)
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
    w = find_window(app, ui.windows())
    if not w:
        print(f"Окно {APPS[app]['name']} не найдено — открой его и запусти ещё раз.")
        sys.exit(1)
    ui.activate(w["hwnd"])
    time.sleep(0.6)
    rect = ui.rect(w["hwnd"])
    lines = ui.ocr(rect)
    rg = _regions(rect, w.get("web", False))
    print(f"окно: {w['app']} «{w['title']}» {rect}{' (браузер)' if w.get('web') else ''}")
    print(f"строк OCR: ru {len(lines.get('ru') or [])}, en {len(lines.get('en') or [])}")
    use = lines.get("ru") or lines.get("en") or []
    print("список чатов:", [ln["text"] for ln in use if _cx(ln) < rg["list_x"] and ln["y"] > rg["top"]][:12])
    print("заголовок чата:", [ln["text"] for ln in _header(use, rg)])
    for who, t in read_chat(w["hwnd"], w.get("web", False), ui, rect, lines)[-8:]:
        print(f"  {'я' if who == 'me' else 'он(а)'}: {t}")
