import keyboard
import pyperclip
from ui_state import shared_state


def _grab_selected_text() -> str:
    """
    Не пытаемся сами копировать — это вызывало крах при конфликте
    с рендер-движком интерфейса. Пользователь сам копирует текст
    (Ctrl+C), затем сразу нажимает горячую клавишу — мы просто
    читаем буфер обмена как есть.
    """
    return pyperclip.paste().strip()


def _on_ask_hotkey():
    text = _grab_selected_text()
    if text:
        shared_state["manual_queue"].append(f"Explain this: {text}")
    else:
        shared_state["manual_queue"].append("Say: I couldn't detect any selected text, sir.")


def _on_translate_hotkey():
    text = _grab_selected_text()
    if text:
        shared_state["manual_queue"].append(f"Translate this to English: {text}")
    else:
        shared_state["manual_queue"].append("Say: I couldn't detect any selected text, sir.")


def start_selection_hotkeys() -> None:
    keyboard.add_hotkey("ctrl+alt+a", _on_ask_hotkey)
    keyboard.add_hotkey("ctrl+alt+t", _on_translate_hotkey)
    print("(selection hotkeys active: Ctrl+Alt+A = ask, Ctrl+Alt+T = translate)")