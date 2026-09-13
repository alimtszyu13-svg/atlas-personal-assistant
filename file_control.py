import os
import shutil
import string
from voice import speak, listen

SAFE_DIRS = [
    os.path.join(os.path.expanduser("~"), "Desktop"),
    os.path.join(os.path.expanduser("~"), "Documents"),
    os.path.join(os.path.expanduser("~"), "Downloads"),
]

SKIP_FOLDERS = {
    "windows", "program files", "program files (x86)",
    "$recycle.bin", "system volume information", "programdata",
    "node_modules", "venv", "__pycache__", ".git",
    "appdata", "site-packages", "lib", "python", "pythoncore"
}


def _get_available_drives() -> list[str]:
    drives = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if os.path.exists(drive):
            drives.append(drive)
    return drives


def find_file(name: str, search_whole_disk: bool = True) -> str | None:
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

            if exact_match:
                return exact_match

    return exact_match or partial_match


def _resolve_location(location: str) -> str:
    location = location.strip()
    friendly_map = {
        "desktop": SAFE_DIRS[0],
        "documents": SAFE_DIRS[1],
        "downloads": SAFE_DIRS[2],
    }
    key = location.lower().rstrip("\\/:")

    if key in friendly_map:
        return friendly_map[key]
    elif len(location) <= 3 and ":" in location:
        return location if location.endswith("\\") else location + "\\"
    else:
        return location


def _describe_location(path: str) -> str:
    """Человекочитаемое имя родительской папки — с обработкой корня диска (D:\\)."""
    folder = os.path.dirname(path)
    name = os.path.basename(folder)
    return name if name else folder


AFFIRM_WORDS = (
    "yes", "yeah", "yep", "yup", "confirm", "confirmed", "sure",
    "correct", "go ahead", "do it", "absolutely", "definitely",
    "of course", "please do", "go for it", "sounds good", "да", "da"
)
DENY_WORDS = (
    "no", "nope", "don't", "dont", "cancel", "stop", "nah", "negative"
)


def _confirm(prompt: str) -> bool:
    """
    Озвучивает вопрос-подтверждение и слушает ответ.
    Сначала проверяем явный отказ (приоритет — если человек сказал
    "no, do X instead", это отказ, даже если где-то рядом есть похожие слова).
    """
    speak(prompt, interruptible=False)
    response = listen(max_duration=5, silence_limit=1.0).lower()

    if any(word in response for word in DENY_WORDS):
        return False
    return any(word in response for word in AFFIRM_WORDS)


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
    """Создаёт новую папку с указанным именем."""
    base = _resolve_location(location)
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
    """Находит файл/папку, подтверждает голосом и удаляет в Корзину."""
    path = find_file(name)
    if path is None:
        return f"Couldn't find '{name}', nothing to delete."

    folder = _describe_location(path)
    kind = "folder" if os.path.isdir(path) else "file"

    if not _confirm(f"I found the {kind} '{os.path.basename(path)}' inside '{folder}'. Should I delete it?"):
        return "Okay, cancelled — nothing was deleted."

    try:
        from send2trash import send2trash
        send2trash(path)
        return f"Deleted '{name}' — moved to Recycle Bin."
    except ImportError:
        return "The safe-delete module isn't installed, let me know."
    except Exception as e:
        print(f"[Ошибка delete_file]: {e}")
        return f"Couldn't delete '{name}': {e}"


def locate_file(name: str) -> str:
    """Ищет файл/папку и озвучивает, где она находится (без открытия)."""
    path = find_file(name)
    if path is None:
        return f"Не нашёл {name}."
    folder_name = _describe_location(path)
    return f"{name} находится в папке {folder_name}."


def rename_file(old_name: str, new_name: str) -> str:
    """Находит файл/папку, подтверждает голосом и переименовывает."""
    path = find_file(old_name)
    if path is None:
        return f"Couldn't find '{old_name}' to rename."

    folder = _describe_location(path)
    kind = "folder" if os.path.isdir(path) else "file"

    if not _confirm(f"I found the {kind} '{os.path.basename(path)}' inside '{folder}'. Rename it to '{new_name}'?"):
        return "Okay, cancelled — nothing was renamed."

    new_path = os.path.join(os.path.dirname(path), new_name)
    try:
        os.rename(path, new_path)
        return f"Renamed '{old_name}' to '{new_name}'."
    except FileExistsError:
        return f"A file named '{new_name}' already exists there."
    except Exception as e:
        print(f"[Ошибка rename_file]: {e}")
        return f"Couldn't rename '{old_name}': {e}"


def copy_file(name: str, destination: str = "Desktop") -> str:
    """Находит файл/папку, подтверждает голосом и копирует в указанное место."""
    path = find_file(name)
    if path is None:
        return f"Couldn't find '{name}' to copy."

    folder = _describe_location(path)
    kind = "folder" if os.path.isdir(path) else "file"
    dest_folder = _resolve_location(destination)

    if not _confirm(f"I found the {kind} '{os.path.basename(path)}' inside '{folder}'. Copy it to {dest_folder}?"):
        return "Okay, cancelled — nothing was copied."

    dest_path = os.path.join(dest_folder, os.path.basename(path))
    try:
        if os.path.isdir(path):
            shutil.copytree(path, dest_path)
        else:
            shutil.copy2(path, dest_path)
        return f"Copied '{name}' to {dest_folder}."
    except FileExistsError:
        return f"'{name}' already exists at the destination."
    except Exception as e:
        print(f"[Ошибка copy_file]: {e}")
        return f"Couldn't copy '{name}': {e}"


def move_file(name: str, destination: str = "Desktop") -> str:
    """Находит файл/папку, подтверждает голосом и перемещает в указанное место."""
    path = find_file(name)
    if path is None:
        return f"Couldn't find '{name}' to move."

    folder = _describe_location(path)
    kind = "folder" if os.path.isdir(path) else "file"
    dest_folder = _resolve_location(destination)

    if not _confirm(f"I found the {kind} '{os.path.basename(path)}' inside '{folder}'. Move it to {dest_folder}?"):
        return "Okay, cancelled — nothing was moved."

    dest_path = os.path.join(dest_folder, os.path.basename(path))
    try:
        shutil.move(path, dest_path)
        return f"Moved '{name}' to {dest_folder}."
    except shutil.Error as e:
        return f"Couldn't move '{name}': {e}"
    except Exception as e:
        print(f"[Ошибка move_file]: {e}")
        return f"Couldn't move '{name}': {e}"