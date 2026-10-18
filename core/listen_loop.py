"""
Слушание фразы с ранним распознаванием — чтобы Atlas отвечал быстрее.

Раньше: ждём паузу 1,2 с → только потом отправляем звук на распознавание (ещё ~0,5–0,8 с).
Теперь:
  • на паузе 0,45 с звук уже уходит на распознавание (в фоне);
  • продолжил говорить — этот вариант выбрасывается, на следующей паузе попытка снова (не больше 2 за фразу);
  • пауза дошла до конца — текст обычно уже готов: экономия ~0,5–0,8 с;
  • если готовый текст — законченная команда, которую Atlas знает («поставь хром слева»), ждать полную паузу
    не нужно: фраза заканчивается через 0,7 с тишины.
SPECULATIVE_STT=off в .env — как раньше.

    run(read, vad_prob, ...) → (звук, готовый_текст_или_None, заметка "hit"|"stale"|"none"|"early")
"""
import os
import threading
import time
from collections import deque

import numpy as np

SR = 16000
BLOCK = 512                    # 32 мс — размер блока для Silero VAD
SPEC_PAUSE_S = 0.45
EARLY_END_S = 0.7
SPEC_MAX_S = 10.0              # длинные монологи не распознаём заранее — экономим запросы
MAX_SPECS = 2
ON, OFF = 0.5, 0.35            # пороги вероятности речи (как в Silero VADIterator)


def enabled() -> bool:
    return (os.getenv("SPECULATIVE_STT") or "on").strip().lower() not in ("off", "0", "false", "no")


def run(read, vad_prob, max_duration: float, silence_limit: float, start_timeout: float,
        transcribe=None, is_complete=None, level=None, clock=time.time):
    """read() → блок int16 (BLOCK сэмплов); vad_prob(блок) → 0..1.
    transcribe(звук) → текст (в фоне); is_complete(текст) → True, если это законченная команда."""
    preroll = deque(maxlen=10)
    frames, started = [], False
    silence = 0.0
    t0 = clock()
    spec = None
    specs = 0
    note = "none"
    dt = BLOCK / SR
    speculate = transcribe is not None and enabled()
    while True:
        chunk = read()
        if level:
            level(chunk)
        p = vad_prob(chunk)
        if not started:
            preroll.append(chunk)
            if p >= ON:
                started = True
                frames.extend(preroll)
            elif clock() - t0 > start_timeout:
                return None, None, "none"
            continue
        frames.append(chunk)
        if p >= ON:
            silence = 0.0
            if spec is not None:                       # заговорил снова — ранний вариант устарел
                spec["stale"] = True
                spec = None
                note = "stale"
        elif p < OFF:
            silence += dt
        dur = len(frames) * dt
        if speculate and spec is None and specs < MAX_SPECS and silence >= SPEC_PAUSE_S and dur <= SPEC_MAX_S:
            spec = _launch(np.concatenate(frames), transcribe)
            specs += 1
        if spec is not None and spec["done"].is_set() and not spec["stale"] and silence >= EARLY_END_S \
                and spec["text"] and is_complete is not None:
            try:
                if is_complete(spec["text"]):
                    return np.concatenate(frames), spec["text"], "early"
            except Exception:
                pass
        if silence >= silence_limit or dur > max_duration:
            break
    audio = np.concatenate(frames)
    if spec is not None and not spec["stale"]:
        spec["done"].wait(4.0)
        if spec["done"].is_set() and spec["error"] is None:
            return audio, spec["text"] or "", "hit"
    return audio, None, note


def _launch(audio, transcribe) -> dict:
    s = {"text": None, "error": None, "stale": False, "done": threading.Event()}

    def work():
        try:
            s["text"] = transcribe(audio)
        except Exception as e:
            s["error"] = e
        finally:
            s["done"].set()
    threading.Thread(target=work, daemon=True, name="early-stt").start()
    return s


def complete_command(text: str) -> bool:
    """Законченная команда, которую Atlas выполняет сам (маршрутизатор её узнал)."""
    words = (text or "").lower().replace(",", " , ").split()
    if not text or len(text) < 4 or not words or words[-1] in (",", "и", "а", "но", "или", "что", "чтобы", "and", "or"):
        return False
    try:
        from core import intent_router
        return intent_router.route(text).label != "other"
    except Exception:
        return False
