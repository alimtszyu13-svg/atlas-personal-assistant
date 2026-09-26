# Общее состояние интерфейса — единая точка правды, на которую
# ссылаются main.py (голосовой цикл), web_gui.py (интерфейс),
# core/proactive.py и core/missions.py (уведомления).
import threading
import time

shared_state = {
    "state": "idle",           # idle / listening / thinking / speaking
    "text": "",                # текущая фраза для субтитров под звездой
    "should_quit": False,
    "theme": "dark",           # dark / light
    "accent_color": "#86D6FF", # можно менять из интерфейса
    "chat_history": [],        # список (speaker, text) для раздела «Разговор»
    "manual_queue": [],        # текстовые команды, введённые руками
    "always_listening": False,
    "speech_envelope": [],
    "speech_duration": 0,
    "speech_start_time": 0,
    "mic_level": 0.0,          # громкость микрофона 0..1, пока Atlas слушает
    "notifications": [],       # уведомления для панели справа (последние 100)
    "notif_seq": 0,            # растёт с каждым уведомлением — интерфейс видит новые
}

_notify_lock = threading.Lock()


def notify(kind: str, key: str, text: str = "") -> None:
    """Уведомление для интерфейса.
    kind: 'warn' | 'ok' | 'info' — цвет и звук;
    key:  что случилось ('clipboard', 'disk', 'ram', 'battery', 'break',
          'mission_done', 'mission_failed', 'info') — заголовок берётся
          из словаря интерфейса, поэтому он всегда на выбранном языке."""
    with _notify_lock:
        shared_state["notif_seq"] += 1
        shared_state["notifications"].append({
            "id": shared_state["notif_seq"],
            "kind": kind,
            "key": key,
            "text": str(text)[:300],
            "time": time.strftime("%H:%M"),
        })
        del shared_state["notifications"][:-100]
