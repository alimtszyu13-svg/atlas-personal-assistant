"""
Atlas узнаёт твой голос.

Как работает:
  1) Запись голоса. «Атлас, запомни мой голос» — Atlas просит прочитать три фразы
     и строит «отпечаток» голоса: вектор из 256 чисел (модель WeSpeaker ResNet34,
     обучена на тысячах дикторов VoxCeleb). Хранится локально в models/speaker/.
  2) Проверка. Каждая голосовая команда превращается в такой же вектор и
     сравнивается с отпечатком (косинусное сходство). Похоже — выполняем,
     не похоже — «это не ваш голос», команда пропускается.
  3) Подстройка. Уверенно узнанные команды чуть-чуть подправляют отпечаток —
     Atlas привыкает к твоему голосу в разных условиях (утро, простуда, другой микрофон).

Всё локально: звук никуда не отправляется. Команды с клавиатуры и F9 не проверяются —
это и есть запасной путь, если Atlas вдруг тебя не узнал.
Включить/выключить: «отвечай только мне» / «отвечай всем» или в Настройках.
"""
import json
import os
import re
import threading
import time
import urllib.request

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "models", "speaker")
MODEL = os.path.join(DIR, "voxceleb_resnet34.onnx")
MODEL_URL = "https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34/resolve/main/voxceleb_resnet34.onnx"
PROFILE = os.path.join(DIR, "profile.npy")
SETTINGS = os.path.join(DIR, "settings.json")
SR = 16000
MIN_CHECK_SEC = 0.8          # короче — по голосу не судим, пропускаем без проверки
ADAPT_MARGIN = 0.15          # насколько уверенно надо узнать, чтобы подстроить отпечаток
ADAPT_RATE = 0.05

PHRASES = {
    "ru": ["Сегодня я проверяю, как мой помощник узнаёт мой голос.",
           "Звёзды над городом светят ярче, когда небо чистое.",
           "Пожалуйста, открой мои заметки и напомни про задачи на вечер."],
    "en": ["Today I am checking how my assistant recognises my voice.",
           "The stars above the city shine brighter when the sky is clear.",
           "Please open my notes and remind me about my tasks for tonight."],
}
_ENROLL_RE = re.compile(r"(запомни|запиши|выучи|перезапиши)\w*\s+(мой|мои)\s+голос|"
                        r"(remember|learn|record|enroll)\s+my\s+voice", re.I)
_ONLY_ME_RE = re.compile(r"(отвечай|слушай|реагируй)\s+только\s+(мне|меня|на мой голос)|"
                         r"(only\s+(respond|listen)\s+to\s+me)", re.I)
_EVERYONE_RE = re.compile(r"(отвечай|слушай|реагируй)\s+(всем|всех|любому)|"
                          r"((respond|listen)\s+to\s+everyone)", re.I)

_lock = threading.Lock()
_sess = None
_profile = None
_settings = None


# ---------------------------------------------------------------------------
# Признаки звука: 80 мел-фильтров, как у Kaldi (так обучали WeSpeaker)
# ---------------------------------------------------------------------------
def _mel(f):
    return 1127.0 * np.log(1.0 + f / 700.0)


_BANKS = None


def _mel_banks(n_mels=80, n_fft=512, low=20.0, high=SR / 2):
    global _BANKS
    if _BANKS is None:
        half = n_fft // 2
        bin_hz = SR / n_fft
        mlow, mhigh = _mel(low), _mel(high)
        delta = (mhigh - mlow) / (n_mels + 1)
        mels = _mel(bin_hz * np.arange(half))
        banks = np.zeros((n_mels, half + 1), dtype=np.float64)
        for b in range(n_mels):
            left, center, right = mlow + b * delta, mlow + (b + 1) * delta, mlow + (b + 2) * delta
            up = (mels - left) / (center - left)
            down = (right - mels) / (right - center)
            banks[b, :half] = np.maximum(0.0, np.minimum(up, down))
        _BANKS = banks
    return _BANKS


