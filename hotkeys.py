import keyboard
from voice import trigger_push_to_talk


def start_push_to_talk_hotkey(combo: str = "ctrl+space") -> None:
    """Регистрирует глобальную горячую клавишу — работает из любого окна, не только Atlas."""
    keyboard.add_hotkey(combo, trigger_push_to_talk)
    print(f"(push-to-talk hotkey active: {combo})")