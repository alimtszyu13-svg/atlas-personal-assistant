"""
Atlas управляет любыми программами Windows.

То же, что браузерный агент, но для всего компьютера: Блокнот, Проводник, Word,
Excel, Параметры Windows, Telegram, Steam — любое окно.

Восприятие
  • Windows UI Automation: кнопки, поля, меню, вкладки, списки, флажки, ссылки
    активного окна — с номерами, названиями, значениями и состояниями.
    Это та же технология, которой пользуются программы экранного доступа.
  • Окно самого Atlas никогда не трогается: берётся последнее окно под ним.

Действия
  • Нажать элемент (через «кнопочный» интерфейс UIA, без движения мыши, или кликом),
    ввести текст (любой язык — через буфер обмена, буфер потом восстанавливается),
    горячие клавиши, прокрутка, переключение окон.
  • После каждого действия — свежий список элементов.

Зрение как запасной путь: номера рисуются на снимке окна, модель со зрением
выбирает номер — для программ, которые не отдают элементы через UIA (игры, Electron).

Безопасность: поля паролей не трогаются; окно Atlas не трогается; мышь в левый
верхний угол экрана — аварийная остановка (pyautogui FAILSAFE); F8 — отмена задачи.
Все вызовы UIA — в одном отдельном потоке с инициализированным COM.
"""
import concurrent.futures
import ctypes
import functools
import json
import os
import re
import time

MAX_ELEMENTS = 70
WALK_DEPTH = 14
WALK_BUDGET_S = 2.5            # обход дерева не дольше — у тяжёлых окон тысячи элементов
_INTERESTING = {
    "ButtonControl", "SplitButtonControl", "MenuItemControl", "TabItemControl", "ListItemControl",
    "TreeItemControl", "CheckBoxControl", "RadioButtonControl", "ComboBoxControl", "EditControl",
    "DocumentControl", "HyperlinkControl", "DataItemControl", "SliderControl", "SpinnerControl",
    "HeaderItemControl", "MenuBarControl",
}
_KIND = {"ButtonControl": "button", "SplitButtonControl": "button", "MenuItemControl": "menu", "TabItemControl": "tab",
         "ListItemControl": "item", "TreeItemControl": "tree", "CheckBoxControl": "checkbox",
         "RadioButtonControl": "radio", "ComboBoxControl": "combobox", "EditControl": "edit",
         "DocumentControl": "document", "HyperlinkControl": "link", "DataItemControl": "cell",
         "SliderControl": "slider", "SpinnerControl": "spinner", "HeaderItemControl": "header",
         "MenuBarControl": "menubar"}

_last = []                     # [{i, kind, name, value, ..., ctrl}]
_target = {"hwnd": None, "title": ""}


def _init_thread():
    try:
        import uiautomation as auto
        auto.InitializeUIAutomationInCurrentThread()
    except Exception as e:
        print(f"[desktop] UI Automation недоступна: {e}")
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # координаты UIA и снимка — в одних пикселях
    except Exception:
        pass


_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, initializer=_init_thread)


def in_desktop_thread(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return _executor.submit(func, *args, **kwargs).result(timeout=60)
        except concurrent.futures.TimeoutError:
            return "Программа не отвечает больше минуты — попробуй ещё раз или выбери другое окно."
    return wrapper


def _auto():
    try:
        import uiautomation as auto
        return auto
    except ImportError:
        raise RuntimeError("не установлен модуль uiautomation — выполни: pip install uiautomation")


# ===========================================================================
# Окна
# ===========================================================================
def _title(h) -> str:
    u = ctypes.windll.user32
    n = u.GetWindowTextLengthW(h)
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(h, buf, n + 1)
    return buf.value


def _is_atlas(title: str) -> bool:
    return title.strip().upper() == "ATLAS"


def _visible_windows() -> list:
    """Видимые окна верхнего уровня в порядке «сверху вниз», без Atlas и служебных."""
    u = ctypes.windll.user32
    out, h = [], u.GetTopWindow(None)
    while h and len(out) < 40:
        if u.IsWindowVisible(h) and not u.IsIconic(h):
            t = _title(h)
            if t and not _is_atlas(t) and t not in ("Program Manager", "Default IME", "MSCTFIME UI"):
                out.append((h, t))
        h = u.GetWindow(h, 2)                               # GW_HWNDNEXT
    return out


def _pick_target(title_hint: str = ""):
    u = ctypes.windll.user32
    if title_hint:
        for h, t in _visible_windows():
            if title_hint.lower() in t.lower():
                return h, t
        return None, ""
    fg = u.GetForegroundWindow()
    if fg and not _is_atlas(_title(fg)):
        return fg, _title(fg)
    if _target["hwnd"] and u.IsWindow(_target["hwnd"]) and u.IsWindowVisible(_target["hwnd"]):
        return _target["hwnd"], _title(_target["hwnd"])
    ws = _visible_windows()
    return ws[0] if ws else (None, "")


def _activate(hwnd) -> None:
    u = ctypes.windll.user32
    try:
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)                           # SW_RESTORE
        _auto().ControlFromHandle(hwnd).SetActive()
    except Exception:
        try:
            u.SetForegroundWindow(hwnd)
        except Exception:
            pass
    time.sleep(0.15)


