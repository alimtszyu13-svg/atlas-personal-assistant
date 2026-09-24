"""
Поиск файлов по содержимому — с индексом.

Ты помнишь, что было написано внутри ("бюджет поездки", "письмо про
стажировку"), но не помнишь имя файла и куда положил. Обычный поиск
Windows ищет по имени и тут бесполезен.

Почему с индексом: без него каждый поиск заново читает все файлы, и на
тысячах документов это десятки секунд. Здесь текст читается один раз и
складывается в базу file_index.db. Дальше проверяется только дата
изменения — если файл не трогали, его не перечитывают. Поэтому первый
поиск небыстрый, а все следующие почти мгновенные.

Читает: txt, md, код, csv, json напрямую; docx, xlsx, pptx — распаковкой
(это zip с xml внутри); pdf — если установлен pypdf.

Ограничения честно: только пользовательские папки, не весь диск; файлы
тяжелее 20 МБ пропускаются; в картинках и видео текста нет.
"""

import os
import re
import sqlite3
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

SEARCH_DIRS = [
    os.path.join(os.path.expanduser("~"), "Desktop"),
    os.path.join(os.path.expanduser("~"), "Documents"),
    os.path.join(os.path.expanduser("~"), "Downloads"),
    os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop"),
    os.path.join(os.path.expanduser("~"), "OneDrive", "Documents"),
    os.path.join(os.path.expanduser("~"), "Рабочий стол"),
    os.path.join(os.path.expanduser("~"), "Документы"),
]

# Папки, которые только мешают: там тысячи служебных файлов
SKIP_DIRS = {
    "node_modules", "venv", ".venv", "__pycache__", ".git", "dist", "build",
    "site-packages", ".cache", "AppData", "browser_profile", "temp_cache",
    ".idea", ".vscode", "env",
}

PLAIN_TEXT = {
    ".txt", ".md", ".markdown", ".py", ".js", ".ts", ".java", ".c", ".cpp",
    ".h", ".cs", ".go", ".rb", ".php", ".sql", ".json", ".xml", ".yaml",
    ".yml", ".csv", ".log", ".ini", ".cfg", ".html", ".htm", ".css", ".srt",
    ".bat", ".ps1", ".sh", ".r", ".tex", ".rst",
}
OFFICE = {".docx", ".xlsx", ".pptx"}
PDF = {".pdf"}

READABLE = PLAIN_TEXT | OFFICE | PDF

MAX_FILE_MB = 20
MAX_READ_CHARS = 20_000    # столько текста храним на файл — для поиска хватает
INDEX_DB = "file_index.db"
INDEX_WORKERS = 8          # чтение упирается в диск, потоки помогают

_index_lock = threading.Lock()


def _read_plain(path: str) -> str:
    for encoding in ("utf-8", "cp1251", "latin-1"):
        try:
            with open(path, encoding=encoding) as f:
                return f.read(MAX_READ_CHARS)
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception:
            return ""
    return ""


def _read_office(path: str) -> str:
    """docx/xlsx/pptx — это zip с xml внутри. Вытаскиваем текст без
    сторонних библиотек: снимаем теги, оставляем содержимое."""
    try:
        chunks = []
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist()
                     if n.endswith(".xml") and
                     any(k in n for k in ("document", "sharedStrings",
                                          "sheet", "slide", "notesSlide"))]
            for name in names[:40]:
                try:
                    raw = z.read(name).decode("utf-8", errors="ignore")
                except Exception:
                    continue
                # между тегами и есть видимый текст
                text = re.sub(r"<[^>]+>", " ", raw)
                chunks.append(text)
                if sum(len(c) for c in chunks) > MAX_READ_CHARS:
                    break
        return re.sub(r"\s+", " ", " ".join(chunks))[:MAX_READ_CHARS]
    except Exception:
        return ""


