import keyboard
import pyperclip
import time
from ui_state import shared_state


def _grab_selected_text() -> str:
    """
    Симулирует Ctrl+C, чтобы скопировать текущее выделение в любом
    приложении, потом читает буфер обмена. Небольшая задержка нужна,
    чтобы ОС успела обработать копирование, прежде чем мы читаем буфер.
    """
    keyboard.send("ctrl+c")
    time.sleep(0.15)
    return pyperclip.paste().strip()


def _on_ask_hotkey():
    text = _grab_selected_text()
    if text:
        shared_state["manual_queue"].append(f"Explain this: {text}")


def _on_translate_hotkey():
    text = _grab_selected_text()
    if text:
        shared_state["manual_queue"].append(f"Translate this to English: {text}")


def start_selection_hotkeys():
    """Регистрирует глобальные горячие клавиши — работают из любого окна, не только Atlas."""
    keyboard.add_hotkey("ctrl+alt+a", _on_ask_hotkey)
    keyboard.add_hotkey("ctrl+alt+t", _on_translate_hotkey)