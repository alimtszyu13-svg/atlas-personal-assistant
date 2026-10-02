"""
Жесты и хлопки.

Жесты распознаются в интерфейсе (MediaPipe в окне Atlas, на видеокарте), а сюда
приходят уже готовые действия: wake / stop / yes / no / pause (кулак — музыка).

Хлопки — здесь, по звуку из общего потока микрофона:
  • хлопок = резкий широкополосный всплеск, который гаснет за десятки миллисекунд
    (речь и музыка так не умеют: у них плавная огибающая и тональный спектр);
  • два хлопка с паузой 0,12–0,8 с — Atlas просыпается, как после «Атлас»;
  • пока Atlas говорит или слушает команду, хлопки не ловятся (его голос из колонок
    и твоя речь не должны его будить);
  • чувствительность: CLAP_SENSITIVITY в .env (1.0 — по умолчанию, 1.5 — чувствительнее).
"""
import json
import os
import threading
import time
from collections import deque

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFS = os.path.join(ROOT, "gestures.json")
BLOCK = 160                                   # 10 мс при 16 кГц
_state = {"claps": True, "running": False}
_last_action = {"t": 0.0}


def _load() -> None:
    try:
        _state.update(json.load(open(PREFS, encoding="utf-8")))
    except Exception:
        pass


def _save() -> None:
    try:
        json.dump({"claps": _state["claps"]}, open(PREFS, "w", encoding="utf-8"))
    except Exception:
        pass


def settings() -> dict:
    return {"claps": bool(_state["claps"])}


def set_claps(on: bool) -> dict:
    _state["claps"] = bool(on)
    _save()
    print(f"[жесты] хлопки: {'вкл' if on else 'выкл'}")
    return settings()


# ---------------------------------------------------------------------------
# Действия
# ---------------------------------------------------------------------------
def action(name: str) -> str:
    """Выполнить действие жеста. Возвращает короткий итог для подсказки в интерфейсе."""
    from ui_state import shared_state
    now = time.time()
    if now - _last_action["t"] < 0.6:            # двойное срабатывание одного жеста
        return "skip"
    _last_action["t"] = now
    print(f"[жесты] → {name}")
    if name == "wake":
        if shared_state.get("state") in ("listening", "thinking"):
            return "busy"
        from voice import trigger_push_to_talk
        trigger_push_to_talk()
        return "ok"
    if name == "stop":
        try:
            import pygame
            from voice import _stop_speaking
            _stop_speaking.set()
            pygame.mixer.music.stop()
        except Exception:
            pass
        try:
            from ai_brain import cancel_current_task
            cancel_current_task()
        except Exception:
            pass
        return "ok"
    if name in ("yes", "no"):
        hist = shared_state.get("chat_history") or []
        last = str(hist[-1][1]) if hist and hist[-1][0] == "Atlas" else ""
        if shared_state.get("follow_up") or last.rstrip().endswith("?"):
            shared_state.setdefault("manual_queue", []).append("Да" if name == "yes" else "Нет")
            return "ok"
        return "no_question"
    if name == "pause":
        try:
            import pyautogui
            pyautogui.press("playpause")
            return "ok"
        except Exception as e:
            return f"error: {e}"
    return "unknown"


# ---------------------------------------------------------------------------
# Хлопки
# ---------------------------------------------------------------------------
def _sens() -> float:
    try:
        return max(0.3, min(3.0, float(os.getenv("CLAP_SENSITIVITY") or 1.0)))
    except ValueError:
        return 1.0


class ClapDetector:
    """Получает блоки звука по 10 мс; feed() → True, когда услышан двойной хлопок.

    Хлопок: резкий подъём (в 4+ раза громче предыдущих 30 мс — так отделяется от музыки
    и речи на фоне), шумовой спектр (много пересечений нуля), длится 10–30 мс
    (щелчок клавиши гаснет быстрее) и затем падает до уровня фона.
    Двойной хлопок: ровно два хлопка с паузой 0,12–0,8 с и тишиной от всплесков
    перед первым (печать на клавиатуре — это много всплесков подряд)."""

    def __init__(self, sens: float = 1.0):
        self.sens = sens
        self.prev = deque(maxlen=3)               # энергия последних 30 мс до всплеска
        self.cand = None                          # [время, энергия, фон до, блоков после, энергия во 2-м блоке, макс. потом]
        self.claps = []
        self.bursts = deque(maxlen=20)            # любые всплески — чтобы отсеять печать
        self.cool_until = 0.0

    def _is_burst(self, x: np.ndarray, e: float) -> bool:
        before = (sum(self.prev) / len(self.prev)) if self.prev else 0.003
        peak = float(np.max(np.abs(x)))
        zcr = float(np.mean(np.abs(np.diff(np.sign(x))) > 0))
        return (e > 0.02 / self.sens and peak > 0.12 / self.sens and e > max(before, 0.002) * 4.0 and zcr > 0.12)

    def feed(self, x: np.ndarray, t: float) -> bool:
        e = float(np.sqrt(np.mean(x * x)))
        hit = False
        if self.cand is not None:
            c = self.cand
            c[3] += 1
            if c[3] == 1:
                c[4] = e                          # 10–20 мс после начала: у хлопка ещё звучит
            elif c[3] >= 3:
                c[5] = max(c[5], e)
            if c[3] >= 6:
                self.cand = None
                ct, ce, before = c[0], c[1], c[2]
                sustained = c[4] >= ce * 0.12     # щелчок клавиши к этому моменту уже затих
                decayed = c[5] < max(ce * 0.3, before * 2.0)
                self.bursts.append(ct)
                if sustained and decayed:
                    hit = self._clap(ct)
        elif t >= self.cool_until and self._is_burst(x, e):
            before = (sum(self.prev) / len(self.prev)) if self.prev else 0.003
            self.cand = [t, e, before, 0, 0.0, 0.0]
        if self.cand is None:
            self.prev.append(e)
        return hit

    def _clap(self, t: float) -> bool:
        self.claps = [c for c in self.claps if t - c < 1.0] + [t]
        if len(self.claps) >= 2 and 0.12 <= self.claps[-1] - self.claps[-2] <= 0.8:
            first = self.claps[-2]
            noisy_before = any(first - 0.8 < b < first - 0.05 for b in self.bursts)
            self.claps = []
            if noisy_before:                      # перед «хлопками» были другие всплески — это печать или стук
                return False
            self.cool_until = t + 2.0
            return True
        return False


def _clap_loop() -> None:
    from voice import _mic_raw
    from ui_state import shared_state
    det = ClapDetector(_sens())
    rd = _mic_raw()
    rd.start()
    print(f"[жесты] слушаю хлопки (чувствительность {det.sens:g}) — два хлопка будят Atlas")
    try:
        while True:
            data, _ = rd.read(BLOCK)
            if not _state["claps"]:
                time.sleep(0.2)
                continue
            st = shared_state.get("state")
            if st in ("speaking", "listening", "thinking"):
                det.cand, det.claps = None, []    # голос Atlas и твоя речь — не хлопки
                continue
            x = np.frombuffer(bytes(data), dtype=np.int16).astype(np.float32) / 32768.0
            if det.feed(x, time.time()):
                print("[жесты] 👏👏 два хлопка — слушаю")
                shared_state["gesture_flash"] = {"t": time.time(), "g": "clap"}
                action("wake")
    except Exception as e:
        print(f"[жесты] хлопки остановлены: {e}")


def start() -> None:
    _load()
    if _state["running"]:
        return
    _state["running"] = True
    threading.Thread(target=_clap_loop, daemon=True, name="claps").start()