def _read_pdf(path: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            return ""
    try:
        reader = PdfReader(path)
        out = []
        for page in reader.pages[:30]:
            out.append(page.extract_text() or "")
            if sum(len(o) for o in out) > MAX_READ_CHARS:
                break
        return " ".join(out)[:MAX_READ_CHARS]
    except Exception:
        return ""


def _extract_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in PLAIN_TEXT:
        return _read_plain(path)
    if ext in OFFICE:
        return _read_office(path)
    if ext in PDF:
        return _read_pdf(path)
    return ""


# ---------------------------------------------------------------------------
# Индекс: читаем каждый файл один раз, дальше только изменившиеся
# ---------------------------------------------------------------------------

def _connect():
    conn = sqlite3.connect(INDEX_DB, check_same_thread=False)
    conn.execute("""CREATE TABLE IF NOT EXISTS files (
        path TEXT PRIMARY KEY,
        mtime REAL,
        name TEXT,
        text TEXT,
        low TEXT
    )""")
    return conn


def _walk_files():
    """Все читаемые файлы пользовательских папок: [(path, mtime)]."""
    out = []
    for base in SEARCH_DIRS:
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs
                       if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in files:
                if os.path.splitext(fname)[1].lower() not in READABLE:
                    continue
                path = os.path.join(root, fname)
                try:
                    st = os.stat(path)
                    if st.st_size > MAX_FILE_MB * 1024 * 1024:
                        continue
                    out.append((path, st.st_mtime))
                except OSError:
                    continue
    return out


def refresh_index(verbose: bool = False) -> dict:
    """Досоздаёт индекс: читает только новые и изменённые файлы."""
    with _index_lock:
        started = time.time()
        conn = _connect()
        known = {r[0]: r[1] for r in conn.execute("SELECT path, mtime FROM files")}

        on_disk = _walk_files()
        disk_paths = {p for p, _m in on_disk}
        todo = [(p, m) for p, m in on_disk
                if p not in known or known[p] < m - 0.5]
        gone = [p for p in known if p not in disk_paths]

        if todo:
            def _job(item):
                path, mtime = item
                text = _extract_text(path)
                name = os.path.basename(path)
                # нижний регистр храним отдельно: SQLite LIKE не приводит
                # кириллицу к одному регистру, и поиск по "бюджет" не нашёл
                # бы "Бюджет" в начале предложения
                return path, mtime, name, text, (name + " " + text).lower()
            with ThreadPoolExecutor(max_workers=INDEX_WORKERS) as pool:
                rows = list(pool.map(_job, todo))
            conn.executemany(
                "INSERT OR REPLACE INTO files (path, mtime, name, text, low) "
                "VALUES (?, ?, ?, ?, ?)", rows)

        if gone:
            conn.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in gone])

        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        conn.close()

        stats = {"total": total, "new": len(todo), "removed": len(gone),
                 "seconds": round(time.time() - started, 2)}
        if verbose and (todo or gone):
            print(f"[индекс] всего {total}, обновлено {len(todo)}, "
                  f"удалено {len(gone)}, за {stats['seconds']}с")
        return stats


def build_index_background() -> None:
    """Строит индекс в фоне при запуске Atlas — тогда первый же поиск
    пользователя будет быстрым, а не долгим."""
    def _run():
        try:
            refresh_index(verbose=True)
        except Exception as e:
            print(f"[индекс] не удалось построить: {e}")
    threading.Thread(target=_run, daemon=True).start()


def index_status() -> str:
    """Сколько файлов сейчас в индексе."""
    try:
        conn = _connect()
        total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        conn.close()
        return f"В индексе {total} файлов."
    except Exception as e:
        return f"Индекс недоступен: {e}"


def _candidates(conn, stems: list, query_low: str = ""):
    """Отбор кандидатов ярусами. Важен порядок: сначала точная фраза,
    потом файлы, где есть ВСЕ слова запроса, и только потом — где есть
    хоть одно. Без этого шумные файлы с одним частым словом вытесняют
    нужный из лимита."""
    if not stems:
        return []

    seen = {}

    def _add(rows):
        for row in rows:
            if row[0] not in seen:
                seen[row[0]] = row

    # ярус 1: фраза целиком — почти наверняка то самое
    if query_low and len(query_low) > 3:
        _add(conn.execute(
            "SELECT path, name, text FROM files WHERE low LIKE ? LIMIT 50",
            (f"%{query_low}%",)).fetchall())

    # ярус 2: все слова запроса в одном файле
    if len(stems) > 1:
        where = " AND ".join(["low LIKE ?"] * len(stems))
        params = [f"%{s}%" for s in stems]
        _add(conn.execute(
            f"SELECT path, name, text FROM files WHERE {where} LIMIT 150",
            params).fetchall())

    # ярус 3: хоть одно слово — на случай, если выше ничего не нашлось
    if len(seen) < 30:
        where = " OR ".join(["low LIKE ?"] * len(stems))
        params = [f"%{s}%" for s in stems]
        _add(conn.execute(
            f"SELECT path, name, text FROM files WHERE {where} LIMIT 200",
            params).fetchall())

    return list(seen.values())


