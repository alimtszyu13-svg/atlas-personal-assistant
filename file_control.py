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


INDEX_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "file_index2.db")
HOME_BUDGET_S = 4.0      # твои папки (рабочий стол, документы, загрузки, весь профиль) — до 4 с
DISK_BUDGET_S = 8.0      # остальные диски — до 8 с и только точное имя
# Раньше поиск обходил ВСЕ диски целиком: «открой WhatsApp» искал 35 с (журнал 20.10) и открыл случайный файл,
# в имени которого было «whatsapp». Теперь: индекс → твои папки → диски, с лимитом времени.


def _from_index(name: str):
    """Готовый индекс файлов (file_index2.db) — мгновенно. → (точное, частичное)."""
    import sqlite3
    if not os.path.exists(INDEX_DB):
        return None, None
    try:
        c = sqlite3.connect(f"file:{INDEX_DB}?mode=ro", uri=True, timeout=1.0)
        try:
            rows = c.execute("SELECT path FROM files WHERE lower(name) = ? OR lower(name) LIKE ? LIMIT 50",
                             (name, f"%{name}%")).fetchall()
        finally:
            c.close()
    except Exception:
        return None, None
    exact = partial = None
    for (path,) in rows:
        if not os.path.exists(path):
            continue
        base = os.path.basename(path).lower()
        if base == name or os.path.splitext(base)[0] == name:
            exact = exact or path
        elif partial is None:
            partial = path
    return exact, partial


def _walk(roots, name: str, deadline: float, want_partial: bool):
    """Обход в ширину до deadline: сначала верхние уровни всех папок, потом глубже —
    ~/atlas/requirements.txt находится сразу, а не после всех глубин «Рабочего стола». → (точное, частичное, успели ли)."""
    import time as _t
    from collections import deque
    partial = None
    queue = deque(r for r in roots if r and os.path.isdir(r))
    seen = set()
    while queue:
        if _t.time() > deadline:
            return None, partial, False
        d = queue.popleft()
        key = os.path.normcase(os.path.abspath(d))
        if key in seen:
            continue
        seen.add(key)
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            low = e.name.lower()
            if low == name or os.path.splitext(low)[0] == name:
                return e.path, partial, True
            if want_partial and partial is None and name in low:
                partial = e.path
            try:
                if e.is_dir(follow_symlinks=False) and low not in SKIP_FOLDERS and not e.name.startswith((".", "$")):
                    queue.append(e.path)
            except OSError:
                pass
    return None, partial, True


def find_file(name: str, search_whole_disk: bool = True) -> str | None:
    """Файл или папка по имени: индекс → твои папки (с частичным совпадением) → диски (только точное имя)."""
    import time as _t
    name = os.path.basename(name.strip().rstrip("\\/")).lower()
    if not name:
        return None
    exact, partial = _from_index(name)
    if exact:
        return exact
    home = os.path.expanduser("~")
    t0 = _t.time()
    ex2, part2, _done = _walk(SAFE_DIRS + [home], name, t0 + HOME_BUDGET_S, want_partial=True)
    if ex2:
        return ex2
    partial = partial or part2
    if partial or not search_whole_disk:
        return partial
    drives = [d for d in _get_available_drives() if not home.lower().startswith(d.lower())] + \
             [d for d in _get_available_drives() if home.lower().startswith(d.lower())]
    ex3, _p, done = _walk(drives, name, _t.time() + DISK_BUDGET_S, want_partial=False)
    if not done and not ex3:
        print(f"[файлы] «{name}» не нашёл за {HOME_BUDGET_S + DISK_BUDGET_S:.0f} с — диски целиком не обхожу")
    return ex3


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