# ===========================================================================
# Восприятие
# ===========================================================================
def _pattern(ctrl, name):
    auto = _auto()
    try:
        return ctrl.GetPattern(getattr(auto.PatternId, name))
    except Exception:
        return None


def _describe(ctrl, ctype: str) -> dict:
    it = {"kind": _KIND.get(ctype, ctype.replace("Control", "").lower()),
          "name": re.sub(r"\s+", " ", (ctrl.Name or "")).strip()[:80]}
    if not it["name"]:
        it["name"] = (getattr(ctrl, "AutomationId", "") or "")[:40]
    vp = _pattern(ctrl, "ValuePattern")
    if vp is not None and ctype in ("EditControl", "ComboBoxControl", "DocumentControl", "SpinnerControl", "DataItemControl"):
        try:
            if not getattr(ctrl, "IsPassword", False):
                v = (vp.Value or "").strip()
                if v:
                    it["value"] = v[:60]
        except Exception:
            pass
    tp = _pattern(ctrl, "TogglePattern")
    if tp is not None:
        try:
            it["checked"] = int(tp.ToggleState) == 1
        except Exception:
            pass
    sp = _pattern(ctrl, "SelectionItemPattern")
    if sp is not None and ctype in ("ListItemControl", "TabItemControl", "TreeItemControl", "RadioButtonControl"):
        try:
            if sp.IsSelected:
                it["selected"] = True
        except Exception:
            pass
    ep = _pattern(ctrl, "ExpandCollapsePattern")
    if ep is not None and ctype in ("MenuItemControl", "TreeItemControl", "ComboBoxControl", "SplitButtonControl"):
        try:
            it["expanded"] = int(ep.ExpandCollapseState) == 1
        except Exception:
            pass
    if getattr(ctrl, "IsPassword", False):
        it["password"] = True
    if not getattr(ctrl, "IsEnabled", True):
        it["disabled"] = True
    return it


def _snapshot(hwnd=None, title_hint: str = "") -> str:
    global _last
    auto = _auto()
    if hwnd is None:
        hwnd, _t = _pick_target(title_hint)
    if not hwnd:
        return ("Не нашёл подходящего окна." if not title_hint else
                f"Нет открытого окна с «{title_hint}» в названии — открой программу (open_app) или посмотри desktop_windows.")
    _target.update(hwnd=hwnd, title=_title(hwnd))
    win = auto.ControlFromHandle(hwnd)
    wr = win.BoundingRectangle
    items, t0 = [], time.time()
    for ctrl, _depth in auto.WalkControl(win, includeTop=False, maxDepth=WALK_DEPTH):
        if len(items) >= MAX_ELEMENTS or time.time() - t0 > WALK_BUDGET_S:
            break
        try:
            ctype = ctrl.ControlTypeName
            if ctype not in _INTERESTING or ctrl.IsOffscreen:
                continue
            r = ctrl.BoundingRectangle
            if r.width() < 3 or r.height() < 3 or r.right < wr.left or r.left > wr.right or r.bottom < wr.top or r.top > wr.bottom:
                continue
            it = _describe(ctrl, ctype)
            if not it["name"] and it["kind"] not in ("edit", "document", "combobox"):
                continue
            it.update(i=len(items), x=r.xcenter(), y=r.ycenter(), rect=(r.left, r.top, r.right, r.bottom), ctrl=ctrl)
            items.append(it)
        except Exception:
            continue
    _last = items
    head = f"Окно: {_title(hwnd)[:100]}"
    if not items:
        return head + ("\nЭлементов через UI Automation не видно (игра, видео или приложение на Electron) — "
                       "попробуй горячие клавиши (desktop_hotkey) или desktop_screenshot_describe.")
    more = "" if len(items) < MAX_ELEMENTS else "\n(показаны не все — desktop_scroll или сузь задачу)"
    return head + "\nЭлементы:\n" + "\n".join(_fmt(it) for it in items) + more


