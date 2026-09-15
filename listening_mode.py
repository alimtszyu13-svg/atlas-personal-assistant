from ui_state import shared_state


def set_always_listening(enabled: bool) -> str:
    """Toggles always-listening mode — Atlas responds without needing to hear its name."""
    shared_state["always_listening"] = bool(enabled)
    if enabled:
        return "Always-listening mode enabled — I'll respond without needing my name, sir."
    return "Back to normal — say 'Atlas' or use push-to-talk to get my attention."