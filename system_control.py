import subprocess

APP_MAP = {
    "блокнот":     ("notepad.exe", "notepad.exe"),
    "калькулятор": ("calc.exe", "CalculatorApp.exe"),
    "браузер":     ("start chrome", "chrome.exe"),
    "хром":        ("start chrome", "chrome.exe"),
    "проводник":   ("explorer.exe", "explorer.exe"),
    "paint":       ("mspaint.exe", "mspaint.exe"),
    "ворд":        ("start winword", "WINWORD.EXE"),
    "word":        ("start winword", "WINWORD.EXE"),
    "эксель":      ("start excel", "EXCEL.EXE"),
    "excel":       ("start excel", "EXCEL.EXE"),
    "вс код":      ("code", "Code.exe"),
    "vscode":      ("code", "Code.exe"),
    "спотифай":    ("start spotify", "Spotify.exe"),
    "телеграм":    ("start telegram", "Telegram.exe"),
    "диспетчер задач": ("taskmgr.exe", "Taskmgr.exe"),
    "командная строка": ("cmd.exe", "cmd.exe"),
    "дискорд": ("start \"\" \"%LocalAppData%\\Discord\\Update.exe\" --processStart Discord.exe", "Discord.exe"),
    "discord": ("start \"\" \"%LocalAppData%\\Discord\\Update.exe\" --processStart Discord.exe", "Discord.exe"),
}

def open_app(app_name: str) -> str:
    app_name = app_name.lower().strip()

    if app_name not in APP_MAP:
        return f"Не знаю приложения {app_name}."

    launch_cmd, _ = APP_MAP[app_name]
    try:
        subprocess.Popen(launch_cmd, shell=True)
        return f"Открываю {app_name}."
    except Exception as e:
        print(f"[Ошибка open_app]: {e}")
        return f"Не получилось открыть {app_name}."


def close_app(app_name: str) -> str:
    """
    Завершает процесс приложения через taskkill.
    """
    app_name = app_name.lower().strip()

    if app_name not in APP_MAP:
        return f"Не знаю приложения {app_name}."

    _, process_name = APP_MAP[app_name]
    try:
        # /IM = image name (имя exe), /F = force (принудительно, без диалога "сохранить?")
        # capture_output=True — чтобы не засорять консоль Атласа выводом taskkill
        result = subprocess.run(
            ["taskkill", "/IM", process_name, "/F"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return f"Закрываю {app_name}."
        else:
            # returncode != 0 обычно значит "процесс не найден" —
            # то есть приложение и так не было запущено
            return f"{app_name} и так не был запущен."
    except Exception as e:
        print(f"[Ошибка close_app]: {e}")
        return f"Не получилось закрыть {app_name}."