def _fmt(it: dict) -> str:
    s = f'[{it["i"]}] {it["kind"]} "{it["name"]}"'
    if it.get("value"):
        s += f' = "{it["value"]}"'
    if "checked" in it:
        s += " (отмечен)" if it["checked"] else " (не отмечен)"
    if it.get("selected"):
        s += " (выбран)"
    if "expanded" in it:
        s += " (раскрыт)" if it["expanded"] else " (свёрнут)"
    if it.get("disabled"):
        s += " (недоступен)"
    if it.get("password"):
        s += " (пароль)"
    return s


def _el(index):
    try:
        index = int(index)
    except (TypeError, ValueError):
        return None
    return next((e for e in _last if e["i"] == index), None)


def _refresh() -> str:
    time.sleep(0.45)
    return _snapshot(_target["hwnd"] if _target["hwnd"] else None)


# ===========================================================================
# Инструменты для модели
# ===========================================================================
@in_desktop_thread
def desktop_look(window: str = "") -> str:
    """Numbered controls of the active program window (or the window whose title contains `window`)."""
    try:
        hwnd, _t = _pick_target(window)
        if hwnd and window:
            _activate(hwnd)
        return _snapshot(hwnd, window)
    except RuntimeError as e:
        return str(e)


@in_desktop_thread
def desktop_windows() -> str:
    """Lists open program windows."""
    ws = _visible_windows()
    return "Окна:\n" + "\n".join(f"- {t[:90]}" for _h, t in ws) if ws else "Открытых окон нет."


@in_desktop_thread
def desktop_switch(window: str) -> str:
    """Brings the window whose title contains `window` to the front and returns its controls."""
    hwnd, t = _pick_target(window)
    if not hwnd:
        return f"Нет окна с «{window}» в названии."
    _activate(hwnd)
    return f"Переключился на «{t[:80]}».\n" + _snapshot(hwnd)


@in_desktop_thread
def desktop_click(index: int, double: bool = False, right: bool = False) -> str:
    """Clicks control [index] from the latest desktop_look list."""
    el = _el(index)
    if not el:
        return f"Нет элемента [{index}] — сначала desktop_look."
    if el.get("password"):
        return "Отказываюсь — это поле пароля. Только с явного подтверждения пользователя вслух."
    if el.get("disabled"):
        return f"[{index}] «{el['name']}» недоступен."
    _activate(_target["hwnd"])
    ctrl, done = el["ctrl"], False
    if not double and not right:
        for pname, action in (("InvokePattern", "Invoke"), ("TogglePattern", "Toggle"),
                              ("SelectionItemPattern", "Select"), ("ExpandCollapsePattern", "Expand")):
            p = _pattern(ctrl, pname)
            if p is not None:
                try:
                    getattr(p, action)()
                    done = True
                    break
                except Exception:
                    pass
    if not done:
        try:
            (ctrl.DoubleClick if double else ctrl.RightClick if right else ctrl.Click)(simulateMove=False)
        except Exception:
            import pyautogui
            (pyautogui.doubleClick if double else pyautogui.rightClick if right else pyautogui.click)(el["x"], el["y"])
    return f"Нажал [{index}] «{el['name']}».\n" + _refresh()


def _paste(text: str) -> None:
    """Любой язык и символы: через буфер обмена, прежнее содержимое буфера возвращается."""
    import pyautogui
    import pyperclip
    try:
        old = pyperclip.paste()
    except Exception:
        old = None
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.2)
    if old is not None:
        try:
            pyperclip.copy(old)
        except Exception:
            pass


