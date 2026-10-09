"""
«Пришли мне на телефон …» — файл с компьютера прямо на телефон.

Компьютер находит файл (по имени, по памяти компьютера, по смыслу содержимого) и:
  • если есть облако (ATLAS_CLOUD_URL) — загружает его туда, и на телефон приходит кнопка «Открыть»
    (а если телефон сейчас не спрашивал — уведомление со ссылкой);
  • если телефон подключён к компьютеру напрямую — отдаёт его сам.
Ссылка одноразового вида (случайный ключ), живёт час, файл до 25 МБ.
"""
import json
import mimetypes
import os
import re
import urllib.parse
import urllib.request

MAX_MB = 25
_job = {"phone": False, "sent": []}     # просьба пришла с телефона через облако — метки уйдут с ответом
MARK = re.compile(r"\[\[file:([A-Za-z0-9_-]{8,64})\|([^\]]{1,200})\]\]")


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


def upload(path: str, url: str, key: str, notify: bool = False, timeout: float = 120) -> str:
    """Файл → облако. notify — облако пришлёт уведомление со ссылкой (телефон сейчас ничего не спрашивал). → id."""
    with open(path, "rb") as f:
        data = f.read()
    name = os.path.basename(path)
    req = urllib.request.Request(url.rstrip("/") + "/api/pc/file", data=data, method="POST", headers={
        "X-Atlas-Key": key, "Content-Type": mimetypes.guess_type(name)[0] or "application/octet-stream",
        "X-File-Name": urllib.parse.quote(name), "X-Notify": "1" if notify else "0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["id"]


def send(query: str, cloud_url: str = None, key: str = None, uploader=None) -> str:
    """Инструмент компьютера: найти и отправить. Ответ с меткой [[file:id|имя]] — облако сделает из неё кнопку."""
    path = resolve(query)
    if not path:
        return f"Couldn't find a file matching '{query}' on the computer."
    size = os.path.getsize(path)
    name = os.path.basename(path)
    if size > MAX_MB * 1024 * 1024:
        return f"Found {name}, but it is {size // (1024 * 1024)} MB — too big to send (limit {MAX_MB} MB)."
    cloud_url = cloud_url if cloud_url is not None else (os.getenv("ATLAS_CLOUD_URL") or "").strip()
    if cloud_url.startswith("https://"):
        if key is None:
            from phone import server
            key = server.pairing_token()
        fid = (uploader or upload)(path, cloud_url, key, notify=not _job["phone"])
        _job["sent"].append((fid, name))
        where = "with an Open button in the phone app" if _job["phone"] else "as a phone notification"
    else:                                                  # телефон подключён к компьютеру напрямую
        from phone import server
        from core import phone_actions
        fid = server.share_file(path)
        phone_actions._pending.append({"label": f"Открыть {name}", "url": f"/api/file/{fid}"})
        where = "with an Open button in the phone app"
    return f"Sent {name} to the phone ({size // 1024} KB) — it arrives {where}."


def run_phone_job(handler, text: str) -> str:
    """Компьютер выполняет просьбу с телефона; отправленные файлы — метками в конце ответа."""
    _job.update(phone=True, sent=[])
    try:
        reply = handler(text) or ""
    finally:
        sent, _job["phone"], _job["sent"] = list(_job["sent"]), False, []
    return reply + "".join(f" [[file:{fid}|{name}]]" for fid, name in sent)


def take_marks(text: str):
    """Ответ компьютера → (текст без меток, [(id, имя)])."""
    marks = MARK.findall(text or "")
    return MARK.sub("", text or "").strip(), marks
