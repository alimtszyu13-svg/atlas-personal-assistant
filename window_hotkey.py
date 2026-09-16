import keyboard


def start_window_toggle_hotkey(gui, combo: str = "ctrl+alt+space") -> None:
    """Регистрирует глобальную горячую клавишу для показа/скрытия окна Atlas."""
    keyboard.add_hotkey(combo, gui.toggle_visibility)
    print(f"(window toggle hotkey active: {combo})")