def _fbank(wav: np.ndarray) -> np.ndarray:
    """int16 или float[-1..1] → (кадры, 80) лог-мел признаки с вычтенным средним."""
    x = wav.astype(np.float64).flatten()
    if np.abs(x).max(initial=0) <= 1.0:
        x = x * 32768.0
    fl, sh = 400, 160                            # окно 25 мс, шаг 10 мс
    if len(x) < fl:
        x = np.pad(x, (0, fl - len(x)))
    n = 1 + (len(x) - fl) // sh
    frames = x[np.arange(fl)[None, :] + sh * np.arange(n)[:, None]]
    frames = frames - frames.mean(axis=1, keepdims=True)
    frames = np.concatenate([frames[:, :1] * 0.03, frames[:, 1:] - 0.97 * frames[:, :-1]], axis=1)
    win = 0.54 - 0.46 * np.cos(2 * np.pi * np.arange(fl) / (fl - 1))
    spec = np.abs(np.fft.rfft(frames * win, n=512)) ** 2
    feats = np.log(np.maximum(spec @ _mel_banks().T, np.finfo(np.float32).eps))
    feats -= feats.mean(axis=0, keepdims=True)
    return feats.astype(np.float32)


# ---------------------------------------------------------------------------
# Модель и отпечаток
# ---------------------------------------------------------------------------
def _ensure_model() -> None:
    if os.path.exists(MODEL) and os.path.getsize(MODEL) > 1_000_000:
        return
    os.makedirs(DIR, exist_ok=True)
    print("[голос] скачиваю модель узнавания голоса (~27 МБ, один раз)…")
    tmp = MODEL + ".part"
    urllib.request.urlretrieve(MODEL_URL, tmp)
    os.replace(tmp, MODEL)
    print("[голос] модель скачана")


def _session():
    global _sess
    if _sess is None:
        _ensure_model()
        import onnxruntime as ort
        _sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    return _sess


def embed(audio: np.ndarray) -> np.ndarray:
    """Звук → нормированный вектор голоса (256 чисел)."""
    s = _session()
    feats = _fbank(audio)[None, :, :]
    out = s.run(None, {s.get_inputs()[0].name: feats})[0][0].astype(np.float32)
    return out / (np.linalg.norm(out) + 1e-9)


def _load():
    global _profile, _settings
    if _settings is None:
        try:
            _settings = json.load(open(SETTINGS, encoding="utf-8"))
        except Exception:
            _settings = {"enabled": False, "threshold": 0.45}
        env_thr = os.getenv("SPEAKER_THRESHOLD")
        if env_thr:
            _settings["threshold"] = float(env_thr)
    if _profile is None and os.path.exists(PROFILE):
        _profile = np.load(PROFILE).astype(np.float32)
    return _profile, _settings


def _save() -> None:
    os.makedirs(DIR, exist_ok=True)
    if _profile is not None:
        np.save(PROFILE, _profile)
    json.dump(_settings, open(SETTINGS, "w", encoding="utf-8"), ensure_ascii=False)


def status() -> dict:
    with _lock:
        prof, st = _load()
    return {"enrolled": prof is not None, "enabled": bool(st.get("enabled")) and prof is not None,
            "threshold": float(st.get("threshold", 0.45))}


def set_enabled(on: bool) -> dict:
    with _lock:
        prof, st = _load()
        st["enabled"] = bool(on) and prof is not None
        _save()
    print(f"[голос] только мой голос: {'вкл' if st['enabled'] else 'выкл'}")
    return status()


# ---------------------------------------------------------------------------
# Проверка команды
# ---------------------------------------------------------------------------
def check(audio) -> tuple:
    """→ (пропустить ли команду, сходство или None)."""
    global _profile
    with _lock:
        prof, st = _load()
    if prof is None or not st.get("enabled") or audio is None:
        return True, None
    if len(audio) < SR * MIN_CHECK_SEC:
        return True, None
    try:
        e = embed(audio)
    except Exception as ex:
        print(f"[голос] проверка недоступна ({ex}) — пропускаю без проверки")
        return True, None
    score = float(np.dot(prof, e))
    thr = float(st.get("threshold", 0.45))
    if score >= thr + ADAPT_MARGIN:              # уверенно узнали — чуть подстраиваем отпечаток
        with _lock:
            p = (1 - ADAPT_RATE) * prof + ADAPT_RATE * e
            _profile = (p / (np.linalg.norm(p) + 1e-9)).astype(np.float32)
            _save()
    return score >= thr, score


