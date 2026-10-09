"""
Отдаёт звук и видео записей окну «Записи», чтобы их можно было смотреть прямо там, с перемоткой.

Только этот компьютер (127.0.0.1), случайный ключ на каждый запуск, только файлы из списка записей.
Поддерживает Range — плеер перематывает и не грузит весь файл сразу.
"""
import mimetypes
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

_s = {"server": None, "port": 0, "token": secrets.token_urlsafe(16), "resolve": None}
_MIME = {".ogg": "audio/ogg", ".flac": "audio/flac", ".mp4": "video/mp4", ".wav": "audio/wav", ".m4a": "audio/mp4"}


def _default_resolve(rid: str, kind: str) -> str:
    from core import meeting_notes
    it = meeting_notes.get(rid)
    return (it or {}).get(kind) or ""


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        m = re.match(r"^/m/([\w-]+)/(video|audio)/(.+)$", self.path.split("?", 1)[0])
        if not m or not secrets.compare_digest(m.group(1), _s["token"]):
            return self.send_error(404)
        path = (_s["resolve"] or _default_resolve)(unquote(m.group(3)), m.group(2))
        if not path or not os.path.isfile(path):
            return self.send_error(404)
        size = os.path.getsize(path)
        mime = _MIME.get(os.path.splitext(path)[1].lower()) or mimetypes.guess_type(path)[0] or "application/octet-stream"
        a, b, partial = 0, size - 1, False
        r = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", "").strip())
        if r and (r.group(1) or r.group(2)):
            if r.group(1):
                a = int(r.group(1))
                b = min(int(r.group(2)), size - 1) if r.group(2) else size - 1
            else:
                a = max(0, size - int(r.group(2)))
            if a > b or a >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            partial = True
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(b - a + 1))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        if partial:
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(a)
                left = b - a + 1
                while left > 0:
                    chunk = f.read(min(left, 256 * 1024))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                                       # плеер перемотал — старый запрос оборвался, это нормально


def start(resolve=None) -> int:
    if resolve:
        _s["resolve"] = resolve
    if _s["server"] is None:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        srv.daemon_threads = True
        _s.update(server=srv, port=srv.server_address[1])
        threading.Thread(target=srv.serve_forever, daemon=True, name="media-server").start()
    return _s["port"]


def url(rid: str, kind: str) -> str:
    from urllib.parse import quote
    return f"http://127.0.0.1:{start()}/m/{_s['token']}/{kind}/{quote(rid)}"