def _snippet(text: str, needle: str, width: int = 90) -> str:
    """Кусок текста вокруг найденного — чтобы было видно, то ли это."""
    pos = text.lower().find(needle.lower())
    if pos == -1:
        return ""
    start = max(0, pos - width // 2)
    piece = text[start:start + width].replace("\n", " ").strip()
    return re.sub(r"\s+", " ", piece)


def _stem(word: str) -> str:
    """Грубая основа слова: отсекаем русские окончания. Нужно, чтобы
    «поездки» в запросе находило «поездку» в тексте — без этого поиск
    по-русски почти бесполезен."""
    w = word.lower()
    for ending in ("ами", "ями", "ого", "ему", "ыми", "ими", "ах", "ях",
                   "ой", "ей", "ом", "ем", "ам", "ям", "ые", "ый", "ая",
                   "ое", "ие", "ку", "ке", "ки", "ка", "у", "ю", "е", "и",
                   "а", "я", "ы", "о", "й"):
        if len(w) > len(ending) + 3 and w.endswith(ending):
            return w[:-len(ending)]
    return w


def _score_text(text_low: str, words: list, query_low: str, name_low: str = ""):
    """Оценка совпадения. Учитываем: сколько слов запроса нашлось, сколько
    раз они встречаются, и есть ли точная фраза целиком."""
    matched = []
    total_count = 0
    for w in words:
        stem = _stem(w)
        count = text_low.count(stem)
        if count:
            matched.append(w)
            total_count += count
    if not matched:
        return 0.0, None

    coverage = len(matched) / len(words)          # доля найденных слов
    density = min(total_count / 10.0, 1.0)        # как часто встречаются
    exact = 2.0 if query_low in text_low else 0.0  # фраза целиком
    # близость слов друг к другу: если все основы рядом — почти точно то самое
    near = 0.0
    if len(matched) > 1:
        positions = [text_low.find(_stem(w)) for w in matched]
        if max(positions) - min(positions) < 200:
            near = 1.0
    in_name = 1.5 if any(_stem(w) in name_low for w in words) else 0.0
    return coverage * 3 + density + exact + near + in_name, matched[0]


def search_file_content(query: str, max_results: int = 5) -> str:
    """Ищет файлы по тому, что написано ВНУТРИ них.

    Для случая «забыл имя файла, но помню содержание». Первый запуск
    строит индекс (это разово), дальше поиск почти мгновенный."""
    query = (query or "").strip()
    if not query:
        return "Скажи, какой текст искать внутри файлов."

    started = time.time()
    stats = refresh_index()

    query_low = query.lower()
    words = [w for w in re.split(r"\s+", query_low) if len(w) > 2] or [query_low]
    stems = [_stem(w) for w in words]

    conn = _connect()
    rows = _candidates(conn, stems, query_low)
    conn.close()

    hits = []
    for path, name, text in rows:
        if not text:
            continue
        sc, first = _score_text(text.lower(), words, query_low, (name or "").lower())
        if sc > 0:
            hits.append((sc, path, _snippet(text, _stem(first))))

    elapsed = round(time.time() - started, 2)
    if not hits:
        return (f"Ничего не нашёл по запросу «{query}». В индексе "
                f"{stats['total']} файлов из Рабочего стола, Документов "
                f"и Загрузок.")

    hits.sort(key=lambda h: -h[0])
    lines = [f"{os.path.basename(p)} — в папке {os.path.dirname(p)}"
             + (f". Фрагмент: «{s}»" if s else "")
             for _sc, p, s in hits[:max_results]]
    return (f"Нашёл {len(hits)} по запросу «{query}» (за {elapsed}с):\n"
            + "\n".join(f"{i}. {l}" for i, l in enumerate(lines, 1)))


def open_found_file(query: str) -> str:
    """Находит файл по содержимому и сразу открывает самый подходящий."""
    query = (query or "").strip()
    if not query:
        return "Скажи, какой текст искать."

    refresh_index()
    query_low = query.lower()
    words = [w for w in re.split(r"\s+", query_low) if len(w) > 2] or [query_low]
    stems = [_stem(w) for w in words]

    conn = _connect()
    rows = _candidates(conn, stems, query_low)
    conn.close()

    best = None
    for path, name, text in rows:
        if not text:
            continue
        sc, _f = _score_text(text.lower(), words, query_low, (name or "").lower())
        if sc > 0 and (best is None or sc > best[0]):
            best = (sc, path)

    if not best:
        return f"Не нашёл файла с текстом «{query}»."

    try:
        os.startfile(best[1])
        return f"Открыл {os.path.basename(best[1])} из папки {os.path.dirname(best[1])}."
    except Exception as e:
        return f"Нашёл {best[1]}, но не смог открыть: {e}"