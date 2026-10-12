"""
Вторая проверка имени «Атлас», чтобы Atlas не откликался на похожие слова.

Первая ступень (voice.wait_for_wake_word) слушает только список форм имени — она быстрая, но любое похожее
слово («атласный», «отлично», «в класс») может «натянуть» на «атлас». Поэтому, когда она сработала,
тот же кусок звука (имя ± 0,35 с) ещё раз распознаётся той же локальной моделью, но уже со всем словарём.
Если и там слышно «атлас» (или очень похожее) — будим. Если там другое слово — не будим и пишем в журнал:
    [wake] отклонено проверкой: слышу «отлично»
Всё локально, ~0,1 с. WAKE_VERIFY=off в .env — выключить проверку.
"""
import difflib
import json
import os
import re

FORMS = ("атлас", "атласа", "атласу", "атласом", "атласе", "атласик", "этлас", "атлэс", "атлес", "atlas")
SIMILAR = 0.72                     # насколько слово должно быть похоже на имя (0..1)
PAD_S = 0.35


def enabled() -> bool:
    return (os.getenv("WAKE_VERIFY") or "on").strip().lower() not in ("off", "0", "no", "нет")


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def looks_like_name(text: str, forms=FORMS) -> bool:
    """Есть ли в свободно распознанном тексте имя (или почти имя). Учитывает «ат лас» раздельно."""
    words = re.findall(r"[a-zа-яё]+", (text or "").lower().replace("ё", "е"))
    cands = words + [a + b for a, b in zip(words, words[1:])]
    return any(_sim(w, f) >= SIMILAR for w in cands if len(w) >= 4 for f in forms if abs(len(w) - len(f)) <= 3)


def clip(ring, start_s: float, end_s: float, rate: int = 16000):
    """Звук от start_s до end_s (с запасом) из кольца [(номер первого сэмпла, кусок)]."""
    import numpy as np
    a, b = int(max(0.0, start_s - PAD_S) * rate), int((end_s + PAD_S) * rate)
    parts = []
    for s, c in ring:
        lo, hi = max(a, s), min(b, s + len(c))
        if lo < hi:
            parts.append(c[lo - s:hi - s])
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)


def free_text(model, audio, rate: int = 16000) -> str:
    """Свободное распознавание куска (весь словарь модели)."""
    from vosk import KaldiRecognizer
    rec = KaldiRecognizer(model, rate)
    rec.AcceptWaveform(audio.astype("int16").tobytes())
    return json.loads(rec.FinalResult()).get("text", "")


def verify(model, ring, word: dict, rate: int = 16000, recognize=None):
    """→ (будить?, что услышала проверка). Ошибка проверки — будим (лучше лишний раз, чем не отозваться)."""
    if not enabled():
        return True, ""
    try:
        audio = clip(ring, float(word.get("start", 0)), float(word.get("end", 0)), rate)
        if len(audio) < rate * 0.2:
            return True, ""
        text = (recognize or (lambda a: free_text(model, a, rate)))(audio)
        return looks_like_name(text), text
    except Exception as e:
        print(f"[wake] проверка не удалась ({e}) — считаю, что звали")
        return True, ""
