import psutil


def get_cpu_usage() -> str:
    """Сообщает текущую загрузку процессора в процентах."""
    # interval=1 — специально ждём секунду и меряем загрузку ЗА этот период,
    # а не мгновенный снимок (который часто неточен/скачет)
    usage = psutil.cpu_percent(interval=1)
    return f"CPU usage is currently at {usage:.0f} percent."


def get_memory_usage() -> str:
    """Сообщает текущее использование оперативной памяти."""
    mem = psutil.virtual_memory()
    used_gb = mem.used / (1024 ** 3)
    total_gb = mem.total / (1024 ** 3)
    return f"Using {used_gb:.1f} out of {total_gb:.1f} gigabytes of RAM, that's {mem.percent:.0f} percent."


def get_battery_status() -> str:
    """Сообщает уровень заряда батареи, если она есть (ноутбук)."""
    battery = psutil.sensors_battery()
    if battery is None:
        return "This device doesn't have a battery, it's likely a desktop PC."

    status = "charging" if battery.power_plugged else "on battery power"
    return f"Battery is at {battery.percent:.0f} percent, currently {status}."


def get_disk_usage(drive: str = "C:") -> str:
    """Сообщает свободное/занятое место на указанном диске."""
    drive = drive.strip().rstrip("\\/")
    if not drive.endswith(":"):
        drive += ":"
    path = drive + "\\"

    try:
        usage = psutil.disk_usage(path)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        return f"Drive {drive} has {free_gb:.1f} gigabytes free out of {total_gb:.1f} total."
    except FileNotFoundError:
        return f"Couldn't find drive {drive}."