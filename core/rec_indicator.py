"""
Огонёк записи — маленькая плашка поверх всех окон, пока Atlas пишет:

    ● REC 05:12   🎤  📷  ■

  • зелёная точка пульсирует, пока идёт запись; жёлтая — пока готовится конспект;
  • 🎤 — микрофон вкл/выкл, 📷 — веб-камера вкл/выкл (прямо во время записи), ■ — остановить;
  • плашку можно перетащить куда удобно — место запоминается (rec_indicator.json);
  • нажать на «REC» — откроется окно «Записи».
Нет на панели задач. Свой поток с tkinter (входит в Python для Windows). Нет tkinter — просто без огонька.
"""
import json
import os
import threading

W, H = 236, 34                             # размер в «пикселях 100 %», дальше умножается на масштаб экрана
MARGIN_X, MARGIN_Y = 18, 46
KEY = "#010203"                            # этот цвет окна становится прозрачным
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POS_FILE = os.path.join(ROOT, "rec_indicator.json")
BUTTONS = {"mic": (122, 152), "cam": (158, 188), "stop": (196, 228)}     # x-диапазоны кнопок
DRAG_PX = 4                                # сдвинул мышь дальше — это перетаскивание, а не нажатие

_s = {"thread": None, "root": None, "want": "hidden", "on_click": None, "info": None,
      "on_mic": None, "on_cam": None, "on_stop": None}


def _fmt(sec: int) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def hit(x: float, y: float) -> str:
    """Куда нажали (координаты плашки при 100 %): open | mic | cam | stop | ''."""
    if not (0 <= y <= H):
        return ""
    for name, (a, b) in BUTTONS.items():
        if a <= x <= b:
            return name
    return "open" if 0 <= x < BUTTONS["mic"][0] else ""


def clamp(x: int, y: int, w: int, h: int, sw: int, sh: int):
    """Не дать унести плашку за край экрана."""
    return max(0, min(int(x), sw - w)), max(0, min(int(y), sh - h))


def load_pos():
    try:
        with open(POS_FILE, encoding="utf-8") as f:
            p = json.load(f)
        return int(p["x"]), int(p["y"])
    except Exception:
        return None


def save_pos(x: int, y: int) -> None:
    try:
        with open(POS_FILE, "w", encoding="utf-8") as f:
            json.dump({"x": int(x), "y": int(y)}, f)
    except Exception:
        pass


