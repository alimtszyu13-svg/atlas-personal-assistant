# onnxruntime должен загрузиться раньше WinRT (OCR), иначе access violation
import onnxruntime  # noqa: F401
import os
import re
import sqlite3
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import snowballstemmer

SEARCH_DIRS = [
    os.path.join(os.path.expanduser("~"), "Desktop"),
    os.path.join(os.path.expanduser("~"), "Documents"),
    os.path.join(os.path.expanduser("~"), "Downloads"),
    os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop"),
    os.path.join(os.path.expanduser("~"), "OneDrive", "Documents"),
    os.path.join(os.path.expanduser("~"), "Рабочий стол"),
    os.path.join(os.path.expanduser("~"), "Документы"),
    os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots"),
    os.path.join(os.path.expanduser("~"), "OneDrive", "Pictures", "Screenshots"),
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
IMAGES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}

READABLE = PLAIN_TEXT | OFFICE | PDF | IMAGES
MAX_OCR_PAGES = 10          # страниц скана на PDF — OCR медленный
OCR_LANGS = ("ru", "en")

MAX_FILE_MB = 20
MAX_READ_CHARS = 32_000
INDEX_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "file_index2.db")
MODEL_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
INDEX_WORKERS = 8
CHUNK_SIZE = 800           # символов в куске
CHUNK_OVERLAP = 100        # перекрытие, чтобы фраза на стыке не потерялась
MAX_CHUNKS = 40            # кусков на файл
EMB_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MIN_SIM = 0.35             # ниже — смысловое совпадение считаем мусором
DEBOUNCE_S = 2.0           # ждём, пока программа допишет файл
BATCH = 25                 # файлов между коммитами
CLIP_IMAGE_MODEL = "Qdrant/clip-ViT-B-32-vision"
CLIP_TEXT_MODEL = "Qdrant/clip-ViT-B-32-text"
CLIP_MIN = 0.22            # ниже — картинка не похожа на запрос
CLIP_GAP = 0.03            # берём только картинки, почти так же похожие, как лучшая
CLIP_Z = 3.0               # картинка должна выделяться на фоне остальных (z-оценка)
CLIP_TEMPLATES = ("a photo of {}", "a picture of {}", "an image showing {}")

_index_lock = threading.Lock()
_ocr_lock = threading.Lock()   # PDFium и WinRT-OCR нельзя вызывать из нескольких потоков сразу


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
                raw = re.sub(r"</(?:w:p|a:p|si|c)>|<w:tab/>|<w:br/>", " ", raw)
                text = re.sub(r"<[^>]+>", "", raw)
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

def _ocr_pil(img) -> str:
    try:
        from winocr import recognize_pil_sync
    except ImportError:
        return ""
    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGB")
    res = {}
    for lang in OCR_LANGS:
        try:
            res[lang] = recognize_pil_sync(img, lang)["text"].strip()
        except Exception:
            res[lang] = ""
    ru, en = res.get("ru", ""), res.get("en", "")
    letters = [ch for ch in ru if ch.isalpha()]
    if not letters:
        return en
    cyr = sum(1 for ch in letters if "а" <= ch.lower() <= "я" or ch.lower() == "ё") / len(letters)
    if cyr > 0.8:
        return ru                     # русский текст — английский движок даст мусор
    if cyr < 0.2:
        return en                     # английский текст — русский движок искажает латиницу
    return ru + "\n" + en             # смешанный текст — храним оба


def _read_image(path: str) -> str:
    try:
        from PIL import Image
        with Image.open(path) as img:
            if min(img.size) < 100:
                return ""
            img.thumbnail((2500, 2500))
            img.load()
            with _ocr_lock:
                return _ocr_pil(img)[:MAX_READ_CHARS]
    except Exception:
        return ""

