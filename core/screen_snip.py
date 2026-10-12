"""
Текст с экрана: Ctrl+Alt+X (или «распознай текст с экрана») → выделяешь мышкой область → Atlas
распознаёт текст (Windows OCR, русский и английский) и кладёт его в буфер обмена. Работает там,
где текст не выделяется: картинки, видео, сканы, скриншоты, защищённые PDF, игры, презентации в созвоне.

    «объясни то, что выделю»  → выделяешь → объяснение
    «переведи то, что выделю» → выделяешь → перевод (русский ↔ английский)
    Esc — отмена.

Снимок сохраняется в Документы/Atlas/Снимки (последние 200). Ничего никуда не отправляется, кроме
текста — модели, если просил объяснить или перевести.

    python -m core.screen_snip pick   → окно выделения, печатает {"box": [x1, y1, x2, y2]} или {}
"""
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEEP = 200
_s = {"last": None}


def _ru(text: str) -> bool:
    return bool(re.search(r"[а-яё]", text or "", re.I))


def folder() -> str:
    base = os.path.expanduser("~/Documents")
    p = os.path.join(base, "Atlas", "Снимки")
    os.makedirs(p, exist_ok=True)
    return p


# --- окно выделения (отдельный процесс: tkinter не мешает окнам Atlas) ---------------------------
def _overlay() -> dict:
    import ctypes
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
    gm = ctypes.windll.user32.GetSystemMetrics
    vx, vy, vw, vh = gm(76), gm(77), gm(78), gm(79)          # все мониторы вместе
    import tkinter as tk
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry(f"{vw}x{vh}+{vx}+{vy}")
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.32)
    root.configure(bg="#000814", cursor="crosshair")
    cv = tk.Canvas(root, bg="#000814", highlightthickness=0, cursor="crosshair")
    cv.pack(fill="both", expand=True)
    hint = "Выдели область с текстом  ·  Esc — отмена"
    cv.create_text(vw // 2 - vx if vx < 0 else vw // 2, 60, text=hint, fill="#7df9ff", font=("Segoe UI", 20, "bold"))
    out, st = {}, {"x": 0, "y": 0, "r": None}

    def down(e):
        st["x"], st["y"] = e.x, e.y
        st["r"] = cv.create_rectangle(e.x, e.y, e.x, e.y, outline="#00e5ff", width=3, fill="#00e5ff")

    def move(e):
        if st["r"]:
            cv.coords(st["r"], st["x"], st["y"], e.x, e.y)

    def up(e):
        x1, x2 = sorted((st["x"], e.x))
        y1, y2 = sorted((st["y"], e.y))
        if x2 - x1 > 6 and y2 - y1 > 6:
            out["box"] = [x1 + vx, y1 + vy, x2 + vx, y2 + vy]
        root.destroy()

    cv.bind("<ButtonPress-1>", down)
    cv.bind("<B1-Motion>", move)
    cv.bind("<ButtonRelease-1>", up)
    root.bind("<Escape>", lambda e: root.destroy())
    root.bind("<Button-3>", lambda e: root.destroy())
    root.after(60000, root.destroy)
    root.focus_force()
    root.mainloop()
    return out


def pick(timeout: float = 70) -> list:
    """Показать окно выделения → [x1, y1, x2, y2] в пикселях экрана или None (отмена)."""
    exe = sys.executable
    if exe.lower().endswith("python.exe") and os.path.exists(exe[:-10] + "pythonw.exe"):
        exe = exe[:-10] + "pythonw.exe"
    try:
        r = subprocess.run([exe, "-m", "core.screen_snip", "pick"], cwd=ROOT, capture_output=True, text=True,
                           timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        line = (r.stdout or "").strip().splitlines()[-1:] or ["{}"]
        box = json.loads(line[0]).get("box")
        if r.returncode and not box:
            print(f"[снимок] окно выделения: {(r.stderr or '')[-300:]}")
        return box
    except Exception as e:
        print(f"[снимок] окно выделения не открылось: {e}")
        return None


def grab(box):
    from PIL import ImageGrab
    return ImageGrab.grab(bbox=tuple(box), all_screens=True)


def ocr(img) -> str:
    from file_search import _ocr_pil, _ocr_lock
    w, h = img.size
    if w < 400 or h < 60:                                 # мелкий текст OCR читает лучше крупным
        k = max(2, int(400 / max(1, w)) + 1) if w < 400 else 2
        img = img.resize((w * k, h * k))
    with _ocr_lock:
        return (_ocr_pil(img) or "").strip()


def _save(img) -> str:
    d = folder()
    path = os.path.join(d, time.strftime("snip_%Y-%m-%d_%H-%M-%S.png"))
    try:
        img.save(path)
        old = sorted(f for f in os.listdir(d) if f.startswith("snip_") and f.endswith(".png"))
        for f in old[:-KEEP]:
            try:
                os.remove(os.path.join(d, f))
            except Exception:
                pass
    except Exception as e:
        print(f"[снимок] не сохранил: {e}")
        return ""
    return path


def _copy(text: str) -> None:
    import pyperclip
    pyperclip.copy(text)              # в историю буфера попадает — «что я копировал» найдёт и это


# --- фразы ---------------------------------------------------------------------------------------
_COPY = re.compile(r"(?:распознай|скопируй|вытащи|достань|возьми|сними|сфоткай)\w*\s+"
                   r"(?:мне\s+)?(?:текст\s+)?(?:с|со|из|на)\s+(?:\w+\s+)?(?:экран\w*|картинк\w*|скрин\w*|снимк\w*|област\w*|"
                   r"изображени\w*|фото\w*|видео)|(?:снимок|скрин\w*) (?:област\w*|части экрана)|"
                   r"(?:распознай|скопируй) (?:то,? )?что (?:я )?выделю|выдели (?:на экране|область)|"
                   r"(?:grab|copy|ocr|read|extract) (?:the )?text (?:from|on|off) (?:the |my )?(?:screen|image|picture|region)|"
                   r"ocr (?:the )?(?:screen|region)|screen snip", re.I)
_EXPLAIN = re.compile(r"объясни\w*\s+(?:мне\s+)?(?:то,? )?(?:что (?:я )?(?:выделю|покажу|обведу)|"
                      r"(?:с|со|на) (?:экран\w*|картинк\w*|скрин\w*|снимк\w*|област\w*))|"
                      r"(?:выдели|покажу|обведу)\w* и объясни|explain (?:what i (?:select|show|mark)|(?:this|the) region)", re.I)
_TRANSLATE = re.compile(r"переведи\w*\s+(?:мне\s+)?(?:то,? )?(?:что (?:я )?(?:выделю|покажу|обведу)|"
                        r"(?:текст )?(?:с|со|на) (?:экран\w*|картинк\w*|скрин\w*|снимк\w*|област\w*|видео))|"
                        r"translate (?:what i (?:select|show|mark)|(?:the )?text (?:on|from) (?:the )?(?:screen|image))", re.I)


def intent(text: str):
    t = " ".join((text or "").replace("ё", "е").split())
    if not t:
        return None
    if _TRANSLATE.search(t):
        return "translate"
    if _EXPLAIN.search(t):
        return "explain"
    if _COPY.search(t):
        return "copy"
    return None


# --- главное ---------------------------------------------------------------------------------------
def snip(action: str = "copy", ru: bool = True, picker=None, grabber=None, reader=None, ask=None, copier=None):
    """→ (короткий ответ вслух, полный текст для чата)."""
    box = (picker or pick)()
    if not box:
        return ("Отменил." if ru else "Cancelled."), ""
    img = (grabber or grab)(box)
    path = _save(img)
    text = (reader or ocr)(img)
    _s["last"] = {"text": text, "path": path, "ts": time.time()}
    if not text:
        name = os.path.basename(path) if path else ""
        s = (f"Текста на снимке не нашёл. Снимок сохранил: {name}." if ru else
             f"No text found. Saved the snip: {name}.") if name else ("Текста не нашёл." if ru else "No text found.")
        return s, ""
    (copier or _copy)(text)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if action == "copy":
        first = lines[0][:70] if lines else ""
        n = len(lines)
        short = (f"Скопировал текст — {n} {'строка' if n == 1 else 'строки' if 2 <= n <= 4 else 'строк'}: «{first}»…"
                 if ru else f"Copied {n} line(s): “{first}”…") if n > 1 else \
            (f"Скопировал: «{first}»." if ru else f"Copied: “{first}”.")
        return short, text
    from core import quick_llm
    q = ask or quick_llm.ask
    if action == "translate":
        to = "English" if _ru(text) else "Russian"
        system = (f"Translate the text the user captured from their screen into {to}. Keep the line breaks and "
                  f"formatting. OCR may have small errors — fix obvious ones. Output only the translation.")
        out = q(system, text, max_tokens=1500)
        try:
            (copier or _copy)(out)
        except Exception:
            pass
        head = "Перевод (уже в буфере обмена):" if ru else "Translation (copied):"
        return (out if len(out) < 300 else out[:280].rsplit(" ", 1)[0] + "…"), f"{head}\n{out}"
    lang = "Russian" if ru else "English"
    system = (f"The user selected a region of their screen; below is the OCR text from it (may contain small "
              f"recognition errors). Explain what it says and what it means for the user, briefly and plainly, "
              f"in {lang}. If it's an error message — say what causes it and how to fix it. If it's a task or "
              f"question — help solve it. No filler.")
    out = q(system, text, max_tokens=900)
    return out, out


def last() -> dict:
    return _s["last"] or {}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "pick":
        try:
            res = _overlay()
        except Exception as e:
            print(f"overlay error: {e}", file=sys.stderr)
            res = {}
        print(json.dumps(res))
        sys.stdout.flush()
