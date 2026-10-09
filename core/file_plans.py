"""
Дела с файлами, пока тебя нет: сначала план → твоё «да» (с телефона или голосом) → выполнение → отчёт.
И всегда можно «верни как было».

    plan_tidy("downloads")            → разложить Загрузки по папкам: PDF, Документы, Картинки…
    plan_collect("эссе", "Эссе")      → скопировать все файлы с «эссе» в названии в Документы/Эссе
    confirm(question)                 → выполнить последний план, если в просьбе есть согласие
    undo()                            → вернуть как было

Ничего не удаляется. Разбор — перемещение внутри той же папки, сбор — копирование (оригиналы на месте).
Планы и сделанное — в memory.db (таблица file_plans), только на этом компьютере.
"""
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid

from core import worklog

PLAN_TTL = 30 * 60              # план ждёт подтверждения полчаса
FRESH_S = 3600                  # файлы моложе часа не трогаем — вдруг ещё качаются или открыты
MAX_FILES = 500
_lock = threading.Lock()
_asked = {}                     # план → просьба, из-за которой он появился: та же реплика согласием не считается
CATEGORIES = {
    "PDF": {".pdf"},
    "Документы": {".doc", ".docx", ".odt", ".rtf", ".txt", ".md"},
    "Таблицы": {".xls", ".xlsx", ".csv", ".ods"},
    "Презентации": {".ppt", ".pptx", ".odp", ".key"},
    "Картинки": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".svg"},
    "Видео": {".mp4", ".mov", ".avi", ".mkv", ".webm"},
    "Музыка": {".mp3", ".wav", ".flac", ".m4a", ".ogg"},
    "Архивы": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Установщики": {".exe", ".msi"},
    "Код": {".py", ".js", ".ts", ".html", ".css", ".json", ".ipynb", ".java", ".cpp"},
}
_PARTIAL = {".crdownload", ".part", ".tmp", ".download", ".partial"}
_YES = re.compile(r"\b(?:да|давай|подтверждаю|подтверди|выполняй|выполни|делай|сделай|согласен|конечно|ок|окей|"
                  r"yes|yeah|confirm|go ahead|do it|sure|ok)\b", re.I)
_FOLDERS = {"downloads": "Downloads", "загрузки": "Downloads", "desktop": "Desktop", "рабочий стол": "Desktop",
            "documents": "Documents", "документы": "Documents", "pictures": "Pictures", "картинки": "Pictures"}


