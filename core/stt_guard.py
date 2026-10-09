"""
Распознавание речи без «официантов».

Whisper на коротких фразах иногда уверенно пишет чепуху («выключись» → «Официанты.»), а мозг потом
честно ищет официантов в интернете. Здесь три защиты:

    prepare(audio, spans)   обрезать тишину по краям, выровнять громкость, добавить немного тишины —
                            Whisper на таком звуке ошибается заметно реже
    local_command(words)    короткую команду («выключись», «стоп», «громче») параллельно слушает
                            локальный Vosk со списком команд; если Whisper сомневается, верим Vosk
    pick(cands, vosk)       выбрать итог и решить, не переспросить ли («Не расслышал — повторите?»),
                            вместо того чтобы выполнять случайную фразу

Модуль без звука и сети — его проверяют тесты.
"""
import re

import numpy as np

SAMPLE_RATE = 16000

# Короткие команды, которые Vosk слышит локально. Только безопасные: «да»/«нет» сюда не входят —
# ими подтверждаются планы с файлами, угадывать их нельзя.
COMMANDS = (
    "выключись", "выключайся", "отключись", "выключи себя", "заверши работу",
    "стоп", "хватит", "стой", "отмена", "замолчи", "прекрати",
    "пауза", "поставь на паузу", "продолжи", "продолжай",
    "дальше", "следующий", "следующий трек", "следующая песня", "предыдущий трек", "назад",
    "громче", "тише", "сделай громче", "сделай тише", "без звука", "включи звук",
    "включи музыку", "выключи музыку",
)
_OFF = {"выключись", "выключайся", "отключись", "выключи себя", "заверши работу"}   # выключение — только если Whisper явно сомневается
LOCAL_CONF = 0.9          # Vosk уверен в каждом слове команды не меньше
SHORT_S = 2.5             # дольше — это уже не короткая команда
DOUBT_SCORE = -0.6        # средний logprob Whisper ниже этого при разных вариантах — сомневается
LOW_SCORE = -0.9          # ниже — сомневается даже при одинаковых вариантах
NO_SPEECH = 0.6           # Whisper сам считает, что речи не было


def norm(text: str) -> str:
    t = re.sub(r"[^\w\s-]", " ", (text or "").lower().replace("ё", "е"))
    t = re.sub(r"^(?:атлас|atlas)\s+", "", re.sub(r"\s+", " ", t).strip())
    return re.sub(r"\s+(?:пожалуйста|please)$", "", t).strip()


# ---------------------------------------------------------------------------
# Звук
# ---------------------------------------------------------------------------
def prepare(audio: np.ndarray, spans=None, pad_s: float = 0.4) -> np.ndarray:
    """int16 → int16: края без тишины (по меткам речи VAD), ровная громкость, тишина по краям."""
    a = np.asarray(audio).reshape(-1).astype(np.int16)
    if spans:
        s = max(0, int(spans[0]["start"]) - int(0.25 * SAMPLE_RATE))
        e = min(len(a), int(spans[-1]["end"]) + int(0.3 * SAMPLE_RATE))
        if e - s > int(0.2 * SAMPLE_RATE):
            a = a[s:e]
    f = a.astype(np.float32)
    peak = float(np.max(np.abs(f))) if len(f) else 0.0
    if 300 < peak < 16000:                                   # тихий микрофон — поднять, но не больше чем в 6 раз
        f *= min(6.0, 29000.0 / peak)
    pad = np.zeros(int(pad_s * SAMPLE_RATE), dtype=np.float32)
    return np.clip(np.concatenate([pad, f, pad]), -32768, 32767).astype(np.int16)


def speech_seconds(spans) -> float:
    return sum(t["end"] - t["start"] for t in (spans or [])) / SAMPLE_RATE


# ---------------------------------------------------------------------------
# Локальная команда (Vosk)
# ---------------------------------------------------------------------------
def grammar(known=None) -> list:
    """Фразы для Vosk: только те, все слова которых модель знает."""
    out = [c for c in COMMANDS if known is None or all(known(w) for w in c.split())]
    return out + ["[unk]"]


def local_command(words: list, text: str = "") -> str:
    """Результат Vosk ({"word", "conf"}…) → команда, если он уверенно услышал ровно её, иначе ''."""
    if not words:
        return ""
    if any(w.get("word") == "[unk]" or float(w.get("conf", 0)) < LOCAL_CONF for w in words):
        return ""
    heard = " ".join(w["word"] for w in words).strip()
    return heard if heard in COMMANDS else ""


# ---------------------------------------------------------------------------
# Выбор
# ---------------------------------------------------------------------------
def pick(cands: list, vosk: str = "", seconds: float = 0.0) -> dict:
    """cands: [(текст, средний logprob, вероятность тишины)] от Whisper.
    → {"text", "source": whisper|vosk, "doubt": переспросить, "why"}"""
    cands = [(str(t or "").strip(), float(s), float(ns or 0)) for t, s, ns in cands if str(t or "").strip()]
    if not cands:
        return {"text": vosk, "source": "vosk", "doubt": False, "why": "whisper пуст"} if vosk else \
            {"text": "", "source": "whisper", "doubt": False, "why": "пусто"}
    best = max(cands, key=lambda c: c[1])
    text, score, no_speech = best
    agree = len({norm(c[0]) for c in cands}) == 1
    words = len(norm(text).split())
    shaky = (not agree and score < DOUBT_SCORE) or score < LOW_SCORE or (no_speech > NO_SPEECH and score < -0.4)
    unsure = shaky or (words <= 2 and score < -0.35 and vosk not in _OFF)
    if vosk and norm(text) != vosk and seconds <= SHORT_S and unsure:
        return {"text": vosk, "source": "vosk", "doubt": False,
                "why": f"Whisper «{text}» ({score:.2f}) не уверен, Vosk слышит команду"}
    if vosk and norm(text) == vosk:
        return {"text": text, "source": "whisper", "doubt": False, "why": "совпало с Vosk"}
    doubt = shaky and words <= 3
    return {"text": text, "source": "whisper", "doubt": doubt,
            "why": f"{score:.2f}{'' if agree else ', варианты разные'}{', похоже на тишину' if no_speech > NO_SPEECH else ''}"}


REPEAT = {"ru": ["Не расслышал — повторите?", "Простите, не разобрал. Ещё раз?"],
          "en": ["Sorry, I didn't catch that — say it again?", "Didn't quite get that, sir. Once more?"]}
