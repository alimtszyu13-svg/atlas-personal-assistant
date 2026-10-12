"""
Карточка-подсказка справа сверху (для суфлёра): отдельный процесс, команды — строки JSON на входе.

    {"title": "вопрос", "text": "ответ", "s": 35}   → показать (через s секунд сама гаснет)
    {"quit": true}                                  → закрыть

Не видна при показе экрана (Windows: исключение из захвата), нет на панели задач, клик — скрыть.
"""
import json
import sys
import threading

W = 430
BG, FG, DIM, ACC = "#0b1420", "#e8f6ff", "#7aa7c7", "#28e0ff"


def main() -> None:
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.94)
    except Exception:
        pass
    scale = root.winfo_fpixels("1i") / 96
    w = int(W * scale)
    frame = tk.Frame(root, bg=BG, highlightthickness=1, highlightbackground=ACC)
    frame.pack(fill="both", expand=True)
    head = tk.Label(frame, text="", bg=BG, fg=DIM, font=("Segoe UI", 9), wraplength=w - 24, justify="left", anchor="w")
    head.pack(fill="x", padx=12, pady=(9, 2))
    body = tk.Label(frame, text="", bg=BG, fg=FG, font=("Segoe UI Semibold", 13), wraplength=w - 24, justify="left",
                    anchor="w")
    body.pack(fill="x", padx=12, pady=(0, 11))
    st = {"after": None}

    def hide(_e=None):
        root.withdraw()

    for wd in (frame, head, body):
        wd.bind("<Button-1>", hide)

    def show(msg):
        head.config(text="❓ " + msg.get("title", ""))
        body.config(text=msg.get("text", ""))
        root.update_idletasks()
        h = frame.winfo_reqheight()
        sw = root.winfo_screenwidth()
        root.geometry(f"{w}x{h}+{sw - w - int(18 * scale)}+{int(90 * scale)}")
        root.deiconify()
        root.lift()
        try:
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)      # не попадает в демонстрацию экрана
            ex = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex | 0x80 | 0x08000000)  # без панели задач и фокуса
        except Exception:
            pass
        if st["after"]:
            root.after_cancel(st["after"])
        st["after"] = root.after(int(msg.get("s", 35)) * 1000, hide)

    def reader():
        for line in sys.stdin:
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("quit"):
                root.after(0, root.destroy)
                return
            root.after(0, show, msg)
        root.after(0, root.destroy)

    threading.Thread(target=reader, daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()
