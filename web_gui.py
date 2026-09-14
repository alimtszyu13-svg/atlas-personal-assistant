import webview
import psutil
from ui_state import shared_state


class Api:
    def send_text_command(self, text: str) -> None:
        text = text.strip()
        if text:
            shared_state["manual_queue"].append(text)

    def set_theme(self, theme: str) -> None:
        shared_state["theme"] = theme

    def set_accent(self, color: str) -> None:
        shared_state["accent_color"] = color

    def confirm_quit(self) -> None:
        """
        Вызывается из JS после анимации выключения.
        os._exit(0) завершает процесс мгновенно и безусловно — в отличие от
        window.destroy(), которому для многих бэкендов pywebview нужна
        точная синхронизация с GUI-потоком, что на практике оказалось
        ненадёжным (зависание на середине анимации). Раз вся полезная
        работа (голос уже остановлен, фраза уже сказана) на этот момент
        сделана — жёсткий выход безопасен и предсказуем.
        """
        import os
        os._exit(0)
        
    def get_state(self) -> dict:
        return {
            "state": shared_state.get("state", "idle"),
            "text": shared_state.get("text", ""),
            "theme": shared_state.get("theme", "dark"),
            "accent_color": shared_state.get("accent_color", "#22d3ee"),
            "chat_history": shared_state.get("chat_history", [])[-30:],
            "cpu": psutil.cpu_percent(interval=None),
            "ram": psutil.virtual_memory().percent,
            "should_quit": shared_state.get("should_quit", False),
        }

class WebGUI:
    def __init__(self, shared_state: dict):
        self.shared_state = shared_state
        self.api = Api()
        self.window = None

    def run(self):
        self.window = webview.create_window(
            "ATLAS", "atlas_ui.html", js_api=self.api,
            fullscreen=True, background_color="#050810"
        )
        webview.start()

    def show_window(self):
        if self.window:
            self.window.show()

    def quit_app(self):
        self.shared_state["should_quit"] = True
        if self.window:
            self.window.destroy()
    