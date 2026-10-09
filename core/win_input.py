"""
Ввод в чужие окна Windows: вставить текст, нажать клавиши, стереть, вернуть фокус.

Текст вставляется через буфер обмена (Ctrl+V): так работают русские буквы, эмодзи и любые программы —
Word, Telegram, браузер. Прежнее содержимое буфера сразу возвращается, а история буфера Atlas
свою вставку не записывает.
"""
import ctypes
import time

_u = None


def _user32():
    global _u
    if _u is None:
        _u = ctypes.windll.user32
    return _u


def foreground() -> int:
    try:
        return int(_user32().GetForegroundWindow() or 0)
    except Exception:
        return 0


def title_of(hwnd: int) -> str:
    try:
        u = _user32()
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value
    except Exception:
        return ""


def is_atlas(hwnd: int) -> bool:
    """Окно самого Atlas (интерфейс, мини-окно, консоль python) — туда не печатаем."""
    try:
        import psutil
        import ctypes.wintypes as wt
        pid = wt.DWORD()
        _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = psutil.Process(pid.value).name().lower()
    except Exception:
        return False
    t = title_of(hwnd).strip().lower()
    return exe in ("python.exe", "pythonw.exe") or t in ("atlas", "atlas mini") or \
        (exe in ("windowsterminal.exe", "cmd.exe", "powershell.exe") and "atlas" in t)


def focus(hwnd: int) -> bool:
    """Вернуть фокус окну (Windows не всегда разрешает — тогда пробуем через Alt)."""
    if not hwnd:
        return False
    u = _user32()
    if foreground() == hwnd:
        return True
    try:
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)                      # SW_RESTORE
        u.keybd_event(0x12, 0, 0, 0)                   # Alt вниз-вверх: Windows отдаёт фокус
        u.keybd_event(0x12, 0, 2, 0)
        u.SetForegroundWindow(hwnd)
        time.sleep(0.08)
    except Exception:
        pass
    return foreground() == hwnd


def minimize(hwnd: int) -> None:
    try:
        _user32().ShowWindow(hwnd, 6)                  # SW_MINIMIZE
    except Exception:
        pass


def keys(combo: str, times: int = 1) -> None:
    import keyboard
    for i in range(times):
        keyboard.send(combo)
        if i % 40 == 39:
            time.sleep(0.02)


def paste(text: str) -> None:
    """Вставить текст в активное окно через буфер, буфер вернуть как был."""
    import pyperclip
    try:
        from core import clipboard_history
        clipboard_history.ignore(text)
    except Exception:
        pass
    try:
        before = pyperclip.paste()
    except Exception:
        before = None
    pyperclip.copy(text)
    time.sleep(0.03)
    keys("ctrl+v")
    time.sleep(0.15)                                   # программа успевает забрать текст
    if before is not None:
        try:
            from core import clipboard_history
            clipboard_history.ignore(before)
        except Exception:
            pass
        pyperclip.copy(before)
