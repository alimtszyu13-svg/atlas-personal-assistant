from ui_state import shared_state


def set_theme(theme: str) -> str:
    """Переключает тему интерфейса на светлую или тёмную."""
    theme = theme.lower().strip()
    if theme not in ("dark", "light"):
        return "Theme must be either 'dark' or 'light'."
    shared_state["theme"] = theme
    return f"Switched to {theme} mode."