@in_desktop_thread
def desktop_type(text: str, index: int = -1, enter: bool = False, replace: bool = False) -> str:
    """Types text into control [index] (or where the cursor is when index=-1); enter=true presses Enter; replace=true clears the field first."""
    import pyautogui
    if _target["hwnd"]:
        _activate(_target["hwnd"])
    index = -1 if index is None else int(index)
    el = _el(index) if index >= 0 else None
    if index >= 0 and not el:
        return f"Нет элемента [{index}] — сначала desktop_look."
    if el:
        if el.get("password"):
            return "Отказываюсь вводить — это поле пароля. Только с явного подтверждения пользователя вслух."
        vp = _pattern(el["ctrl"], "ValuePattern")
        if replace and vp is not None and el["kind"] in ("edit", "combobox"):
            try:
                vp.SetValue(str(text))
                if enter:
                    el["ctrl"].SetFocus()
                    pyautogui.press("enter")
                return f"Записал «{str(text)[:60]}» в [{index}] «{el['name']}».\n" + _refresh()
            except Exception:
                pass
        try:
            el["ctrl"].Click(simulateMove=False)
        except Exception:
            pyautogui.click(el["x"], el["y"])
        if replace:
            pyautogui.hotkey("ctrl", "a")
    _paste(str(text))
    if enter:
        pyautogui.press("enter")
    where = f"в [{index}] «{el['name']}»" if el else "в активное поле"
    return f"Ввёл «{str(text)[:60]}» {where}" + (" и нажал Enter" if enter else "") + ".\n" + _refresh()


_KEYMAP = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift", "win": "win", "windows": "win",
           "enter": "enter", "esc": "esc", "escape": "esc", "tab": "tab", "del": "delete", "delete": "delete",
           "backspace": "backspace", "space": "space", "up": "up", "down": "down", "left": "left", "right": "right",
           "home": "home", "end": "end", "pageup": "pageup", "pagedown": "pagedown"}


@in_desktop_thread
def desktop_hotkey(keys: str) -> str:
    """Presses a key or shortcut in the program window, e.g. 'ctrl+s', 'alt+f4', 'enter', 'f5', 'ctrl+shift+n'."""
    import pyautogui
    parts = [p.strip().lower() for p in re.split(r"[+\s]+", keys or "") if p.strip()]
    if not parts:
        return "Не понял, какие клавиши нажать."
    if _target["hwnd"]:
        if _is_atlas(_title(_target["hwnd"])):
            return "Отказываюсь — это окно самого Atlas."
        _activate(_target["hwnd"])
    keys_ = [_KEYMAP.get(p, p) for p in parts]
    pyautogui.hotkey(*keys_) if len(keys_) > 1 else pyautogui.press(keys_[0])
    return f"Нажал {'+'.join(keys_)}.\n" + _refresh()


