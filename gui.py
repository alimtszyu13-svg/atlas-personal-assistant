import tkinter as tk
import math
import colorsys
import psutil
from ui_state import shared_state

THEMES = {
    "dark": {
        "bg": "#080b10",
        "panel_bg": "#0a0d13",
        "topbar_bg": "#0b0e14",
        "card_bg": "#11161f",
        "card_border": "#1d2531",
        "text_primary": "#eef6fb",
        "text_secondary": "#5a7180",
        "user_bubble": "#161d27",
        "atlas_bubble": "#0e1d26",
        "user_text": "#9fb3bf",
        "atlas_text": "#7fe0ff",
        "divider": "#161c25",
    },
    "light": {
        "bg": "#eef2f6",
        "panel_bg": "#e7edf2",
        "topbar_bg": "#ffffff",
        "card_bg": "#ffffff",
        "card_border": "#dbe4ea",
        "text_primary": "#0f2028",
        "text_secondary": "#6d8494",
        "user_bubble": "#eef2f5",
        "atlas_bubble": "#e4f7fd",
        "user_text": "#4a5f6b",
        "atlas_text": "#0089ad",
        "divider": "#dde5eb",
    },
}


def _rounded_points(x1, y1, x2, y2, r):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def _mix(hex_a, hex_b, t):
    """Линейно смешивает два HEX-цвета — используется для имитации свечения/градиента."""
    a = tuple(int(hex_a[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(hex_b[i:i + 2], 16) for i in (1, 3, 5))
    mixed = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return "#%02x%02x%02x" % mixed


class RoundedCard(tk.Canvas):
    """Карточка со скруглёнными углами — фон рисуется на Canvas,
    а содержимое (обычные виджеты) встраивается через create_window."""
    def __init__(self, parent, bg_color, border_color, radius=18, **kwargs):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, **kwargs)
        self.bg_color = bg_color
        self.border_color = border_color
        self.radius = radius
        self.inner = tk.Frame(self, bg=bg_color)
        self.bind("<Configure>", self._redraw)

    def _redraw(self, event=None):
        self.delete("card_bg")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        self.create_polygon(_rounded_points(1, 1, w - 1, h - 1, self.radius),
                             fill=self.bg_color, outline=self.border_color,
                             width=1, smooth=True, tags="card_bg")
        self.create_window(0, 0, window=self.inner, anchor="nw", width=w, height=h, tags="card_bg")


class PillButton:
    def __init__(self, parent, text, command, bg, fg, hover_bg=None,
                 width=110, height=36, font=("Segoe UI", 10), radius=18):
        self.command = command
        self.bg = bg
        self.hover_bg = hover_bg or bg
        self.canvas = tk.Canvas(parent, width=width, height=height,
                                 bg=parent["bg"], highlightthickness=0)
        self.shape = self.canvas.create_polygon(
            _rounded_points(1, 1, width - 1, height - 1, radius),
            fill=bg, smooth=True, outline=""
        )
        self.label = self.canvas.create_text(width // 2, height // 2, text=text,
                                               fill=fg, font=font)
        for tag in (self.shape, self.label):
            self.canvas.tag_bind(tag, "<Button-1>", lambda e: self.command())
        self.canvas.bind("<Enter>", lambda e: self.canvas.itemconfig(self.shape, fill=self.hover_bg))
        self.canvas.bind("<Leave>", lambda e: self.canvas.itemconfig(self.shape, fill=self.bg))

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)


