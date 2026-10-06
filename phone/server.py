"""
Сервер Atlas для телефона и других устройств.

    GET  /                    — приложение (страница; без ключа ничего не умеет)
    GET  /manifest.webmanifest, /sw.js, /icon-*.png — чтобы телефон мог его «установить»
    POST /api/pair            — проверить ключ сопряжения
    POST /api/ask   {text}    — текстом      → {text, audio, mime}
    POST /api/talk  (аудио)   — голосом      → {heard, text, audio, mime}

Все /api/*, кроме pair, требуют ключ сопряжения (заголовок X-Atlas-Key). Ключ знает только
телефон, который отсканировал QR-код. Страница с QR-кодом по сети не отдаётся вообще — она
открывается файлом на самом компьютере.
"""
import base64
import json
import os
import re
import secrets
import socketserver
import tempfile
import threading
import time
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PAIR_FILE = os.path.join(ROOT, "phone_pairing.json")
PORT = int(os.getenv("PHONE_PORT") or 8765)
MAX_AUDIO = 8 * 1024 * 1024                       # 8 МБ — больше минуты речи не бывает

_state = {"server": None, "thread": None, "port": PORT, "url": None, "kind": None, "hint": ""}
_brain_lock = threading.Lock()                    # телефон и компьютер не думают одновременно


