"""
Конспект по ссылке — без записи: «Атлас, сделай конспект этого ролика», «законспектируй эту статью»,
«конспект по ссылке» (ссылка в буфере) или кнопка «+ По ссылке» в окне «Записи».

  • YouTube: сначала субтитры (свои или автоматические — мгновенно, с временем), если их нет — скачивается
    только звук и распознаётся, как видео с компьютера. Нажал на фразу в окне — ролик открывается с этого места.
  • Статья / любая страница: берётся основной текст (без меню, рекламы и подвалов) → конспект, как у документа.
«Этот ролик» = вкладка, открытая сейчас в браузере (по истории браузера). Нужен yt-dlp — ставится сам.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36")
URL = re.compile(r"https?://[^\s<>\"«»]+")
YT = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([\w-]{11})")

_THIS = re.compile(r"(?:этого|этой|это|эту|этот|текущ\w*|открыт\w*)\s+(?:ролик\w*|видео|видос\w*|страниц\w*|стать\w*|"
                   r"вкладк\w*|сайт\w*|пост\w*|выпуск\w*|this\s+(?:video|page|article|tab))|по\s+ссылке|from\s+the\s+link",
                   re.I)
_ASKS = re.compile(r"конспект|законспект|перескажи|расшифр|краткое содержание|о ч[её]м (?:этот|эта|это)|summar|transcri", re.I)


def youtube_id(url: str) -> str:
    m = YT.search(url or "")
    return m.group(1) if m else ""


def intent(text: str):
    """→ ("link", адрес или "") | None. "" — «этот ролик / эта страница»: адрес возьмём из браузера."""
    t = (text or "").strip()
    if not _ASKS.search(t):
        return None
    m = URL.search(t)
    if m:
        return ("link", m.group(0).rstrip(".,)"))
    if _THIS.search(t):
        return ("link", "")
    return None


# ---------------------------------------------------------------------------
# Какая страница открыта
# ---------------------------------------------------------------------------
_PLAYERS = ("vlc", "mpc", "potplayer", "wmplayer", "video.ui", "movies", "кино и тв", "media player", "проигрыватель",
            "mpv", "kmplayer", "gom")


def current_page(tries: int = 3):
    """(адрес, заголовок) вкладки, которая сейчас на экране, или (None, причина).
    Если на экране плеер с файлом — ("file:" + путь, заголовок)."""
    from core import worklog
    try:
        app, title, _ = worklog._foreground()
    except Exception:
        app, title = "", ""
    low = (app or "").lower()
    if not any(b in low for b in worklog._BROWSERS):
        if any(p in low or p in (title or "").lower() for p in _PLAYERS) and title:
            name = re.split(r"\s+[-—–]\s+", title)[0].strip()
            try:
                from core import media_import
                hits = media_import.find(name)
                if hits:
                    return "file:" + hits[0], name
            except Exception:
                pass
        if (app or "").lower() == "atlas":                   # говоришь из окна Atlas — берём последнюю страницу
            last = _last_browser_page()
            if last[0]:
                return last
        return None, "на экране сейчас не браузер"
    page = title.rsplit(" - ", 1)[0].strip()
    for _ in range(tries):                                  # браузер пишет историю с небольшой задержкой
        now = time.time()
        rows = worklog.browser_history(now - 6 * 3600, now)
        url = next((r[3] for r in rows if r[2] and r[2].strip() == page), None) or \
            next((r[3] for r in rows if r[2] and page and (page in r[2] or r[2] in page)), None)
        if url:
            return url, page
        time.sleep(1.5)
    return None, f"не нашёл адрес вкладки «{page[:60]}» в истории браузера"


def _last_browser_page():
    try:
        from core import worklog
        now = time.time()
        rows = worklog.browser_history(now - 600, now, limit=5)
        if rows:
            return rows[0][3], rows[0][2]
    except Exception:
        pass
    return None, ""


def clipboard_url() -> str:
    try:
        import pyperclip
        m = URL.search(pyperclip.paste() or "")
        return m.group(0) if m else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------
def _yt_dlp():
    try:
        import yt_dlp
        return yt_dlp
    except ImportError:
        print("[записи] ставлю yt-dlp (ролики по ссылке)…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp"], check=False)
        import yt_dlp
        return yt_dlp


def parse_vtt(text: str) -> list:
    """WebVTT → [(секунда, фраза)] без повторов (автосубтитры YouTube повторяют строки «лесенкой»)."""
    out, seen = [], set()
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        m = re.search(r"(\d+):(\d{2}):(\d{2})[.,](\d{3})\s+-->", block) or re.search(r"(\d{2}):(\d{2})[.,](\d{3})\s+-->", block)
        if not m:
            continue
        g = m.groups()
        t = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) if len(g) == 4 else int(g[0]) * 60 + int(g[1])
        lines = block.split("\n")
        body = [ln for ln in lines[[i for i, ln in enumerate(lines) if "-->" in ln][0] + 1:]]
        for ln in body:
            clean = re.sub(r"<[^>]+>", "", ln).strip()
            if clean and clean not in seen:
                seen.add(clean)
                out.append((float(t), clean))
    merged = []                                              # склеиваем в фразы ~ по 6–12 секунд
    for t, s in out:
        if merged and t - merged[-1][0] < 8 and not re.search(r"[.!?]$", merged[-1][1]):
            merged[-1] = (merged[-1][0], merged[-1][1] + " " + s)
        else:
            merged.append((t, s))
    return merged


def youtube(url: str, workdir: str, ytdlp=None):
    """→ {"title", "segments" | None, "audio" | None, "duration"}. Субтитры, если есть, иначе звук."""
    y = ytdlp or _yt_dlp()
    langs = ["ru", "ru-orig", "en", "en-orig", "uk", "kk", "ky"]
    opts = {"skip_download": True, "writesubtitles": True, "writeautomaticsub": True, "subtitleslangs": langs,
            "subtitlesformat": "vtt", "outtmpl": os.path.join(workdir, "%(id)s.%(ext)s"), "quiet": True,
            "no_warnings": True, "noplaylist": True}
    with y.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    title, dur = info.get("title") or "Ролик", float(info.get("duration") or 0)
    orig = (info.get("language") or "").split("-")[0]
    subs = sorted((f for f in os.listdir(workdir) if f.endswith(".vtt")),
                  key=lambda f: (0 if orig and f".{orig}" in f else 1, 0 if ".ru" in f else 1, len(f)))
    if subs:
        with open(os.path.join(workdir, subs[0]), encoding="utf-8", errors="ignore") as f:
            segs = parse_vtt(f.read())
        if segs:
            return {"title": title, "segments": segs, "audio": None, "duration": dur}
    from core import screen_rec
    aopts = {"format": "bestaudio[ext=m4a]/bestaudio/best", "outtmpl": os.path.join(workdir, "%(id)s.%(ext)s"),
             "quiet": True, "no_warnings": True, "noplaylist": True}
    ff = screen_rec.ffmpeg_exe()
    if ff:
        aopts["ffmpeg_location"] = ff
    with y.YoutubeDL(aopts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
    return {"title": title, "segments": None, "audio": path, "duration": dur}


# ---------------------------------------------------------------------------
# Статья
# ---------------------------------------------------------------------------
def fetch(url: str, timeout: float = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(8 * 1024 * 1024)
        enc = r.headers.get_content_charset() or (re.search(rb'charset=["\']?([\w-]+)', raw[:4000]) or [None, b"utf-8"])[1]
    enc = enc.decode() if isinstance(enc, bytes) else enc
    return raw.decode(enc or "utf-8", "ignore")


def article(raw_html: str) -> dict:
    """HTML → {"title", "text"}: основной текст статьи."""
    from core import doc_text
    import html as _h
    t = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw_html)
    og = re.search(r'(?is)<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', raw_html)
    title = _h.unescape((og.group(1) if og else t.group(1) if t else "Страница").strip())
    title = re.split(r"\s+[|—–-]\s+", title)[0][:120]
    best = ""
    for tag in ("article", "main"):
        for m in re.finditer(rf"(?is)<{tag}\b[^>]*>(.*?)</{tag}>", raw_html):
            if len(m.group(1)) > len(best):
                best = m.group(1)
    body = best or (re.search(r"(?is)<body[^>]*>(.*)</body>", raw_html) or [None, raw_html])[1]
    text = doc_text.html_text(body)
    paras = [p for p in text.split("\n\n") if len(p) > 40 or p.startswith("#")]   # без коротких меню-обрывков
    return {"title": title, "text": "\n\n".join(paras)}


# ---------------------------------------------------------------------------
# Всё вместе
# ---------------------------------------------------------------------------
def summarize(url: str = "", announce=None, ask=None, transcribe=None, wait: bool = False, ytdlp=None,
              fetcher=None) -> str:
    """Голос / окно / модель: конспект страницы или ролика. → что сказать."""
    if not url:
        url, why = current_page()
        if not url:
            url = clipboard_url()
        if not url:
            return (f"Не понял, что конспектировать: {why}. Открой ролик или статью в браузере и скажи ещё раз, "
                    "скопируй ссылку и скажи «конспект по ссылке» — или «запиши это видео», чтобы записать то, "
                    "что играет сейчас.")
    if url.startswith("file:"):                               # на экране плеер с файлом — конспект файла
        from core import media_import
        media_import.import_file(url[5:], announce=announce, transcribe=transcribe, ask=ask, wait=wait)
        return f"Делаю конспект: {os.path.basename(url[5:])}. Скажу, когда будет готово."
    import threading
    from core import doc_notes, media_import, meeting_notes as M

    def run():
        import tempfile
        import shutil
        import uuid
        tmp = tempfile.mkdtemp(prefix="atlas_link_")
        jid = uuid.uuid4().hex[:8]                             # пока качаем — в окне видно «загружаю…»
        media_import._jobs[jid] = {"title": url[:80], "pct": 2, "stage": "загружаю", "error": "", "path": url}
        M._changed()
        try:
            if youtube_id(url):
                info = youtube(url, tmp, ytdlp=ytdlp)
                if info["segments"]:
                    text = M.transcript_text(info["segments"])
                    jid = doc_notes.import_text(text, info["title"], announce=announce, ask=ask, url=url, kind="yt",
                                                wait=True)
                else:
                    keep = os.path.join(M.folder(), f"youtube_{youtube_id(url)}{os.path.splitext(info['audio'])[1]}")
                    shutil.move(info["audio"], keep)
                    media_import.import_file(keep, title=info["title"], announce=announce, transcribe=transcribe,
                                             ask=ask, wait=True, url=url)
            else:
                page = article((fetcher or fetch)(url))
                if len(page["text"]) < 300:
                    raise RuntimeError("на странице почти нет текста (возможно, она открывается только в браузере)")
                doc_notes.import_text(page["text"], page["title"], announce=announce, ask=ask, url=url, kind="web",
                                      wait=True)
        except Exception as e:
            print(f"[записи] ссылка {url}: {e}")
            if announce:
                announce(f"Не получилось сделать конспект по ссылке: {str(e)[:140]}")
        finally:
            media_import._jobs.pop(jid, None)
            M._changed()
            shutil.rmtree(tmp, ignore_errors=True)
    t = threading.Thread(target=run, daemon=True, name="link-notes")
    t.start()
    if wait:
        t.join()
    what = "ролика" if youtube_id(url) else "страницы"
    return f"Делаю конспект {what}. Скажу, когда будет готово — он появится в окне «Записи»."
