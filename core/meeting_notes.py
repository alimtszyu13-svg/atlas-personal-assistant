"""
Записи: созвоны, лекции, видео. «Атлас, запиши созвон» / «запиши это видео» … «прекрати запись».

Как устроено, чтобы было качественно:
  • звук компьютера (Zoom, Meet, Discord, YouTube — всё, что играет) и, для созвонов, твой микрофон
    пишутся раздельно и сводятся по часам: тишина в колонках не останавливает запись микрофона;
  • звук сохраняется файлом (.ogg, ~15 МБ в час) — запись можно переслушать;
  • распознаётся по ходу записи кусками ~1,5 минуты, разрезанными по паузам (не посреди слова),
    по очереди, с подсказкой из предыдущего куска — так фразы и имена не рвутся; тишина пропускается;
  • в конце — конспект: коротко, решения, задачи со сроками, цифры и даты, открытые вопросы.

Всё лежит в Документы/Atlas/Конспекты: звук (.ogg), полный текст (.txt), конспект (.md). Только здесь.
Окно «Записи» (web_gui) показывает их, а зелёный огонёк справа сверху горит, пока идёт запись.
Запись сама останавливается через 4 часа. Нужна библиотека soundcard (ставится сама).
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

import numpy as np

SR = 16000
CHUNK_S = 90                        # кусок для распознавания — режем в паузе около этой длины
CHUNK_MAX_S = 120
MAX_S = 4 * 3600
SILENCE_RMS = 0.003                 # тише — кусок не распознаём
STT_MODEL = os.getenv("MEETING_STT_MODEL") or "whisper-large-v3"     # точнее turbo на русском

_s = {"phase": "idle", "id": "", "title": "", "started": 0.0, "mic": True, "segments": [], "stop": threading.Event(),
      "thread": None, "error": "", "announce": None, "level": 0.0, "audio": "", "queue": None, "worker": None,
      "busy": 0, "listeners": [], "notes_ready": None}
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
    return _s["phase"] == "recording"


def phase() -> str:
    return _s["phase"]


def on_change(fn) -> None:
    """Подписка на «запись началась / закончилась» (зелёный огонёк, окно записей)."""
    if fn not in _s["listeners"]:
        _s["listeners"].append(fn)


def _changed() -> None:
    for fn in list(_s["listeners"]):
        try:
            fn(_s["phase"])
        except Exception as e:
            print(f"[записи] {e}")


# ---------------------------------------------------------------------------
# Голосом: «запиши созвон», «прекрати запись», «открой записи»
# ---------------------------------------------------------------------------
_KINDS = (("созвон", True, "Созвон"), ("звонок", True, "Звонок"), ("встреч", True, "Встреча"),
          ("разговор", True, "Разговор"), ("совещан", True, "Совещание"), ("собеседован", True, "Собеседование"),
          ("видео", False, "Видео"), ("ролик", False, "Видео"), ("лекци", False, "Лекция"), ("урок", False, "Урок"),
          ("вебинар", False, "Вебинар"), ("подкаст", False, "Подкаст"), ("стрим", False, "Стрим"),
          ("трансляц", False, "Трансляция"), ("звук", False, "Запись"), ("call", True, "Call"),
          ("meeting", True, "Meeting"), ("video", False, "Video"), ("lecture", False, "Lecture"))
_START = re.compile(r"^(?:запиши|записывай|начни\s+запис\w*|включи\s+запись|сделай\s+конспект|"
                    r"законспектируй|record|start\s+recording)\b(.*)$")
_STOP = re.compile(r"^(?:прекрати|останови|закончи|заверши|выключи|стоп|хватит|остановить|stop|end|finish)\s+"
                   r"(?:запись|записывать|запис\w*|recording|the\s+recording)\b|^хватит\s+записывать")
_OPEN = re.compile(r"^(?:открой|покажи|show|open)\s+(?:мои\s+|my\s+)?(?:записи|конспекты|recordings|notes)\b")


def intent(text: str):
    """Фраза → ("start", название, микрофон, экран) | ("stop",) | ("open",) | None."""
    o = (text or "").strip().strip(" .!?,")
    o = re.sub(r"^(?:атлас|atlas)[,\s]+", "", o, flags=re.I)
    o = re.sub(r"[,\s]+(?:пожалуйста|please)$", "", o, flags=re.I)
    t = o.lower().replace("ё", "е")                        # та же длина — позиции совпадают с o
    if _STOP.search(t):
        return ("stop",)
    if _OPEN.search(t):
        return ("open",)
    m = _START.match(t)
    if not m:
        return None
    rest = m.group(1)
    if re.search(r"календар|заметк|напомин|в список|в дела|задач|расписан|calendar|note|remind", rest):
        return None                                     # «запиши встречу в календарь» — это не запись звука
    screen = None
    if re.search(r"без экран|без видео|только звук|without (?:the )?screen|audio only", rest):
        screen = False
    elif re.search(r"с экран|и экран|экран|with (?:the )?screen|screen", rest):
        screen = True
    rest_clean = re.sub(r"\s*(?:с|и|без)\s+экран\w*|\s*только звук|\s*(?:with|without) (?:the )?screen", "", rest)
    for key, mic, title in _KINDS:
        k = rest_clean.find(key)
        if k >= 0:
            tail = re.match(r"\w*\s*", rest_clean[k + len(key):])
            extra = rest_clean[k + len(key) + tail.end():].strip(" ,.")
            if extra:                                   # имена с большой буквы — из исходной фразы
                at = o.lower().replace("ё", "е").find(extra)
                extra = o[at:at + len(extra)] if at >= 0 else extra
            name = f"{title} {extra}".strip()[:80] if extra and len(extra) < 50 else title
            return ("start", name, mic, (not mic) if screen is None else screen)
    if screen is not None and re.search(r"экран|screen", rest):
        return ("start", "Экран", False, True)          # «запиши экран»
    return None


# ---------------------------------------------------------------------------
# Звук
# ---------------------------------------------------------------------------
def _soundcard():
    try:
        import soundcard
        return soundcard
    except ImportError:
        print("[записи] ставлю soundcard (запись звука компьютера)…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "soundcard>=0.4.5"], check=False)
        import soundcard
        return soundcard


def to_16k_mono(block, rate: int):
    """float32 (кадры, каналы) → float32 моно 16 кГц."""
    a = np.asarray(block, dtype=np.float32)
    if a.ndim > 1:
        a = a.mean(axis=1)
    if rate != SR and len(a):
        n = int(round(len(a) * SR / rate))
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32) if n else a[:0]
    return a


def rms(a) -> float:
    return float(np.sqrt(np.mean(np.square(a, dtype=np.float32)))) if len(a) else 0.0


def loud_enough(a) -> bool:
    return rms(a) > SILENCE_RMS


class Source:
    """Один источник звука в своём потоке: копит отсчёты 16 кГц."""

    GAP_S = 0.2                          # звук пропадал дольше — вставляем тишину, чтобы не уехать по времени

    def __init__(self, name: str, t0: float = None):
        self.name, self.parts, self.n, self.last, self.t0 = name, [], 0, 0.0, t0
        self.lock = threading.Lock()

    def push(self, a, now: float = None) -> None:
        if not len(a):
            return
        now = now or time.time()
        if self.t0 is not None:
            behind = int((now - self.t0) * SR) - len(a) - self.n
            if behind > self.GAP_S * SR:          # Windows не отдавал звук, пока в колонках было тихо
                self._add(np.zeros(behind, dtype=np.float32))
        self._add(a)
        self.last = now

    def _add(self, a) -> None:
        with self.lock:
            self.parts.append(a)
            self.n += len(a)

    def pad_to(self, n: int) -> None:
        if n > self.n:
            self._add(np.zeros(n - self.n, dtype=np.float32))
            self.last = time.time()

    def available(self) -> int:
        with self.lock:
            return sum(len(p) for p in self.parts)

    def take(self, n: int):
        with self.lock:
            buf = np.concatenate(self.parts) if self.parts else np.zeros(0, np.float32)
            out, rest = buf[:n], buf[n:]
            self.parts = [rest] if len(rest) else []
        return out


def duck_gain(loud, frame: int = SR // 10, level: float = 0.01, low: float = 0.3):
    """Усиление микрофона по кадрам 0,1 с: 0.3, где в колонках звук, иначе 1."""
    n = len(loud)
    k = max(1, -(-n // frame))
    pad = np.zeros(k * frame, dtype=np.float32)
    pad[:n] = loud
    e = np.sqrt(np.mean(pad.reshape(k, frame) ** 2, axis=1))
    return np.repeat(np.where(e > level, low, 1.0).astype(np.float32), frame)[:n]


class Mixer:
    """Сводит источники по часам: источник, который молчит (в колонках ничего не играет), добивается тишиной."""
    IDLE_S, LAG_S = 0.6, 0.3

    def __init__(self, sources: list, t0: float, duck: bool = False):
        self.sources, self.t0, self.emitted, self.duck = sources, t0, 0, duck

    def step(self, now: float = None):
        now = now or time.time()
        wall = int((now - self.t0 - self.LAG_S) * SR)
        for s in self.sources:
            if now - (s.last or self.t0) > self.IDLE_S:
                s.pad_to(wall)
        n = min(s.available() for s in self.sources)
        if n < SR // 4:
            return None
        parts = [s.take(n) for s in self.sources]
        if self.duck and len(parts) > 1:      # говорит собеседник из колонок — микрофон слышит его эхо, приглушаем
            parts[1] = parts[1] * duck_gain(parts[0])
        out = parts[0]
        for a in parts[1:]:
            out = out + a
        self.emitted += n
        return np.clip(out, -1.0, 1.0)


def _capture(emit, stop: threading.Event, with_mic: bool) -> None:
    """Звук компьютера (+ микрофон) → emit(блок float32 16 кГц) примерно 4 раза в секунду."""
    sc = _soundcard()
    rate = 48000
    spk = sc.default_speaker()
    devices = [("компьютер", sc.get_microphone(id=str(spk.name), include_loopback=True))]
    if with_mic:
        devices.append(("микрофон", sc.default_microphone()))
    try:
        import voice
        duck = with_mic and not voice.output_is_headphones()
    except Exception:
        duck = with_mic
    t0 = time.time()
    _s["audio_t0"] = t0                                       # начало звука — от него сводится видео
    sources = [Source(n, t0) for n, _ in devices]
    errors = []

    def reader(dev, src, gain):
        try:
            with dev.recorder(samplerate=rate, blocksize=1024) as rec:
                while not stop.is_set():
                    data = rec.record(numframes=None)               # что есть сейчас — не ждёт звука
                    if data is not None and len(data):
                        src.push(to_16k_mono(data, rate) * gain)
                    else:
                        time.sleep(0.02)
        except Exception as e:
            errors.append(f"{src.name}: {e}")
            print(f"[записи] {src.name} не пишется: {e}")
    for (name, dev), src in zip(devices, sources):
        threading.Thread(target=reader, args=(dev, src, 1.0 if name == "компьютер" else 0.9), daemon=True,
                         name=f"rec-{name}").start()
    print(f"[записи] пишу: звук компьютера ({spk.name})" + (f" + микрофон ({devices[1][1].name})" if with_mic else ""))
    mixer = Mixer(sources, t0, duck=duck)
    while not stop.is_set():
        time.sleep(0.25)
        if errors and len(errors) == len(sources):
            raise RuntimeError("; ".join(errors))
        block = mixer.step()
        if block is not None:
            emit(block)
        if time.time() - t0 > MAX_S:
            print("[записи] 4 часа — останавливаю")
            threading.Thread(target=stop_recording, daemon=True).start()
            break
    block = mixer.step(time.time() + Mixer.LAG_S)
    if block is not None:
        emit(block)


class Chunker:
    """Копит звук и отдаёт куски ~CHUNK_S секунд, разрезанные в самом тихом месте последних 25 секунд."""

    def __init__(self, on_chunk, target: float = None, most: float = None):
        self.on_chunk = on_chunk
        self.target, self.most = int((target or CHUNK_S) * SR), int((most or CHUNK_MAX_S) * SR)
        self.parts, self.n, self.offset = [], 0, 0          # offset — с какой секунды записи начинается буфер

    def add(self, a) -> None:
        self.parts.append(a)
        self.n += len(a)
        while self.n >= self.target:
            buf = np.concatenate(self.parts)
            cut = self._quiet_cut(buf) if self.n < self.most else self.most
            self._emit(buf[:cut])
            rest = buf[cut:]
            self.parts, self.n = [rest], len(rest)

    def _quiet_cut(self, buf) -> int:
        win = int(0.4 * SR)
        lo = max(win, len(buf) - 25 * SR, self.target // 2)      # не режем совсем короткий кусок
        best, at = None, len(buf)
        for i in range(lo, len(buf) - win + 1, win // 2):
            e = rms(buf[i:i + win])
            if best is None or e < best:
                best, at = e, i + win // 2
        return at

    def _emit(self, a) -> None:
        start = self.offset / SR
        self.offset += len(a)
        self.on_chunk(a, start)

    def flush(self) -> None:
        if self.n:
            self._emit(np.concatenate(self.parts))
            self.parts, self.n = [], 0


class AudioFile:
    """Звук записи на диск по ходу (OGG, если нет — FLAC). Не получилось — пишем без звука."""

    def __init__(self, base: str):
        self.f, self.path = None, ""
        try:
            import soundfile as sf
            for ext, fmt, sub in ((".ogg", "OGG", "VORBIS"), (".flac", "FLAC", "PCM_16")):
                try:
                    self.f = sf.SoundFile(base + ext, "w", samplerate=SR, channels=1, format=fmt, subtype=sub)
                    self.path = base + ext
                    break
                except Exception:
                    continue
        except Exception as e:
            print(f"[записи] звук не сохраняю ({e}) — будет только текст")

    def write(self, a) -> None:
        if self.f is not None:
            try:
                self.f.write(a)
            except Exception as e:
                print(f"[записи] звук: {e}")
                self.f = None

    def close(self) -> None:
        if self.f is not None:
            try:
                self.f.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Распознавание
# ---------------------------------------------------------------------------
def _transcribe(audio, path: str, prompt: str = "") -> list:
    """Groq Whisper → [(секунда от начала куска, текст)]."""
    import wave
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())
    import voice
    kw = dict(model=STT_MODEL, temperature=0.0, response_format="verbose_json")
    if prompt:
        kw["prompt"] = prompt[-220:]
    lang = (os.getenv("MEETING_LANGUAGE") or "").strip().lower()
    if lang in ("ru", "en"):
        kw["language"] = lang
    last = None
    for attempt in range(3):
        try:
            with open(path, "rb") as f:
                r = voice.groq_client.audio.transcriptions.create(file=(os.path.basename(path), f.read()), **kw)
            break
        except Exception as e:
            last = e
            time.sleep(3 * (attempt + 1))
    else:
        raise RuntimeError(f"распознавание не удалось: {last}")
    segs = getattr(r, "segments", None) or (getattr(r, "model_extra", None) or {}).get("segments") or []
    out = []
    for sg in segs:
        g = (lambda k: sg.get(k)) if isinstance(sg, dict) else (lambda k: getattr(sg, k, None))
        text = (g("text") or "").strip()
        if text and (g("no_speech_prob") or 0) < 0.7 and (g("avg_logprob") or 0) > -1.2 and not _junk(text):
            out.append((float(g("start") or 0), text))
    return out


_JUNK = re.compile(r"^(?:продолжение следует|субтитры (?:сделал|создавал)|спасибо за просмотр|редактор субтитров|"
                   r"thanks? for watching|subtitles by|подписывайтесь на канал)", re.I)


def _junk(text: str) -> bool:
    return bool(_JUNK.search(text.strip(" .!")))


def _worker(q: "queue.Queue", transcribe) -> None:
    """Куски распознаются по очереди: подсказка — конец предыдущего текста."""
    while True:
        item = q.get()
        if item is None:
            q.task_done()
            return
        audio, start = item
        try:
            if not loud_enough(audio):
                continue
            prompt = " ".join(t for _, t in _s["segments"][-6:])
            path = os.path.join(folder(), f".chunk_{os.getpid()}_{int(start)}.wav")
            try:
                parts = (transcribe or _transcribe)(audio, path, prompt)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            with _lock:
                for off, text in parts:
                    if not (_s["segments"] and _s["segments"][-1][1] == text):     # Whisper иногда повторяет
                        _s["segments"].append((start + off, text))
        except Exception as e:
            _s["error"] = str(e)
            print(f"[записи] кусок {int(start)} с не распознан: {e}")
        finally:
            q.task_done()


# ---------------------------------------------------------------------------
# Начать / закончить
# ---------------------------------------------------------------------------
def _new_id(started: float, title: str) -> str:
    stamp = datetime.fromtimestamp(started).strftime("%Y-%m-%d %H-%M")
    safe = re.sub(r"[\\/:*?\"<>|]", "_", title).strip()[:60]
    return f"{stamp} {safe}"


def start_recording(title: str = "", with_mic: bool = True, announce=None, recorder=None, transcribe=None,
                    with_screen: bool = None, screen=None) -> str:
    """with_screen: писать ещё и экран (по умолчанию — для видео и лекций, то есть без микрофона)."""
    if _s["phase"] == "recording":
        return f"Already recording '{_s['title']}' for {round((time.time() - _s['started']) / 60)} min."
    if _s["phase"] == "processing":
        return "Still making notes from the previous recording — try again in a moment."
    title = (title or "").strip() or ("Созвон" if with_mic else "Запись")
    started = time.time()
    rid = _new_id(started, title)
    stop = threading.Event()
    q = queue.Queue()
    audio = AudioFile(os.path.join(folder(), rid))
    if with_screen is None:
        with_screen = not with_mic
    _s.update(phase="recording", id=rid, title=title[:80], started=started, mic=with_mic, segments=[], stop=stop,
              error="", level=0.0, audio=audio.path, queue=q, busy=0, notes_ready=None, audio_t0=started,
              screen=None, screen_on=False)
    if with_screen and (screen is not None or recorder is None):   # с подставным звуком (тесты) — экран не трогаем
        _start_screen(os.path.join(folder(), rid), screen)
    if announce:
        _s["announce"] = announce
    _s["worker"] = threading.Thread(target=_worker, args=(q, transcribe), daemon=True, name="rec-stt")
    _s["worker"].start()
    chunker = Chunker(lambda a, start: q.put((a, start)))

    def emit(block):
        _s["level"] = min(1.0, rms(block) * 12)
        audio.write(block)
        chunker.add(block)

    def run():
        try:
            (recorder or _capture)(emit, stop, with_mic)
        except Exception as e:
            _s["error"] = str(e)
            print(f"[записи] запись не идёт: {e}")
            if _s["phase"] == "recording":
                _s["phase"] = "failed"
                _changed()
        finally:
            chunker.flush()
            audio.close()
            q.put(None)
    _s["thread"] = threading.Thread(target=run, daemon=True, name="rec-capture")
    _s["thread"].start()
    _changed()
    print(f"[записи] ● {title} — запись идёт")
    return (f"Recording started: '{_s['title']}' — the computer's sound" + (" and the microphone" if with_mic else "") +
            (" and the screen (video)" if with_screen else "") +
            ". Say 'прекрати запись' when it ends; then I'll make the notes. Confirm briefly.")


def _start_screen(base: str, screen=None) -> None:
    """Экран пишется параллельно; не запустился — запись идёт дальше только звуком."""
    def run():
        from core import screen_rec
        rec = screen or screen_rec.ScreenRecorder(base + ".screen.mp4")
        _s["screen"] = rec
        if rec.start():
            if _s["phase"] != "recording":                 # запись уже остановили, пока экран запускался
                rec.stop()
                return
            _s["screen_on"] = True
        else:
            _s["screen"] = None
            _s["error"] = f"экран не записался ({rec.error.strip()[:120]}) — пишу только звук"
            print(f"[записи] {_s['error']}")
        _changed()
    threading.Thread(target=run, daemon=True, name="rec-screen").start()


def _finish_screen(base: str, audio_path: str) -> str:
    """Остановить запись экрана и склеить со звуком. → путь к видео или ''."""
    rec = _s.get("screen")
    _s["screen"], _s["screen_on"] = None, False
    if rec is None or not rec.stop():
        return ""
    raw = rec.out
    if audio_path and os.path.exists(audio_path) and rec.t0:
        from core import screen_rec
        out = base + ".mp4"
        if screen_rec.mux(raw, audio_path, out, rec.t0 - _s.get("audio_t0", _s["started"])):
            try:
                os.remove(raw)
            except OSError:
                pass
            return out
    out = base + ".mp4"                                     # звука нет / не склеилось — хотя бы картинка
    try:
        os.replace(raw, out)
        return out
    except OSError:
        return raw


def transcript_text(segments=None) -> str:
    segs = _s["segments"] if segments is None else segments
    return "\n".join(f"[{int(t) // 3600:d}:{int(t) % 3600 // 60:02d}:{int(t) % 60:02d}] {text}" if t >= 3600 else
                     f"[{int(t) // 60:02d}:{int(t) % 60:02d}] {text}" for t, text in segs)


NOTES_RULES = ("You make notes from a transcript of a call, lecture or video. The transcript comes from speech "
               "recognition and may contain errors — fix obvious ones silently, never invent facts. Write in {lang}. "
               "Markdown with these sections, skip empty ones: '## Коротко' (3–5 sentences: what it was about and "
               "the outcome), '## Главное' (key points as bullets), '## Решения', '## Задачи' (checklist '- [ ] кто — "
               "что — срок'), '## Цифры и даты', '## Открытые вопросы'. Use the section titles in {lang}. "
               "Add times like [12:30] to key points so they can be found in the recording.")


def _lang_name() -> str:
    try:
        from voice import get_response_language
        return "Russian" if get_response_language() == "ru" else "English"
    except Exception:
        return "Russian"


def make_notes(text: str, ask=None) -> str:
    from core import quick_llm
    ask = ask or quick_llm.ask
    rules = NOTES_RULES.replace("{lang}", _lang_name())
    if len(text) > 50000:                                   # длинная запись: конспект по частям, потом общий
        parts = [text[i:i + 40000] for i in range(0, len(text), 40000)]
        text = "\n\n".join(ask(rules, p, max_tokens=1200) for p in parts)
    return ask(rules, text, max_tokens=1800)


def stop_recording(ask=None, wait: float = 600) -> str:
    if _s["phase"] != "recording":
        return "Nothing is being recorded right now."
    _s["phase"] = "processing"
    _changed()
    _s["stop"].set()
    if _s["thread"]:
        _s["thread"].join(timeout=15)
    video = _finish_screen(os.path.join(folder(), _s["id"]), _s["audio"])
    if _s["worker"]:
        _s["worker"].join(timeout=wait)                     # дождаться распознавания последних кусков
    minutes = max(1, round((time.time() - _s["started"]) / 60))
    rid, title = _s["id"], _s["title"]
    base = os.path.join(folder(), rid)
    text = transcript_text()
    entry = {"id": rid, "title": title, "started": _s["started"], "minutes": minutes, "mic": _s["mic"],
             "audio": _s["audio"] if _s["audio"] and os.path.exists(_s["audio"]) else "", "video": video,
             "txt": "", "md": ""}
    if not text.strip():
        _s["phase"] = "idle"
        _save_entry(entry)
        _changed()
        why = f" ({_s['error']})" if _s["error"] else " — maybe nothing was playing or the volume was muted"
        kept = "video" if video else "audio" if entry["audio"] else ""
        return f"Recorded {minutes} min, but no speech was recognized{why}. The {kept} is kept." if kept \
            else f"Recorded {minutes} min, but no speech was recognized{why}."
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write(text)
    entry["txt"] = base + ".txt"
    try:
        notes = make_notes(text, ask)
    except Exception as e:
        notes = f"(Конспект не получился: {e}. Полный текст — в файле .txt.)"
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n{datetime.fromtimestamp(_s['started']).strftime('%d.%m.%Y %H:%M')} · {minutes} мин\n\n"
                f"{notes}\n")
    entry["md"] = base + ".md"
    _save_entry(entry)
    _s["phase"] = "idle"
    _s["notes_ready"] = rid
    short = re.split(r"\n##\s", re.split(r"##\s*(?:Коротко|Summary|In short)\s*\n", notes, maxsplit=1)[-1],
                     maxsplit=1)[0].strip()[:700]
    _s["summary"] = re.sub(r"\s*\[\d{1,2}:\d{2}(?::\d{2})?\]", "", short).strip()
    _changed()
    return (f"Recording '{title}' ({minutes} min) saved with notes. Summary: {short}\n"
            f"Tell the user the gist in 2–3 sentences; the notes are in the Records window and can go to the phone.")


def last_summary() -> str:
    """«Коротко» из конспекта последней записи — чтобы сказать вслух."""
    return _s.get("summary") or ""


def stop_async(done=None) -> None:
    """Из окна: остановить, а конспект делать в фоне."""
    def run():
        r = stop_recording()
        if done:
            done(r)
    threading.Thread(target=run, daemon=True, name="rec-stop").start()


# ---------------------------------------------------------------------------
# Список записей
# ---------------------------------------------------------------------------
def _index_path() -> str:
    return os.path.join(folder(), ".atlas_index.json")


def _load_index() -> list:
    try:
        with open(_index_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _write_index(items: list) -> None:
    tmp = _index_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items[-500:], f, ensure_ascii=False, indent=1)
    os.replace(tmp, _index_path())


def _save_entry(entry: dict) -> None:
    with _lock:
        items = [i for i in _load_index() if i.get("id") != entry["id"]] + [entry]
        _write_index(items)


def _alive(i: dict) -> bool:
    return any(i.get(k) and os.path.exists(i[k]) for k in ("txt", "md", "audio", "video"))


def recordings() -> list:
    """Старые → новые."""
    out = []
    for i in _load_index():
        if not _alive(i):
            continue
        i.setdefault("id", os.path.splitext(os.path.basename(i.get("txt") or i.get("md") or ""))[0])
        out.append(i)
    return out


def get(rid: str) -> dict:
    it = next((i for i in recordings() if i["id"] == rid), None)
    if not it:
        return {}
    read = lambda p: open(p, encoding="utf-8").read() if p and os.path.exists(p) else ""
    return dict(it, notes=read(it.get("md")), transcript=read(it.get("txt")))


def delete(rid: str) -> bool:
    with _lock:
        items = _load_index()
        it = next((i for i in items if i.get("id") == rid), None)
        if not it:
            return False
        for k in ("txt", "md", "audio", "video"):
            if it.get(k) and os.path.exists(it[k]):
                try:
                    os.remove(it[k])
                except OSError:
                    pass
        _write_index([i for i in items if i.get("id") != rid])
    return True


def rename(rid: str, title: str) -> bool:
    with _lock:
        items = _load_index()
        for i in items:
            if i.get("id") == rid:
                i["title"] = title.strip()[:80] or i["title"]
                _write_index(items)
                return True
    return False


def _pick(which: str = "") -> dict:
    items = recordings()
    if not items:
        return {}
    w = (which or "").lower().strip()
    if w and w not in ("last", "последний", "последняя", "последнюю", "последнее"):
        for it in reversed(items):
            if w in it["title"].lower() or w in it["id"].lower():
                return it
    return items[-1]


def ask_recording(question: str, which: str = "", ask=None) -> str:
    it = _pick(which)
    if not it or not it.get("txt"):
        return "There are no recordings with text yet."
    with open(it["txt"], encoding="utf-8") as f:
        text = f.read()
    if len(text) > 45000:                                    # длинная запись — куски со словами вопроса
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
    return (it.get("md") or it.get("txt") or "") if it else ""


def live(tail: int = 40) -> dict:
    """Для окна и огонька: что происходит прямо сейчас."""
    return {"phase": _s["phase"], "id": _s["id"], "title": _s["title"], "mic": _s["mic"], "screen": _s.get("screen_on", False),
            "seconds": int(time.time() - _s["started"]) if _s["phase"] in ("recording", "processing") else 0,
            "level": round(_s["level"], 3), "lines": transcript_text(_s["segments"][-tail:]).splitlines(),
            "error": _s["error"], "notes_ready": _s["notes_ready"]}


def status() -> str:
    if _s["phase"] == "recording":
        return (f"Recording '{_s['title']}' for {round((time.time() - _s['started']) / 60)} min, "
                f"{len(_s['segments'])} phrases recognized so far.")
    if _s["phase"] == "processing":
        return "The recording has stopped; making the notes now."
    items = recordings()[-5:]
    return "Not recording. Recent: " + "; ".join(f"{i['title']} ({i['minutes']} min)" for i in reversed(items)) \
        if items else "Not recording, no saved recordings yet."


# ---------------------------------------------------------------------------
# Проверка на твоём компьютере:  python -m core.meeting_notes check
# Включи видео с речью (YouTube) и запусти — 20 секунд записи, потом распознавание.
# ---------------------------------------------------------------------------
def selfcheck(seconds: int = 20, with_mic: bool = True) -> bool:
    import tempfile
    from dotenv import load_dotenv
    load_dotenv()
    print(f"Проверка записи: {seconds} с. Включи видео с речью (YouTube) и, если хочешь, скажи что-нибудь сам.\n")
    got, stop = [], threading.Event()
    t0 = time.time()

    def emit(block):
        got.append(block)
        sec = int(time.time() - t0)
        if len(got) % 4 == 0:
            bar = "█" * int(min(1.0, rms(block) * 12) * 30)
            print(f"  {sec:3d} с  уровень {bar:<30} {rms(block):.4f}")
    th = threading.Thread(target=_capture, args=(emit, stop, with_mic), daemon=True)
    th.start()
    time.sleep(seconds)
    stop.set()
    th.join(timeout=5)
    if not got:
        print("\n✗ Звук не пришёл вообще — проверь, что soundcard установлен и выбраны колонки/наушники по умолчанию.")
        return False
    audio = np.concatenate(got)
    print(f"\nЗаписано {len(audio) / SR:.1f} с (ожидалось ~{seconds}), средний уровень {rms(audio):.4f}")
    if len(audio) / SR < seconds * 0.8:
        print("! Записалось меньше, чем шло времени — звук компьютера мог прерываться.")
    if not loud_enough(audio):
        print("✗ Слишком тихо — видео точно играло? Громкость системы не на нуле?")
        return False
    path = os.path.join(tempfile.gettempdir(), "atlas_selfcheck.wav")
    t1 = time.time()
    try:
        parts = _transcribe(audio, path)
    except Exception as e:
        print(f"✗ Распознавание не удалось: {e}")
        return False
    print(f"Распознано за {time.time() - t1:.1f} с ({STT_MODEL}):\n")
    print(transcript_text(parts) or "(пусто)")
    ok = bool(parts)
    print("\n✓ Запись и распознавание работают." if ok else "\n✗ Речь не распознана — было ли в видео говорение?")
    print("\nПроверяю запись экрана (4 с)…")
    from core import screen_rec
    out = os.path.join(tempfile.gettempdir(), "atlas_selfcheck_screen.mp4")
    rec = screen_rec.ScreenRecorder(out)
    if rec.start():
        time.sleep(4)
        good = rec.stop()
        print(f"{'✓' if good else '✗'} Экран пишется ({rec.how}), файл {os.path.getsize(out) // 1024} КБ: {out}" if good
              else "✗ Экран начал писаться, но файл пустой.")
        if good and os.name == "nt":
            os.startfile(out)                                # открой и посмотри: весь ли экран попал
    else:
        print(f"✗ Экран не пишется: {rec.error.strip()[:200]} — видео-записи будут только со звуком.")
    return ok


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "check":
    sys.exit(0 if selfcheck(int(sys.argv[2]) if len(sys.argv) > 2 else 20) else 1)
