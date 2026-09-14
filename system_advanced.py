import subprocess
import os
from datetime import datetime
import psutil
import screen_brightness_control as sbc
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume


def _get_volume_interface():
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def set_volume(level: int) -> str:
    """Sets system volume to a specific percentage (0-100)."""
    level = max(0, min(100, level))
    volume = _get_volume_interface()
    volume.SetMasterVolumeLevelScalar(level / 100, None)
    return f"Volume set to {level} percent."


def get_volume() -> str:
    """Gets the current system volume percentage."""
    volume = _get_volume_interface()
    current = round(volume.GetMasterVolumeLevelScalar() * 100)
    return f"Volume is currently at {current} percent."


def volume_up(step: int = 10) -> str:
    """Increases system volume by a step (default 10 percent)."""
    volume = _get_volume_interface()
    current = volume.GetMasterVolumeLevelScalar() * 100
    new_level = min(100, current + step)
    volume.SetMasterVolumeLevelScalar(new_level / 100, None)
    return f"Volume increased to {round(new_level)} percent."


def volume_down(step: int = 10) -> str:
    """Decreases system volume by a step (default 10 percent)."""
    volume = _get_volume_interface()
    current = volume.GetMasterVolumeLevelScalar() * 100
    new_level = max(0, current - step)
    volume.SetMasterVolumeLevelScalar(new_level / 100, None)
    return f"Volume decreased to {round(new_level)} percent."


def mute_volume() -> str:
    """Mutes system audio."""
    volume = _get_volume_interface()
    volume.SetMute(1, None)
    return "Muted."


def unmute_volume() -> str:
    """Unmutes system audio."""
    volume = _get_volume_interface()
    volume.SetMute(0, None)
    return "Unmuted."


def set_brightness(level: int) -> str:
    """Sets screen brightness to a percentage (0-100). May not work on desktop monitors without DDC/CI support."""
    try:
        sbc.set_brightness(max(0, min(100, level)))
        return f"Brightness set to {level} percent."
    except Exception as e:
        print(f"[Ошибка set_brightness]: {e}")
        return "Couldn't change brightness — this display may not support it."


def get_brightness() -> str:
    """Gets current screen brightness percentage."""
    try:
        level = sbc.get_brightness()[0]
        return f"Brightness is at {level} percent."
    except Exception as e:
        print(f"[Ошибка get_brightness]: {e}")
        return "Couldn't read brightness on this display."


def lock_screen() -> str:
    """Locks the Windows screen immediately."""
    try:
        subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"])
        return "Locking the screen now."
    except Exception as e:
        print(f"[Ошибка lock_screen]: {e}")
        return "Couldn't lock the screen."


def take_screenshot() -> str:
    """Takes a screenshot and saves it to the Desktop with a timestamped filename."""
    try:
        from PIL import ImageGrab
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        path = os.path.join(desktop, filename)
        img = ImageGrab.grab()
        img.save(path)
        return f"Screenshot saved as {filename} on the Desktop."
    except Exception as e:
        print(f"[Ошибка take_screenshot]: {e}")
        return "Couldn't take a screenshot."


def list_top_processes(count: int = 5) -> str:
    """Lists the top processes currently using the most CPU."""
    try:
        procs = sorted(psutil.process_iter(['name', 'cpu_percent']),
                        key=lambda p: p.info['cpu_percent'] or 0, reverse=True)
        names = [p.info['name'] for p in procs[:count] if p.info['name']]
        return "Top processes: " + ", ".join(names)
    except Exception as e:
        print(f"[Ошибка list_top_processes]: {e}")
        return "Couldn't list processes."


def kill_process(name: str) -> str:
    """Force-closes any process by its exact name (e.g. 'chrome.exe'), even ones not in the known app list."""
    name = name.strip()
    if not name.lower().endswith(".exe"):
        name += ".exe"
    try:
        result = subprocess.run(["taskkill", "/IM", name, "/F"], capture_output=True, text=True)
        if result.returncode == 0:
            return f"Closed {name}."
        return f"Couldn't find a running process named {name}."
    except Exception as e:
        print(f"[Ошибка kill_process]: {e}")
        return f"Couldn't close {name}."

def empty_recycle_bin() -> str:
    """Empties the Windows Recycle Bin."""
    try:
        import winshell
        winshell.recycle_bin().empty(confirm=False, show_progress=False, sound=False)
        return "Recycle Bin emptied."
    except Exception as e:
        return f"Couldn't empty the Recycle Bin: {e}"


def get_uptime() -> str:
    """Reports how long the PC has been running since last restart."""
    boot_time = psutil.boot_time()
    uptime_seconds = datetime.now().timestamp() - boot_time
    hours = int(uptime_seconds // 3600)
    minutes = int((uptime_seconds % 3600) // 60)
    return f"System has been running for {hours} hours and {minutes} minutes."