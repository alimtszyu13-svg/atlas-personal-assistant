"""
Конспект созвона, лекции или видео: «запиши созвон» → … → «закончи запись».

Atlas слушает звук самого компьютера (Zoom, Meet, Discord, YouTube — всё, что играет в колонках или
наушниках) и, для созвонов, твой микрофон. Каждые ~3 минуты кусок распознаётся (Groq Whisper turbo:
дёшево, ~4 цента за час), тишина пропускается. В конце — конспект: о чём говорили, решения, задачи
со сроками, открытые вопросы. Потом можно спросить: «что сказали про дедлайн?».

Файлы — в Документы/Atlas/Конспекты: полный текст (.txt) и конспект (.md). Только на этом компьютере.
Запись сама останавливается через 3 часа. Нужна библиотека soundcard (ставится сама при первой записи).
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime

SR = 16000
CHUNK_S = 180                       # кусок для распознавания
MAX_S = 3 * 3600
SILENCE_RMS = 0.004                 # тише — кусок не распознаём (тишина, пауза в видео)
STT_MODEL = os.getenv("MEETING_STT_MODEL") or "whisper-large-v3-turbo"

_s = {"on": False, "title": "", "started": 0.0, "mic": True, "segments": [], "pending": 0, "thread": None,
      "stop": threading.Event(), "last": None, "error": "", "announce": None}
_lock = threading.Lock()


def folder() -> str:
    try:
        from core import file_plans
        base = file_plans.folder_path("documents")
    except Exception:
        base = os.path.expanduser("~/Documents")
    p = os.path.join(base, "Atlas", "Конспекты")
    os.makedirs(p, exist_ok=True)
    return p


def active() -> bool:
    return _s["on"]


# ---------------------------------------------------------------------------
# Звук
# ---------------------------------------------------------------------------
def _soundcard():
    try:
        import soundcard
        return soundcard
    except ImportError:
        print("[конспект] ставлю soundcard (запись звука компьютера)…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "soundcard>=0.4.5"], check=False)
        import soundcard
        return soundcard


def to_16k_mono(block, rate: int):
    """float32 (кадры, каналы) → float32 моно 16 кГц."""
    import numpy as np
    a = np.asarray(block, dtype=np.float32)
    if a.ndim > 1:
        a = a.mean(axis=1)
    if rate != SR:
        n = int(len(a) * SR / rate)
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32) if n else a[:0]
    return a


def mix(a, b):
    import numpy as np
    if b is None or not len(b):
        return a
    n = min(len(a), len(b))
    return np.clip(a[:n] + b[:n], -1.0, 1.0)


def loud_enough(a) -> bool:
    import numpy as np
    return len(a) > 0 and float(np.sqrt(np.mean(a.astype(np.float32) ** 2))) > SILENCE_RMS


def _record_loop(on_chunk, stop: threading.Event, with_mic: bool) -> None:
    import numpy as np
    sc = _soundcard()
    rate = 48000
    spk = sc.default_speaker()
    loop = sc.get_microphone(id=str(spk.name), include_loopback=True)
    mic = sc.default_microphone() if with_mic else None
    block = rate                                             # 1 с
    buf, t0 = [], time.time()
    from contextlib import ExitStack
    with ExitStack() as st:
        lr = st.enter_context(loop.recorder(samplerate=rate, blocksize=1024))
        mr = st.enter_context(mic.recorder(samplerate=rate, channels=1, blocksize=1024)) if mic else None
        print(f"[конспект] пишу звук: {spk.name}" + (f" + микрофон {mic.name}" if mic else ""))
        while not stop.is_set() and time.time() - t0 < MAX_S:
            a = to_16k_mono(lr.record(numframes=block), rate)
            if mr is not None:
                a = mix(a, to_16k_mono(mr.record(numframes=block), rate) * 0.9)
            buf.append(a)
            if len(buf) >= CHUNK_S:
                on_chunk(np.concatenate(buf), time.time() - len(buf))
                buf = []
    if buf:
        on_chunk(np.concatenate(buf), time.time() - len(buf))
    if time.time() - t0 >= MAX_S:
        print("[конспект] 3 часа — запись остановлена")
        threading.Thread(target=stop_recording, daemon=True).start()


# ---------------------------------------------------------------------------
# Распознавание
# ---------------------------------------------------------------------------
def _transcribe(audio, path: str) -> list:
    """→ [(секунда от начала куска, текст)]."""
    import numpy as np
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())
    import voice
    with open(path, "rb") as f:
        r = voice.groq_client.audio.transcriptions.create(file=(os.path.basename(path), f.read()), model=STT_MODEL,
                                                          temperature=0.0, response_format="verbose_json")
    segs = getattr(r, "segments", None) or (getattr(r, "model_extra", None) or {}).get("segments") or []
    out = []
    for sg in segs:
        g = (lambda k: sg.get(k)) if isinstance(sg, dict) else (lambda k: getattr(sg, k, None))
        text = (g("text") or "").strip()
        if text and (g("no_speech_prob") or 0) < 0.8:
            out.append((float(g("start") or 0), text))
    return out or ([(0.0, (r.text or "").strip())] if (r.text or "").strip() else [])


def _on_chunk(audio, t_start: float, transcribe=None) -> None:
    if not loud_enough(audio):
        return
    with _lock:
        _s["pending"] += 1

    def run():
        try:
            path = os.path.join(folder(), f".chunk_{int(t_start)}.wav")
            try:
                parts = (transcribe or _transcribe)(audio, path)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            with _lock:
                _s["segments"] += [(t_start + off, text) for off, text in parts]
                _s["segments"].sort()
        except Exception as e:
            _s["error"] = str(e)
            print(f"[конспект] кусок не распознан: {e}")
        finally:
            with _lock:
                _s["pending"] -= 1
    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------------------
# Начать / закончить
# ---------------------------------------------------------------------------
def start_recording(title: str = "", with_mic: bool = True, announce=None, recorder=None) -> str:
    if _s["on"]:
        return f"Already recording '{_s['title']}' for {round((time.time() - _s['started']) / 60)} min."
    title = (title or "").strip() or ("Созвон" if with_mic else "Запись")
    stop = threading.Event()
    _s.update(on=True, title=title[:80], started=time.time(), mic=with_mic, segments=[], pending=0, stop=stop,
              error="")
    if announce:
        _s["announce"] = announce

    def run():
        try:
            (recorder or _record_loop)(_on_chunk, stop, with_mic)
        except Exception as e:
            _s["error"] = str(e)
            print(f"[конспект] запись не идёт: {e}")
            _s["on"] = False
    _s["thread"] = threading.Thread(target=run, daemon=True, name="meeting-rec")
    _s["thread"].start()
    return (f"Recording started: '{_s['title']}' — the computer's sound" + (" and the microphone" if with_mic else "") +
            ". Say 'stop the recording' when it ends; then I'll make the notes. Confirm briefly.")


def transcript_text(segments=None, started: float = None) -> str:
    segs = _s["segments"] if segments is None else segments
    t0 = _s["started"] if started is None else started
    return "\n".join(f"[{int(max(0, t - t0)) // 60:02d}:{int(max(0, t - t0)) % 60:02d}] {text}" for t, text in segs)


NOTES_RULES = ("You make notes from a transcript of a call, lecture or video (it may contain recognition errors — "
               "fix obvious ones silently). Write in {lang}. Markdown with these sections, skip empty ones: "
               "'## Коротко' (3–5 sentences), '## Решения', '## Задачи' (who — what — deadline, as a checklist "
               "'- [ ] ...'), '## Важные цифры и даты', '## Открытые вопросы'. Be concrete, no filler.")


def _lang_name() -> str:
    try:
        from voice import get_response_language
        return "Russian" if get_response_language() == "ru" else "English"
    except Exception:
        return "Russian"


def make_notes(text: str, ask=None) -> str:
    from core import quick_llm
    if len(text) > 60000:                                   # очень длинное — конспект по частям, потом общий
        parts = [text[i:i + 50000] for i in range(0, len(text), 50000)]
        text = "\n\n".join(make_notes(p, ask) for p in parts)
    rules = NOTES_RULES.format(lang=_lang_name())
    return (ask or quick_llm.ask)(rules.replace("{lang}", _lang_name()), text, max_tokens=1500)


def stop_recording(ask=None, wait: float = 120) -> str:
    if not _s["on"]:
        return "Nothing is being recorded right now."
    _s["stop"].set()
    if _s["thread"]:
        _s["thread"].join(timeout=10)
    deadline = time.time() + wait
    while _s["pending"] > 0 and time.time() < deadline:
        time.sleep(0.3)
    _s["on"] = False
    minutes = max(1, round((time.time() - _s["started"]) / 60))
    text = transcript_text()
    if not text.strip():
        return (f"Recorded {minutes} min, but no speech was recognized" +
                (f" ({_s['error']})" if _s["error"] else "") + ". Nothing saved.")
    stamp = datetime.fromtimestamp(_s["started"]).strftime("%Y-%m-%d %H-%M")
    safe = re.sub(r"[\\/:*?\"<>|]", "_", _s["title"])[:60]
    base = os.path.join(folder(), f"{stamp} {safe}")
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write(text)
    try:
        notes = make_notes(text, ask)
    except Exception as e:
        notes = f"(Конспект не получился: {e}. Полный текст — в файле .txt.)"
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(f"# {_s['title']} — {stamp}\n\n{notes}\n")
    _s["last"] = {"title": _s["title"], "txt": base + ".txt", "md": base + ".md", "minutes": minutes}
    _save_index()
    short = re.split(r"\n##\s", notes.split("## Коротко", 1)[-1], maxsplit=1)[0].strip()[:700]
    return (f"Recording '{_s['title']}' ({minutes} min) saved with notes: {base}.md. "
            f"Summary: {short}\nTell the user the gist in 2–3 sentences and offer to send the notes to the phone.")


# ---------------------------------------------------------------------------
# Прошлые записи
# ---------------------------------------------------------------------------
def _index_path() -> str:
    return os.path.join(folder(), ".atlas_index.json")


def _save_index() -> None:
    try:
        items = recordings()
        items = [i for i in items if i.get("md") != _s["last"]["md"]] + [_s["last"]]
        with open(_index_path(), "w", encoding="utf-8") as f:
            json.dump(items[-200:], f, ensure_ascii=False)
    except Exception as e:
        print(f"[конспект] список записей: {e}")


def recordings() -> list:
    try:
        with open(_index_path(), encoding="utf-8") as f:
            return [i for i in json.load(f) if os.path.exists(i.get("txt", ""))]
    except Exception:
        return []


def _pick(which: str = "") -> dict:
    items = recordings()
    if not items:
        return {}
    w = (which or "").lower().strip()
    if w and w not in ("last", "последний", "последняя", "последнюю"):
        for it in reversed(items):
            if w in it["title"].lower() or w in os.path.basename(it["txt"]).lower():
                return it
    return items[-1]


def ask_recording(question: str, which: str = "", ask=None) -> str:
    it = _pick(which)
    if not it:
        return "There are no recordings yet."
    with open(it["txt"], encoding="utf-8") as f:
        text = f.read()
    if len(text) > 45000:                                    # длинная запись — берём куски со словами вопроса
        words = [w[:5] for w in re.findall(r"\w{4,}", question.lower())]
        lines = text.splitlines()
        keep = [i for i, ln in enumerate(lines) if any(w in ln.lower() for w in words)]
        near = sorted({j for i in keep for j in range(max(0, i - 3), min(len(lines), i + 4))})
        text = "\n".join(lines[j] for j in near)[:45000] or text[:45000]
    from core import quick_llm
    return (ask or quick_llm.ask)(
        f"Answer the question using only this transcript of '{it['title']}'. Quote times like [12:30] when useful. "
        f"If it isn't in the transcript, say so. Answer in {_lang_name()}, briefly.", f"{text}\n\nQuestion: {question}",
        max_tokens=600)


def notes_file(which: str = "") -> str:
    it = _pick(which)
    return it.get("md", "") if it else ""


def status() -> str:
    if _s["on"]:
        return (f"Recording '{_s['title']}' for {round((time.time() - _s['started']) / 60)} min, "
                f"{len(_s['segments'])} phrases so far.")
    items = recordings()[-5:]
    return "Not recording. Recent: " + "; ".join(f"{i['title']} ({i['minutes']} min)" for i in reversed(items)) \
        if items else "Not recording, no saved recordings yet."
