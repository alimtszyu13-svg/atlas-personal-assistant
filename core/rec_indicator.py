"""
Зелёный огонёк записи: пока Atlas пишет созвон или видео, справа сверху (чуть ниже угла, чтобы не закрывать
кнопки окон) горит пульсирующая зелёная точка с таймером. Поверх всех окон, на панели задач не появляется.
Нажать — откроется окно «Записи». Пока конспект готовится — точка жёлтая.

Свой поток с tkinter (входит в Python для Windows). Нет tkinter — просто без огонька.
"""
import threading
import time

W, H = 112, 30
MARGIN_X, MARGIN_Y = 18, 46
KEY = "#010203"                          # этот цвет окна становится прозрачным

_s = {"thread": None, "root": None, "want": "hidden", "on_click": None, "info": None}


def _fmt(sec: int) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def _run() -> None:
    import tkinter as tk
    root = tk.Tk()
    _s["root"] = root
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-transparentcolor", KEY)
        bg = KEY
    except tk.TclError:
        bg = "#0B0F14"
    root.configure(bg=bg)
    try:
        sc = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)     # масштаб экрана 125–200 %
    except Exception:
        sc = 1.0
    w, h = int(W * sc), int(H * sc)
    sw = root.winfo_screenwidth()
    root.geometry(f"{w}x{h}+{sw - w - int(MARGIN_X * sc)}+{int(MARGIN_Y * sc)}")
    c = tk.Canvas(root, width=w, height=h, bg=bg, highlightthickness=0, cursor="hand2")
    c.pack()

    def pill(x0, y0, x1, y1, r, **kw):
        c.create_oval(x0, y0, x0 + 2 * r, y1, **kw)
        c.create_oval(x1 - 2 * r, y0, x1, y1, **kw)
        c.create_rectangle(x0 + r, y0, x1 - r, y1, **kw)

    pill(1, 1, W - 1, H - 1, (H - 2) // 2, fill="#0B0F14", outline="")
    glow = c.create_oval(8, 6, 26, 24, fill="#0E3B22", outline="")
    dot = c.create_oval(11, 9, 23, 21, fill="#22E06B", outline="")
    label = c.create_text(34, H // 2, anchor="w", fill="#E8FFF0", font=("Segoe UI Semibold", 10), text="REC 00:00")
    if sc != 1.0:
        c.scale("all", 0, 0, sc, sc)                            # шрифт в пунктах масштабируется сам

    def click(_e=None):
        f = _s["on_click"]
        if f:
            threading.Thread(target=f, daemon=True).start()
    c.bind("<Button-1>", click)
    root.withdraw()
    shown = {"v": False}

    def tick(i=[0]):
        i[0] += 1
        want = _s["want"]
        if want == "hidden":
            if shown["v"]:
                root.withdraw()
                shown["v"] = False
        else:
            if not shown["v"]:
                root.deiconify()
                root.attributes("-topmost", True)
                shown["v"] = True
            info = (_s["info"] or (lambda: {}))() or {}
            busy = want == "processing"
            on = (i[0] // 3) % 2 == 0
            color = ("#F5C542" if on else "#8A6D1E") if busy else ("#22E06B" if on else "#13843F")
            c.itemconfigure(dot, fill=color)
            c.itemconfigure(glow, fill="#3B3110" if busy else ("#0E3B22" if on else "#0B0F14"))
            c.itemconfigure(label, text="КОНСПЕКТ…" if busy else f"REC {_fmt(info.get('seconds', 0))}")
            if i[0] % 20 == 0:
                root.attributes("-topmost", True)       # другое окно «поверх всех» не должно его закрыть
        root.after(200, tick)
    tick()
    root.mainloop()


def start(on_click=None, info=None) -> bool:
    """Запустить поток огонька (один раз). on_click — открыть окно записей; info() → {"seconds": …}."""
    _s["on_click"] = on_click or _s["on_click"]
    _s["info"] = info or _s["info"]
    if _s["thread"] and _s["thread"].is_alive():
        return True
    try:
        import tkinter  # noqa: F401
    except Exception as e:
        print(f"[записи] огонька не будет: нет tkinter ({e})")
        return False
    _s["thread"] = threading.Thread(target=_run, daemon=True, name="rec-indicator")
    _s["thread"].start()
    return True


def show(phase: str) -> None:
    """recording — зелёный, processing — жёлтый, иначе — спрятать."""
    _s["want"] = phase if phase in ("recording", "processing") else "hidden"
