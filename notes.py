import json
import os

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "atlas_data.json")


def _load():
    if not os.path.exists(DATA_FILE):
        return {"notes": [], "todos": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_note(text: str) -> str:
    """Saves a short note for later."""
    data = _load()
    data["notes"].append(text)
    _save(data)
    return "Note saved."


def list_notes() -> str:
    """Lists all saved notes."""
    data = _load()
    if not data["notes"]:
        return "You have no saved notes."
    return "Your notes: " + " | ".join(f"{i+1}. {n}" for i, n in enumerate(data["notes"]))


def delete_note(index: int) -> str:
    """Deletes a note by its number, as listed by list_notes."""
    data = _load()
    if 1 <= index <= len(data["notes"]):
        removed = data["notes"].pop(index - 1)
        _save(data)
        return f"Deleted note: {removed}"
    return "That note number doesn't exist."


def add_todo(task: str) -> str:
    """Adds a task to the to-do list."""
    data = _load()
    data["todos"].append({"task": task, "done": False})
    _save(data)
    return f"Added to your to-do list: {task}"


def list_todos() -> str:
    """Lists all to-do items with their completion status."""
    data = _load()
    if not data["todos"]:
        return "Your to-do list is empty."
    parts = [f"{i+1}. {t['task']} ({'done' if t['done'] else 'pending'})"
             for i, t in enumerate(data["todos"])]
    return "To-do list: " + " | ".join(parts)


def complete_todo(index: int) -> str:
    """Marks a to-do item as done by its number."""
    data = _load()
    if 1 <= index <= len(data["todos"]):
        data["todos"][index - 1]["done"] = True
        _save(data)
        return f"Marked '{data['todos'][index-1]['task']}' as done."
    return "That to-do number doesn't exist."


def delete_todo(index: int) -> str:
    """Deletes a to-do item by its number."""
    data = _load()
    if 1 <= index <= len(data["todos"]):
        removed = data["todos"].pop(index - 1)
        _save(data)
        return f"Deleted to-do: {removed['task']}"
    return "That to-do number doesn't exist."