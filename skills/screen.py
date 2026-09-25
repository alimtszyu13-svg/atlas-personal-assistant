"""
Зрение: что на экране.

Сначала Windows UI Automation — система сама отдаёт тексты, поля и кнопки
окна: мгновенно, бесплатно и точно. Если окно «немое» (игры, часть
Electron-приложений) — OCR снимка экрана. Экран читается только по просьбе.
"""
import ctypes
from core.skills import skill
import re
import time
from ui_state import shared_state

user32 = ctypes.windll.user32

_TEXT_TYPES = {
    "TextControl", "EditControl", "DocumentControl", "ButtonControl", "HyperlinkControl",
    "ListItemControl", "MenuItemControl", "TabItemControl", "DataItemControl",
    "HeaderItemControl", "TreeItemControl", "CheckBoxControl", "RadioButtonControl",
    "ComboBoxControl",
}
_CLICKABLE = {
    "ButtonControl", "HyperlinkControl", "MenuItemControl", "TabItemControl",
    "ListItemControl", "CheckBoxControl", "RadioButtonControl", "TreeItemControl",
}
# необратимое — только после явного подтверждения
_RISKY = ("удал", "delete", "оплат", "pay", "купи", "buy", "отправ", "send",
          "submit", "подтверд", "confirm", "формат", "format")
MAX_CHARS = 1400                     # в историю модели всё равно уходит не больше 1500
# Подтверждение необратимого проверяет код, а не модель: нужна НОВАЯ реплика
# пользователя со словом согласия после вопроса. Сама себе модель подтвердить не может.
_YES = re.compile(r"\b(?:да|давай|подтверждаю|подтверди|удаляй|конечно|согласен|"
                  r"yes|yeah|confirm|go ahead|do it|sure)\b", re.I)
_pending = {"target": None, "ts": 0.0, "mark": 0}
CONFIRM_WINDOW_S = 90


def _user_said_yes_since(mark: int) -> bool:
    for who, text in shared_state.get("chat_history", [])[mark:]:
        if who == "You" and _YES.search(str(text)):
            return True
    return False

def _title(h) -> str:
    n = user32.GetWindowTextLengthW(h)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, buf, n + 1)
    return buf.value


def _target_hwnd():
    """Активное окно; если это сам Atlas — ближайшее окно под ним."""
    GW_HWNDNEXT = 2
    h = user32.GetForegroundWindow()
    while h and (_title(h).strip().upper() in ("ATLAS", "") or not user32.IsWindowVisible(h)):
        h = user32.GetWindow(h, GW_HWNDNEXT)
    return h


def _uia_text(hwnd) -> str:
    """Сначала содержимое (страница, документ, поля), потом — коротко интерфейс окна."""
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        auto.SetGlobalSearchTimeout(1)
        win = auto.ControlFromHandle(hwnd)
        seen, docs, other = set(), [], []
        for c, _depth in auto.WalkControl(win, maxDepth=14):
            kind = c.ControlTypeName
            if kind not in _TEXT_TYPES:
                continue
            t = (c.Name or "").strip()
            if kind == "DocumentControl":              # страница браузера, документ
                try:
                    t = c.GetTextPattern().DocumentRange.GetText(MAX_CHARS).strip() or t
                except Exception:
                    pass
            elif kind == "EditControl":                # поле ввода — его содержимое
                try:
                    t = c.GetValuePattern().Value.strip() or t
                except Exception:
                    pass
            if len(t) < 2 or t in seen:
                continue
            seen.add(t)
            (docs if kind in ("DocumentControl", "EditControl") else other).append(t)
            if len(seen) > 400:
                break
        content = "\n".join(docs)
        # есть содержимое — интерфейс окна только коротким хвостом
        ui_budget = 300 if content else MAX_CHARS
        ui = "\n".join(other)[:ui_budget]
        return (content + ("\n[interface] " + ui if ui else ""))[:MAX_CHARS]


def _ocr_screen() -> str:
    from PIL import ImageGrab
    from file_search import _ocr_pil, _ocr_lock
    img = ImageGrab.grab()
    with _ocr_lock:
        return _ocr_pil(img)[:MAX_CHARS]


@skill("vision", read_only=True,
       description="Reads what is on the user's screen right now: text, fields and buttons of the "
                   "active window (not Atlas itself). Use for 'what's on my screen', 'what does "
                   "this error say', 'translate this', 'summarize this page'.")
def read_screen() -> str:
    hwnd = _target_hwnd()
    if not hwnd:
        return "No window found."
    title = _title(hwnd)
    text, source = "", "UI Automation"
    try:
        text = _uia_text(hwnd)
    except Exception as e:
        print(f"[screen] UI Automation: {e}")
    if len(text) < 60:                         # «немое» окно — читаем снимок
        try:
            text, source = _ocr_screen(), "OCR"
        except Exception as e:
            print(f"[screen] OCR: {e}")
    print(f"[screen] «{title[:50]}» — {len(text)} символов ({source})")
    return f"Window: {title}\n{text}" if text else f"Window: {title}\n(no readable text)"


@skill("vision",
       description="Clicks a button, link, tab or menu item by its visible name in the active "
                   "window (not Atlas itself). For irreversible actions (delete, pay, send) ask "
                   "the user first and pass confirmed=true only after they agree.",
       params={"target": "Visible name of the element, e.g. 'Save' or 'Настройки'",
               "confirmed": "True only after the user explicitly confirmed an irreversible action"})
def click_on_screen(target: str, confirmed: bool = False) -> str:
    low = target.lower().strip()
    if any(r in low for r in _RISKY):
        fresh = _pending["target"] == low and time.time() - _pending["ts"] < CONFIRM_WINDOW_S
        if not (confirmed and fresh and _user_said_yes_since(_pending["mark"])):
            _pending.update(target=low, ts=time.time(),
                            mark=len(shared_state.get("chat_history", [])))
            print(f"[screen] «{target}» ждёт подтверждения пользователя")
            return (f"'{target}' is irreversible and the user has NOT confirmed. Ask them one "
                    f"short yes/no question and STOP. Call again with confirmed=true only after "
                    f"their spoken answer in the next turn.")
        _pending["target"] = None                   # подтверждение использовано
    hwnd = _target_hwnd()
    if not hwnd:
        return "No window found."
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        auto.SetGlobalSearchTimeout(1)
        win = auto.ControlFromHandle(hwnd)
        best = None
        for c, _depth in auto.WalkControl(win, maxDepth=14):
            name = (c.Name or "").lower()
            if c.ControlTypeName in _CLICKABLE and low in name:
                best = c
                if name == low:                    # точное совпадение — лучше не найти
                    break
        if best is None:
            return f"No clickable element named '{target}' in '{_title(hwnd)}'."
        user32.SetForegroundWindow(hwnd)
        try:
            best.GetInvokePattern().Invoke()
        except Exception:
            best.Click(simulateMove=False)
        return f"Clicked '{best.Name}' in '{_title(hwnd)}'."