def _ocr_pdf(path: str) -> str:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return ""
    with _ocr_lock:
        try:
            pdf = pdfium.PdfDocument(path)
            out = []
            for i in range(min(len(pdf), MAX_OCR_PAGES)):
                out.append(_ocr_pil(pdf[i].render(scale=2).to_pil()))
                if sum(len(o) for o in out) > MAX_READ_CHARS:
                    break
            pdf.close()
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
        text = _read_pdf(path)
        return text if len(text.strip()) > 50 else _ocr_pdf(path)   # пусто → скан
    if ext in IMAGES:
        return _read_image(path)
    return ""


# ---------------------------------------------------------------------------
# Индекс: читаем каждый файл один раз, дальше только изменившиеся
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Индекс v2: FTS5 (слова) + эмбеддинги (смысл) → слияние RRF
# ---------------------------------------------------------------------------
_ru = snowballstemmer.stemmer("russian")
_en = snowballstemmer.stemmer("english")
_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_STOP = {
    "a", "an", "the", "on", "in", "at", "of", "with", "that", "this", "it", "is",
    "and", "or", "for", "to", "my", "your", "computer", "pc", "says", "say",
    "saying", "written", "shows", "showing", "there", "which", "where", "find",
    "file", "files", "picture", "image", "photo", "screenshot",
    "на", "в", "во", "с", "со", "и", "или", "где", "что", "это", "мой", "моём",
    "моем", "компьютере", "компе", "файл", "файле", "картинка", "картинку",
    "картинке", "фото", "скриншот", "скриншоте", "найди", "написано", "есть",
    "который", "которой", "про", "о", "об",
}


def _clean_query(query: str) -> str:
    words = [w for w in _TOKEN_RE.findall(query.lower()) if w not in _STOP]
    return " ".join(words)
_CYR = re.compile(r"[а-яё]")


def _stem_text(text: str) -> str:
    out = []
    for tok in _TOKEN_RE.findall(text.lower()):
        if len(tok) < 2:
            continue
        out.append(_ru.stemWord(tok) if _CYR.search(tok) else _en.stemWord(tok))
    return " ".join(out)


def _chunks(text: str) -> list:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    return [text[i:i + CHUNK_SIZE] for i in range(0, len(text), step)][:MAX_CHUNKS]


# --- эмбеддинги (модель грузится один раз, лениво) ---
_embedder = None
_emb_lock = threading.Lock()


def _embed(texts: list) -> np.ndarray:
    global _embedder
    with _emb_lock:
        if _embedder is None:
            from fastembed import TextEmbedding
            print("[индекс] загружаю модель эмбеддингов...")
            _embedder = TextEmbedding(EMB_MODEL, cache_dir=MODEL_CACHE)
        vecs = np.array(list(_embedder.embed(texts, batch_size=32)), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.maximum(norms, 1e-9)

# --- CLIP: поиск по содержимому картинки ---
_clip_img = None
_clip_txt = None
_clip_lock = threading.Lock()
_img_cache = {"paths": None, "mat": None, "dirty": True}


def _norm(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    return v / max(float(np.linalg.norm(v)), 1e-9)


def _clip_image(path: str) -> np.ndarray:
    global _clip_img
    with _clip_lock:
        if _clip_img is None:
            from fastembed import ImageEmbedding
            print("[индекс] загружаю CLIP (картинки)...")
            _clip_img = ImageEmbedding(CLIP_IMAGE_MODEL, cache_dir=MODEL_CACHE)
        return _norm(next(iter(_clip_img.embed([path]))))


def _clip_text(text: str) -> np.ndarray:
    global _clip_txt
    with _clip_lock:
        if _clip_txt is None:
            from fastembed import TextEmbedding
            _clip_txt = TextEmbedding(CLIP_TEXT_MODEL, cache_dir=MODEL_CACHE)
        return _norm(next(iter(_clip_txt.embed([text]))))

def _load_images(conn):
    if not _img_cache["dirty"] and _img_cache["mat"] is not None:
        return _img_cache["paths"], _img_cache["mat"]
    rows = conn.execute("SELECT path, emb FROM images").fetchall()
    paths = [r[0] for r in rows]
    mat = (np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), -1)
           if rows else np.zeros((0, 512), np.float32))
    _img_cache.update(paths=paths, mat=mat, dirty=False)
    return paths, mat


