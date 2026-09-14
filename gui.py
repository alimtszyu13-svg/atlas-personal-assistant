import tkinter as tk
import math


class AtlasHUD:
    def __init__(self, shared_state: dict):
        self.shared_state = shared_state

        self.root = tk.Tk()
        self.root.title("ATLAS")
        self.root.protocol("WM_DELETE_WINDOW", self._minimize_to_tray)
        self.root.configure(bg="#05080d")
        self.root.geometry("500x560")
        self.root.resizable(False, False)

        self.canvas = tk.Canvas(self.root, width=500, height=500, bg="#05080d", highlightthickness=0)
        self.canvas.pack()

        self.status_text = self.canvas.create_text(
            250, 470, text="ONLINE", fill="#00d9ff", font=("Consolas", 16, "bold")
        )
        self.subtitle_text = self.canvas.create_text(
            250, 500, text="", fill="#3d5a66", font=("Consolas", 9), width=460
        )

        self.angle = 0.0
        self.pulse = 0.0
        self._animate()

    def _draw_idle(self):
        cx, cy = 250, 230
        radius = 100 + 6 * math.sin(self.pulse)
        self.canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                                 outline="#00d9ff", width=2, tags="hud")
        inner = radius - 25
        self.canvas.create_oval(cx - inner, cy - inner, cx + inner, cy + inner,
                                 outline="#0a4a5c", width=1, tags="hud")

    def _draw_listening(self):
        cx, cy = 250, 230
        for i in range(10):
            a = self.angle + i * (360 / 10)
            rad = math.radians(a)
            r1, r2 = 85, 130
            x1, y1 = cx + r1 * math.cos(rad), cy + r1 * math.sin(rad)
            x2, y2 = cx + r2 * math.cos(rad), cy + r2 * math.sin(rad)
            self.canvas.create_line(x1, y1, x2, y2, fill="#00ffaa", width=3, tags="hud")

    def _draw_thinking(self):
        cx, cy = 250, 230
        for i in range(3):
            radius = 60 + i * 30
            start = (self.angle * (i + 1) * 0.6) % 360
            self.canvas.create_arc(cx - radius, cy - radius, cx + radius, cy + radius,
                                    start=start, extent=110, outline="#ffaa00",
                                    width=2, style="arc", tags="hud")

    def _draw_speaking(self):
        cx, cy = 250, 230
        for i in range(-5, 6):
            h = 25 + 45 * abs(math.sin(self.pulse * 1.5 + i * 0.6))
            x = cx + i * 18
            self.canvas.create_line(x, cy - h, x, cy + h, fill="#00d9ff", width=5, tags="hud")

    def _animate(self):
        self.canvas.delete("hud")

        state = self.shared_state.get("state", "idle")
        text = self.shared_state.get("text", "")

        if state == "listening":
            self._draw_listening()
        elif state == "thinking":
            self._draw_thinking()
        elif state == "speaking":
            self._draw_speaking()
        else:
            self._draw_idle()

        self.canvas.itemconfig(self.status_text, text=state.upper())
        self.canvas.itemconfig(self.subtitle_text, text=text[:120])

        self.angle = (self.angle + 4) % 360
        self.pulse += 0.15

        if self.shared_state.get("should_quit"):
            self.root.destroy()
            return

        self.root.after(50, self._animate)

    def run(self):
        self.root.mainloop()

    def _minimize_to_tray(self):
        """Вместо закрытия окна — прячем его. Программа продолжает работать в трее."""
        self.root.withdraw()

    def show_window(self):
        """Возвращает окно из трея на экран."""
        self.root.deiconify()
        self.root.lift()

    def quit_app(self):
        """Полное закрытие — вызывается только из меню трея 'Quit'."""
        self.shared_state["should_quit"] = True
        self.root.destroy()