# =============================================================================
# Сопряжение
# =============================================================================
def _load_pair() -> dict:
    try:
        with open(PAIR_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def pairing_token(create: bool = False) -> str:
    """Секретный ключ: создаётся один раз и хранится в phone_pairing.json (в git не попадает)."""
    data = _load_pair()
    if not data.get("token") and create:
        data = {"token": secrets.token_urlsafe(24), "created": time.time()}
        with open(PAIR_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    return data.get("token", "")


def is_paired() -> bool:
    return bool(_load_pair().get("token"))


class HTTPError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _check_key(environ) -> None:
    token = pairing_token()
    got = environ.get("HTTP_X_ATLAS_KEY") or ""
    if not token or not got or not secrets.compare_digest(got, token):
        raise HTTPError(401, "Нужен ключ сопряжения: скажи Атласу на компьютере «установи себя на телефон».")


# =============================================================================
# Мозг: тот же путь, что голосом на компьютере, но ответ уходит в телефон
# =============================================================================
def _emo():
    try:
        from core import emotions
        return emotions
    except Exception:
        return None


PHONE_QUIET = 30          # столько секунд после фразы с телефона компьютер не реагирует на «Атлас»
PHONE_HINT = ("(Said on the phone: reply in 1-2 short spoken sentences; do only what was asked; apps, music and files "
              "open on the computer — say so if it matters.) ")


def _hint(text: str) -> str:
    """Язык ответа — как сказал человек (кириллица → русский), иначе как в настройках Atlas."""
    if re.search(r"[а-яё]", text, re.I):
        lang = "ru"
    else:
        try:
            from voice import get_response_language
            lang = get_response_language()
        except Exception:
            lang = "ru"
    return ("(Respond in Russian.) " if lang == "ru" else "(Respond in English.) ") + PHONE_HINT


def _quiet_pc() -> None:
    try:
        from ui_state import shared_state
        shared_state["phone_active_until"] = time.time() + PHONE_QUIET
    except Exception:
        pass


def handle_text(text: str) -> str:
    """Быстрый путь → мозг. Компьютер при этом молчит: отвечает телефон."""
    text = (text or "").strip()
    if not text:
        return ""
    _quiet_pc()
    with _brain_lock:
        reply = None
        try:
            from fast_commands import try_fast_command
            fast = try_fast_command(text)
            if fast is not None:
                reply = fast[1] if isinstance(fast, tuple) else str(fast)
                try:
                    from ai_brain import remember_exchange
                    remember_exchange(text, reply)
                except Exception:
                    pass
        except Exception as e:
            print(f"[телефон] быстрый путь: {e}")
        if reply is None:
            from ai_brain import ask_ai
            reply = ask_ai(_hint(text) + text)
    em = _emo()
    shown = em.strip(reply) if em else reply
    print(f"[телефон] ← {shown}")
    _quiet_pc()                                         # отсчёт тишины — с конца ответа
    try:
        from ui_state import shared_state                  # разговор виден и в окне на компьютере
        shared_state.setdefault("chat_history", []).append(("You 📱", text))
        shared_state["chat_history"].append(("Atlas", shown))
    except Exception:
        pass
    try:
        from core import memory
        memory.log_turn("user", text)
        memory.log_turn("assistant", shown)
    except Exception:
        pass
    return reply


def synthesize(text: str):
    """Голос Atlas файлом для телефона → (base64, mime) или (None, None) — тогда телефон скажет сам."""
    if not text:
        return None, None
    try:
        import voice
        fn = os.path.join(tempfile.mkdtemp(prefix="atlas_phone_"), "reply.mp3")
        real = voice._generate_any(text, fn)
        with open(real, "rb") as f:
            data = f.read()
        mime = "audio/wav" if real.lower().endswith(".wav") else "audio/mpeg"
        try:
            os.remove(real)
        except OSError:
            pass
        return base64.b64encode(data).decode("ascii"), mime
    except Exception as e:
        print(f"[телефон] голос не получился ({e}) — телефон скажет своим голосом")
        return None, None


_EXT = {"webm": "webm", "ogg": "ogg", "mp4": "m4a", "m4a": "m4a", "aac": "m4a", "mpeg": "mp3", "wav": "wav",
        "x-wav": "wav"}


def transcribe(data: bytes, mime: str) -> str:
    """Распознавание речи с телефона: тот же Whisper в Groq, те же настройки языка, что на компьютере."""
    import voice
    sub = (mime or "").split(";")[0].split("/")[-1].lower()
    ext = _EXT.get(sub, "webm")
    kw = dict(file=(f"phone.{ext}", data), model=getattr(voice, "STT_WHISPER", "whisper-large-v3"), temperature=0.0)
    try:
        lang = voice._stt_language()
        if lang:
            kw["language"] = lang
        kw["prompt"] = voice._stt_prompt()
    except Exception:
        pass
    r = voice.groq_client.audio.transcriptions.create(**kw)
    text = (getattr(r, "text", "") or "").strip()
    junk = getattr(voice, "_WHISPER_JUNK", set())
    if not re.search(r"[^\W_]", text) or re.sub(r"[^\w\s]", "", text.lower()).strip() in junk:
        return ""
    return text


def _answer(text: str, heard: str = None) -> dict:
    reply = handle_text(text)
    audio, mime = synthesize(reply)
    em = _emo()
    out = {"text": em.strip(reply) if em else reply, "audio": audio, "mime": mime}
    if heard is not None:
        out["heard"] = heard
    return out


# =============================================================================
# Маршруты (стандартный WSGI — без сторонних библиотек)
# =============================================================================
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".png": "image/png"}


def _manifest() -> bytes:
    return json.dumps({
        "name": "Atlas", "short_name": "Atlas", "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#050814", "theme_color": "#050814", "orientation": "portrait",
        "description": "Голосовой ассистент Atlas — с компьютера на телефон",
        "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                  {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
    }, ensure_ascii=False).encode("utf-8")


def _static(name: str):
    path = os.path.join(STATIC, name)
    if not os.path.isfile(path):
        raise HTTPError(404, "Нет такой страницы.")
    with open(path, "rb") as f:
        data = f.read()
    headers = [("Content-Type", _TYPES.get(os.path.splitext(name)[1], "application/octet-stream")),
               ("Cache-Control", "no-cache")]
    if name == "sw.js":
        headers.append(("Service-Worker-Allowed", "/"))
    return "200 OK", headers, data


def _json(obj, status="200 OK"):
    return status, [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")], \
        json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _body(environ, limit: int) -> bytes:
    try:
        n = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        n = 0
    if n > limit:
        raise HTTPError(413, "Слишком длинная запись.")
    return environ["wsgi.input"].read(n) if n else b""


def _route(environ):
    method, path = environ.get("REQUEST_METHOD", "GET"), environ.get("PATH_INFO", "/")
    if method == "GET":
        if path in ("/", "/index.html"):
            return _static("index.html")
        if path == "/sw.js":
            return _static("sw.js")
        if path == "/manifest.webmanifest":
            return "200 OK", [("Content-Type", "application/manifest+json")], _manifest()
        if re.fullmatch(r"/icon-(?:192|512|180)\.png", path):
            ensure_icons()
            return _static(path[1:])
        raise HTTPError(404, "Нет такой страницы.")
    if method != "POST":
        raise HTTPError(405, "Метод не поддерживается.")
    if path == "/api/pair":
        _check_key(environ)
        return _json({"ok": True, "name": "Atlas"})
    if path == "/api/ask":
        _check_key(environ)
        try:
            data = json.loads(_body(environ, 64 * 1024) or b"{}")
        except ValueError:
            raise HTTPError(400, "Нужен JSON.")
        text = str(data.get("text") or "").strip()[:2000]
        if not text:
            raise HTTPError(400, "Пустой вопрос.")
        return _json(_answer(text))
    if path == "/api/talk":
        _check_key(environ)
        data = _body(environ, MAX_AUDIO)
        if not data:
            raise HTTPError(400, "Пустая запись.")
        heard = transcribe(data, environ.get("CONTENT_TYPE", ""))
        if not heard:
            return _json({"heard": "", "text": "", "audio": None, "mime": None})
        print(f"[телефон] 📱 {heard}")
        return _json(_answer(heard, heard))
    raise HTTPError(404, "Нет такой страницы.")


_REASON = {400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 405: "Method Not Allowed", 413: "Payload Too Large",
           500: "Internal Server Error"}


def app(environ, start_response):
    try:
        status, headers, data = _route(environ)
    except HTTPError as e:
        status, headers, data = _json({"error": e.message}, f"{e.status} {_REASON.get(e.status, 'Error')}")
    except Exception as e:
        print(f"[телефон] ошибка: {e}")
        status, headers, data = _json({"error": "Внутренняя ошибка Atlas."}, "500 Internal Server Error")
    start_response(status, headers + [("Content-Length", str(len(data)))])
    return [data]


# =============================================================================
# Иконки: звезда Atlas
# =============================================================================
def ensure_icons() -> None:
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError:
        return
    for size in (192, 512, 180):
        path = os.path.join(STATIC, f"icon-{size}.png")
        if os.path.exists(path):
            continue
        img = Image.new("RGB", (size, size), (5, 8, 20))
        glow = Image.new("RGB", (size, size), (0, 0, 0))
        d = ImageDraw.Draw(glow)
        c, r = size / 2, size * 0.30
        d.ellipse((c - r, c - r, c + r, c + r), fill=(60, 120, 255))
        glow = glow.filter(ImageFilter.GaussianBlur(size * 0.09))
        img = Image.blend(img, glow, 0.85)
        d = ImageDraw.Draw(img)
        rr = size * 0.11
        d.ellipse((c - rr, c - rr, c + rr, c + rr), fill=(225, 238, 255))
        img.save(path)


# =============================================================================
# Запуск
# =============================================================================
class _Server(socketserver.ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _Quiet(WSGIRequestHandler):
    def log_message(self, *a):
        pass


def start(port: int = None) -> int:
    """Запустить сервер (только на этом компьютере; наружу его выводит туннель). → порт."""
    if _state["server"] is not None:
        return _state["port"]
    ensure_icons()
    srv = make_server("127.0.0.1", PORT if port is None else port, app, server_class=_Server, handler_class=_Quiet)
    _state.update(server=srv, port=srv.server_port)
    _state["thread"] = threading.Thread(target=srv.serve_forever, daemon=True, name="phone-server")
    _state["thread"].start()
    print(f"[телефон] сервер на порту {srv.server_port}")
    return srv.server_port


def stop() -> None:
    if _state["server"] is not None:
        _state["server"].shutdown()
        _state["server"] = None


def connect(force: bool = False) -> str:
    """Сервер + защищённый адрес. → https-адрес или "" (тогда в _state['hint'] — что установить)."""
    from phone import tunnel
    port = start()
    if _state["url"] and not force:
        return _state["url"]
    url, kind, hint = tunnel.start(port)
    _state.update(url=url, kind=kind, hint=hint)
    if url:
        print(f"[телефон] адрес для телефона: {url} ({kind})")
    else:
        print(f"[телефон] защищённого адреса нет: {hint}")
    return url or ""


def start_if_paired() -> None:
    """При запуске Atlas: если телефон уже сопряжён — поднять сервер и адрес в фоне."""
    if is_paired() and (os.getenv("PHONE_ENABLED") or "on").lower() not in ("off", "0", "false"):
        threading.Thread(target=connect, daemon=True, name="phone-connect").start()
