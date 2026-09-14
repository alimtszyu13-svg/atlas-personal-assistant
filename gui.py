import tkinter as tk
from tkinter import font as tkfont
import math
import psutil
from collections import deque
from ui_state import shared_state

THEMES = {
    "dark": {
        "bg": "#05080d",
        "panel_bg": "#0a0f16",
        "text_primary": "#e6f7ff",
        "text_secondary": "#3d5a66",
        "border": "#1a2733",
        "user_msg": "#8fa8b3",
        "atlas_msg": "#00d9ff",
    },
    "light": {
        "bg": "#f0f4f7",
        "panel_bg": "#e4ebf0",
        "text_primary": "#0a1a24",
        "text_secondary": "#5a7a88",
        "border": "#c3d1d9",
        "user_msg": "#3d5a66",
        "atlas_msg": "#0088aa",
    },
}


class AtlasHUD:
    def __init__(self, shared_state: dict):
        self.shared_state = shared_state

        self.root = tk.Tk()
        self.root.title("ATLAS")
        self.root.attributes("-fullscreen", True)
        self.root.bind("<Escape>", lambda e: self.root.attributes("-fullscreen", False))

        self.root.protocol("WM_DELETE_WINDOW", self._minimize_to_tray)

        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()
        self.panel_w = 380
        self.hud_w = self.screen_w - self.panel_w

        self.mono_font = tkfont.Font(family="Consolas", size=10)

        # История для графиков CPU/RAM — храним последние 60 точек (примерно минута при частоте 1 сек)
        self.cpu_history = deque([0] * 60, maxlen=60)
        self.ram_history = deque([0] * 60, maxlen=60)
        self._last_chat_len = 0

        self._build_layout()
        self.angle = 0.0
        self.pulse = 0.0
        self._animate()
        self._sample_metrics()

    # ---------- построение интерфейса ----------

    def _build_layout(self):
        theme = THEMES[self.shared_state["theme"]]
        self.root.configure(bg=theme["bg"])

        self.hud_canvas = tk.Canvas(self.root, width=self.hud_w, height=self.screen_h,
                                     bg=theme["bg"], highlightthickness=0)
        self.hud_canvas.place(x=0, y=0)

        self.status_text = self.hud_canvas.create_text(
            self.hud_w // 2, self.screen_h - 90, text="ONLINE",
            fill=self.shared_state["accent_color"], font=("Consolas", 18, "bold")
        )
        self.subtitle_text = self.hud_canvas.create_text(
            self.hud_w // 2, self.screen_h - 55, text="",
            fill=theme["text_secondary"], font=("Consolas", 10), width=self.hud_w - 80
        )

        self.panel = tk.Frame(self.root, width=self.panel_w, height=self.screen_h, bg=theme["panel_bg"])
        self.panel.place(x=self.hud_w, y=0)

        # --- верх панели: заголовок + переключатель темы ---
        header = tk.Frame(self.panel, bg=theme["panel_bg"])
        header.pack(fill="x", padx=14, pady=(14, 6))

        tk.Label(header, text="ATLAS", bg=theme["panel_bg"], fg=self.shared_state["accent_color"],
                 font=("Consolas", 16, "bold")).pack(side="left")

        self.theme_btn = tk.Button(header, text="☀ / 🌙", command=self._toggle_theme,
                                    bg=theme["border"], fg=theme["text_primary"], relief="flat",
                                    font=("Consolas", 10))
        self.theme_btn.pack(side="right")

        # --- быстрые действия ---
        actions = tk.Frame(self.panel, bg=theme["panel_bg"])
        actions.pack(fill="x", padx=14, pady=6)

        tk.Button(actions, text="Weather", command=lambda: self._queue_command("check the weather"),
                  bg=theme["border"], fg=theme["text_primary"], relief="flat", font=self.mono_font,
                  width=10).pack(side="left", padx=(0, 4))
        tk.Button(actions, text="News", command=lambda: self._queue_command("read me the news"),
                  bg=theme["border"], fg=theme["text_primary"], relief="flat", font=self.mono_font,
                  width=10).pack(side="left", padx=4)
        tk.Button(actions, text="5min Timer", command=lambda: self._queue_command("set a timer for 5 minutes"),
                  bg=theme["border"], fg=theme["text_primary"], relief="flat", font=self.mono_font,
                  width=10).pack(side="left", padx=(4, 0))

        # --- цвет акцента ---
        accent_row = tk.Frame(self.panel, bg=theme["panel_bg"])
        accent_row.pack(fill="x", padx=14, pady=(0, 6))
        tk.Label(accent_row, text="Accent:", bg=theme["panel_bg"], fg=theme["text_secondary"],
                 font=self.mono_font).pack(side="left")
        for color in ("#00d9ff", "#00ffaa", "#ff5566", "#ffaa00", "#aa66ff"):
            tk.Button(accent_row, bg=color, width=2, relief="flat",
                      command=lambda c=color: self._set_accent(c)).pack(side="left", padx=2)

        # --- графики CPU / RAM ---
        self.graph_canvas = tk.Canvas(self.panel, width=self.panel_w - 28, height=90,
                                       bg=theme["bg"], highlightthickness=1,
                                       highlightbackground=theme["border"])
        self.graph_canvas.pack(padx=14, pady=6)

        # --- история диалога ---
        tk.Label(self.panel, text="CONVERSATION", bg=theme["panel_bg"], fg=theme["text_secondary"],
                 font=("Consolas", 9, "bold")).pack(anchor="w", padx=14, pady=(6, 2))

        chat_frame = tk.Frame(self.panel, bg=theme["panel_bg"])
        chat_frame.pack(fill="both", expand=True, padx=14)

        scrollbar = tk.Scrollbar(chat_frame)
        scrollbar.pack(side="right", fill="y")

        self.chat_box = tk.Text(chat_frame, bg=theme["bg"], fg=theme["text_primary"],
                                 font=("Consolas", 9), wrap="word", relief="flat",
                                 yscrollcommand=scrollbar.set, state="disabled")
        self.chat_box.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.chat_box.yview)

        self.chat_box.tag_config("user", foreground=theme["user_msg"])
        self.chat_box.tag_config("atlas", foreground=theme["atlas_msg"])

        # --- ввод текста ---
        input_row = tk.Frame(self.panel, bg=theme["panel_bg"])
        input_row.pack(fill="x", padx=14, pady=10)

        self.text_entry = tk.Entry(input_row, bg=theme["bg"], fg=theme["text_primary"],
                                    insertbackground=theme["text_primary"], font=self.mono_font,
                                    relief="flat")
        self.text_entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.text_entry.bind("<Return>", self._submit_text)

        tk.Button(input_row, text="Send", command=self._submit_text,
                  bg=self.shared_state["accent_color"], fg="#05080d", relief="flat",
                  font=self.mono_font).pack(side="left", padx=(6, 0))

    def _rebuild_layout(self):
        """Пересобирает весь интерфейс при смене темы — проще, чем перекрашивать каждый виджет вручную."""
        for widget in self.root.winfo_children():
            widget.destroy()
        self._build_layout()

    # ---------- обработчики ----------

    def _toggle_theme(self):
        current = self.shared_state["theme"]
        self.shared_state["theme"] = "light" if current == "dark" else "dark"
        self._rebuild_layout()

    def _set_accent(self, color: str):
        self.shared_state["accent_color"] = color
        self._rebuild_layout()

    def _queue_command(self, text: str):
        self.shared_state["manual_queue"].append(text)

    def _submit_text(self, event=None):
        text = self.text_entry.get().strip()
        if text:
            self._queue_command(text)
            self.text_entry.delete(0, "end")

    def _minimize_to_tray(self):
        self.root.withdraw()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()

    def quit_app(self):
        self.shared_state["should_quit"] = True
        self.root.destroy()

    # ---------- отрисовка HUD-круга ----------

    def _draw_idle(self, theme):
        cx, cy = self.hud_w // 2, self.screen_h // 2 - 40
        radius = 100 + 6 * math.sin(self.pulse)
        accent = self.shared_state["accent_color"]
        self.hud_canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                                     outline=accent, width=2, tags="hud")
        inner = radius - 25
        self.hud_canvas.create_oval(cx - inner, cy - inner, cx + inner, cy + inner,
                                     outline=theme["border"], width=1, tags="hud")

    def _draw_listening(self, theme):
        cx, cy = self.hud_w // 2, self.screen_h // 2 - 40
        for i in range(10):
            a = self.angle + i * (360 / 10)
            rad = math.radians(a)
            r1, r2 = 85, 130
            x1, y1 = cx + r1 * math.cos(rad), cy + r1 * math.sin(rad)
            x2, y2 = cx + r2 * math.cos(rad), cy + r2 * math.sin(rad)
            self.hud_canvas.create_line(x1, y1, x2, y2, fill="#00ffaa", width=3, tags="hud")

    def _draw_thinking(self, theme):
        cx, cy = self.hud_w // 2, self.screen_h // 2 - 40
        for i in range(3):
            radius = 60 + i * 30
            start = (self.angle * (i + 1) * 0.6) % 360
            self.hud_canvas.create_arc(cx - radius, cy - radius, cx + radius, cy + radius,
                                        start=start, extent=110, outline="#ffaa00",
                                        width=2, style="arc", tags="hud")

    def _draw_speaking(self, theme):
        cx, cy = self.hud_w // 2, self.screen_h // 2 - 40
        accent = self.shared_state["accent_color"]
        for i in range(-5, 6):
            h = 25 + 45 * abs(math.sin(self.pulse * 1.5 + i * 0.6))
            x = cx + i * 18
            self.hud_canvas.create_line(x, cy - h, x, cy + h, fill=accent, width=5, tags="hud")

    def _draw_graphs(self, theme):
        self.graph_canvas.delete("graph")
        w = self.panel_w - 28
        h = 90

        def draw_line(data, color, y_offset):
            points = []
            for i, val in enumerate(data):
                x = i * (w / len(data))
                y = y_offset - (val / 100) * (h / 2 - 5)
                points.extend([x, y])
            if len(points) >= 4:
                self.graph_canvas.create_line(*points, fill=color, width=2, tags="graph")

        draw_line(self.cpu_history, "#00d9ff", h // 4 + 5)
        draw_line(self.ram_history, "#ff5566", h * 3 // 4)

        self.graph_canvas.create_text(6, 6, anchor="nw", text="CPU", fill="#00d9ff",
                                       font=("Consolas", 8), tags="graph")
        self.graph_canvas.create_text(6, h // 2 + 6, anchor="nw", text="RAM", fill="#ff5566",
                                       font=("Consolas", 8), tags="graph")

    # ---------- обновление истории диалога ----------

    def _update_chat_box(self):
        history = self.shared_state["chat_history"]
        if len(history) == self._last_chat_len:
            return

        self.chat_box.config(state="normal")
        for speaker, text in history[self._last_chat_len:]:
            tag = "user" if speaker == "You" else "atlas"
            self.chat_box.insert("end", f"{speaker}: ", tag)
            self.chat_box.insert("end", f"{text}\n\n")
        self.chat_box.see("end")
        self.chat_box.config(state="disabled")
        self._last_chat_len = len(history)

    # ---------- метрики ----------

    def _sample_metrics(self):
        """Раз в секунду снимает CPU/RAM — отдельный, более редкий цикл, чем анимация."""
        self.cpu_history.append(psutil.cpu_percent(interval=None))
        self.ram_history.append(psutil.virtual_memory().percent)
        self.root.after(1000, self._sample_metrics)

    # ---------- главный цикл анимации ----------

    def _animate(self):
        theme = THEMES[self.shared_state["theme"]]

        self.hud_canvas.delete("hud")

        state = self.shared_state.get("state", "idle")
        text = self.shared_state.get("text", "")

        if state == "listening":
            self._draw_listening(theme)
        elif state == "thinking":
            self._draw_thinking(theme)
        elif state == "speaking":
            self._draw_speaking(theme)
        else:
            self._draw_idle(theme)

        self.hud_canvas.itemconfig(self.status_text, text=state.upper())
        self.hud_canvas.itemconfig(self.subtitle_text, text=text[:150])

        self._draw_graphs(theme)
        self._update_chat_box()

        self.angle = (self.angle + 4) % 360
        self.pulse += 0.15

        if self.shared_state.get("should_quit"):
            self.root.destroy()
            return

        self.root.after(50, self._animate)

    def run(self):
        self.root.mainloop()