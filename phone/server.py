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


# Облако: быстрый путь ответил «недоступно из облака» → передать просьбу компьютеру (core/pc_link).
# FORWARD(text) → ответ компьютера или None (компьютер не на связи). На самом компьютере — None.
FORWARD = None
PC_HUB = False            # облако принимает связь от компьютера (/api/pc/poll, /api/pc/result)


def _needs_pc(reply) -> bool:
    return isinstance(reply, str) and "недоступно из облака" in reply


def handle_text(text: str, log: bool = True) -> str:
    """Быстрый путь → мозг. Компьютер при этом молчит: отвечает телефон.
    log=False — просьба пришла с телефона через облако: разговор уже записан там."""
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
        if _needs_pc(reply):                           # нет компьютера на связи — пусть ответит мозг, по-человечески
            reply = (FORWARD(text) if FORWARD is not None else None) or None
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
    if log:
        try:
            from core import memory
            memory.log_turn("user", text)
            memory.log_turn("assistant", shown)
        except Exception:
            pass
    return reply


def _parse_voices(raw: str) -> list:
    """'Джарвис:id1,Леонид:id2' → [{'name': 'Джарвис', 'id': 'id1'}, …] (как FISH_VOICES_RU в .env)."""
    out = []
    for item in (raw or "").split(","):
        name, sep, vid = item.partition(":")
        if sep and re.fullmatch(r"[0-9a-fA-F]{16,64}", vid.strip()):
            out.append({"name": name.strip() or vid.strip()[:8], "id": vid.strip()})
    return out


def voices() -> dict:
    """Голоса Fish на выбор: FISH_VOICES_RU / FISH_VOICES_EN (списки) и FISH_VOICE_RU / _EN (голос по умолчанию)."""
    out = {"fish": bool(os.getenv("FISH_API_KEY"))}
    for lang in ("ru", "en"):
        lst = _parse_voices(os.getenv(f"FISH_VOICES_{lang.upper()}") or "")
        one = (os.getenv(f"FISH_VOICE_{lang.upper()}") or "").strip()
        if ":" in one:                                 # вписали в одиночное поле целый список — тоже поймём
            lst += [v for v in _parse_voices(one) if v["id"] not in {x["id"] for x in lst}]
            one = lst[0]["id"] if lst else ""
        if one and one not in {v["id"] for v in lst}:
            lst.insert(0, {"name": "Голос по умолчанию", "id": one})
        out[lang] = lst
    return out


def _voice_choice(environ) -> dict:
    """Голос, выбранный в настройках телефона (заголовки X-Atlas-Voice-Ru / -En); только из разрешённого списка."""
    known = voices()
    got = {}
    for lang in ("ru", "en"):
        vid = (environ.get(f"HTTP_X_ATLAS_VOICE_{lang.upper()}") or "").strip()
        if vid and vid in {v["id"] for v in known[lang]}:
            got[lang] = vid
    return got


def _fish_say(text: str, filename: str, voice_id: str) -> bool:
    """Сказать конкретным голосом Fish (и на компьютере, и в облаке). → удалось ли."""
    import voice
    fn = getattr(voice, "_generate_speech_fish", None) or getattr(voice, "_fish", None)
    if not fn or not os.getenv("FISH_API_KEY"):
        return False
    try:
        fn(text, filename, voice_id)
        return os.path.exists(filename) and os.path.getsize(filename) > 0
    except Exception as e:
        print(f"[телефон] выбранный голос Fish не ответил ({e}) — говорю голосом по умолчанию")
        return False


def synthesize(text: str, choice: dict = None):
    """Голос Atlas файлом для телефона → (base64, mime) или (None, None) — тогда телефон скажет сам."""
    if not text:
        return None, None
    try:
        import voice
        fn = os.path.join(tempfile.mkdtemp(prefix="atlas_phone_"), "reply.mp3")
        vid = (choice or {}).get("ru" if re.search(r"[а-яё]", text, re.I) else "en")
        real = fn if vid and _fish_say(text, fn, vid) else voice._generate_any(text, fn)
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


