"""
Перерывы и осанка — деликатно, без резких команд.

Перерывы (включены всегда, без камеры): Atlas считает, сколько ты непрерывно за компьютером (мышь и
клавиатура; 5 минут без них — уже перерыв). Через ~55 минут — мягко: «Сэр, вы работаете уже почти час
без перерыва. Рекомендовал бы встать и немного размяться». Не чаще раза в 40 минут.

Осанка (только если попросишь: «следи за осанкой»): раз в 2 минуты один кадр с веб-камеры — где лицо и
какого оно размера. Ссутулился (лицо заметно ниже и ближе, чем когда сидел ровно) три проверки подряд —
мягкое напоминание, не чаще раза в 30 минут. Кадры нигде не сохраняются. «Я сижу ровно» — запомнить,
как правильно. «Не следи за осанкой» — выключить.

Молчит: во время записи, суфлёра, игры, полноэкранного видео, фокуса (тогда — только текстом в чат) и
если ты отошёл. «Перерывы реже / чаще / не напоминай про перерывы».
"""
import json
import os
import random
import re
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "atlas_wellbeing.json")
BREAK_AFTER = 55 * 60
BREAK_GAP = 40 * 60
IDLE_RESET = 5 * 60
POSTURE_EVERY = 120
POSTURE_GAP = 30 * 60
SLOUCH_HITS = 3
_s = {"active_since": None, "last_break_say": -1e12, "last_posture_say": -1e12, "hits": 0, "thread": None}

BREAK_RU = [
    "Сэр, вы работаете уже почти час без перерыва. Рекомендовал бы встать и немного размяться — минут пять.",
    "Сэр, прошу прощения, что отвлекаю: около часа без перерыва. Может быть, стоит пройтись и дать глазам отдохнуть?",
    "Сэр, позволю себе напомнить: час за экраном. Короткая разминка сейчас поможет сосредоточиться потом.",
]
BREAK_EN = ["Sir, you've been working for almost an hour without a break. I'd suggest standing up and stretching "
            "for a few minutes.",
            "Sir, sorry to interrupt — about an hour without a break. Perhaps a short walk to rest your eyes?"]
POSTURE_RU = [
    "Сэр, деликатно напомню об осанке — рекомендовал бы выпрямиться и расслабить плечи.",
    "Сэр, кажется, вы немного ссутулились. Если удобно — выпрямитесь, спине будет легче.",
    "Сэр, позволю себе маленькую подсказку: спина ровнее, плечи назад — и экран чуть дальше.",
]
POSTURE_EN = ["Sir, a gentle reminder about your posture — I'd suggest sitting up and relaxing your shoulders.",
              "Sir, you seem to be slouching a little. If it's comfortable, straighten up — your back will thank you."]


def cfg() -> dict:
    try:
        with open(CFG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"breaks": True, "break_min": 55, "posture": False, "base": None}


def _save(c: dict) -> None:
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)


def _ru() -> bool:
    try:
        from voice import get_response_language
        return get_response_language() == "ru"
    except Exception:
        return True


def _busy() -> str:
    """Почему сейчас нельзя говорить вслух ('' — можно)."""
    try:
        from core import meeting_notes, prompter, game_mode, focus, dictation
        if meeting_notes.active() or prompter.active() or game_mode.active() or dictation.active():
            return "silent"
        if focus.active():
            return "text"
    except Exception:
        pass
    try:
        from core import proactive
        if proactive.is_fullscreen():
            return "silent"
    except Exception:
        pass
    return ""


# --- перерывы ----------------------------------------------------------------------------------------------
def break_tick(idle: float, now: float, c: dict = None) -> str:
    c = c or cfg()
    if not c.get("breaks", True):
        return ""
    if idle >= IDLE_RESET:
        _s["active_since"] = None
        return ""
    if _s["active_since"] is None:
        _s["active_since"] = now - idle
        return ""
    after = int(c.get("break_min", 55)) * 60
    if now - _s["active_since"] >= after and now - _s["last_break_say"] >= BREAK_GAP:
        _s["last_break_say"] = now
        return random.choice(BREAK_RU if _ru() else BREAK_EN)
    return ""


# --- осанка ---------------------------------------------------------------------------------------------------
class Camera:
    def face(self):
        """(центр_y, высота) лица в долях кадра или None. Кадр не сохраняется."""
        try:
            import cv2
        except ImportError:
            import subprocess
            import sys
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "opencv-python-headless"], check=False)
            import cv2
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW) if hasattr(cv2, "CAP_DSHOW") else cv2.VideoCapture(0)
        try:
            ok, frame = False, None
            for _ in range(4):                       # первые кадры у камер тёмные
                ok, frame = cap.read()
            if not ok or frame is None:
                return None
        finally:
            cap.release()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = det.detectMultiScale(gray, 1.15, 5, minSize=(60, 60))
        if len(faces) == 0:
            return None
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        H = gray.shape[0]
        return ((y + h / 2) / H, h / H)


