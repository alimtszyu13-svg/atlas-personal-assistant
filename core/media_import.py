"""
Конспект готового видео или аудио: «Атлас, сделай конспект видео про электричество», «расшифруй вчерашнее
видео из загрузок» — или перетащи файл в окно «Записи» / кнопка «Добавить файл».

Быстро даже для тяжёлых файлов:
  1) из файла вытаскивается только звук (16 кГц моно) — ffmpeg, секунды даже для гигабайтного видео;
  2) звук режется на куски ~5 минут по паузам и распознаётся параллельно (4 сразу);
  3) полный текст с временем + конспект — как у записей, в окне «Записи», с плеером и переходом по фразам.
Сам файл не копируется: запись ссылается на него (переместишь файл — плеер его не найдёт, текст останется).

Поиск файла голосом: названия бывают «коварные» (VID_20261010_1234.mp4), поэтому ищем не только по словам из
имени, но и по «последнее / вчерашнее / сегодняшнее», папке («из загрузок», «с рабочего стола») и тому, что ты
недавно открывал. Несколько похожих — Atlas называет их, а ты выбираешь: «второй».
"""
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import numpy as np

VIDEO = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg", ".ts", ".3gp")
AUDIO = (".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".aac", ".wma", ".amr")
PLAYS_VIDEO = (".mp4", ".webm", ".m4v")                   # окно (WebView2) показывает сразу
PLAYS_AUDIO = (".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".aac")
PIECE_S = 300
WORKERS = int(os.getenv("IMPORT_WORKERS") or 4)
_NOWIN = 0x08000000 if os.name == "nt" else 0

_jobs = {}                                                 # id → {"title", "pct", "stage", "error"}
_pending = {"items": [], "t": 0.0}                         # «какой из них?» — ждём выбора
_lock = threading.Lock()


def is_media(path: str) -> bool:
    return os.path.splitext(path or "")[1].lower() in VIDEO + AUDIO


def is_supported(path: str) -> bool:
    """Видео, аудио или документ (PDF, Word, PowerPoint, EPUB…)."""
    from core import doc_text
    return is_media(path) or doc_text.is_doc(path)


_DOC_WORD = re.compile(r"документ|pdf|пдф|книг|стать|учебник|презентац|глав[уаы]|договор|реферат|эссе|ворд|word|docx|"
                       r"конспект лекц|методичк|parag|параграф|document|book|article|paper|slides", re.I)


# ---------------------------------------------------------------------------
# Голосом: «сделай конспект видео …», «второй»
# ---------------------------------------------------------------------------
_ASK = re.compile(r"^(?:сделай|сделать|напиши|составь|дай)\s+(?:мне\s+)?(?:конспект|расшифровку|транскрипт|текст|"
                  r"краткое содержание|пересказ)\s+(?P<q>.+)$|^(?:законспектируй|расшифруй|транскрибируй|перескажи)\s+"
                  r"(?P<q2>.+)$|^(?:summarize|transcribe)\s+(?:the\s+)?(?P<q3>.+)$", re.I)
_MEDIA_WORD = re.compile(r"видео|ролик|аудио|запис|лекци|урок|вебинар|подкаст|файл|фильм|клип|mp4|mp3|video|audio|"
                         r"recording|lecture|file|документ|pdf|пдф|книг|стать|учебник|презентац|глав[уаы]|договор|"
                         r"реферат|эссе|ворд|word|docx|методичк|параграф|document|book|article|paper|slides", re.I)
_PICK = {"первый": 1, "первое": 1, "первую": 1, "1": 1, "второй": 2, "второе": 2, "вторую": 2, "2": 2,
         "третий": 3, "третье": 3, "третью": 3, "3": 3, "first": 1, "second": 2, "third": 3}


def intent(text: str):
    """→ ("import", запрос) | ("pick", номер) | None."""
    t = (text or "").strip().strip(" .!?")
    t = re.sub(r"^(?:атлас|atlas)[,\s]+", "", t, flags=re.I)
    low = t.lower().replace("ё", "е")
    if _pending["items"] and time.time() - _pending["t"] < 300:
        w = re.sub(r"^(?:давай|возьми|выбери|открой|сделай)\s+", "", low)
        w = re.sub(r"\s+(?:файл|видео|вариант)$", "", w).strip()
        if w in _PICK:
            return ("pick", _PICK[w])
    m = _ASK.match(t)
    if not m:
        return None
    q = (m.group("q") or m.group("q2") or m.group("q3") or "").strip()
    if not _MEDIA_WORD.search(q) and not os.path.exists(q.strip('"')):
        return None                                       # «сделай конспект книги» — не про файлы
    if re.match(r"^(?:этого|это|этот|эту)\s+(?:видео|ролика?|созвона?|звонка?)$", q.lower()):
        return None                                       # «законспектируй это видео» — то, что играет: запись
    return ("import", q)


# ---------------------------------------------------------------------------
# Поиск файла
# ---------------------------------------------------------------------------
def _folders() -> dict:
    home = os.path.expanduser("~")
    out = {}
    for key, names in (("downloads", ("Downloads", "Загрузки")), ("videos", ("Videos", "Видео")),
                       ("desktop", ("Desktop", "Рабочий стол")), ("documents", ("Documents", "Документы")),
                       ("music", ("Music", "Музыка"))):
        for base in (home, os.path.join(home, "OneDrive")):
            for n in names:
                p = os.path.join(base, n)
                if os.path.isdir(p):
                    out.setdefault(key, []).append(p)
    return out


def _scan(dirs, depth: int = 3, limit: int = 4000) -> list:
    found = []
    for root in dirs:
        base_depth = root.rstrip(os.sep).count(os.sep)
        for cur, subdirs, files in os.walk(root):
            if cur.count(os.sep) - base_depth >= depth:
                subdirs[:] = []
            subdirs[:] = [d for d in subdirs if not d.startswith((".", "$")) and d.lower() not in ("node_modules", "venv")]
            for f in files:
                if is_supported(f):
                    found.append(os.path.join(cur, f))
                    if len(found) >= limit:
                        return found
    return found


_STOPW = set("сделай конспект расшифровку расшифруй законспектируй видео видос ролик ролика аудио запись записи файл "
             "файла про о об с со из в на мне мой моё мое мою мои этот эта это то тот та который которое которую "
             "лекцию лекции урок урока вебинар подкаст фильм клип последнее последний последнюю вчерашнее вчерашний "
             "сегодняшнее сегодняшний загрузок загрузках рабочего стола стол папки папке документов видеозапись "
             "документ документа документы pdf пдф книгу книги книга статью статьи статья учебник учебника презентацию "
             "презентации главу главы договор договора реферат эссе ворд word docx методичку параграф "
             "the a an of from about video audio recording file last latest yesterday today downloads desktop "
             "document book article paper slides".split())


def _words(q: str) -> list:
    return [w for w in re.findall(r"\w+", (q or "").lower().replace("ё", "е")) if w not in _STOPW and len(w) > 1]


def _name_score(words, path: str) -> float:
    """Насколько слова запроса похожи на имя файла и папку (0..1). Учитывает «электричеств» ~ «electricity»."""
    if not words:
        return 0.0
    import difflib
    name = re.sub(r"[_\-.()\[\]]+", " ", os.path.splitext(os.path.basename(path))[0].lower()).replace("ё", "е")
    parent = os.path.basename(os.path.dirname(path)).lower()
    tokens = re.findall(r"\w+", name + " " + parent)
    got = 0.0
    for w in words:
        best = 0.0
        for t in tokens:
            if t.startswith(w[:5]) or (len(t) >= 4 and w.startswith(t[:5])):
                best = 1.0
                break
            best = max(best, difflib.SequenceMatcher(None, w, t).ratio())
        got += best if best >= 0.7 else 0.0
    return got / len(words)


def find(query: str, now: datetime = None, files: list = None) -> list:
    """Запрос → [путь] лучшие сначала (до 5)."""
    now = now or datetime.now()
    q = (query or "").strip().strip('"«»')
    if os.path.isfile(q) and is_supported(q):
        return [q]
    low = q.lower().replace("ё", "е")
    folders = _folders()
    want = [k for k, rx in (("downloads", r"загруз|скачан|download"), ("desktop", r"рабоч\w* стол|desktop"),
                            ("videos", r"папк\w* видео|videos"), ("documents", r"из документов|в документах|папк\w* документ|documents folder"))
            if re.search(rx, low)]
    if files is None:
        dirs = [d for k in (want or ["downloads", "videos", "desktop", "documents", "music"]) for d in folders.get(k, [])]
        files = _scan(dirs)
        try:                                                  # что ты недавно открывал (память компьютера)
            from core import worklog
            for it in worklog.search(q, limit=10) if _words(q) else []:
                p = it.get("path") or it.get("doc") or ""
                if p and os.path.isfile(p) and is_supported(p) and p not in files:
                    files.append(p)
        except Exception:
            pass
    from core import doc_text
    kind = VIDEO if re.search(r"видео|ролик|фильм|клип|video", low) else \
        AUDIO if re.search(r"аудио|подкаст|mp3|audio|голосов", low) else \
        doc_text.DOCS + doc_text.IMAGES if _DOC_WORD.search(low) else None
    if kind:
        files = [f for f in files if os.path.splitext(f)[1].lower() in kind] or files
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    span = ((day0 - timedelta(days=1)).timestamp(), day0.timestamp()) if "вчера" in low or "yesterday" in low else \
        (day0.timestamp(), now.timestamp() + 60) if "сегодня" in low or "today" in low else None
    words = _words(q)
    scored = []
    for f in files:
        try:
            mt = os.path.getmtime(f)
        except OSError:
            continue
        if span and not span[0] <= mt < span[1]:
            continue
        s = _name_score(words, f) if words else 0.0
        if words and s == 0.0:
            continue
        recency = max(0.0, 1 - (now.timestamp() - mt) / (30 * 86400))        # свежее — чуть выше
        scored.append((s + 0.15 * recency, mt, f))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [f for _, _, f in scored[:5]]


def describe_choices(items: list) -> str:
    lines = []
    for i, p in enumerate(items[:3], 1):
        try:
            when = datetime.fromtimestamp(os.path.getmtime(p)).strftime("%d.%m %H:%M")
            size = os.path.getsize(p) // (1024 * 1024)
        except OSError:
            when, size = "?", 0
        lines.append(f"{i}) {os.path.basename(p)} — {os.path.basename(os.path.dirname(p))}, {when}, {size} МБ")
    return "\n".join(lines)


def ask(query: str, announce=None) -> str:
    """Голос / модель: найти файл и начать конспект (или спросить, какой)."""
    items = find(query)
    if not items:
        return ("Не нашёл такой файл в Загрузках, Видео, на Рабочем столе и в Документах. Открой окно «Записи» "
                "и нажми «Добавить файл» (или перетащи его туда). А «запиши это видео» — запишет то, что играет сейчас.")
    if len(items) == 1 or _clear_winner(query, items):
        import_file(items[0], announce=announce)
        return f"Делаю конспект: {os.path.basename(items[0])}. Скажу, когда будет готово."
    _pending.update(items=items[:3], t=time.time(), announce=announce)
    return "Нашёл несколько. Какой?\n" + describe_choices(items)


def _clear_winner(query, items) -> bool:
    w = _words(query)
    if not w:
        return False                                          # «последнее видео» — назовём варианты
    a, b = _name_score(w, items[0]), _name_score(w, items[1])
    return a >= 0.99 and b < 0.6


def pick(n: int) -> str:
    items = _pending["items"]
    if not 1 <= n <= len(items):
        return "Такого варианта нет."
    p = items[n - 1]
    _pending["items"] = []
    import_file(p, announce=_pending.get("announce"))
    return f"Делаю конспект: {os.path.basename(p)}. Скажу, когда будет готово."


# ---------------------------------------------------------------------------
# Обработка
# ---------------------------------------------------------------------------
def _ffmpeg() -> str:
    from core import screen_rec
    return screen_rec.ffmpeg_exe()


def duration(path: str, exe: str = None) -> float:
    try:
        r = subprocess.run([exe or _ffmpeg(), "-hide_banner", "-i", path], capture_output=True, timeout=60,
                           creationflags=_NOWIN)
        m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", r.stderr.decode("utf-8", "ignore"))
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    except Exception:
        return 0.0


def extract(path: str, out_wav: str, exe: str = None) -> bool:
    """Только звук: 16 кГц моно — быстро даже для большого видео."""
    r = subprocess.run([exe or _ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", path, "-vn", "-ac", "1",
                        "-ar", "16000", "-c:a", "pcm_s16le", out_wav], capture_output=True, timeout=3 * 3600,
                       creationflags=_NOWIN)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "ignore")[-200:] or "в файле нет звука")
    return True


