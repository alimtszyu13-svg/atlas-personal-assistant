import os
import shutil
import string

SAFE_DIRS = [
    os.path.join(os.path.expanduser("~"), "Desktop"),
    os.path.join(os.path.expanduser("~"), "Documents"),
    os.path.join(os.path.expanduser("~"), "Downloads"),
]

# Папки, которые пропускаем при поиске по всему диску — системные,
# служебные или просто гигантские (нет смысла искать пользовательские
# файлы там, а сканирование займёт очень долго)
SKIP_FOLDERS = {
    "windows", "program files", "program files (x86)",
    "$recycle.bin", "system volume information", "programdata",
    "node_modules", "venv", "__pycache__", ".git"
}


def _get_available_drives() -> list[str]:
    """Возвращает список букв дисков, реально существующих на этом ПК (C:, D:, ...)."""
    drives = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if os.path.exists(drive):
            drives.append(drive)
    return drives


SKIP_FOLDERS = {
    "windows", "program files", "program files (x86)",
    "$recycle.bin", "system volume information", "programdata",
    "node_modules", "venv", "__pycache__", ".git",
    "appdata", "site-packages", "lib", "python", "pythoncore"
}


def find_file(name: str, search_whole_disk: bool = True) -> str | None:
    """
    Ищет файл или папку по имени. Сначала точное совпадение,
    потом частичное — сначала в SAFE_DIRS, потом (если разрешено)
    по всему диску, пропуская системные/служебные папки.
    """
    name = os.path.basename(name.strip().rstrip("\\/"))
    name = name.lower()

    exact_match = None
    partial_match = None

    search_roots = list(SAFE_DIRS)
    if search_whole_disk:
        search_roots += _get_available_drives()

    for root in search_roots:
        if not os.path.exists(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_FOLDERS]

            for entry in dirnames + filenames:
                entry_lower = entry.lower()
                full_path = os.path.join(dirpath, entry)

                if entry_lower == name and exact_match is None:
                    exact_match = full_path
                elif name in entry_lower and partial_match is None:
                    partial_match = full_path

            # Нашли точное совпадение — можно сразу остановиться,
            # незачем продолжать сканировать весь диск дальше
            if exact_match:
                return exact_match

    return exact_match or partial_match

def open_file(name: str) -> str:
    """Находит файл/папку по имени и открывает через ассоциированную программу."""
    path = find_file(name)
    if path is None:
        return f"Не нашёл {name}."
    try:
        os.startfile(path)
        return f"Открываю {name}."
    except Exception as e:
        print(f"[Ошибка open_file]: {e}")
        return f"Не получилось открыть {name}."


def create_folder(name: str, location: str = "Desktop") -> str:
    """
    Создаёт новую папку с указанным именем.
    location может быть: 'Desktop', 'Documents', 'Downloads',
    буква диска вида 'D:' или полный путь.
    """
    location = location.strip()

    friendly_map = {
        "desktop": SAFE_DIRS[0],
        "documents": SAFE_DIRS[1],
        "downloads": SAFE_DIRS[2],
    }
    key = location.lower().rstrip("\\/:")

    if key in friendly_map:
        base = friendly_map[key]
    elif len(location) <= 3 and ":" in location:
        base = location if location.endswith("\\") else location + "\\"
    else:
        base = location

    path = os.path.join(base, name)
    try:
        os.makedirs(path, exist_ok=False)
        return f"Created folder '{name}' at {base}"
    except FileExistsError:
        return f"Folder '{name}' already exists there."
    except Exception as e:
        print(f"[Ошибка create_folder]: {e}")
        return f"Couldn't create the folder '{name}': {e}"


def delete_file(name: str) -> str:
    """
    Удаляет файл/папку по имени — перемещает в Корзину (send2trash),
    не удаляет безвозвратно. Ищет по всему диску через find_file.
    """
    path = find_file(name)
    if path is None:
        return f"Не нашёл {name}, нечего удалять."

    try:
        from send2trash import send2trash
        send2trash(path)
        return f"Удалил {name} в корзину."
    except ImportError:
        return "Модуль для безопасного удаления не установлен, скажи мне об этом."
    except Exception as e:
        print(f"[Ошибка delete_file]: {e}")
        return f"Не получилось удалить {name}."


def locate_file(name: str) -> str:
    """Ищет файл/папку и озвучивает, в какой именно папке она находится (без открытия)."""
    path = find_file(name)
    if path is None:
        return f"Не нашёл {name}."
    folder = os.path.dirname(path)
    folder_name = os.path.basename(folder)
    return f"{name} находится в папке {folder_name}."