import keyboard


def play_pause_media() -> str:
    """Toggles play/pause on whatever media player is currently active (Spotify, YouTube, VLC, etc.)."""
    keyboard.send("play/pause media")
    return "Toggled play and pause."


def next_track() -> str:
    """Skips to the next track in the active media player."""
    keyboard.send("next track")
    return "Skipped to the next track."


def previous_track() -> str:
    """Goes back to the previous track in the active media player."""
    keyboard.send("previous track")
    return "Went back to the previous track."