def _connect(db: str = None):
    c = sqlite3.connect(db or worklog.DB, check_same_thread=False, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS file_plans (id TEXT PRIMARY KEY, created REAL, kind TEXT, title TEXT, "
              "ops TEXT, status TEXT, done TEXT)")
    return c


def folder_path(name: str, home: str = None) -> str:
    home = home or os.path.expanduser("~")
    n = (name or "downloads").strip().lower()
    if os.path.isabs(name or "") and os.path.isdir(name):
        return name
    sub = _FOLDERS.get(n, name or "Downloads")
    for base in (home, os.path.join(home, "OneDrive")):
        p = os.path.join(base, sub)
        if os.path.isdir(p):
            return p
    return os.path.join(home, sub)


def _category(ext: str) -> str:
    return next((c for c, exts in CATEGORIES.items() if ext in exts), "Прочее")


def _free(path: str) -> str:
    """Имя без перезаписи: «a.pdf» → «a (2).pdf», если уже есть."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"


def _save(plan: dict, db: str = None, question: str = "") -> dict:
    _asked[plan["id"]] = _bare(question).strip().lower()
    with _lock:
        c = _connect(db)
        try:
            c.execute("UPDATE file_plans SET status='replaced' WHERE status='pending'")
            c.execute("INSERT INTO file_plans (id, created, kind, title, ops, status, done) VALUES (?, ?, ?, ?, ?, 'pending', '[]')",
                      (plan["id"], plan["created"], plan["kind"], plan["title"], json.dumps(plan["ops"], ensure_ascii=False)))
            c.commit()
        finally:
            c.close()
    return plan


def plan_tidy(folder: str = "downloads", now: float = None, db: str = None, home: str = None,
              question: str = "") -> dict:
    """Разложить файлы папки по подпапкам-категориям (только верхний уровень, только старше часа)."""
    root = folder_path(folder, home)
    now = now or time.time()
    if not os.path.isdir(root):
        return {"error": f"Folder not found: {root}"}
    ops = []
    for name in sorted(os.listdir(root)):
        src = os.path.join(root, name)
        ext = os.path.splitext(name)[1].lower()
        if not os.path.isfile(src) or name.startswith((".", "~$")) or ext in _PARTIAL or name.lower() == "desktop.ini":
            continue
        try:
            if now - os.path.getmtime(src) < FRESH_S:
                continue
        except OSError:
            continue
        ops.append({"op": "move", "src": src, "dst": os.path.join(root, _category(ext), name), "cat": _category(ext)})
        if len(ops) >= MAX_FILES:
            break
    return _save({"id": uuid.uuid4().hex[:10], "created": now, "kind": "tidy",
                  "title": f"Разобрать {os.path.basename(root)}", "ops": ops, "root": root}, db, question)


def _words(q: str) -> list:
    return [w[:5] if len(w) > 5 else w for w in re.findall(r"\w+", (q or "").lower())
            if len(w) > 1 and w not in ("все", "мои", "мой", "файлы", "files", "all", "my", "the")]


def plan_collect(query: str, dest_name: str = "", dirs: list = None, now: float = None, db: str = None,
                 home: str = None, question: str = "") -> dict:
    """Скопировать файлы, в названии которых есть слова запроса, в одну папку в Документах."""
    words = _words(query)
    if not words:
        return {"error": "Say which files to collect, e.g. 'all essays'."}
    if dirs is None:
        try:
            from file_search import SEARCH_DIRS
            dirs = SEARCH_DIRS
        except Exception:
            h = home or os.path.expanduser("~")
            dirs = [os.path.join(h, d) for d in ("Desktop", "Documents", "Downloads")]
    dest_name = (dest_name or query).strip().strip("«»\"'")[:60] or "Собрано"
    dest = os.path.join(folder_path("documents", home), dest_name.capitalize())
    ops, seen = [], set()
    for base in dirs:
        if not os.path.isdir(base):
            continue
        for root, sub, files in os.walk(base):
            sub[:] = [d for d in sub if not d.startswith(".") and d.lower() not in ("node_modules", "venv", "__pycache__")
                      and os.path.join(root, d) != dest]
            for f in files:
                low = f.lower()
                if all(w in low for w in words) and os.path.splitext(low)[1] not in _PARTIAL:
                    src = os.path.join(root, f)
                    key = (f.lower(), os.path.getsize(src) if os.path.exists(src) else 0)
                    if key in seen:                        # та же копия в двух местах — берём одну
                        continue
                    seen.add(key)
                    ops.append({"op": "copy", "src": src, "dst": os.path.join(dest, f), "cat": dest_name})
                    if len(ops) >= MAX_FILES:
                        break
    return _save({"id": uuid.uuid4().hex[:10], "created": now or time.time(), "kind": "collect",
                  "title": f"Собрать «{dest_name}»", "ops": ops, "dest": dest}, db, question)


def describe(plan: dict) -> str:
    if plan.get("error"):
        return plan["error"]
    ops = plan["ops"]
    if not ops:
        return ("Nothing to do: no files to sort (files newer than an hour are left alone)." if plan["kind"] == "tidy"
                else "No files with those words in their names were found.")
    cats = {}
    for o in ops:
        cats[o["cat"]] = cats.get(o["cat"], 0) + 1
    examples = ", ".join(os.path.basename(o["src"]) for o in ops[:3])
    if plan["kind"] == "tidy":
        what = f"move {len(ops)} files into folders: " + ", ".join(f"{c} {n}" for c, n in sorted(cats.items(), key=lambda x: -x[1]))
    else:
        what = f"copy {len(ops)} files into Documents/{os.path.basename(plan['dest'])} (originals stay where they are)"
    return (f"PLAN (not done yet): {what}. For example: {examples}. Nothing is deleted. "
            "Ask the user to confirm — it runs only after they say yes; they can undo it later.")


def pending(db: str = None, now: float = None):
    now = now or time.time()
    with _lock:
        c = _connect(db)
        try:
            r = c.execute("SELECT id, created, kind, title, ops FROM file_plans WHERE status='pending' "
                          "ORDER BY created DESC LIMIT 1").fetchone()
        finally:
            c.close()
    if not r or now - r[1] > PLAN_TTL:
        return None
    return {"id": r[0], "created": r[1], "kind": r[2], "title": r[3], "ops": json.loads(r[4])}


def _bare(q: str) -> str:
    return re.sub(r"^\s*(?:\([^)]*\)\s*)+", "", q or "")


def user_agreed(question: str) -> bool:
    return bool(_YES.search(_bare(question)))


def execute(plan: dict, db: str = None) -> str:
    done, failed = [], []
    for o in plan["ops"]:
        try:
            if not os.path.exists(o["src"]):
                failed.append(os.path.basename(o["src"]))
                continue
            os.makedirs(os.path.dirname(o["dst"]), exist_ok=True)
            dst = _free(o["dst"])
            if o["op"] == "move":
                shutil.move(o["src"], dst)
            else:
                shutil.copy2(o["src"], dst)
            done.append({"op": o["op"], "src": o["src"], "dst": dst})
        except Exception:
            failed.append(os.path.basename(o["src"]))
    with _lock:
        c = _connect(db)
        try:
            c.execute("UPDATE file_plans SET status='done', done=? WHERE id=?", (json.dumps(done, ensure_ascii=False), plan["id"]))
            c.commit()
        finally:
            c.close()
    verb = "Moved" if plan["kind"] == "tidy" else "Copied"
    text = f"Done: {verb.lower()} {len(done)} files ({plan['title']})."
    if failed:
        text += f" Skipped {len(failed)} (gone or busy): " + ", ".join(failed[:3]) + "."
    return text + " Say 'undo' to put everything back."


def confirm(question: str, db: str = None) -> str:
    plan = pending(db)
    if not plan:
        return "There is no file plan waiting for confirmation (plans expire after 30 minutes)."
    if not user_agreed(question) or _asked.get(plan["id"]) == _bare(question).strip().lower():
        return "The user has not confirmed yet. Read them the plan and ask; run it only after they say yes."
    return execute(plan, db)


def cancel(db: str = None) -> str:
    plan = pending(db)
    if not plan:
        return "No file plan to cancel."
    with _lock:
        c = _connect(db)
        try:
            c.execute("UPDATE file_plans SET status='cancelled' WHERE id=?", (plan["id"],))
            c.commit()
        finally:
            c.close()
    return f"Cancelled: {plan['title']}. Nothing was changed."


def undo(db: str = None) -> str:
    """Вернуть как было последний выполненный план: перемещённое — обратно, скопированное — убрать копии."""
    with _lock:
        c = _connect(db)
        try:
            r = c.execute("SELECT id, title, done FROM file_plans WHERE status='done' ORDER BY created DESC LIMIT 1").fetchone()
        finally:
            c.close()
    if not r:
        return "Nothing to undo."
    back, failed = 0, 0
    for d in reversed(json.loads(r[2])):
        try:
            if d["op"] == "move" and os.path.exists(d["dst"]):
                os.makedirs(os.path.dirname(d["src"]), exist_ok=True)
                shutil.move(d["dst"], _free(d["src"]))
                back += 1
            elif d["op"] == "copy" and os.path.exists(d["dst"]):
                os.remove(d["dst"])                       # только копию, которую сделал сам Atlas
                back += 1
        except Exception:
            failed += 1
    for d in json.loads(r[2]):                            # пустые папки, которые Atlas создал, — убрать
        try:
            os.rmdir(os.path.dirname(d["dst"]))
        except OSError:
            pass
    with _lock:
        c = _connect(db)
        try:
            c.execute("UPDATE file_plans SET status='undone' WHERE id=?", (r[0],))
            c.commit()
        finally:
            c.close()
    return f"Put back {back} files ({r[1]})." + (f" {failed} could not be restored." if failed else "")