@in_desktop_thread
def desktop_scroll(direction: str = "down", times: int = 5) -> str:
    """Scrolls inside the program window."""
    import pyautogui
    if not _target["hwnd"]:
        return "Сначала desktop_look."
    _activate(_target["hwnd"])
    try:
        import ctypes.wintypes as wt
        r = wt.RECT()
        ctypes.windll.user32.GetWindowRect(_target["hwnd"], ctypes.byref(r))
        pyautogui.moveTo((r.left + r.right) // 2, (r.top + r.bottom) // 2)
    except Exception:
        pass
    pyautogui.scroll((-120 if direction != "up" else 120) * int(times or 5))
    return f"Прокрутил {'вверх' if direction == 'up' else 'вниз'}.\n" + _refresh()


@in_desktop_thread
def desktop_read_text() -> str:
    """Reads visible text of the program window: documents, fields, labels, list items."""
    auto = _auto()
    hwnd, _t = _pick_target()
    if not hwnd:
        return "Нет подходящего окна."
    win = auto.ControlFromHandle(hwnd)
    parts, seen, t0 = [], set(), time.time()
    for ctrl, _d in auto.WalkControl(win, includeTop=False, maxDepth=WALK_DEPTH):
        if time.time() - t0 > WALK_BUDGET_S or sum(len(p) for p in parts) > 3500:
            break
        try:
            if ctrl.IsOffscreen or getattr(ctrl, "IsPassword", False):
                continue
            txt = ""
            if ctrl.ControlTypeName in ("DocumentControl", "EditControl"):
                tp = _pattern(ctrl, "TextPattern")
                if tp is not None:
                    txt = tp.DocumentRange.GetText(4000)
                else:
                    vp = _pattern(ctrl, "ValuePattern")
                    txt = vp.Value if vp is not None else ""
            elif ctrl.ControlTypeName in ("TextControl", "ListItemControl", "DataItemControl", "HeaderItemControl"):
                txt = ctrl.Name
            txt = (txt or "").strip()
            if txt and txt not in seen:
                seen.add(txt)
                parts.append(txt[:1500])
        except Exception:
            continue
    body = "\n".join(parts)[:3500]
    return f"Окно: {_title(hwnd)[:100]}\n" + (body or "(текста через UI Automation не видно — попробуй read_screen)")


# ===========================================================================
# Зрение: номера на снимке окна
# ===========================================================================
_VISION_PREFS = ("qwen/qwen3.8-27b", "qwen/qwen3.6-27b", "meta-llama/llama-4-maverick-17b-128e-instruct")
_vision = {"model": None, "checked": False}


def draw_marks(img, items, origin):
    """Рисует рамки и номера элементов на снимке (координаты экрана → снимка)."""
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    ox, oy = origin
    for it in items:
        l, t, r, b = it["rect"]
        box = (l - ox, t - oy, r - ox, b - oy)
        d.rectangle(box, outline=(225, 29, 72), width=2)
        tx, ty = box[0], max(0, box[1] - 13)
        label = str(it["i"])
        d.rectangle((tx, ty, tx + 7 * len(label) + 4, ty + 13), fill=(225, 29, 72))
        d.text((tx + 2, ty), label, fill=(255, 255, 255))
    return img


@in_desktop_thread
def desktop_screenshot_describe(instruction: str) -> str:
    """Vision fallback: numbers the window's controls on a screenshot; a vision model picks one; it gets clicked."""
    import base64
    import io
    from PIL import ImageGrab
    snap = _snapshot()
    hwnd = _target["hwnd"]
    if not hwnd:
        return snap
    import ctypes.wintypes as wt
    r = wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    _activate(hwnd)
    img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
    draw_marks(img, _last, (r.left, r.top))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=70)
    from groq import Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    if not _vision["checked"]:
        _vision["checked"] = True
        try:
            have = [m.id for m in client.models.list().data]
            _vision["model"] = next((m for m in _VISION_PREFS if m in have), None)
        except Exception:
            pass
    if not _vision["model"]:
        return "Модели со зрением на аккаунте Groq нет — используй desktop_look и горячие клавиши."
    prompt = (f"Screenshot of a Windows program window ({img.width}x{img.height} px); controls have red number labels. "
              f"Task: {instruction}. Reply ONLY JSON: {{\"index\": <label>}}; if the target has no label, "
              "{\"x\": <px>, \"y\": <px>} in screenshot pixels; if not visible, {\"found\": false}.")
    kw = dict(model=_vision["model"], temperature=0, max_tokens=300, messages=[{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}}]}])
    if "qwen" in _vision["model"]:
        kw["reasoning_format"] = "hidden"
    try:
        raw = client.chat.completions.create(**kw).choices[0].message.content or ""
    except Exception:
        kw.pop("reasoning_format", None)
        raw = client.chat.completions.create(**kw).choices[0].message.content or ""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    m = re.search(r"\{.*?\}", raw, re.S)
    try:
        res = json.loads(m.group(0)) if m else {}
    except Exception:
        return f"Модель со зрением ответила непонятно: {raw[:150]}"
    import pyautogui
    if res.get("index") is not None and _el(res["index"]):
        el = _el(res["index"])
        if el.get("password"):
            return "Отказываюсь — это поле пароля."
        try:
            el["ctrl"].Click(simulateMove=False)
        except Exception:
            pyautogui.click(el["x"], el["y"])
        return f"По снимку нажал [{el['i']}] «{el['name']}».\n" + _refresh()
    if res.get("x") is not None:
        pyautogui.click(r.left + float(res["x"]), r.top + float(res["y"]))
        return f"По снимку нажал в точку ({res['x']}, {res['y']}).\n" + _refresh()
    return f"На снимке не нашёл: {instruction}"