_tr_client = None
_tr_cache = {}


def _to_english(text: str) -> str:
    """CLIP понимает только английский — русский запрос переводим (с кешем)."""
    global _tr_client
    if not _CYR.search(text):
        return text
    if text in _tr_cache:
        return _tr_cache[text]
    out = text
    try:
        if _tr_client is None:
            from openai import OpenAI
            from dotenv import load_dotenv
            load_dotenv()
            _tr_client = OpenAI(api_key=os.getenv("GROQ_API_KEY"),
                                base_url="https://api.groq.com/openai/v1")
        r = _tr_client.chat.completions.create(
            model="openai/gpt-oss-20b", reasoning_effort="low", max_tokens=300,
            messages=[{"role": "system", "content":
                       "Translate the user's text to English. Reply with the translation only."},
                      {"role": "user", "content": text}])
        out = (r.choices[0].message.content or "").strip() or text
    except Exception as e:
        print(f"[поиск] перевод не удался: {e}")
    _tr_cache[text] = out
    return out


# --- тип файла из запроса: «картинка», «pdf», «таблица» ---
_KINDS = (
    (IMAGES, ("картинк", "фото", "скриншот", "снимок", "изображен",
              "image", "picture", "photo", "screenshot")),
    (PDF, ("pdf", "пдф")),
    ({".docx", ".txt", ".md"}, ("документ", "document", "ворд", "word")),
    ({".xlsx", ".csv"}, ("таблиц", "excel", "эксель", "spreadsheet")),
    ({".pptx"}, ("презентац", "presentation", "slides")),
)


def _detect_exts(query: str):
    q = query.lower()
    exts = set()
    for es, words in _KINDS:
        if any(w in q for w in words):
            exts |= es
    return exts or None

# --- база ---
def _connect():
    conn = sqlite3.connect(INDEX_DB, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")   # поиск читает, пока индексатор пишет
    conn.execute("CREATE TABLE IF NOT EXISTS files ("
                 "path TEXT PRIMARY KEY, mtime REAL, name TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS chunks ("
                 "id INTEGER PRIMARY KEY, path TEXT, ord INTEGER, text TEXT, emb BLOB)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)")
    conn.execute("CREATE TABLE IF NOT EXISTS images (path TEXT PRIMARY KEY, emb BLOB)")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
                 "stem, tokenize='unicode61 remove_diacritics 2')")
    return conn


def _is_indexable(path: str) -> bool:
    name = os.path.basename(path)
    if name.lower() in ("desktop.ini", "thumbs.db"):
        return False
    if name.startswith("~$") or os.path.splitext(name)[1].lower() not in READABLE:
        return False
    parts = os.path.normpath(path).split(os.sep)
    if any(p in SKIP_DIRS or p.startswith(".") for p in parts):
        return False
    try:
        return os.path.getsize(path) <= MAX_FILE_MB * 1024 * 1024
    except OSError:
        return False

def _delete_path(conn, path: str) -> None:
    ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE path=?", (path,))]
    if ids:
        conn.executemany("DELETE FROM chunks_fts WHERE rowid=?", [(i,) for i in ids])
        conn.execute("DELETE FROM chunks WHERE path=?", (path,))
    conn.execute("DELETE FROM files WHERE path=?", (path,))
    conn.execute("DELETE FROM images WHERE path=?", (path,))
    _img_cache["dirty"] = True


def _prepare(path: str):
    """Работает в пуле потоков: только чтение диска и разбор."""
    name = os.path.basename(path)
    parts = _chunks(_extract_text(path)) or [""]   # пустой файл ищется хотя бы по имени
    return path, name, parts