def _actions() -> list:
    """Что открыть на телефоне после ответа (core/phone_actions: сайт, поиск, приложение…)."""
    try:
        from core import phone_actions
        return phone_actions.take()
    except Exception:
        return []


# Голос потоком: ответ приходит текстом сразу, а звук телефон забирает отдельно по одноразовой ссылке
# /api/say/<id> и начинает играть с первых кусков, пока остальное ещё говорится.
_SAY = {}
_say_lock = threading.Lock()
SAY_TTL = 120


# Файлы для телефона: «пришли мне эссе» — ссылка /api/file/<id> (случайный ключ сам пропуск), живёт 3 часа.
# Файлы не держим в памяти: компьютер присылает их частями прямо на диск, телефон забирает потоком (с докачкой).
_FILES = {}
FILE_TTL = 3 * 3600
FILE_MAX = int(float(os.getenv("FILE_MAX_MB") or 4096) * 1024 * 1024)   # потолок — свободное место на диске
FILE_PART_MAX = 16 * 1024 * 1024        # одна часть загрузки
DISK_RESERVE = 200 * 1024 * 1024        # столько места оставляем свободным
_UPLOADS = os.path.join(tempfile.gettempdir(), "atlas_files")


def _clean_files() -> None:
    now = time.time()
    for k in [k for k, v in _FILES.items() if now - v["t"] > FILE_TTL]:
        v = _FILES.pop(k)
        if v.get("temp"):
            try:
                os.remove(v["path"])
            except OSError:
                pass


def share_file(path: str, name: str = None, temp: bool = False) -> str:
    """Отдать файл телефону по ссылке /api/file/<id>. → id."""
    with _say_lock:
        _clean_files()
        fid = secrets.token_urlsafe(18)
        _FILES[fid] = {"path": path, "name": name or os.path.basename(path), "t": time.time(), "temp": temp,
                       "done": True}
    return fid


