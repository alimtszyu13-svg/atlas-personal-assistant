"""
Компьютер как руки облачного Atlas.

Телефон говорит с облаком. Всё, что умеет только компьютер (музыка, программы, громкость, файлы,
браузер), облако передаёт компьютеру — если на нём запущен Atlas. Компьютер сам держит связь:
каждые ~25 секунд спрашивает облако «есть дело?» (долгий запрос), выполняет просьбу своим мозгом
и возвращает ответ. Входящих подключений к компьютеру нет — ни портов, ни настроек роутера.

Облако:     submit("включи музыку")  → ответ компьютера (или PcOffline / PcTimeout)
            poll(wait) / result(id, text) — маршруты /api/pc/poll и /api/pc/result
Компьютер:  start_agent(url, key, handler) — фоновый поток в main.py
"""
import json
import threading
import time
import urllib.error
import urllib.request
import uuid

ONLINE_FOR = 45          # компьютер «на связи», если спрашивал облако не позже стольких секунд назад
POLL_WAIT = 25           # сколько облако держит запрос компьютера, если дел нет
SUBMIT_WAIT = 60         # сколько ждать ответа компьютера


class PcOffline(Exception):
    pass


class PcTimeout(Exception):
    pass


# =============================================================================
# Облако: очередь дел для компьютера
# =============================================================================
_cv = threading.Condition()
_queue = []                # [{"id", "text"}]
_results = {}              # id → текст ответа
_state = {"last_poll": 0.0, "name": ""}


def online() -> bool:
    return time.time() - _state["last_poll"] < ONLINE_FOR


def submit(text: str, wait: float = SUBMIT_WAIT) -> str:
    """Передать просьбу компьютеру и дождаться ответа."""
    if not online():
        raise PcOffline()
    job = {"id": uuid.uuid4().hex[:12], "text": str(text)[:2000]}
    deadline = time.time() + wait
    with _cv:
        _queue.append(job)
        _cv.notify_all()
        while job["id"] not in _results:
            left = deadline - time.time()
            if left <= 0:
                if job in _queue:                  # компьютер так и не забрал — отменяем
                    _queue.remove(job)
                raise PcTimeout()
            _cv.wait(min(left, 1.0))
        return _results.pop(job["id"])


def poll(wait: float = POLL_WAIT, name: str = "") -> dict:
    """Компьютер спрашивает «есть дело?». → {"job": {...}} или {"job": None} по истечении wait."""
    deadline = time.time() + wait
    with _cv:
        _state["last_poll"] = time.time()
        _state["name"] = str(name or "")[:60]
        while not _queue:
            left = deadline - time.time()
            if left <= 0:
                _state["last_poll"] = time.time()
                return {"job": None}
            _cv.wait(min(left, 1.0))
            _state["last_poll"] = time.time()
        return {"job": _queue.pop(0)}


def result(job_id: str, text: str) -> bool:
    with _cv:
        _state["last_poll"] = time.time()
        _results[str(job_id)] = str(text or "")
        _cv.notify_all()
    return True


OFFLINE = ("The computer is offline: Atlas isn't running on it right now, so this can't be done. "
           "Say so briefly; it will work once Atlas on the computer is started.")
TIMEOUT = "The computer took too long to answer. Say it didn't respond in time."


def use_computer(request: str) -> str:
    """Инструмент облачного мозга: выполнить просьбу на компьютере пользователя."""
    try:
        reply = submit(request)
    except PcOffline:
        return OFFLINE
    except PcTimeout:
        return TIMEOUT
    return f"Done on the computer. Atlas on the computer replied: {reply}"


SCHEMA = {"type": "function", "function": {
    "name": "use_computer",
    "description": "Does something on the user's computer, where Atlas runs with full control: play music "
                   "(Spotify), open apps or sites, volume, media keys, files, screenshots, games, what the user did "
                   "on the computer and which documents or sites they worked on, reopening them — anything "
                   "that needs the computer. request: the user's request as a short instruction in their language.",
    "parameters": {"type": "object", "properties": {"request": {"type": "string"}}, "required": ["request"]}}}


# =============================================================================
# Компьютер: держит связь с облаком и выполняет просьбы
# =============================================================================
def _post(url: str, key: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "X-Atlas-Key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def run_agent(url: str, key: str, handler, name: str = "computer", stop: threading.Event = None) -> None:
    """Цикл компьютера. handler(text) → ответ. stop — для тестов."""
    url = url.rstrip("/")
    pause, told = 5, None
    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            r = _post(url + "/api/pc/poll", key, {"name": name}, timeout=POLL_WAIT + 20)
            if told is not True:
                print("[руки] связь с облаком есть — телефон может управлять компьютером")
                told = True
            pause = 5
            job = r.get("job")
            if not job:
                continue
            print(f"[руки] 📱 → компьютер: {job['text']}")
            try:
                reply = handler(job["text"])
            except Exception as e:
                reply = f"На компьютере что-то пошло не так: {e}"
            _post(url + "/api/pc/result", key, {"id": job["id"], "text": reply or ""}, timeout=30)
        except urllib.error.HTTPError as e:
            if told is not False:
                why = "ключ телефона не совпадает с облаком (PHONE_KEY в Render)" if e.code == 401 else f"ошибка {e.code}"
                print(f"[руки] облако не пускает: {why} — попробую позже")
                told = False
            stop.wait(pause)
            pause = min(pause * 2, 120)
        except Exception as e:
            if told is not False:
                print(f"[руки] нет связи с облаком ({e}) — попробую позже")
                told = False
            stop.wait(pause)                     # облако просыпается или нет интернета
            pause = min(pause * 2, 120)


def start_agent(url: str, key: str, handler, name: str = "computer") -> threading.Thread:
    t = threading.Thread(target=run_agent, args=(url, key, handler, name), daemon=True, name="pc-link")
    t.start()
    return t


def start_from_env():
    """Компьютер: связаться с облачным Atlas (ATLAS_CLOUD_URL в .env, ключ — как у телефона)."""
    import os
    import socket
    url = (os.getenv("ATLAS_CLOUD_URL") or "").strip().rstrip("/")
    if not url.startswith("https://"):
        print("[руки] облака нет (ATLAS_CLOUD_URL в .env) — телефон управляет компьютером только напрямую")
        return None
    from phone import server
    key = server.pairing_token()
    if not key:
        print("[руки] телефон ещё не сопряжён — скажи «установи себя на телефон», потом перезапусти Atlas")
        return None
    return start_agent(url, key, lambda text: server.handle_text(text, log=False), name=socket.gethostname())