def _store(conn, path: str, mtime: float, name: str, parts: list) -> None:
    _delete_path(conn, path)
    vecs = _embed([f"{name}. {p}" for p in parts])
    conn.execute("INSERT INTO files VALUES (?,?,?)", (path, mtime, name))
    for i, (p, v) in enumerate(zip(parts, vecs)):
        cur = conn.execute("INSERT INTO chunks (path, ord, text, emb) VALUES (?,?,?,?)",
                           (path, i, p, v.tobytes()))
        conn.execute("INSERT INTO chunks_fts (rowid, stem) VALUES (?,?)",
                     (cur.lastrowid, _stem_text(name + " " + p)))
    if os.path.splitext(path)[1].lower() in IMAGES:
        try:
            emb = _clip_image(path)
            conn.execute("INSERT OR REPLACE INTO images VALUES (?,?)", (path, emb.tobytes()))
        except Exception as e:
            print(f"[индекс] CLIP пропустил {os.path.basename(path)}: {e}")

# --- кеш векторов в памяти ---
_vec_cache = {"ids": None, "mat": None, "dirty": True}


def _load_vectors(conn):
    if not _vec_cache["dirty"] and _vec_cache["mat"] is not None:
        return _vec_cache["ids"], _vec_cache["mat"]
    rows = conn.execute("SELECT id, emb FROM chunks").fetchall()
    if rows:
        ids = np.array([r[0] for r in rows])
        mat = np.frombuffer(b"".join(r[1] for r in rows),
                            dtype=np.float32).reshape(len(rows), -1)
    else:
        ids, mat = np.array([], dtype=int), np.zeros((0, 384), np.float32)
    _vec_cache.update(ids=ids, mat=mat, dirty=False)
    return ids, mat


# --- построение индекса ---
def refresh_index(verbose: bool = False) -> dict:
    with _index_lock:
        started = time.time()
        conn = _connect()
        known = dict(conn.execute("SELECT path, mtime FROM files").fetchall())
        on_disk = [(p, m) for p, m in _walk_files() if _is_indexable(p)]
        disk_paths = {p for p, _ in on_disk}
        todo = [(p, m) for p, m in on_disk if p not in known or known[p] < m - 0.5]
        gone = [p for p in known if p not in disk_paths]

        for p in gone:
            _delete_path(conn, p)
        conn.commit()

        mtimes = dict(todo)
        with ThreadPoolExecutor(max_workers=INDEX_WORKERS) as pool:
            for i in range(0, len(todo), BATCH):
                batch = [p for p, _ in todo[i:i + BATCH]]
                for path, name, parts in pool.map(_prepare, batch):
                    try:
                        _store(conn, path, mtimes[path], name, parts)
                    except Exception as e:
                        print(f"[индекс] пропускаю {path}: {e}")
                conn.commit()
                _vec_cache["dirty"] = True
                if verbose:
                    print(f"[индекс] {min(i + BATCH, len(todo))}/{len(todo)}")

        if gone:
            _vec_cache["dirty"] = True
        total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        conn.close()
        stats = {"total": total, "new": len(todo), "removed": len(gone),
                 "seconds": round(time.time() - started, 2)}
        if verbose:
            print(f"[индекс] готово: {stats}")
        return stats


def index_one(path: str) -> None:
    """Переиндексирует один файл (или удаляет из индекса, если его нет)."""
    with _index_lock:
        conn = _connect()
        try:
            if os.path.isfile(path) and _is_indexable(path):
                _, name, parts = _prepare(path)
                _store(conn, path, os.path.getmtime(path), name, parts)
            else:
                _delete_path(conn, path)
            conn.commit()
            _vec_cache["dirty"] = True
        finally:
            conn.close()