def _safe_name(name: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", "_", os.path.basename(name or "file"))[:120] or "file"


def _room_for(size: int) -> None:
    """Хватит ли места на диске облака под файл такого размера."""
    if size > FILE_MAX:
        raise HTTPError(413, f"Файл больше {FILE_MAX // (1024 * 1024)} МБ.")
    os.makedirs(_UPLOADS, exist_ok=True)
    import shutil
    free = shutil.disk_usage(_UPLOADS).free
    if size + DISK_RESERVE > free:
        raise HTTPError(507, f"В облаке сейчас мало места: свободно {max(0, free - DISK_RESERVE) // (1024 * 1024)} МБ.")


def _new_upload(name: str, size: int) -> str:
    _room_for(size)
    safe = _safe_name(name)
    path = os.path.join(_UPLOADS, secrets.token_hex(8) + "_" + safe)
    open(path, "wb").close()
    fid = share_file(path, safe, temp=True)
    with _say_lock:
        _FILES[fid].update(size=size, have=0, done=False)
    return fid


def _copy_body(environ, f, n: int) -> int:
    """Тело запроса → файл, по мегабайту (в памяти не копится)."""
    src, left = environ["wsgi.input"], n
    while left > 0:
        piece = src.read(min(left, 1024 * 1024))
        if not piece:
            break
        f.write(piece)
        left -= len(piece)
    return n - left


def store_upload(data: bytes, name: str) -> str:
    """Файл, присланный компьютером в облако одним куском, → временный файл + ссылка."""
    fid = _new_upload(name, len(data))
    with _say_lock:
        item = _FILES[fid]
    with open(item["path"], "wb") as f:
        f.write(data)
    item.update(have=len(data), done=True)
    return fid


def _upload_item(fid: str) -> dict:
    with _say_lock:
        item = _FILES.get(fid)
    if not item or "size" not in item:
        raise HTTPError(404, "Такой загрузки нет — начни заново.")
    return item


def _length(environ) -> int:
    try:
        return int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return 0


def _pc_file_route(environ, path: str):
    """Компьютер присылает файл: /start (имя, размер) → /part/<id> (части по порядку) → /done/<id>."""
    from urllib.parse import unquote
    _check_key(environ)
    name = unquote(environ.get("HTTP_X_FILE_NAME") or "file")
    if path == "/api/pc/file":                                  # старый способ: весь файл одним запросом
        n = _length(environ)
        if not n:
            raise HTTPError(400, "Пустой файл.")
        fid = _new_upload(name, n)
        item = _upload_item(fid)
        with open(item["path"], "wb") as f:
            got = _copy_body(environ, f, n)
        item.update(have=got, done=got == n)
        if got != n:
            raise HTTPError(400, "Файл пришёл не целиком.")
        _file_ready(environ, fid, name)
        return _json({"id": fid})
    if path == "/api/pc/file/start":
        try:
            size = int(environ.get("HTTP_X_FILE_SIZE") or -1)
        except ValueError:
            size = -1
        if size <= 0:
            raise HTTPError(400, "Нужен размер файла (X-File-Size).")
        return _json({"id": _new_upload(name, size), "part": FILE_PART_MAX})
    if path.startswith("/api/pc/file/part/"):
        item = _upload_item(path[len("/api/pc/file/part/"):])
        try:
            offset = int(environ.get("HTTP_X_OFFSET") or 0)
        except ValueError:
            offset = -1
        n = _length(environ)
        if n > FILE_PART_MAX:
            raise HTTPError(413, "Слишком большая часть.")
        if offset != item["have"] or item["have"] + n > item["size"]:
            return _json({"have": item["have"]}, "409 Conflict")   # компьютер продолжит с нужного места
        with open(item["path"], "r+b") as f:
            f.seek(offset)
            got = _copy_body(environ, f, n)
        item["have"] = offset + got
        item["t"] = time.time()
        return _json({"have": item["have"]})
    if path.startswith("/api/pc/file/done/"):
        fid = path[len("/api/pc/file/done/"):]
        item = _upload_item(fid)
        if item["have"] != item["size"]:
            return _json({"have": item["have"]}, "409 Conflict")
        item.update(done=True, t=time.time())
        _file_ready(environ, fid, item["name"])
        return _json({"id": fid})
    raise HTTPError(404, "Нет такой страницы.")


def _file_ready(environ, fid: str, name: str) -> None:
    if environ.get("HTTP_X_NOTIFY") == "1":
        try:
            from core import push
            push.notify("Atlas · файл с компьютера", name, tag="atlas-file", url=f"/api/file/{fid}")
        except Exception as e:
            print(f"[телефон] уведомление о файле: {e}")


def _range(environ, size: int):
    """Range: bytes=a-b → (a, b) или None. Телефон докачивает и перематывает видео."""
    m = re.match(r"bytes=(\d*)-(\d*)$", (environ.get("HTTP_RANGE") or "").strip())
    if not m or (not m.group(1) and not m.group(2)):
        return None
    if m.group(1):
        a = int(m.group(1))
        b = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    else:
        a, b = max(0, size - int(m.group(2))), size - 1
    if a > b or a >= size:
        raise HTTPError(416, "Неверный диапазон.")
    return a, b


def _stream(path: str, a: int, n: int):
    with open(path, "rb") as f:
        f.seek(a)
        while n > 0:
            piece = f.read(min(n, 256 * 1024))
            if not piece:
                break
            n -= len(piece)
            yield piece


def _file_response(fid: str, environ=None):
    with _say_lock:
        _clean_files()
        item = _FILES.get(fid)
    if not item or not os.path.isfile(item["path"]):
        raise HTTPError(404, "Ссылка на файл устарела — попроси Atlas прислать его ещё раз.")
    if not item.get("done", True):
        pct = int(100 * item.get("have", 0) / max(1, item.get("size", 1)))
        raise HTTPError(409, f"Файл ещё загружается с компьютера ({pct}%) — открой чуть позже.")
    import mimetypes
    from urllib.parse import quote
    size = os.path.getsize(item["path"])
    mime = mimetypes.guess_type(item["name"])[0] or "application/octet-stream"
    headers = [("Content-Type", mime), ("Cache-Control", "no-store"), ("Accept-Ranges", "bytes"),
               ("Content-Disposition", f"inline; filename*=UTF-8''{quote(item['name'])}")]
    r = _range(environ or {}, size) if size else None
    if r:
        a, b = r
        return "206 Partial Content", headers + [("Content-Range", f"bytes {a}-{b}/{size}"),
                                                 ("Content-Length", str(b - a + 1))], _stream(item["path"], a, b - a + 1)
    return "200 OK", headers + [("Content-Length", str(size))], _stream(item["path"], 0, size)


def _audio_mode(environ) -> str:
    """X-Atlas-Audio: stream — звук потоком; none — без звука (озвучка выключена); иначе — файлом в ответе."""
    m = (environ.get("HTTP_X_ATLAS_AUDIO") or "").strip().lower()
    return m if m in ("stream", "none") else "inline"


def _put_say(text: str, choice: dict) -> str:
    sid = secrets.token_urlsafe(18)
    now = time.time()
    with _say_lock:
        for k in [k for k, v in _SAY.items() if now - v["t"] > SAY_TTL]:
            del _SAY[k]
        _SAY[sid] = {"text": text, "choice": choice or {}, "t": now}
    return sid


def stream_audio(text: str, choice: dict = None):
    """Звук ответа кусками. В облаке — поток Fish/Edge; на компьютере — целым файлом (тот же голос)."""
    import voice
    vid = (choice or {}).get("ru" if re.search(r"[а-яё]", text, re.I) else "en")
    fn = getattr(voice, "stream_speech", None)
    if fn:
        try:
            yield from fn(text, vid)
            return
        except Exception as e:
            print(f"[телефон] голос потоком не получился ({e})")
            return
    audio, _ = synthesize(text, choice)
    if audio:
        yield base64.b64decode(audio)


def _answer(text: str, heard: str = None, choice: dict = None, mode: str = "inline") -> dict:
    t0 = time.time()
    _actions()                                          # остатки прошлого ответа не открываем
    reply = handle_text(text)
    acts = _actions()
    t1 = time.time()
    em = _emo()
    shown = em.strip(reply) if em else reply
    audio = mime = say = None
    if reply and mode == "stream":
        say = _put_say(reply, choice)
    elif reply and mode == "inline":
        audio, mime = synthesize(reply, choice)
    print(f"[время] телефон: ответ {t1 - t0:.1f} с" + (f", голос {time.time() - t1:.1f} с" if mode == "inline" else
                                                         ", голос — потоком" if say else ""))
    out = {"text": shown, "audio": audio, "mime": mime, "actions": acts}
    if say:
        out["say"] = say
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
        if path.startswith("/api/file/"):
            return _file_response(path[len("/api/file/"):], environ)
        if path.startswith("/api/say/"):                # одноразовая ссылка на звук ответа — сама и есть пропуск
            with _say_lock:
                item = _SAY.pop(path[len("/api/say/"):], None)
            if not item or time.time() - item["t"] > SAY_TTL:
                raise HTTPError(404, "Звук уже забран или устарел.")
            return "200 OK", [("Content-Type", "audio/mpeg"), ("Cache-Control", "no-store")], \
                stream_audio(item["text"], item["choice"])
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
        return _json(_answer(text, choice=_voice_choice(environ), mode=_audio_mode(environ)))
    if path == "/api/transcribe":                   # ответ на карточку голосом — только текст, без мозга
        _check_key(environ)
        data = _body(environ, MAX_AUDIO)
        if not data:
            raise HTTPError(400, "Пустая запись.")
        return _json({"text": transcribe(data, environ.get("CONTENT_TYPE", ""))})
    if path == "/api/voices":
        _check_key(environ)
        return _json(voices())
    if path == "/api/voice/test":                      # «Послушать» в настройках
        _check_key(environ)
        try:
            data = json.loads(_body(environ, 4096) or b"{}")
        except ValueError:
            data = {}
        lang = "en" if data.get("lang") == "en" else "ru"
        sample = "Добрый вечер, сэр. Так я буду звучать." if lang == "ru" else "Good evening, sir. This is how I'll sound."
        audio, mime = synthesize(sample, _voice_choice(environ))
        return _json({"text": sample, "audio": audio, "mime": mime})
    if PC_HUB and path == "/api/pc/notify":                     # облако: компьютер просит уведомить телефон
        _check_key(environ)
        try:
            data = json.loads(_body(environ, 8192) or b"{}")
        except ValueError:
            raise HTTPError(400, "Нужен JSON.")
        url = str(data.get("url") or "/")
        if not (url == "/" or url.startswith(("https://", "/api/file/"))):
            url = "/"
        from core import push
        n = push.notify(str(data.get("title") or "Atlas")[:80], str(data.get("body") or "")[:300], tag="atlas-pc", url=url)
        return _json({"sent": n})
    if PC_HUB and (path == "/api/pc/file" or path.startswith("/api/pc/file/")):   # облако: файл с компьютера
        return _pc_file_route(environ, path)
    if PC_HUB and path in ("/api/pc/poll", "/api/pc/result"):     # облако: связь с компьютером
        _check_key(environ)
        try:
            data = json.loads(_body(environ, 64 * 1024) or b"{}")
        except ValueError:
            raise HTTPError(400, "Нужен JSON.")
        from core import pc_link
        if path == "/api/pc/poll":
            return _json(pc_link.poll(name=str(data.get("name") or "")))
        return _json({"ok": pc_link.result(str(data.get("id") or ""), str(data.get("text") or ""))})
    if path == "/api/pc/status":
        _check_key(environ)
        from core import pc_link
        return _json({"online": bool(PC_HUB and pc_link.online())})
    from phone import panels
    if path in panels.ROUTES:
        _check_key(environ)
        try:
            data = json.loads(_body(environ, 64 * 1024) or b"{}")
        except ValueError:
            raise HTTPError(400, "Нужен JSON.")
        return _json(panels.ROUTES[path](data if isinstance(data, dict) else {}))
    if path == "/api/talk":
        _check_key(environ)
        data = _body(environ, MAX_AUDIO)
        if not data:
            raise HTTPError(400, "Пустая запись.")
        heard = transcribe(data, environ.get("CONTENT_TYPE", ""))
        if not heard:
            return _json({"heard": "", "text": "", "audio": None, "mime": None})
        print(f"[телефон] 📱 {heard}")
        return _json(_answer(heard, heard, _voice_choice(environ), _audio_mode(environ)))
    raise HTTPError(404, "Нет такой страницы.")


_REASON = {400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 405: "Method Not Allowed", 409: "Conflict",
           413: "Payload Too Large", 416: "Range Not Satisfiable", 500: "Internal Server Error", 507: "Insufficient Storage"}


def app(environ, start_response):
    try:
        status, headers, data = _route(environ)
    except HTTPError as e:
        status, headers, data = _json({"error": e.message}, f"{e.status} {_REASON.get(e.status, 'Error')}")
    except Exception as e:
        print(f"[телефон] ошибка: {e}")
        status, headers, data = _json({"error": "Внутренняя ошибка Atlas."}, "500 Internal Server Error")
    if isinstance(data, (bytes, bytearray)):
        start_response(status, [h for h in headers if h[0] != "Content-Length"] + [("Content-Length", str(len(data)))])
        return [data]
    start_response(status, headers)                     # поток (звук): без длины, куски уходят сразу
    return _chunks(data)


def _chunks(gen):
    try:
        for chunk in gen:
            if chunk:
                yield chunk
    except Exception as e:
        print(f"[телефон] поток оборвался: {e}")


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


def start(port: int = None, host: str = "127.0.0.1") -> int:
    """Запустить сервер. На компьютере — только для него самого (наружу выводит туннель);
    в облаке — host="0.0.0.0". → порт."""
    if _state["server"] is not None:
        return _state["port"]
    ensure_icons()
    srv = make_server(host, PORT if port is None else port, app, server_class=_Server, handler_class=_Quiet)
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