def slouching(face, base) -> bool:
    """Лицо ниже на 6 % кадра или ближе (крупнее) на 18 % — чем когда сидел ровно."""
    if not face or not base:
        return False
    cy, fh = face
    by, bh = base
    return (cy - by) > 0.06 or (fh / bh) > 1.18


def posture_tick(face, now: float, c: dict = None) -> str:
    c = c or cfg()
    if not c.get("posture") or face is None:
        return ""
    if not c.get("base"):
        c["base"] = list(face)                      # первая уверенная проверка — как «ровно»
        _save(c)
        return ""
    if slouching(face, c["base"]):
        _s["hits"] += 1
    else:
        _s["hits"] = 0
    if _s["hits"] >= SLOUCH_HITS and now - _s["last_posture_say"] >= POSTURE_GAP:
        _s["hits"] = 0
        _s["last_posture_say"] = now
        return random.choice(POSTURE_RU if _ru() else POSTURE_EN)
    return ""


# --- фразы -------------------------------------------------------------------------------------------------------
def handle(text: str, camera=None):
    t = " ".join((text or "").split()).strip(" .!").lower()
    c = cfg()
    if re.search(r"(?:не следи|перестань следить|хватит следить|stop watching)\s+(?:за\s+)?(?:моей\s+)?осанк|posture off", t):
        c["posture"] = False
        _save(c)
        return "Хорошо, за осанкой не слежу. Камера больше не включается."
    if re.search(r"(?:следи|присматривай|смотри)\s+за\s+(?:моей\s+)?осанк|watch my posture", t):
        c["posture"] = True
        c["base"] = None
        _save(c)
        return ("Хорошо, сэр. Раз в пару минут буду смотреть одним кадром с камеры, как вы сидите, — кадры не "
                "сохраняются. Сядьте ровно на минутку — я запомню правильное положение.")
    if re.search(r"(?:я\s+)?(?:сижу|сел)\s+ровно|запомни,? как я сижу|i'?m sitting straight", t):
        face = (camera or Camera()).face()
        if not face:
            return "Не вижу лица в камере — сядьте перед ней и скажите ещё раз."
        c["base"] = list(face)
        c["posture"] = True
        _save(c)
        return "Запомнил, сэр. Так и буду сверять."
    if re.search(r"не напоминай (?:про|о) перерыв|без напоминаний о перерывах|no break reminders", t):
        c["breaks"] = False
        _save(c)
        return "Хорошо, про перерывы молчу."
    m = re.search(r"перерыв\w*\s+(реже|чаще)|напоминай (?:про|о) перерыв\w* (?:каждые|раз в)\s+(\d+)\s*мин", t)
    if m:
        if m.group(2):
            c["break_min"] = max(20, min(180, int(m.group(2))))
        else:
            c["break_min"] = min(120, c.get("break_min", 55) + 20) if m.group(1) == "реже" else \
                max(25, c.get("break_min", 55) - 15)
        c["breaks"] = True
        _save(c)
        return f"Хорошо, напоминаю про перерыв примерно раз в {c['break_min']} минут."
    if re.search(r"напоминай (?:про|о) перерыв|break reminders on", t):
        c["breaks"] = True
        _save(c)
        return f"Хорошо, напомню про перерыв примерно через {c.get('break_min', 55)} минут работы."
    return None


def start(announce, every: float = 30, camera=None) -> threading.Thread:
    if _s["thread"] and _s["thread"].is_alive():
        return _s["thread"]

    def say(msg):
        mode = _busy()
        if mode == "silent":
            print(f"[забота] молчу (занят): {msg}")
            return
        if mode == "text":
            try:
                from ui_state import shared_state
                shared_state.setdefault("chat_history", []).append(("Atlas", msg))
            except Exception:
                pass
            return
        announce(msg)

    def loop():
        last_cam = 0
        while True:
            time.sleep(every)
            try:
                from core import focus
                idle = focus._idle()
                now = time.time()
                msg = break_tick(idle, now)
                if msg:
                    say(msg)
                c = cfg()
                if c.get("posture") and idle < 60 and now - last_cam >= POSTURE_EVERY and not _busy():
                    last_cam = now
                    msg = posture_tick((camera or Camera()).face(), now, c)
                    if msg:
                        say(msg)
            except Exception as e:
                print(f"[забота] {e}")
    _s["thread"] = threading.Thread(target=loop, daemon=True, name="wellbeing")
    _s["thread"].start()
    return _s["thread"]