# --- слежение за папками ---
def start_file_watcher() -> None:
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        print("[индекс] watchdog не установлен — слежение выключено")
        return

    pending = {}
    lock = threading.Lock()

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, e):
            with lock:
                if e.is_directory:
                    # modified у папки летит постоянно — реагируем только на удаление/перенос
                    if e.event_type in ("deleted", "moved"):
                        pending["__rescan__"] = time.time()
                    return
                for p in (e.src_path, getattr(e, "dest_path", "")):
                    if p and os.path.splitext(p)[1].lower() in READABLE \
                            and not os.path.basename(p).startswith("~$"):
                        pending[p] = time.time()

    def _flusher():
        while True:
            time.sleep(1)
            now = time.time()
            with lock:
                ready = [p for p, t in pending.items() if now - t > DEBOUNCE_S]
                for p in ready:
                    del pending[p]
            for p in ready:
                try:
                    if p == "__rescan__":
                        refresh_index()
                    else:
                        index_one(p)
                        print(f"[индекс] обновлён: {os.path.basename(p)}")
                except Exception as ex:
                    print(f"[индекс] watcher: {ex}")

    observer = Observer()
    for d in SEARCH_DIRS:
        if os.path.isdir(d):
            observer.schedule(_Handler(), d, recursive=True)
    observer.daemon = True
    observer.start()
    threading.Thread(target=_flusher, daemon=True).start()
    print("[индекс] слежение за папками включено")


def build_index_background() -> None:
    def _run():
        try:
            _embed(["warmup"])   # модель грузится до того, как OCR начнёт работать
            _clip_text("warmup")
            stats = refresh_index(verbose=True)
            start_file_watcher()
            try:
                from core.bus import bus
                bus.publish("fs.index_ready", priority=9, **stats)
            except ImportError:
                pass
        except Exception as e:
            print(f"[индекс] не удалось построить: {e}")
    threading.Thread(target=_run, daemon=True).start()


def index_status() -> str:
    try:
        conn = _connect()
        f = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        c = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        conn.close()
        return f"В индексе {f} файлов ({c} фрагментов)."
    except Exception as e:
        return f"Индекс недоступен: {e}"


# --- поиск ---
def _fts_query(query: str) -> str:
    return " OR ".join(f'"{t}"*' for t in _stem_text(query).split())


def _hybrid(query: str, k: int = 5) -> list:
    """[(path, текст_лучшего_фрагмента)] — по одному на файл."""
    want = _detect_exts(query)
    clean = _clean_query(query) or query
    conn = _connect()
    try:
        # 1) текст: FTS + эмбеддинги → оценка каждого фрагмента
        chunk_score = {}
        fq = _fts_query(clean)
        fts_ids = set()
        if fq:
            rows = conn.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? "
                "ORDER BY bm25(chunks_fts) LIMIT 50", (fq,)).fetchall()
            fts_ids = {cid for (cid,) in rows}
            for rank, (cid,) in enumerate(rows):
                chunk_score[cid] = chunk_score.get(cid, 0) + 1 / (60 + rank)
        ids, mat = _load_vectors(conn)
        if len(ids):
            sims = mat @ _embed([clean])[0]
            for rank, j in enumerate(np.argsort(-sims)[:50]):
                if sims[j] < MIN_SIM:
                    break
                cid = int(ids[j])
                chunk_score[cid] = chunk_score.get(cid, 0) + 1 / (60 + rank)

        # 2) фрагменты → файлы (лучший фрагмент на файл)
        file_score, best_text = {}, {}
        for cid, sc in chunk_score.items():
            row = conn.execute("SELECT path, text FROM chunks WHERE id=?", (cid,)).fetchone()
            if not row or (not row[1].strip() and cid not in fts_ids):
                continue          # пустой фрагмент — только если совпало имя файла
            ext = os.path.splitext(row[0])[1].lower()
            if want and cid not in fts_ids and (ext in IMAGES or ext not in want):
                continue          # просили тип: смысл — только у нужного типа; картинки по смыслу ищет CLIP
            if sc > file_score.get(row[0], 0):
                file_score[row[0]], best_text[row[0]] = sc, row[1]

        # 3) картинки по содержимому — только если просили картинку
        if want and want & IMAGES:
            img_paths, img_mat = _load_images(conn)
            if len(img_paths):
                en = _to_english(clean)
                qv = _norm(sum(_clip_text(t.format(en)) for t in CLIP_TEMPLATES))
                csims = img_mat @ qv
                mu, sd = float(csims.mean()), float(csims.std()) or 1e-6
                cutoff = max(CLIP_MIN, float(csims.max()) - CLIP_GAP, mu + CLIP_Z * sd)
                for rank, j in enumerate(np.argsort(-csims)[:20]):
                    if csims[j] < cutoff:
                        break
                    p = img_paths[j]
                    file_score[p] = file_score.get(p, 0) + 1 / (60 + rank)
                    best_text.setdefault(p, "")

        # 4) нужный тип файла — выше
        if want:
            for p in file_score:
                if os.path.splitext(p)[1].lower() in want:
                    file_score[p] *= 2

        top, seen_txt = [], set()
        for p in sorted(file_score, key=file_score.get, reverse=True):
            key = re.sub(r"\s+", "", best_text.get(p, ""))[:200]
            if key and key in seen_txt:
                continue          # копия того же файла ("... (1).md") — не показываем дважды
            seen_txt.add(key)
            top.append(p)
            if len(top) >= k:
                break
        return [(p, best_text.get(p, "")) for p in top]
    finally:
        conn.close()