class AtlasHUD:
    def __init__(self, shared_state: dict):
        self.shared_state = shared_state

        self.root = tk.Tk()
        self.root.title("ATLAS")
        self.root.attributes("-fullscreen", True)
        self.root.bind("<Escape>", lambda e: self.root.attributes("-fullscreen", False))
        self.root.protocol("WM_DELETE_WINDOW", self._minimize_to_tray)

        # Ждём реальной отрисовки окна и берём ФАКТИЧЕСКИЕ размеры —
        # winfo_screenwidth() может не совпадать с реальным рендером при DPI-масштабировании
        self.root.update_idletasks()
        self.screen_w = self.root.winfo_width()
        self.screen_h = self.root.winfo_height()

        self.topbar_h = 72
        self.panel_w = 420
        self.hud_w = self.screen_w - self.panel_w

        self.settings_open = False
        self._last_chat_len = 0
        self._cpu, self._ram = 0, 0

        self._build_layout()
        self.angle = 0.0
        self.pulse = 0.0
        self._animate()
        self._sample_metrics()

    # ---------- layout ----------

    def _build_layout(self):
        theme = THEMES[self.shared_state["theme"]]
        self.root.configure(bg=theme["bg"])
        self._build_topbar(theme)
        self._build_hud(theme)
        self._build_sidebar(theme)
        if self.settings_open:
            self._build_settings_panel(theme)

    def _rebuild(self):
        for w in self.root.winfo_children():
            w.destroy()
        self._build_layout()

    def _build_topbar(self, theme):
        bar = tk.Frame(self.root, bg=theme["topbar_bg"], height=self.topbar_h, width=self.screen_w)
        bar.place(x=0, y=0)
        bar.pack_propagate(False)

        divider = tk.Frame(self.root, bg=theme["divider"], height=1, width=self.screen_w)
        divider.place(x=0, y=self.topbar_h)

        left = tk.Frame(bar, bg=theme["topbar_bg"])
        left.pack(side="left", padx=28)
        tk.Label(left, text="◆", bg=theme["topbar_bg"], fg=self.shared_state["accent_color"],
                 font=("Segoe UI", 15)).pack(side="left", pady=24)
        tk.Label(left, text=" ATLAS", bg=theme["topbar_bg"], fg=theme["text_primary"],
                 font=("Segoe UI Semibold", 16)).pack(side="left", pady=24)

        actions = tk.Frame(bar, bg=theme["topbar_bg"])
        actions.pack(side="left", expand=True, pady=18)

        quick_actions = [
            ("🌤  Weather", "check the weather"),
            ("📰  News", "read me the news"),
            ("⏱  Timer 5m", "set a timer for 5 minutes"),
            ("✉  Mail", "do I have any unread emails"),
        ]
        for label, cmd_text in quick_actions:
            btn = PillButton(actions, label, lambda c=cmd_text: self._queue_command(c),
                              bg=theme["card_bg"], fg=theme["text_primary"],
                              hover_bg=theme["card_border"], width=130, height=36)
            btn.pack(side="left", padx=5)

        right = tk.Frame(bar, bg=theme["topbar_bg"])
        right.pack(side="right", padx=28, pady=18)
        gear = PillButton(right, "⚙  Settings", self._toggle_settings,
                           bg=theme["card_bg"], fg=theme["text_primary"],
                           hover_bg=theme["card_border"], width=120, height=36)
        gear.pack()

    def _build_hud(self, theme):
        h = self.screen_h - self.topbar_h - 1
        self.hud_canvas = tk.Canvas(self.root, width=self.hud_w, height=h,
                                     bg=theme["bg"], highlightthickness=0)
        self.hud_canvas.place(x=0, y=self.topbar_h + 1)

        self.status_text = self.hud_canvas.create_text(
            self.hud_w // 2, h - 120, text="ONLINE",
            fill=self.shared_state["accent_color"], font=("Segoe UI Semibold", 22)
        )
        self.subtitle_text = self.hud_canvas.create_text(
            self.hud_w // 2, h - 78, text="",
            fill=theme["text_secondary"], font=("Segoe UI", 11), width=self.hud_w - 160
        )

    def _build_sidebar(self, theme):
        h = self.screen_h - self.topbar_h - 1
        self.panel = tk.Frame(self.root, width=self.panel_w, height=h, bg=theme["panel_bg"])
        self.panel.place(x=self.hud_w, y=self.topbar_h + 1)

        # --- карточка с индикаторами ---
        gauge_card = RoundedCard(self.panel, theme["card_bg"], theme["card_border"],
                                  radius=18, width=self.panel_w - 40, height=150)
        gauge_card.place(x=20, y=20)
        self.gauge_canvas = tk.Canvas(gauge_card.inner, width=self.panel_w - 40, height=150,
                                       bg=theme["card_bg"], highlightthickness=0)
        self.gauge_canvas.pack()

        # --- карточка диалога ---
        chat_y = 190
        chat_h = h - chat_y - 78
        chat_card = RoundedCard(self.panel, theme["card_bg"], theme["card_border"],
                                 radius=18, width=self.panel_w - 40, height=chat_h)
        chat_card.place(x=20, y=chat_y)

        tk.Label(chat_card.inner, text="CONVERSATION", bg=theme["card_bg"], fg=theme["text_secondary"],
                 font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=16, pady=(14, 6))

        chat_frame = tk.Frame(chat_card.inner, bg=theme["card_bg"])
        chat_frame.pack(fill="both", expand=True, padx=16, pady=(0, 14))

        scrollbar = tk.Scrollbar(chat_frame)
        scrollbar.pack(side="right", fill="y")

        self.chat_box = tk.Text(chat_frame, bg=theme["card_bg"], fg=theme["text_primary"],
                                 font=("Segoe UI", 10), wrap="word", relief="flat", bd=0,
                                 yscrollcommand=scrollbar.set, state="disabled",
                                 spacing1=2, spacing3=12)
        self.chat_box.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.chat_box.yview)

        self.chat_box.tag_config("user", foreground=theme["user_text"], background=theme["user_bubble"],
                                  lmargin1=10, lmargin2=10, rmargin=10, spacing1=8, spacing3=8)
        self.chat_box.tag_config("atlas", foreground=theme["atlas_text"], background=theme["atlas_bubble"],
                                  lmargin1=10, lmargin2=10, rmargin=10, spacing1=8, spacing3=8)
        self.chat_box.tag_config("gap", background=theme["card_bg"])
        self._last_chat_len = 0

        # --- ввод текста ---
        input_row = tk.Frame(self.panel, bg=theme["panel_bg"])
        input_row.place(x=20, y=h - 58, width=self.panel_w - 40, height=48)

        entry_card = RoundedCard(input_row, theme["card_bg"], theme["card_border"],
                                  radius=16, width=self.panel_w - 40 - 78, height=48)
        entry_card.pack(side="left")
        self.text_entry = tk.Entry(entry_card.inner, bg=theme["card_bg"], fg=theme["text_primary"],
                                    insertbackground=theme["text_primary"], font=("Segoe UI", 10),
                                    relief="flat", bd=0)
        self.text_entry.pack(fill="both", expand=True, padx=14, pady=12)
        self.text_entry.bind("<Return>", self._submit_text)

        send_btn = PillButton(input_row, "Send", self._submit_text,
                               bg=self.shared_state["accent_color"], fg="#05080d",
                               hover_bg=theme["text_secondary"], width=70, height=48, radius=16)
        send_btn.pack(side="left", padx=(8, 0))

    def _build_settings_panel(self, theme):
        w, h = 300, 220
        x = self.screen_w - w - 28
        y = self.topbar_h + 14

        card = RoundedCard(self.root, theme["card_bg"], theme["card_border"], radius=18, width=w, height=h)
        card.place(x=x, y=y)
        panel = card.inner

        tk.Label(panel, text="SETTINGS", bg=theme["card_bg"], fg=theme["text_secondary"],
                 font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=18, pady=(16, 12))

        tk.Label(panel, text="Theme", bg=theme["card_bg"], fg=theme["text_primary"],
                 font=("Segoe UI", 9)).pack(anchor="w", padx=18)
        theme_row = tk.Frame(panel, bg=theme["card_bg"])
        theme_row.pack(anchor="w", padx=18, pady=(6, 16))

        for label, val in (("Dark", "dark"), ("Light", "light")):
            selected = self.shared_state["theme"] == val
            btn = PillButton(theme_row, label, lambda v=val: self._set_theme(v),
                              bg=self.shared_state["accent_color"] if selected else theme["bg"],
                              fg="#05080d" if selected else theme["text_primary"],
                              hover_bg=theme["card_border"], width=95, height=32)
            btn.pack(side="left", padx=(0, 8))

        tk.Label(panel, text="Accent color", bg=theme["card_bg"], fg=theme["text_primary"],
                 font=("Segoe UI", 9)).pack(anchor="w", padx=18)
        accent_row = tk.Frame(panel, bg=theme["card_bg"])
        accent_row.pack(anchor="w", padx=18, pady=(8, 10))

        for color in ("#00d9ff", "#00ffaa", "#ff5566", "#ffaa00", "#aa66ff"):
            swatch = tk.Canvas(accent_row, width=30, height=30, bg=theme["card_bg"], highlightthickness=0)
            ring = theme["text_primary"] if color == self.shared_state["accent_color"] else theme["card_bg"]
            swatch.create_oval(3, 3, 27, 27, fill=color, outline=ring, width=2)
            swatch.bind("<Button-1>", lambda e, c=color: self._set_accent(c))
            swatch.pack(side="left", padx=4)

    # ---------- handlers ----------

    def _toggle_settings(self):
        self.settings_open = not self.settings_open
        self._rebuild()

    def _set_theme(self, value):
        self.shared_state["theme"] = value
        self._rebuild()

    def _set_accent(self, color):
        self.shared_state["accent_color"] = color
        self._rebuild()

    def _queue_command(self, text):
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

    # ---------- HUD orb with glow ----------

    def _draw_idle(self, theme, cx, cy):
        accent = self.shared_state["accent_color"]
        base_r = 100 + 5 * math.sin(self.pulse)

        # Свечение — кольца, плавно уходящие в цвет фона (имитация alpha-прозрачности)
        for i in range(5, 0, -1):
            glow_r = base_r + i * 14
            glow_color = _mix(accent, theme["bg"], i / 5 * 0.92)
            self.hud_canvas.create_oval(cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r,
                                         outline=glow_color, width=2, tags="hud")

        self.hud_canvas.create_oval(cx - base_r, cy - base_r, cx + base_r, cy + base_r,
                                     outline=accent, width=2, tags="hud")
        inner = base_r - 24
        self.hud_canvas.create_oval(cx - inner, cy - inner, cx + inner, cy + inner,
                                     outline=theme["card_border"], width=1, tags="hud")

    def _draw_listening(self, theme, cx, cy):
        for i in range(14):
            a = self.angle + i * (360 / 14)
            rad = math.radians(a)
            r1, r2 = 92, 142
            x1, y1 = cx + r1 * math.cos(rad), cy + r1 * math.sin(rad)
            x2, y2 = cx + r2 * math.cos(rad), cy + r2 * math.sin(rad)
            self.hud_canvas.create_line(x1, y1, x2, y2, fill="#00ffaa", width=3,
                                         capstyle="round", tags="hud")

    def _draw_thinking(self, theme, cx, cy):
        for i in range(3):
            radius = 66 + i * 32
            start = (self.angle * (i + 1) * 0.6) % 360
            self.hud_canvas.create_arc(cx - radius, cy - radius, cx + radius, cy + radius,
                                        start=start, extent=115, outline="#ffaa00",
                                        width=3, style="arc", tags="hud")

    def _draw_speaking(self, theme, cx, cy):
        accent = self.shared_state["accent_color"]
        for i in range(-6, 7):
            h = 25 + 52 * abs(math.sin(self.pulse * 1.5 + i * 0.55))
            x = cx + i * 17
            self.hud_canvas.create_line(x, cy - h, x, cy + h, fill=accent, width=6,
                                         capstyle="round", tags="hud")

    # ---------- gauges ----------

    def _draw_gauges(self, theme):
        self.gauge_canvas.delete("gauge")
        w = self.panel_w - 40
        r = 44
        centers = [(w * 0.28, 68), (w * 0.72, 68)]
        values = [self._cpu, self._ram]
        labels = ["CPU", "RAM"]
        colors = [self.shared_state["accent_color"], "#ff5566"]

        for (cx, cy), val, label, color in zip(centers, values, labels, colors):
            self.gauge_canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                           outline=theme["card_border"], width=9, tags="gauge")
            extent = -3.6 * val
            self.gauge_canvas.create_arc(cx - r, cy - r, cx + r, cy + r,
                                          start=90, extent=extent, outline=color,
                                          width=9, style="arc", tags="gauge")
            self.gauge_canvas.create_text(cx, cy - 4, text=f"{val:.0f}%", fill=theme["text_primary"],
                                           font=("Segoe UI Semibold", 14), tags="gauge")
            self.gauge_canvas.create_text(cx, cy + r + 18, text=label, fill=theme["text_secondary"],
                                           font=("Segoe UI", 9), tags="gauge")

    # ---------- chat ----------

    def _update_chat_box(self):
        history = self.shared_state["chat_history"]
        if len(history) == self._last_chat_len:
            return
        self.chat_box.config(state="normal")
        for speaker, text in history[self._last_chat_len:]:
            tag = "user" if speaker == "You" else "atlas"
            self.chat_box.insert("end", f"{speaker}\n{text}\n", tag)
            self.chat_box.insert("end", "\n", "gap")
        self.chat_box.see("end")
        self.chat_box.config(state="disabled")
        self._last_chat_len = len(history)

    # ---------- metrics ----------

    def _sample_metrics(self):
        self._cpu = psutil.cpu_percent(interval=None)
        self._ram = psutil.virtual_memory().percent
        self.root.after(1000, self._sample_metrics)

    # ---------- main loop ----------

    def _animate(self):
        theme = THEMES[self.shared_state["theme"]]
        cx, cy = self.hud_w // 2, (self.screen_h - self.topbar_h) // 2 - 40

        self.hud_canvas.delete("hud")
        state = self.shared_state.get("state", "idle")
        text = self.shared_state.get("text", "")

        if state == "listening":
            self._draw_listening(theme, cx, cy)
        elif state == "thinking":
            self._draw_thinking(theme, cx, cy)
        elif state == "speaking":
            self._draw_speaking(theme, cx, cy)
        else:
            self._draw_idle(theme, cx, cy)

        self.hud_canvas.itemconfig(self.status_text, text=state.upper())
        self.hud_canvas.itemconfig(self.subtitle_text, text=text[:150])

        self._draw_gauges(theme)
        self._update_chat_box()

        self.angle = (self.angle + 4) % 360
        self.pulse += 0.15

        if self.shared_state.get("should_quit"):
            self.root.destroy()
            return

        self.root.after(50, self._animate)

    def run(self):
        self.root.mainloop()