def check_last() -> tuple:
    try:
        from voice import _last_audio
        return check(_last_audio.get("audio"))
    except Exception as ex:
        print(f"[голос] {ex}")
        return True, None


# ---------------------------------------------------------------------------
# Запись голоса и голосовые команды модуля
# ---------------------------------------------------------------------------
def _lang() -> str:
    try:
        from voice import get_response_language
        return get_response_language()
    except Exception:
        return "ru"


def enroll(speak) -> str:
    """Три фразы вслух → отпечаток голоса и порог узнавания."""
    global _profile
    from voice import _listen_once
    from ui_state import shared_state
    ru = _lang() == "ru"
    try:
        _session()
    except Exception as ex:
        msg = (f"Не получилось загрузить модель узнавания голоса: {ex}" if ru
               else f"Couldn't load the voice recognition model: {ex}")
        speak(msg)
        return msg
    speak("Сейчас я запомню ваш голос. Я скажу фразу — повторите её за мной обычным голосом."
          if ru else "Let me learn your voice. I'll say a phrase — repeat it after me in your normal voice.")
    embs = []
    for i, phrase in enumerate(PHRASES["ru" if ru else "en"], 1):
        for attempt in range(2):
            speak(f"Фраза {i}: {phrase}" if ru else f"Phrase {i}: {phrase}")
            shared_state["state"] = "listening"
            audio = _listen_once(max_duration=12, silence_limit=1.0, start_timeout=8)
            shared_state["state"] = "idle"
            if audio is not None and len(audio) >= SR * 1.5:
                embs.append(embed(audio))
                break
            speak("Не расслышал, давайте ещё раз." if ru else "I didn't catch that, once more.")
    if len(embs) < 2:
        msg = "Не удалось записать голос — попробуйте в тишине." if ru else "Couldn't record your voice — try somewhere quieter."
        speak(msg)
        return msg
    m = np.mean(embs, axis=0)
    prof = (m / (np.linalg.norm(m) + 1e-9)).astype(np.float32)
    pair = [float(np.dot(a, b)) for k, a in enumerate(embs) for b in embs[k + 1:]]
    thr = float(np.clip(np.mean(pair) - 0.25, 0.30, 0.55)) if pair else 0.45
    with _lock:
        _load()
        _profile = prof
        _settings["threshold"] = float(os.getenv("SPEAKER_THRESHOLD") or thr)
        _settings["enabled"] = True
        _save()
    print(f"[голос] отпечаток записан: фраз {len(embs)}, сходство между ними {np.mean(pair):.2f}, порог {thr:.2f}")
    msg = ("Готово, я запомнил ваш голос и теперь отвечаю только вам. Отключить можно фразой «отвечай всем»."
           if ru else "Done — I've learned your voice and will respond only to you. Say 'respond to everyone' to turn it off.")
    speak(msg)
    return msg


def handle_command(text: str, speak) -> bool:
    """Команды самого модуля — без модели. True, если команда обработана."""
    t = (text or "").strip()
    if _ENROLL_RE.search(t):
        enroll(speak)
        return True
    ru = _lang() == "ru"
    if _ONLY_ME_RE.search(t):
        st = set_enabled(True)
        speak(("Хорошо, отвечаю только вам." if ru else "Alright, I'll respond only to you.") if st["enabled"] else
              ("Сначала нужно записать ваш голос: скажите «запомни мой голос»." if ru
               else "First I need to learn your voice: say 'remember my voice'."))
        return True
    if _EVERYONE_RE.search(t):
        set_enabled(False)
        speak("Хорошо, отвечаю всем." if ru else "Alright, I'll respond to everyone.")
        return True
    return False
