"""
Файлы голосом — пачкой, одной фразой.

    «сожми эти фото» / «сожми фото в Загрузках»             → копии меньше в 3–10 раз, папка «Сжатые»
    «сожми это видео»                                       → «<имя> (сжато).mp4»
    «сделай из видео mp3» / «вытащи звук из видео»          → «<имя>.mp3»
    «переведи эти картинки в jpg / png / webp»
    «сделай из фото pdf»                                    → один PDF из картинок по порядку
    «склей эти pdf»                                         → «Объединённый.pdf»
    «сделай pdf из ворда / из презентации»                  → через Word / PowerPoint
    «заархивируй эту папку» / «запакуй эти файлы в zip»     → .zip рядом
    «распакуй архив»                                        → папка рядом
    «переименуй фото по дате»                               → «2026-10-12 14-30-05.jpg» (дата съёмки),
    «верни названия»                                        → как было

Какие файлы: «эти / выделенные» — выделенные в Проводнике; «в этой папке» — открытая в Проводнике;
«в Загрузках / на рабочем столе / в Документах / в Картинках / в Видео»; или по названию («видео лекция»).
Оригиналы НИКОГДА не меняются и не удаляются — всё пишется новыми файлами рядом (кроме переименования,
и его можно вернуть). Долгое (видео, сотни фото) делается в фоне — Atlas скажет, когда готово.
"""
import json
import os
import re
import subprocess
import threading
import time
import zipfile