def _snippet(text: str, query: str, width: int = 120) -> str:
    low = (text or "").lower()
    for st in _stem_text(query).split():
        pos = low.find(st)
        if pos != -1:
            s = max(0, pos - width // 3)
            return re.sub(r"\s+", " ", text[s:s + width]).strip()
    return re.sub(r"\s+", " ", (text or "")[:width]).strip()

_last_results = []
_ORD = {"первый": 1, "первую": 1, "второй": 2, "вторую": 2, "третий": 3, "третью": 3,
        "четвёртый": 4, "четвертый": 4, "пятый": 5, "пятую": 5,
        "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}


def _pick(n):
    if isinstance(n, str):
        n = _ORD.get(n.lower().strip(), int(n) if n.strip().isdigit() else 0)
    return _last_results[n - 1] if 1 <= n <= len(_last_results) else None


def open_search_result(n) -> str:
    """Открывает N-й файл из последнего поиска по содержимому."""
    path = _pick(n)
    if not path:
        return "Такого номера нет в последних результатах поиска."
    os.startfile(path)
    return f"Открыл {os.path.basename(path)}."


def show_search_result_in_folder(n) -> str:
    """Открывает папку с N-м файлом из последнего поиска и выделяет его."""
    import subprocess
    path = _pick(n)
    if not path:
        return "Такого номера нет в последних результатах поиска."
    subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
    return f"Показал {os.path.basename(path)} в папке."

def search_file_content(query: str, max_results: int = 5) -> str:
    """Ищет файлы по словам и по смыслу того, что написано внутри."""
    query = (query or "").strip()
    if not query:
        return "Скажи, какой текст искать внутри файлов."
    started = time.time()
    hits = _hybrid(query, max_results)
    if not hits:
        return f"Ничего не нашёл по запросу «{query}». {index_status()}"
    global _last_results
    _last_results = [p for p, _ in hits]
    lines = []
    for i, (path, text) in enumerate(hits, 1):
        snip = _snippet(text, query)
        lines.append(f"{i}. {os.path.basename(path)} — в папке {os.path.dirname(path)}"
                     + (f". Фрагмент: «{snip}»" if snip else ""))
    return (f"Нашёл {len(hits)} по запросу «{query}» "
            f"(за {round(time.time() - started, 2)}с):\n" + "\n".join(lines))


def open_found_file(query: str) -> str:
    """Находит файл по содержимому и сразу открывает самый подходящий."""
    query = (query or "").strip()
    if not query:
        return "Скажи, какой текст искать."
    hits = _hybrid(query, 1)
    if not hits:
        return f"Не нашёл файла с текстом «{query}»."
    path = hits[0][0]
    try:
        os.startfile(path)
        return f"Открыл {os.path.basename(path)} из папки {os.path.dirname(path)}."
    except Exception as e:
        return f"Нашёл {path}, но не смог открыть: {e}"