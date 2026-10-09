"""
«Пришли мне на телефон …» — файл с компьютера прямо на телефон.

Компьютер находит файл (по имени, по памяти компьютера, по смыслу содержимого) и:
  • если есть облако (ATLAS_CLOUD_URL) — загружает его туда, и на телефон приходит кнопка «Открыть»
    (а если телефон сейчас не спрашивал — уведомление со ссылкой);
  • если телефон подключён к компьютеру напрямую — отдаёт его сам.
Ссылка одноразового вида (случайный ключ), живёт 3 часа. Размер — до FILE_MAX_MB (по умолчанию 4 ГБ):
файл уходит частями по 8 МБ (обрыв связи — докачка с того же места), большие — в фоне, а когда готово,
на телефон приходит уведомление «Открыть».
"""
import json
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_MB = float(os.getenv("FILE_MAX_MB") or 4096)
PART = 8 * 1024 * 1024
BACKGROUND_MB = 20          # больше — отправляем в фоне, ответ сразу
_job = {"phone": False, "sent": []}     # просьба пришла с телефона через облако — метки уйдут с ответом
MARK = re.compile(r"\[\[file:([A-Za-z0-9_-]{8,64})\|([^\]]{1,200})\]\]")
LINK = re.compile(r"\[\[link:(https://[^|\]\s]{1,2000})\|([^\]]{1,200})\]\]")


def _worklog_doc(query: str) -> str:
    try:
        from core import worklog
        for it in worklog.search(query, limit=5):
            if it.get("doc"):
                return it["doc"]
    except Exception:
        pass
    return ""


def resolve(query: str) -> str:
    """Запрос → путь к файлу или ''."""
    q = (query or "").strip().strip("«»\"'")
    if not q:
        return ""
    if os.path.isfile(q):
        return q
    try:
        import file_search
        if q.isdigit() and getattr(file_search, "_last_results", None):     # «пришли второй» после поиска
            n = int(q)
            if 1 <= n <= len(file_search._last_results):
                return file_search._last_results[n - 1]
    except Exception:
        pass
    try:
        from file_control import find_file
    except Exception:
        find_file = None
    if find_file and re.search(r"\.\w{2,5}$", q):
        p = find_file(q, search_whole_disk=False) or find_file(q)
        if p:
            return p
    doc = _worklog_doc(q)
    if doc and find_file:
        p = find_file(doc, search_whole_disk=False) or find_file(doc)
        if p:
            return p
    try:
        import file_search
        hits = file_search._hybrid(q, 1)
        if hits:
            return hits[0][0]
    except Exception:
        pass
    return ""