IMG = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic")
VID = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv", ".m4v", ".3gp", ".ts")
AUD = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus", ".wma")
PDF = (".pdf",)
OFFICE = (".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".rtf")
ARCH = (".zip",)
_s = {"renamed": None, "busy": 0}
_lock = threading.Lock()


def _ru(text: str) -> bool:
    return bool(re.search(r"[а-яё]", text or "", re.I))


def _w(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    return one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def nice(n: float) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} ГБ"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} МБ".replace(".0 ", " ")
    return f"{max(1, round(n / 1024))} КБ"


def free_name(path: str) -> str:
    """Имя, которого ещё нет: «отчёт.pdf» → «отчёт (2).pdf»."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


# --- окружение (в тестах подменяется) ------------------------------------------------------
class Env:
    home = os.path.expanduser("~")

    def _ps(self, script: str, timeout: float = 8) -> str:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                           text=True, timeout=timeout, encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout or ""

    def _explorer(self) -> dict:
        """Окно Проводника сверху: {"folder": путь, "selected": [пути]} или {}."""
        from core import window_layout
        wins = window_layout._backend().windows()
        top = next((w for w in wins if w["app"].lower() == "explorer" and not w["min"]), None)
        if not top:
            return {}
        script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                  "$h=" + str(top["hwnd"]) + ";$sh=New-Object -ComObject Shell.Application;"
                  "foreach($w in $sh.Windows()){try{if($w.HWND -eq $h){"
                  "'F|'+$w.Document.Folder.Self.Path; foreach($i in $w.Document.SelectedItems()){'S|'+$i.Path}}}catch{}}")
        out = {"folder": "", "selected": []}
        for line in self._ps(script).splitlines():
            if line.startswith("F|"):
                out["folder"] = line[2:].strip()
            elif line.startswith("S|"):
                out["selected"].append(line[2:].strip())
        return out if out["folder"] else {}

    def selection(self) -> list:
        return self._explorer().get("selected", [])

    def explorer_folder(self) -> str:
        return self._explorer().get("folder", "")

    def office_to_pdf(self, src: str, dst: str) -> bool:
        ext = os.path.splitext(src)[1].lower()
        q = lambda p: p.replace("'", "''")
        if ext in (".pptx", ".ppt"):
            s = (f"$a=New-Object -ComObject PowerPoint.Application;$p=$a.Presentations.Open('{q(src)}',$true,$false,$false);"
                 f"$p.SaveAs('{q(dst)}',32);$p.Close();$a.Quit()")
        elif ext in (".xlsx", ".xls"):
            s = (f"$a=New-Object -ComObject Excel.Application;$a.DisplayAlerts=$false;$b=$a.Workbooks.Open('{q(src)}');"
                 f"$b.ExportAsFixedFormat(0,'{q(dst)}');$b.Close($false);$a.Quit()")
        else:
            s = (f"$a=New-Object -ComObject Word.Application;$d=$a.Documents.Open('{q(src)}',$false,$true);"
                 f"$d.SaveAs([ref]'{q(dst)}',[ref]17);$d.Close([ref]0);$a.Quit()")
        try:
            self._ps(s, timeout=180)
        except Exception as e:
            print(f"[файлы] Office: {e}")
        return os.path.exists(dst)

    def ffmpeg(self) -> str:
        from core import screen_rec
        return screen_rec.ffmpeg_exe()


_env = None


def _get_env():
    global _env
    if _env is None:
        _env = Env()
    return _env


# --- операции (все пишут новые файлы) ----------------------------------------------------------
def compress_images(files: list, out_dir: str = None, quality: int = 78, max_side: int = 2560) -> dict:
    from PIL import Image, ImageOps
    done, before, after, fails = [], 0, 0, 0
    for f in files:
        try:
            dst_dir = out_dir or os.path.join(os.path.dirname(f), "Сжатые")
            os.makedirs(dst_dir, exist_ok=True)
            with Image.open(f) as im:
                im = ImageOps.exif_transpose(im)
                if max(im.size) > max_side:
                    im.thumbnail((max_side, max_side))
                alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
                stem = os.path.splitext(os.path.basename(f))[0]
                if alpha:
                    dst = free_name(os.path.join(dst_dir, stem + ".png"))
                    im.save(dst, optimize=True)
                else:
                    dst = free_name(os.path.join(dst_dir, stem + ".jpg"))
                    im.convert("RGB").save(dst, quality=quality, optimize=True, progressive=True)
            before += os.path.getsize(f)
            after += os.path.getsize(dst)
            done.append(dst)
        except Exception as e:
            fails += 1
            print(f"[файлы] не сжал {f}: {e}")
    return {"files": done, "before": before, "after": after, "fails": fails,
            "where": os.path.dirname(done[0]) if done else ""}


def convert_images(files: list, fmt: str) -> dict:
    from PIL import Image, ImageOps
    fmt = fmt.lower().strip(".").replace("jpeg", "jpg")
    done, fails = [], 0
    for f in files:
        try:
            with Image.open(f) as im:
                im = ImageOps.exif_transpose(im)
                dst = free_name(os.path.splitext(f)[0] + "." + fmt)
                if fmt == "jpg":
                    im.convert("RGB").save(dst, quality=92)
                elif fmt == "webp":
                    im.save(dst, quality=88)
                else:
                    im.save(dst)
            done.append(dst)
        except Exception as e:
            fails += 1
            print(f"[файлы] не перевёл {f}: {e}")
    return {"files": done, "fails": fails, "where": os.path.dirname(done[0]) if done else ""}


def images_to_pdf(files: list, out: str = None) -> dict:
    from PIL import Image, ImageOps
    pages = []
    for f in files:
        try:
            with Image.open(f) as im:
                pages.append(ImageOps.exif_transpose(im).convert("RGB"))
        except Exception as e:
            print(f"[файлы] пропустил {f}: {e}")
    if not pages:
        return {"files": [], "fails": len(files)}
    out = free_name(out or os.path.join(os.path.dirname(files[0]), "Фото.pdf"))
    pages[0].save(out, save_all=True, append_images=pages[1:], resolution=150)
    return {"files": [out], "fails": len(files) - len(pages), "pages": len(pages)}


def merge_pdfs(files: list, out: str = None) -> dict:
    from pypdf import PdfWriter
    w = PdfWriter()
    ok, pages = 0, 0
    for f in files:
        try:
            before = len(w.pages)
            w.append(f)
            pages += len(w.pages) - before
            ok += 1
        except Exception as e:
            print(f"[файлы] PDF {f}: {e}")
    if not ok:
        return {"files": [], "fails": len(files)}
    out = free_name(out or os.path.join(os.path.dirname(files[0]), "Объединённый.pdf"))
    with open(out, "wb") as fh:
        w.write(fh)
    return {"files": [out], "fails": len(files) - ok, "pages": pages}


def to_zip(paths: list, out: str = None) -> dict:
    if not paths:
        return {"files": [], "fails": 0}
    first = paths[0]
    name = os.path.basename(first.rstrip("\\/")) if len(paths) == 1 else "Архив"
    if len(paths) == 1 and os.path.isfile(first):
        name = os.path.splitext(name)[0]
    out = free_name(out or os.path.join(os.path.dirname(first.rstrip("\\/")), name + ".zip"))
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in paths:
            p = p.rstrip("\\/")
            if os.path.isdir(p):
                root = os.path.dirname(p)
                for d, _, fs in os.walk(p):
                    for f in fs:
                        full = os.path.join(d, f)
                        if os.path.abspath(full) == os.path.abspath(out):
                            continue
                        z.write(full, os.path.relpath(full, root))
                        n += 1
            elif os.path.isfile(p):
                z.write(p, os.path.basename(p))
                n += 1
    return {"files": [out], "count": n, "fails": 0, "after": os.path.getsize(out)}


def unzip(files: list) -> dict:
    done, fails = [], 0
    for f in files:
        try:
            dst = free_name(os.path.splitext(f)[0])
            with zipfile.ZipFile(f) as z:
                for m in z.infolist():                       # без «../» — только внутрь своей папки
                    target = os.path.abspath(os.path.join(dst, m.filename))
                    if not target.startswith(os.path.abspath(dst) + os.sep) and target != os.path.abspath(dst):
                        continue
                    z.extract(m, dst)
            done.append(dst)
        except Exception as e:
            fails += 1
            print(f"[файлы] не распаковал {f}: {e}")
    return {"files": done, "fails": fails}


def _run_ffmpeg(args: list, env) -> bool:
    exe = env.ffmpeg()
    if not exe:
        return False
    r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y"] + args, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode:
        print(f"[файлы] ffmpeg: {(r.stderr or b'')[-300:].decode('utf-8', 'replace')}")
    return r.returncode == 0


def video_to_mp3(files: list, env=None) -> dict:
    env = env or _get_env()
    done, fails = [], 0
    for f in files:
        dst = free_name(os.path.splitext(f)[0] + ".mp3")
        if _run_ffmpeg(["-i", f, "-vn", "-codec:a", "libmp3lame", "-q:a", "2", dst], env) and os.path.exists(dst):
            done.append(dst)
        else:
            fails += 1
    return {"files": done, "fails": fails}


def compress_video(files: list, env=None) -> dict:
    env = env or _get_env()
    done, before, after, fails = [], 0, 0, 0
    for f in files:
        dst = free_name(os.path.splitext(f)[0] + " (сжато).mp4")
        args = ["-i", f, "-vf", "scale='min(1920,iw)':-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", dst]
        if _run_ffmpeg(args, env) and os.path.exists(dst):
            before += os.path.getsize(f)
            after += os.path.getsize(dst)
            done.append(dst)
        else:
            fails += 1
    return {"files": done, "before": before, "after": after, "fails": fails}


def office_to_pdf(files: list, env=None) -> dict:
    env = env or _get_env()
    done, fails = [], 0
    for f in files:
        dst = free_name(os.path.splitext(f)[0] + ".pdf")
        if env.office_to_pdf(os.path.abspath(f), os.path.abspath(dst)):
            done.append(dst)
        else:
            fails += 1
    return {"files": done, "fails": fails}


def _shot_time(path: str):
    try:
        from PIL import Image
        with Image.open(path) as im:
            ex = im.getexif()
            raw = (ex.get_ifd(0x8769) or {}).get(36867) or ex.get(306)
            if raw:
                return time.mktime(time.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S"))
    except Exception:
        pass
    return os.path.getmtime(path)


def rename_by_date(files: list) -> dict:
    """«IMG_2041.jpg» → «2026-10-12 14-30-05.jpg» (дата съёмки или изменения). Можно вернуть."""
    done, undo = [], []
    for f in sorted(files, key=_shot_time):
        try:
            stamp = time.strftime("%Y-%m-%d %H-%M-%S", time.localtime(_shot_time(f)))
            dst = os.path.join(os.path.dirname(f), stamp + os.path.splitext(f)[1].lower())
            if os.path.abspath(dst) == os.path.abspath(f):
                continue
            dst = free_name(dst)
            os.rename(f, dst)
            undo.append((f, dst))
            done.append(dst)
        except Exception as e:
            print(f"[файлы] не переименовал {f}: {e}")
    _s["renamed"] = undo
    return {"files": done, "fails": len(files) - len(done)}


def undo_rename() -> int:
    undo = _s.get("renamed") or []
    n = 0
    for old, new in reversed(undo):
        try:
            if os.path.exists(new) and not os.path.exists(old):
                os.rename(new, old)
                n += 1
        except Exception:
            pass
    _s["renamed"] = None
    return n


# --- какие файлы ----------------------------------------------------------------------------------
_FOLDERS = [(r"загрузк\w*|скачанн\w*|downloads?", "Downloads"), (r"рабоч\w* стол\w*|desktop", "Desktop"),
            (r"документ\w*(?! в)|documents", "Documents"), (r"картинк\w*|изображени\w*|pictures", "Pictures"),
            (r"папк\w* видео|видеозапис\w*|videos folder", "Videos"), (r"музык\w*|music", "Music")]
_THIS = re.compile(r"(?<![\w])(?:эт\w+|выделенн\w*|выбранн\w*|these|this|selected)(?![\w])", re.I)
_HERE = re.compile(r"(?:в|из|во)\s+(?:этой|текущей|открытой)\s+папк\w*|this folder|current folder", re.I)


def _folder_in(text: str, env) -> str:
    for rx, name in _FOLDERS:
        if re.search(r"(?:в|из|на|во|со|с|in|on|from)\s+(?:папк\w+\s+|the\s+|my\s+)?(?:" + rx + ")", text, re.I):
            return os.path.join(env.home, name)
    m = re.search(r"(?:в|из)\s+папк\w+\s+[«\"]?([\w .-]{2,40}?)[»\"]?(?:\s|$)", text, re.I)
    if m:
        name = m.group(1).strip()
        for base in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", ""):
            p = os.path.join(env.home, base, name)
            if os.path.isdir(p):
                return p
    return ""


def _list(folder: str, exts) -> list:
    try:
        names = sorted(os.listdir(folder))
    except Exception:
        return []
    return [os.path.join(folder, n) for n in names
            if os.path.isfile(os.path.join(folder, n)) and (not exts or n.lower().endswith(exts))]


def targets(text: str, exts, env=None, want_dirs: bool = False):
    """→ (список путей, откуда: 'selection' | 'folder' | '', папка)."""
    env = env or _get_env()
    here = _HERE.search(text)
    named = _folder_in(text, env)
    if named and not here:
        return _list(named, exts), "folder", named
    if here:
        try:
            f = env.explorer_folder()
        except Exception:
            f = ""
        return (_list(f, exts), "folder", f) if f else ([], "", "")
    try:
        sel = env.selection()
    except Exception as e:
        print(f"[файлы] выделение в Проводнике: {e}")
        sel = []
    ok = [p for p in sel if (want_dirs and os.path.isdir(p)) or
          (os.path.isfile(p) and (not exts or p.lower().endswith(exts)))]
    if ok:
        return ok, "selection", os.path.dirname(ok[0])
    if want_dirs and re.search(r"папк|folder", text, re.I):
        try:
            f = env.explorer_folder()
        except Exception:
            f = ""
        if f:
            return [f], "folder", os.path.dirname(f)
    return [], "", ""


# --- фразы --------------------------------------------------------------------------------------------
_KIND_IMG = r"фот\w*|картин\w*|изображ\w*|скрин\w*|снимк\w*|photo\w*|image\w*|picture\w*|png|jpe?g|webp|heic"
_KIND_VID = r"видео\w*|ролик\w*|клип\w*|video\w*|mp4|mkv|mov"
_FMT = re.compile(r"(?:в|to|into)\s+(?:формат\s+)?\.?(jpe?g|png|webp|bmp|pdf|mp3|zip)\b", re.I)
_RULES = [
    ("undo", re.compile(r"верни\w*\s+(?:старые\s+|прежние\s+)?(?:названи|имен)|undo (?:the )?renam", re.I)),
    ("unzip", re.compile(r"(?:распакуй|разархивир|извлеки (?:из )?архив|unzip|extract)\w*", re.I)),
    ("zip", re.compile(r"(?:заархивир|запакуй|упакуй|архивир|сожми в (?:zip|архив)|сделай (?:zip|архив)|"
                       r"в (?:zip|зип|архив)\b|zip (?:this|these|it|the)|make a zip)", re.I)),
    ("mp3", re.compile(r"(?:mp3|мп3|звук|аудио|музык\w* из|audio|sound)\w*.{0,30}(?:из|from)\s+(?:" + _KIND_VID + ")|"
                       r"(?:" + _KIND_VID + r").{0,25}(?:в|to)\s+(?:mp3|мп3|аудио|audio)|"
                       r"(?:mp3|мп3)\s+(?:из|from)|(?:из|from)\s+(?:" + _KIND_VID + r").{0,15}(?:mp3|мп3|аудио)", re.I)),
    ("merge", re.compile(r"(?:склей|объедин|соедин|собери|merge|combine)\w*.{0,30}(?:pdf|пдф)", re.I)),
    ("img2pdf", re.compile(r"(?:из|from)\s+(?:" + _KIND_IMG + r").{0,20}(?:pdf|пдф)|(?:" + _KIND_IMG +
                           r").{0,20}(?:в|to|into)\s+(?:один\s+)?(?:pdf|пдф)|(?:pdf|пдф)\s+(?:из|from)\s+(?:" + _KIND_IMG + ")", re.I)),
    ("office2pdf", re.compile(r"(?:из|from)\s+(?:ворд\w*|word\w*|docx?|документ\w*|презентац\w*|powerpoint|"
                              r"эксел\w*|excel|таблиц\w*).{0,20}(?:pdf|пдф)|(?:ворд\w*|word\w*|docx?|документ\w*|"
                              r"презентац\w*|эксел\w*|таблиц\w*).{0,20}(?:в|to|into)\s+(?:pdf|пдф)|"
                              r"(?:pdf|пдф)\s+(?:из|from)\s+(?:ворд\w*|word\w*|docx?|документ\w*|презентац\w*|"
                              r"эксел\w*|таблиц\w*)", re.I)),
    ("rename", re.compile(r"переимен\w*.{0,40}(?:по дат\w*|по времен\w*)|rename.{0,30}by date", re.I)),
    ("compress_video", re.compile(r"(?:сожми|ужми|уменьши|compress|shrink)\w*.{0,30}(?:" + _KIND_VID + ")", re.I)),
    ("compress_img", re.compile(r"(?:сожми|ужми|уменьши|оптимизир|compress|shrink|optimi[sz]e)\w*.{0,30}(?:" +
                                _KIND_IMG + ")", re.I)),
    ("convert_img", re.compile(r"(?:переведи|конвертир|преобраз|сделай|сохрани|convert|turn)\w*.{0,30}(?:" + _KIND_IMG +
                               r").{0,20}(?:в|to|into)\s+(?:формат\s+)?\.?(?:jpe?g|png|webp|bmp)\b", re.I)),
]


def intent(text: str):
    t = " ".join((text or "").replace("ё", "е").split())
    if not t or len(t) > 160:
        return None
    for kind, rx in _RULES:
        if rx.search(t):
            if kind == "convert_img":
                return {"do": kind, "fmt": _FMT.search(t).group(1).lower(), "text": t}
            return {"do": kind, "text": t}
    return None


_EXTS = {"compress_img": IMG, "convert_img": IMG, "img2pdf": IMG, "compress_video": VID, "mp3": VID + AUD,
         "merge": PDF, "office2pdf": OFFICE, "unzip": ARCH, "rename": IMG + VID, "zip": ()}
_LONG = {"compress_video", "mp3", "office2pdf"}


def describe(do: str, r: dict, ru: bool) -> str:
    n = len(r.get("files") or [])
    fails = r.get("fails") or 0
    tail = ""
    if fails:
        tail = (f" {fails} {_w(fails, 'файл', 'файла', 'файлов')} не получилось." if ru else f" {fails} failed.")
    if not n:
        return ("Ничего не получилось." if ru else "Nothing worked.") + tail
    first = r["files"][0]
    where = os.path.basename(os.path.dirname(first)) or os.path.dirname(first)
    name = os.path.basename(first)
    if do in ("compress_img", "compress_video"):
        b, a = r.get("before", 0), r.get("after", 0)
        ratio = f" — было {nice(b)}, стало {nice(a)}" if ru and b else (f" — {nice(b)} → {nice(a)}" if b else "")
        what = (f"{n} {_w(n, 'фото', 'фото', 'фото')}" if do == "compress_img" else f"{n} {_w(n, 'видео', 'видео', 'видео')}")
        if ru:
            return (f"Сжал {what}{ratio}. Копии в папке «{where}», оригиналы на месте." if do == "compress_img" else
                    f"Сжал {what}{ratio}: «{name}». Оригинал на месте.") + tail
        return f"Compressed {n} file(s){ratio}. Originals untouched." + tail
    if do == "convert_img":
        return (f"Готово: {n} {_w(n, 'картинка', 'картинки', 'картинок')} в «{where}»." if ru else
                f"Done: {n} image(s) in {where}.") + tail
    if do == "img2pdf":
        return (f"Сделал PDF из {r.get('pages', n)} {_w(r.get('pages', n), 'картинки', 'картинок', 'картинок')}: «{name}»."
                if ru else f"Made a PDF of {r.get('pages', n)} images: {name}.") + tail
    if do == "merge":
        return (f"Склеил в «{name}» — {r.get('pages', 0)} {_w(r.get('pages', 0), 'страница', 'страницы', 'страниц')}."
                if ru else f"Merged into {name} — {r.get('pages', 0)} pages.") + tail
    if do == "office2pdf":
        return (f"Готово: {', '.join(os.path.basename(f) for f in r['files'][:3])}." if ru else
                f"Done: {', '.join(os.path.basename(f) for f in r['files'][:3])}.") + tail
    if do == "mp3":
        return (f"Готово: {', '.join(os.path.basename(f) for f in r['files'][:3])}." if ru else
                f"Done: {', '.join(os.path.basename(f) for f in r['files'][:3])}.") + tail
    if do == "zip":
        c = r.get("count", 0)
        return (f"Запаковал {c} {_w(c, 'файл', 'файла', 'файлов')} в «{name}» ({nice(r.get('after', 0))})." if ru else
                f"Zipped {c} files into {name} ({nice(r.get('after', 0))}).") + tail
    if do == "unzip":
        return (f"Распаковал в папку «{name}»." if ru else f"Extracted to {name}.") + tail
    if do == "rename":
        return (f"Переименовал {n} {_w(n, 'файл', 'файла', 'файлов')} по дате. Скажи «верни названия», если не нравится."
                if ru else f"Renamed {n} files by date. Say “undo rename” to revert.") + tail
    return "Готово." if ru else "Done."


def _do(do: str, files: list, it: dict, env):
    if do == "compress_img":
        return compress_images(files)
    if do == "convert_img":
        return convert_images(files, it["fmt"])
    if do == "img2pdf":
        return images_to_pdf(files)
    if do == "merge":
        return merge_pdfs(files)
    if do == "office2pdf":
        return office_to_pdf(files, env)
    if do == "mp3":
        return video_to_mp3([f for f in files if f.lower().endswith(VID)] or files, env)
    if do == "compress_video":
        return compress_video(files, env)
    if do == "zip":
        return to_zip(files)
    if do == "unzip":
        return unzip(files)
    if do == "rename":
        return rename_by_date(files)
    return {"files": [], "fails": 0}


def run(it: dict, env=None, announce=None, wait: bool = False) -> str:
    env = env or _get_env()
    t = it["text"]
    ru = _ru(t)
    do = it["do"]
    if do == "undo":
        n = undo_rename()
        return (f"Вернул прежние названия ({n})." if ru else f"Restored {n} names.") if n else \
            ("Нечего возвращать." if ru else "Nothing to undo.")
    files, src, folder = targets(t, _EXTS[do], env, want_dirs=(do == "zip"))
    if not files:
        if src == "folder":
            return (f"В папке «{os.path.basename(folder)}» нет подходящих файлов." if ru else
                    f"No matching files in {os.path.basename(folder)}.")
        return ("Выдели файлы в Проводнике и скажи ещё раз — или скажи, где они: «в Загрузках», «в этой папке»." if ru
                else "Select the files in Explorer, or say where they are: “in Downloads”, “in this folder”.")
    if do == "merge" and len(files) < 2:
        return "Для склейки нужно хотя бы два PDF." if ru else "Need at least two PDFs to merge."
    if do in ("img2pdf", "merge"):
        files = sorted(files, key=lambda p: os.path.basename(p).lower())
    big = do in _LONG or len(files) > 40

    def work():
        with _lock:
            _s["busy"] += 1
        try:
            r = _do(do, files, it, env)
            msg = describe(do, r, ru)
        except Exception as e:
            msg = (f"Не получилось: {e}" if ru else f"Failed: {e}")[:200]
        finally:
            with _lock:
                _s["busy"] -= 1
        print(f"[файлы] {msg}")
        return msg
    if not big or wait:
        return work()

    def bg():
        msg = work()
        if announce:
            announce(msg)
    threading.Thread(target=bg, daemon=True, name="file-ops").start()
    n = len(files)
    return (f"Взялся: {n} {_w(n, 'файл', 'файла', 'файлов')}. Скажу, когда закончу." if ru else
            f"On it: {n} file(s). I'll tell you when done.")


def handle(text: str, env=None, announce=None):
    it = intent(text)
    return run(it, env, announce) if it else None


def tool(action: str, where: str = "selected", fmt: str = "") -> str:
    """Для модели: action = compress_images | compress_video | to_mp3 | convert_images | images_to_pdf |
    merge_pdf | office_to_pdf | zip | unzip | rename_by_date | undo_rename; where = selected | this folder |
    downloads | desktop | documents | pictures | videos."""
    m = {"compress_images": "compress_img", "compress_video": "compress_video", "to_mp3": "mp3",
         "convert_images": "convert_img", "images_to_pdf": "img2pdf", "merge_pdf": "merge",
         "office_to_pdf": "office2pdf", "zip": "zip", "unzip": "unzip", "rename_by_date": "rename",
         "undo_rename": "undo"}
    do = m.get((action or "").strip().lower())
    if not do:
        return "Unknown action. Use: " + ", ".join(m)
    w = (where or "selected").lower()
    place = {"selected": "these", "this folder": "in this folder", "downloads": "in downloads",
             "desktop": "on desktop", "documents": "in documents", "pictures": "in pictures",
             "videos": "in videos"}.get(w, "these")
    it = {"do": do, "text": place, "fmt": (fmt or "jpg").lower()}
    return run(it, announce=_announce_fn())


def _announce_fn():
    try:
        from brain import state
        return state.announce.get("fn")
    except Exception:
        return None