def _call(name: str, *args) -> None:
    f = _s.get(name)
    if f:
        threading.Thread(target=f, args=args, daemon=True).start()


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
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    pos = load_pos()
    x, y = clamp(*(pos or (sw - w - int(MARGIN_X * sc), int(MARGIN_Y * sc))), w, h, sw, sh)
    root.geometry(f"{w}x{h}+{x}+{y}")
    c = tk.Canvas(root, width=w, height=h, bg=bg, highlightthickness=0)
    c.pack()

    def pill(x0, y0, x1, y1, r, **kw):
        c.create_oval(x0, y0, x0 + 2 * r, y1, **kw)
        c.create_oval(x1 - 2 * r, y0, x1, y1, **kw)
        c.create_rectangle(x0 + r, y0, x1 - r, y1, **kw)

    pill(1, 1, W - 1, H - 1, (H - 2) // 2, fill="#0B0F14", outline="")
    glow = c.create_oval(9, 7, 27, 25, fill="#0E3B22", outline="")
    dot = c.create_oval(12, 10, 24, 22, fill="#22E06B", outline="")
    label = c.create_text(34, H // 2, anchor="w", fill="#E8FFF0", font=("Segoe UI Semibold", 10), text="REC 00:00")
    items = {}                                    # кнопки: подложка + значок + косая черта «выключено»
    for name, (a, b) in BUTTONS.items():
        cx, cy, r = (a + b) / 2, H / 2, 12
        items[name + "_bg"] = c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#16202C", outline="")
    mx, my = sum(BUTTONS["mic"]) / 2, H / 2
    items["mic"] = [c.create_rectangle(mx - 3, my - 8, mx + 3, my + 2, fill="#E8FFF0", outline=""),
                    c.create_oval(mx - 3, my - 10, mx + 3, my - 5, fill="#E8FFF0", outline=""),
                    c.create_oval(mx - 3, my - 1, mx + 3, my + 4, fill="#E8FFF0", outline=""),
                    c.create_arc(mx - 6, my - 6, mx + 6, my + 6, start=200, extent=140, style="arc",
                                 outline="#E8FFF0", width=1.5),
                    c.create_line(mx, my + 6, mx, my + 9, fill="#E8FFF0", width=1.5)]
    kx, ky = sum(BUTTONS["cam"]) / 2, H / 2
    items["cam"] = [c.create_rectangle(kx - 8, ky - 5, kx + 3, ky + 5, fill="#E8FFF0", outline=""),
                    c.create_polygon(kx + 3, ky - 1, kx + 8, ky - 5, kx + 8, ky + 5, kx + 3, ky + 1,
                                     fill="#E8FFF0", outline="")]
    for name in ("mic", "cam"):
        cx = sum(BUTTONS[name]) / 2
        items[name + "_off"] = c.create_line(cx - 8, H / 2 + 8, cx + 8, H / 2 - 8, fill="#FF6B6B", width=2)
    sx, sy = sum(BUTTONS["stop"]) / 2, H / 2
    items["stop"] = c.create_rectangle(sx - 5, sy - 5, sx + 5, sy + 5, fill="#FF6B6B", outline="")
    if sc != 1.0:
        c.scale("all", 0, 0, sc, sc)                            # шрифт в пунктах масштабируется сам

    drag = {"x": 0, "y": 0, "moved": False, "wx": 0, "wy": 0}
    state = {"mic": False, "cam": False}

    def press(e):
        drag.update(x=e.x_root, y=e.y_root, moved=False, wx=root.winfo_x(), wy=root.winfo_y())

    def motion(e):
        dx, dy = e.x_root - drag["x"], e.y_root - drag["y"]
        if abs(dx) > DRAG_PX or abs(dy) > DRAG_PX:
            drag["moved"] = True
        if drag["moved"]:
            nx, ny = clamp(drag["wx"] + dx, drag["wy"] + dy, w, h, sw, sh)
            root.geometry(f"+{nx}+{ny}")

    def release(e):
        if drag["moved"]:
            save_pos(root.winfo_x(), root.winfo_y())
            return
        what = hit(e.x / sc, e.y / sc)
        if what == "open":
            _call("on_click")
        elif what == "mic":
            _call("on_mic", not state["mic"])
        elif what == "cam":
            _call("on_cam", not state["cam"])
        elif what == "stop" and _s["want"] == "recording":
            _call("on_stop")
    c.bind("<ButtonPress-1>", press)
    c.bind("<B1-Motion>", motion)
    c.bind("<ButtonRelease-1>", release)
    c.bind("<Motion>", lambda e: c.configure(cursor="hand2" if hit(e.x / sc, e.y / sc) else "fleur"))
    root.withdraw()
    shown = {"v": False}

    def paint_toggle(name, on, busy):
        c.itemconfigure(items[name + "_bg"], fill="#1D3B2A" if on and not busy else "#16202C")
        color = "#E8FFF0" if on else "#6B7C90"
        for it in items[name]:
            c.itemconfigure(it, **({"outline": color} if c.type(it) == "arc" else {"fill": color}))
        c.itemconfigure(items[name + "_off"], state="hidden" if on else "normal")

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
            try:
                info = (_s["info"] or (lambda: {}))() or {}
            except Exception:
                info = {}
            busy = want == "processing"
            state["mic"], state["cam"] = bool(info.get("mic")), bool(info.get("camera"))
            on = (i[0] // 3) % 2 == 0
            color = ("#F5C542" if on else "#8A6D1E") if busy else ("#22E06B" if on else "#13843F")
            c.itemconfigure(dot, fill=color)
            c.itemconfigure(glow, fill="#3B3110" if busy else ("#0E3B22" if on else "#0B0F14"))
            c.itemconfigure(label, text="КОНСПЕКТ…" if busy else f"REC {_fmt(info.get('seconds', 0))}")
            paint_toggle("mic", state["mic"], busy)
            paint_toggle("cam", state["cam"], busy)
            for k in ("stop", "stop_bg"):
                c.itemconfigure(items[k], state="hidden" if busy else "normal")
            if i[0] % 20 == 0:
                root.attributes("-topmost", True)       # другое окно «поверх всех» не должно его закрыть
        root.after(200, tick)
    tick()
    root.mainloop()


def start(on_click=None, info=None, on_mic=None, on_cam=None, on_stop=None) -> bool:
    """Запустить поток огонька (один раз). info() → {"seconds", "mic", "camera"}."""
    for k, v in (("on_click", on_click), ("info", info), ("on_mic", on_mic), ("on_cam", on_cam), ("on_stop", on_stop)):
        if v:
            _s[k] = v
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