def _call(url: str, key: str, data: bytes = b"", headers: dict = None, timeout: float = 120) -> dict:
    req = urllib.request.Request(url, data=data, method="POST", headers={"X-Atlas-Key": key, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 409:                                 # облако подсказывает, с какого места продолжить
            return json.loads(e.read() or b"{}")
        raise


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.0f} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"


def upload(path: str, url: str, key: str, notify: bool = False, timeout: float = 120, progress=None) -> str:
    """Файл → облако частями (с докачкой). notify — облако пришлёт уведомление со ссылкой. → id."""
    base = url.rstrip("/") + "/api/pc/file"
    name, size = os.path.basename(path), os.path.getsize(path)
    head = {"X-File-Name": urllib.parse.quote(name), "X-Notify": "1" if notify else "0"}
    try:
        start = _call(base + "/start", key, headers={**head, "X-File-Size": str(size)}, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code != 404 or size > 25 * 1024 * 1024:
            raise
        with open(path, "rb") as f:                       # облако ещё старое — одним куском, как раньше
            return _call(base, key, f.read(), {**head, "Content-Type": mimetypes.guess_type(name)[0]
                                               or "application/octet-stream"}, timeout)["id"]
    fid, part = start["id"], min(PART, int(start.get("part") or PART))
    have, fails, shown = 0, 0, -1
    with open(path, "rb") as f:
        while have < size:
            f.seek(have)
            chunk = f.read(part)
            try:
                r = _call(f"{base}/part/{fid}", key, chunk, {"X-Offset": str(have),
                                                             "Content-Type": "application/octet-stream"}, timeout)
                have, fails = int(r.get("have", have)), 0
            except Exception as e:
                fails += 1
                if fails > 5:
                    raise RuntimeError(f"связь с облаком оборвалась: {e}")
                time.sleep(2 * fails)
                continue
            pct = int(100 * have / size)
            if progress and pct // 10 != shown:
                shown = pct // 10
                progress(pct)
    r = _call(f"{base}/done/{fid}", key, headers=head, timeout=30)
    if "id" not in r:
        raise RuntimeError("облако не приняло файл целиком")
    return fid


def _background(path: str, name: str, size: int, cloud_url: str, key: str, uploader) -> None:
    def run():
        t0 = time.time()
        try:
            kw = {"progress": lambda p: print(f"[файл → телефон] {name}: {p}%")} if uploader is upload else {}
            uploader(path, cloud_url, key, notify=True, **kw)
            print(f"[файл → телефон] {name} ({_mb(size)}) готов за {time.time() - t0:.0f} с — уведомление ушло")
        except Exception as e:
            print(f"[файл → телефон] {name} не отправился: {e}")
            try:
                from core import handoff
                handoff.notify_phone("Atlas · файл не отправился", f"{name}: {e}"[:200])
            except Exception:
                pass
    threading.Thread(target=run, daemon=True, name="file-to-phone").start()


def send(query: str, cloud_url: str = None, key: str = None, uploader=None) -> str:
    """Инструмент компьютера: найти и отправить. Ответ с меткой [[file:id|имя]] — облако сделает из неё кнопку."""
    path = resolve(query)
    if not path:
        return f"Couldn't find a file matching '{query}' on the computer."
    size = os.path.getsize(path)
    name = os.path.basename(path)
    if size > MAX_MB * 1024 * 1024:
        return f"Found {name}, but it is {_mb(size)} — too big to send (limit {MAX_MB:.0f} MB)."
    cloud_url = cloud_url if cloud_url is not None else (os.getenv("ATLAS_CLOUD_URL") or "").strip()
    if cloud_url.startswith("https://"):
        if key is None:
            from phone import server
            key = server.pairing_token()
        if size > BACKGROUND_MB * 1024 * 1024:            # большой: в фоне, телефон получит уведомление
            _background(path, name, size, cloud_url, key, uploader or upload)
            return (f"Started sending {name} ({_mb(size)}) to the phone. It is big, so it uploads in the background; "
                    f"a notification with an Open button arrives when it is ready. Tell the user that briefly.")
        fid = (uploader or upload)(path, cloud_url, key, notify=not _job["phone"])
        _job["sent"].append((fid, name))
        where = "with an Open button in the phone app" if _job["phone"] else "as a phone notification"
    else:                                                  # телефон подключён к компьютеру напрямую
        from phone import server
        from core import phone_actions
        fid = server.share_file(path)
        phone_actions._pending.append({"label": f"Открыть {name}", "url": f"/api/file/{fid}"})
        where = "with an Open button in the phone app"
    return f"Sent {name} to the phone ({_mb(size)}) — it arrives {where}."


def run_phone_job(handler, text: str) -> str:
    """Компьютер выполняет просьбу с телефона; отправленные файлы — метками в конце ответа."""
    _job.update(phone=True, sent=[], links=[])
    try:
        reply = handler(text) or ""
    finally:
        sent, links = list(_job["sent"]), list(_job.get("links") or [])
        _job.update(phone=False, sent=[], links=[])
    return reply + "".join(f" [[file:{fid}|{name}]]" for fid, name in sent) + "".join(
        f" [[link:{urllib.parse.quote(url, safe=':/?=&%#.-_~+,;@!$')}|{label.replace(']', ')').replace('|', '/')}]]"
        for url, label in links)


def take_marks(text: str):
    """Ответ компьютера → (текст без меток, [(id, имя)])."""
    marks = MARK.findall(text or "")
    return MARK.sub("", text or "").strip(), marks


def take_links(text: str):
    """Ответ компьютера → (текст без ссылок-меток, [(адрес, подпись)])."""
    links = LINK.findall(text or "")
    return LINK.sub("", text or "").strip(), links