def pieces(wav_path: str, out_dir: str, piece_s: int = None) -> list:
    """Длинный .wav → куски ~piece_s, разрезанные в самом тихом месте последних 20 с. → [(начало, путь)]."""
    piece_s = piece_s or PIECE_S
    out, start, idx = [], 0, 0
    with wave.open(wav_path, "rb") as wf:
        sr, total = wf.getframerate(), wf.getnframes()
        carry = np.zeros(0, dtype=np.int16)
        while True:
            need = piece_s * sr - len(carry)
            raw = wf.readframes(max(0, need))
            buf = np.concatenate([carry, np.frombuffer(raw, dtype=np.int16)])
            if not len(buf):
                break
            last = wf.tell() >= total
            if last:
                cut = len(buf)
            else:
                win = int(0.4 * sr)
                lo = max(win, len(buf) - 20 * sr, len(buf) // 2)       # кусок не короче половины
                best, cut = None, len(buf)
                for i in range(lo, len(buf) - win + 1, win // 2):
                    seg = buf[i:i + win].astype(np.float32)
                    e = float(np.sqrt(np.mean(seg * seg)))
                    if best is None or e < best:
                        best, cut = e, i + win // 2
            p = os.path.join(out_dir, f"piece_{idx:04d}.wav")
            with wave.open(p, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(buf[:cut].tobytes())
            loud = float(np.sqrt(np.mean(buf[:cut].astype(np.float32) ** 2))) / 32768 if cut else 0.0
            out.append((start / sr, p, loud))
            start += cut
            idx += 1
            carry = buf[cut:]
            if last:
                break
    return out


def _player_audio(src: str, out_base: str, exe: str = None) -> str:
    """Для форматов, которые окно не играет (mkv, avi, wma…), — звук рядом, чтобы слушать и перематывать.
    .ogg (Vorbis), а если в этой сборке ffmpeg его нет — .m4a (AAC)."""
    for ext, codec in ((".ogg", ["-c:a", "libvorbis", "-q:a", "4"]), (".m4a", ["-c:a", "aac", "-b:a", "160k"])):
        out = out_base + ext
        r = subprocess.run([exe or _ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-vn", "-ac", "2",
                            "-ar", "48000", *codec, out], capture_output=True, timeout=3 * 3600, creationflags=_NOWIN)
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
            return out
    return ""


def import_file(path: str, title: str = None, announce=None, transcribe=None, ask=None, wait: bool = False,
                url: str = "") -> str:
    """Начать конспект файла (в фоне). → id задачи. Документ — через doc_notes, видео/аудио — здесь."""
    if not is_media(path):
        from core import doc_notes
        return doc_notes.import_doc(path, title, announce=announce, ask=ask, wait=wait)
    from core import meeting_notes as M
    jid = uuid.uuid4().hex[:8]
    title = (title or re.sub(r"[_\-]+", " ", os.path.splitext(os.path.basename(path))[0]).strip())[:80] or "Видео"
    _jobs[jid] = {"title": title, "pct": 0, "stage": "звук", "error": "", "path": path}
    M._changed()

    def run():
        tmp = tempfile.mkdtemp(prefix="atlas_import_")
        job = _jobs[jid]
        try:
            ext = os.path.splitext(path)[1].lower()
            dur = duration(path)
            wav = os.path.join(tmp, "all.wav")
            extract(path, wav)
            job.update(stage="текст", pct=5)
            M._changed()
            parts = pieces(wav, tmp)
            done = {"n": 0}
            results = {}

            def one(item):
                start, p, loud = item
                if loud < 0.002:
                    segs = []
                else:
                    segs = (transcribe or M._transcribe)(None, p, "")
                with _lock:
                    done["n"] += 1
                    job["pct"] = 5 + int(85 * done["n"] / max(1, len(parts)))
                results[start] = [(start + off, text) for off, text in segs]
            with ThreadPoolExecutor(max_workers=max(1, WORKERS)) as ex:
                list(ex.map(one, parts))
            segments = [s for k in sorted(results) for s in results[k]]
            job.update(stage="конспект", pct=92)
            M._changed()
            text = M.transcript_text(segments)
            started = time.time()
            rid = M._new_id(started, title)
            base = os.path.join(M.folder(), rid)
            entry = {"id": rid, "title": title, "started": started, "minutes": max(1, round(dur / 60)) if dur else 1,
                     "mic": False, "imported": True, "source": path, "txt": "", "md": "", "audio": "", "video": "",
                     "url": url}
            if ext in PLAYS_VIDEO:
                entry["video"] = path
            elif ext in PLAYS_AUDIO:
                entry["audio"] = path
            else:
                entry["audio"] = _player_audio(path, base)
            if text.strip():
                with open(base + ".txt", "w", encoding="utf-8") as f:
                    f.write(text)
                entry["txt"] = base + ".txt"
                try:
                    notes = M.make_notes(text, ask)
                except Exception as e:
                    notes = f"(Конспект не получился: {e}. Полный текст — в файле .txt.)"
                with open(base + ".md", "w", encoding="utf-8") as f:
                    f.write(f"# {title}\n\n{datetime.now().strftime('%d.%m.%Y %H:%M')} · "
                            f"{entry['minutes']} мин · файл: {os.path.basename(path)}\n\n{notes}\n")
                entry["md"] = base + ".md"
            M._save_entry(entry)
            job.update(stage="готово", pct=100, id=rid)
            M._s["notes_ready"] = rid
            M._changed()
            msg = (f"Конспект «{title}» готов — он в окне «Записи»." if text.strip()
                   else f"В «{title}» не нашлось речи — текста нет.")
            print(f"[записи] {msg}")
            if announce:
                announce(msg)
        except Exception as e:
            job.update(stage="ошибка", error=str(e)[:200])
            print(f"[записи] {title}: не получилось — {e}")
            M._changed()
            if announce:
                announce(f"Не получилось сделать конспект «{title}»: {str(e)[:120]}")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
            threading.Timer(30, lambda: _jobs.pop(jid, None) if _jobs.get(jid, {}).get("stage") in ("готово", "ошибка")
                            else None).start()
    t = threading.Thread(target=run, daemon=True, name=f"import-{jid}")
    t.start()
    if wait:
        t.join()
    return jid


def jobs() -> list:
    return [{"job": k, "title": v["title"], "pct": v["pct"], "stage": v["stage"], "error": v["error"]}
            for k, v in list(_jobs.items())]
