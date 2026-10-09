"""
Ранние уши: «Атлас» слышно уже через ~1 секунду после запуска, а не после загрузки всего остального.

Пока main.py несколько секунд загружает мозг, навыки, окно и службы, этот поток уже открыл микрофон
и слушает имя локально (Vosk). Если ты позвал Atlas в это время, первый же разговор начнётся сразу:
take() отдаёт голосовому циклу «имя было» и звук после имени (там, может быть, уже команда).

Модуль лёгкий на импорт: numpy, sounddevice и vosk — без pygame, моделей ИИ и интерфейса.
"""
import json
import os
import threading
import time

SAMPLE_RATE = 16000
WAKE_CONF = 0.9
MAX_AFTER_S = 10                     # звук после имени храним не дольше
QUIET_S = 0.8                        # после имени замолчал на столько — фраза закончена

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(ROOT, "models", "vosk-model-small-ru-0.22")

_s = {"model": None, "thread": None, "stop": threading.Event(), "woke": None, "after": [], "loud": 0.0,
      "t0": time.time(), "ready": None}
_lock = threading.Lock()


def model(wait: float = 6.0):
    """Загруженная модель Vosk (её подхватывает voice.py — второй раз не грузим). Ждёт, если ещё грузится."""
    deadline = time.time() + wait
    while _s["model"] is None and _s["thread"] is not None and not _s["stop"].is_set() and time.time() < deadline:
        time.sleep(0.05)
    return _s["model"]


def _words():
    """Имя и его формы — как WAKE_WORDS_LOCAL в voice.py (+ свои из .env)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
    except Exception:
        pass
    base = ["атлас", "атласик", "атласа", "атласу", "атласом", "атласе", "этлас", "атлэс", "атлес"]
    extra = [w.strip().lower() for w in os.getenv("WAKE_WORDS_EXTRA", "").split(",") if w.strip()]
    return list(dict.fromkeys(base + extra))


def _run(words) -> None:
    import numpy as np
    import sounddevice as sd
    from vosk import KaldiRecognizer, Model, SetLogLevel
    SetLogLevel(-1)
    m = Model(MODEL_PATH)
    _s["model"] = m
    finder = getattr(m, "find_word", None)
    known = [w for w in words if not finder or finder(w) >= 0] or words[:1]
    rec = KaldiRecognizer(m, SAMPLE_RATE, json.dumps(known + ["[unk]"], ensure_ascii=False))
    rec.SetWords(True)
    with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=4000, dtype="int16", channels=1) as st:
        _s["ready"] = time.time()
        print(f"[запуск] уже слышу «Атлас» — через {_s['ready'] - _s['t0']:.1f} с после запуска")
        while not _s["stop"].is_set():
            data, _ = st.read(4000)
            data = bytes(data)
            with _lock:
                if _s["woke"] is not None:                       # имя было — копим то, что сказано после
                    _s["after"].append(data)
                    rms = float(np.sqrt(np.mean(np.frombuffer(data, np.int16).astype(np.float32) ** 2)))
                    if rms > 500:
                        _s["loud"] = time.time()
                    if len(_s["after"]) * 0.25 > MAX_AFTER_S:
                        _s["after"].pop(0)
                    continue
            if not rec.AcceptWaveform(data):
                continue
            for w in json.loads(rec.Result()).get("result", []):
                if w["word"] in known and w["conf"] >= WAKE_CONF:
                    with _lock:
                        _s["woke"], _s["loud"] = time.time(), time.time()
                    print(f"[wake] {w['word']} ({w['conf']:.2f}) — ещё во время запуска")
                    break


def start() -> bool:
    """Запустить в фоне. False — нет модели или микрофона (тогда всё как раньше)."""
    if os.getenv("EARLY_EARS", "on").lower() in ("off", "0", "no") or not os.path.isdir(MODEL_PATH):
        return False
    words = _words()

    def run():
        try:
            _run(words)
        except Exception as e:
            print(f"[запуск] ранние уши не включились ({e}) — услышу, когда всё загрузится")
        finally:
            _s["stop"].set()
    _s["thread"] = threading.Thread(target=run, daemon=True, name="early-ears")
    _s["thread"].start()
    return True


def take(wait_quiet: bool = True):
    """Голосовой цикл забирает микрофон. → (имя было?, звук после имени int16 или None)."""
    if _s["thread"] is None:
        return False, None
    if wait_quiet and _s["woke"] is not None:            # договорить команду, начатую во время запуска
        deadline = time.time() + MAX_AFTER_S
        while time.time() < deadline and time.time() - _s["loud"] < QUIET_S and not _s["stop"].is_set():
            time.sleep(0.05)
    _s["stop"].set()
    _s["thread"].join(timeout=2)
    with _lock:
        woke, after = _s["woke"], b"".join(_s["after"])
        _s["woke"], _s["after"] = None, []
    _s["thread"] = None
    if not woke:
        return False, None
    import numpy as np
    return True, (np.frombuffer(after, dtype=np.int16).reshape(-1, 1).copy() if after else None)
