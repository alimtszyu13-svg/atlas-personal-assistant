from audioop import rms
import sounddevice as sd
import numpy as np
import os
import threading
import re
import wave
import pygame
from groq import Groq
from dotenv import load_dotenv
import asyncio
import edge_tts
from elevenlabs.client import ElevenLabs
import time
import random
from ui_state import shared_state

load_dotenv()

pygame.mixer.init()

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

TTS_MODEL = "canopylabs/orpheus-v1-english"
TTS_VOICE = "troy"
STT_MODEL = "whisper-large-v3"

SAMPLE_RATE = 16000
INTERRUPT_WORDS = ("stop", "enough", "quiet", "стоп", "хватит", "тихо")
# Локальное распознавание (Vosk): слышит только эти слова — быстро и без интернета
WAKE_WORDS_LOCAL = ("атлас",)
INTERRUPT_WORDS_LOCAL = ("стоп", "хватит", "тихо", "замолчи")
INTERRUPT_CONF = 0.85   # минимальная уверенность Vosk в каждом слове
STOP_REPEAT_WINDOW = 4.0   # два «стоп» в пределах стольких секунд = прерывание
VOSK_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "models", "vosk-model-small-ru-0.22")
_vosk_model = None


def _vosk_recognizer(words):
    """Распознаватель, которому разрешено слышать только words (остальное — [unk])."""
    global _vosk_model
    import json
    from vosk import Model, KaldiRecognizer, SetLogLevel
    if _vosk_model is None:
        SetLogLevel(-1)
        _vosk_model = Model(VOSK_MODEL_PATH)
    return KaldiRecognizer(_vosk_model, SAMPLE_RATE,
                           json.dumps(list(words) + ["[unk]"], ensure_ascii=False))# Локальное распознавание (Vosk): слышит только эти слова — быстро и без интернета
WAKE_WORDS_LOCAL = ("атлас",)
INTERRUPT_WORDS_LOCAL = ("стоп", "хватит", "тихо", "замолчи")
VOSK_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "models", "vosk-model-small-ru-0.22")
_vosk_model = None


def _vosk_recognizer(words):
    """Распознаватель, которому разрешено слышать только words (остальное — [unk])."""
    global _vosk_model
    import json
    from vosk import Model, KaldiRecognizer, SetLogLevel
    if _vosk_model is None:
        SetLogLevel(-1)
        _vosk_model = Model(VOSK_MODEL_PATH)
    return KaldiRecognizer(_vosk_model, SAMPLE_RATE,
                           json.dumps(list(words) + ["[unk]"], ensure_ascii=False))
WAKE_WORD = "atlas"
_push_to_talk_event = threading.Event()
# Ставится, когда пользователь сказал стоп-слово во время речи.
# Нужен, чтобы потоковая озвучка выбросила недоигранные куски,
# а не продолжала болтать после прерывания.
_stop_speaking = threading.Event()

_response_language = {"lang": "en"}  # "en" или "ru"


def set_response_language(lang: str) -> str:
    """Switches Atlas's spoken/response language between English and Russian."""
    lang = lang.lower().strip()
    if lang not in ("en", "ru", "english", "russian"):
        return "Supported languages: English or Russian."
    _response_language["lang"] = "ru" if lang.startswith("ru") else "en"
    name = "Russian" if _response_language["lang"] == "ru" else "English"
    return f"Language switched to {name}."


def get_response_language() -> str:
    return _response_language["lang"]

def trigger_push_to_talk() -> None:
    """Вызывается извне (горячая клавиша или кнопка интерфейса) —
    мгновенно 'будит' Атласа, минуя произнесение имени вслух."""
    import traceback
    src = " / ".join(f"{os.path.basename(f.filename)}:{f.name}"
                     for f in traceback.extract_stack()[-5:-1])
    print(f"[PTT] вызван ← {src}")
    _push_to_talk_event.set()


KOKORO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "kokoro")
KOKORO_VOICE = "bm_lewis"      # британский мужской — ближе всего к Джарвису
KOKORO_SPEED = 1.05
_kokoro = None
_kokoro_lock = threading.Lock()


def _generate_speech_kokoro(text: str, filename: str) -> None:
    """Английская речь локально: без интернета и без суточных лимитов."""
    global _kokoro
    import soundfile as sf
    with _kokoro_lock:
        if _kokoro is None:
            from kokoro_onnx import Kokoro
            _kokoro = Kokoro(os.path.join(KOKORO_DIR, "kokoro-v1.0.onnx"),
                             os.path.join(KOKORO_DIR, "voices-v1.0.bin"))
        samples, sr = _kokoro.create(text, voice=KOKORO_VOICE,
                                     speed=KOKORO_SPEED, lang="en-gb")
    sf.write(filename, samples, sr)

def _generate_speech(text: str, filename: str) -> None:
    """Английская речь по выбору в настройках: Fish, Groq или Kokoro.
    Kokoro работает всегда — на него уходим, если облачный голос недоступен."""
    vid = _fish_choice["en"]
    if vid and FISH_API_KEY:
        try:
            _generate_speech_fish(text, filename, vid)
            return
        except Exception as e:
            print(f"[Fish error]: {e}, пробую Kokoro")
    if _en_engine["name"] == "groq" and time.time() >= _groq_tts_until["t"]:
        try:
            response = groq_client.audio.speech.create(
                model=TTS_MODEL, voice=TTS_VOICE, input=text, response_format="wav")
            response.write_to_file(filename)
            return
        except Exception as e:
            msg = str(e)
            if "429" in msg:
                wait = 1800.0
                m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", msg)
                if m and any(m.groups()):
                    h, mi, s = (float(x) if x else 0.0 for x in m.groups())
                    wait = h * 3600 + mi * 60 + s
                _groq_tts_until["t"] = time.time() + wait
                print(f"[TTS] лимит голоса Groq исчерпан — Kokoro на ~{int(wait // 60)} мин")
            else:
                print(f"[TTS] Groq недоступен ({e}), пробую Kokoro")
    _generate_speech_kokoro(text, filename)

def _vary_pitch(filename: str) -> None:
    """Лёгкая случайная вариация скорости/высоты голоса (±4%) — трюк с
    изменением заявленной частоты дискретизации в WAV-заголовке, без
    ресемплинга. Работает только для WAV (Groq/Silero); для MP3
    (ElevenLabs/Edge-TTS) честного способа без ffmpeg нет — пропускаем."""
    if not filename.lower().endswith(".wav"):
        return
    try:
        with wave.open(filename, 'rb') as wf:
            params = wf.getparams()
            frames = wf.readframes(wf.getnframes())
        factor = random.uniform(0.96, 1.04)
        new_rate = int(params.framerate * factor)
        with wave.open(filename, 'wb') as wf:
            wf.setnchannels(params.nchannels)
            wf.setsampwidth(params.sampwidth)
            wf.setframerate(new_rate)
            wf.writeframes(frames)
    except Exception as e:
        print(f"[pitch variation error]: {e}")


AMBIENT_SOUNDS_DIR = "sounds"
AMBIENT_CHANCE = 0.06  # ~6% шанс, и только перед репликой подлиннее


def _maybe_play_ambient(text: str) -> None:
    """Изредка, перед длинной репликой — короткий смешок/вздох из готового
    сэмпла (sounds/chuckle_en.wav, sounds/sigh_ru.wav и т.п.), НЕ через TTS.
    Файлов сейчас нет на диске — если папки/файлов нет, просто ничего не
    происходит. Положи туда пару 2-3-секундных .wav, названных по шаблону
    chuckle_<lang>_*.wav / sigh_<lang>_*.wav, чтобы это заработало."""
    if len(text.split()) < 8:
        return
    if random.random() > AMBIENT_CHANCE:
        return
    lang = _response_language["lang"]
    if not os.path.isdir(AMBIENT_SOUNDS_DIR):
        return
    candidates = [
        f for f in os.listdir(AMBIENT_SOUNDS_DIR)
        if f.startswith(f"chuckle_{lang}") or f.startswith(f"sigh_{lang}")
    ]
    if not candidates:
        return
    path = os.path.join(AMBIENT_SOUNDS_DIR, random.choice(candidates))
    try:
        sound = pygame.mixer.Sound(path)
        sound.play()
        time.sleep(sound.get_length() * 0.85)
    except Exception as e:
        print(f"[ambient sound error]: {e}")

_vad_model = None


def _get_vad():
    global _vad_model
    if _vad_model is None:
        from silero_vad import load_silero_vad
        _vad_model = load_silero_vad(onnx=True)
    return _vad_model


def _has_speech(recording, min_speech_s: float = 0.3) -> bool:
    """Silero VAD: есть ли в записи человеческая речь, а не шум.
    Без этой проверки Whisper на тишине «слышит» фразы вроде 'Thank you.'"""
    try:
        import torch
        from silero_vad import get_speech_timestamps
        audio = torch.from_numpy(recording.flatten().astype("float32") / 32768.0)
        ts = get_speech_timestamps(audio, _get_vad(), sampling_rate=SAMPLE_RATE)
        speech = sum(t["end"] - t["start"] for t in ts) / SAMPLE_RATE
        return speech >= min_speech_s
    except Exception as e:
        print(f"[VAD] недоступен ({e}) — пропускаю проверку")
        return True


_WHISPER_JUNK = {
    "продолжение следует", "субтитры сделал dimatorzok", "спасибо за просмотр",
    "thanks for watching", "thank you for watching", "subtitles by the amara.org community",
}

def _transcribe_audio(recording: np.ndarray) -> str:
    if not _has_speech(recording):
        print("[VAD] речи нет — Whisper не вызываю")
        return ""
    """
    В английском режиме: переводит речь на ЛЮБОМ языке в английский текст
    (endpoint /audio/translations). В русском режиме: распознаёт речь как есть,
    без перевода, ожидая русский язык (endpoint /audio/transcriptions).
    """
    temp_path = "temp_stt.wav"

    with wave.open(temp_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(recording.tobytes())

    try:
        with open(temp_path, "rb") as f:
            if _response_language["lang"] == "ru":
                result = groq_client.audio.transcriptions.create(
                    file=(temp_path, f.read()),
                    model="whisper-large-v3-turbo",
                    language="ru"
                )
            else:
                result = groq_client.audio.translations.create(
                    file=(temp_path, f.read()),
                    model=STT_MODEL
                )
        text = result.text.strip()
        if not re.search(r"[^\W\d_]", text):       # ни одной буквы: «...», «?!»
            print(f"[Whisper] пустая фраза отброшена: {text!r}")
            return ""
        if re.sub(r"[^\w\s]", "", text.lower()).strip() in _WHISPER_JUNK:
            print(f"[Whisper] выдуманная фраза отброшена: {text!r}")
            return ""
        return text
    except Exception as e:
        print(f"[STT error]: {e}")
        return ""
    finally:
        os.remove(temp_path)


def wait_for_wake_word() -> str:
    """Ждёт «Атлас» локально через Vosk. 'manual' — если нажали push-to-talk."""
    import json
    print("(waiting for wake word 'Атлас' — локально)")
    rec = _vosk_recognizer(WAKE_WORDS_LOCAL)
    with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=4000,
                           dtype="int16", channels=1) as stream:
        while True:
            if _push_to_talk_event.is_set():
                _push_to_talk_event.clear()
                return "manual"
            data, _overflow = stream.read(4000)          # 0.25 с звука
            if rec.AcceptWaveform(bytes(data)):
                text = json.loads(rec.Result()).get("text", "")
            else:
                text = json.loads(rec.PartialResult()).get("partial", "")
            if "атлас" in text.split():
                return "voice"


def _watch_for_interrupt(stop_event: threading.Event) -> None:
    """Прерывание речи по фразе «Атлас, стоп». Одно стоп-слово Vosk иногда
    «слышит» в собственном голосе Atlas из колонок, а имя + стоп подряд — нет."""
    import json
    rec = _vosk_recognizer(INTERRUPT_WORDS_LOCAL + WAKE_WORDS_LOCAL)
    rec.SetWords(True)                      # нужна уверенность по каждому слову
    last_stop = 0.0
    with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=4000,
                           dtype="int16", channels=1) as stream:
        while pygame.mixer.music.get_busy() and not stop_event.is_set():
            data, _overflow = stream.read(4000)
            if not rec.AcceptWaveform(bytes(data)):
                continue
            heard = [(w["word"], round(w["conf"], 2))
                     for w in json.loads(rec.Result()).get("result", [])]
            if heard:
                print(f"[vosk] слышу: {heard}")
            sure = [w for w, c in heard if c >= INTERRUPT_CONF]
            n_stop = sum(1 for w in sure if w in INTERRUPT_WORDS_LOCAL)
            now = time.time()
            repeated = n_stop >= 2 or (n_stop and now - last_stop < STOP_REPEAT_WINDOW)
            if n_stop:
                last_stop = now
            if n_stop and ("атлас" in sure or repeated):
                print("[Atlas]: (прервано)")
                _stop_speaking.set()
                pygame.mixer.music.stop()
                return

FALLBACK_VOICE = "en-US-GuyNeural"


def _generate_speech_fallback(text: str, filename: str) -> None:
    """Резервный TTS через Edge-TTS — бесплатный, без дневного лимита токенов,
    подхватывает, если у Groq/Orpheus исчерпан суточный лимит."""
    async def _gen():
        communicate = edge_tts.Communicate(text, FALLBACK_VOICE)
        await communicate.save(filename)
    asyncio.run(_gen())


RUSSIAN_TTS_VOICE = "ru-RU-DmitryNeural"
elevenlabs_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
ELEVENLABS_VOICE_OPTIONS = {
    "male": {
        "george": "JBFqnCBsd6RMkjVDRZzb",
        "daniel": "onwK4e9ZLuTAKqWW03F9",
        "adam": "pNInz6obpgDQGcFmaJgB",
        "chris": "iP95p4xoKVk53GoZ742B",
    },
    "female": {
        "sarah": "EXAVITQu4vr4xnSDxMaL",
        "jessica": "cgSgspJ2msm6clMCkdW9",
        "alice": "Xb7hH8MSUJpSbSDYk0k2",
        "matilda": "XrExE9yKIg1WjnnlVkGX",
    },
}
_elevenlabs_voice = {"id": ELEVENLABS_VOICE_OPTIONS["male"]["george"], "name": "george"}


def list_elevenlabs_voices() -> str:
    """Lists available Russian TTS voices (ElevenLabs), grouped by gender."""
    male = ", ".join(ELEVENLABS_VOICE_OPTIONS["male"].keys())
    female = ", ".join(ELEVENLABS_VOICE_OPTIONS["female"].keys())
    return f"Male voices: {male}. Female voices: {female}. Current voice: {_elevenlabs_voice['name']}."


def set_elevenlabs_voice(name: str) -> str:
    """Switches the ElevenLabs voice used for Russian responses."""
    name = name.lower().strip()
    all_voices = {**ELEVENLABS_VOICE_OPTIONS["male"], **ELEVENLABS_VOICE_OPTIONS["female"]}
    if name not in all_voices:
        return f"Unknown voice '{name}'. Available: {', '.join(all_voices.keys())}."
    _elevenlabs_voice["id"] = all_voices[name]
    _elevenlabs_voice["name"] = name
    return f"Voice switched to {name}."

def _generate_speech_elevenlabs(text: str, filename: str) -> None:
    """Основной голос для русского — самый естественный из доступных, но с месячным лимитом символов."""
    audio = elevenlabs_client.text_to_speech.convert(
        text=text,
        voice_id=_elevenlabs_voice["id"],
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128"
    )
    with open(filename, "wb") as f:
        for chunk in audio:
            f.write(chunk)

_silero_model = None
SILERO_SPEAKER = "baya"  # варианты: aidar (муж., глубокий), baya (жен., мягкий),
                          # kseniya (жен., чёткий), xenia (жен., спокойный), eugene (муж.)


def _get_silero_model():
    """Загружает модель Silero один раз и кэширует — сама генерация после этого мгновенная."""
    global _silero_model
    if _silero_model is None:
        from silero import silero_tts
        _silero_model, _ = silero_tts(language='ru', speaker='v5_ru')
    return _silero_model


def _generate_speech_silero(text: str, filename: str) -> None:
    """Генерирует речь локально через Silero — без сетевого запроса, естественнее и быстрее Edge-TTS."""
    model = _get_silero_model()
    model.save_wav(text=text, speaker=SILERO_SPEAKER, sample_rate=48000, audio_path=filename)

# ---------------------------------------------------------------------------
# Ускорение: кэш фраз, тайминги, потоковая озвучка
# ---------------------------------------------------------------------------
import hashlib
import shutil as _shutil

PHRASE_CACHE_DIR = "temp_cache"
TIMING = True  # печатать, сколько заняла каждая стадия


class _T:
    """Замер времени стадии."""
    def __init__(self, label):
        self.label = label

    def __enter__(self):
        self.t0 = time.time()
        return self

    def __exit__(self, *exc):
        if TIMING:
            print(f"[время] {self.label}: {time.time() - self.t0:.2f}с")


def _cache_path(text: str, ext: str) -> str:
    os.makedirs(PHRASE_CACHE_DIR, exist_ok=True)
    lang = _response_language["lang"]
    key = hashlib.md5(f"{lang}:{TTS_VOICE}:{KOKORO_VOICE}:{_fish_choice[lang]}:{text}".encode("utf-8")).hexdigest()[:16]
    return os.path.join(PHRASE_CACHE_DIR, f"{lang}_{key}{ext}")


def _find_cached(text: str):
    for ext in (".wav", ".mp3"):
        p = _cache_path(text, ext)
        if os.path.exists(p):
            return p
    return None


def _play_file(path: str, interruptible: bool = False) -> None:
    """Проигрывает готовый файл и держит интерфейс в курсе."""
    pygame.mixer.music.load(path)
    pygame.mixer.music.play()
    duration = pygame.mixer.Sound(path).get_length()
    shared_state["speech_envelope"] = _compute_envelope(path, duration)
    shared_state["speech_duration"] = duration
    shared_state["speech_start_time"] = time.time()

    stop_event = threading.Event()
    listener = None
    if interruptible:
        listener = threading.Thread(target=_watch_for_interrupt,
                                    args=(stop_event,), daemon=True)
        listener.start()
    while pygame.mixer.music.get_busy():
        pygame.time.wait(50)
    stop_event.set()
    if listener is not None:
        listener.join(timeout=2)
    pygame.mixer.music.unload()


def speak_cached(text: str, interruptible: bool = False) -> None:
    """Для повторяющихся коротких реплик — отклик на имя, приветствие,
    прощание. Первый раз генерируем и кладём в кэш, дальше играем с диска:
    мгновенно и, для русского, без расхода символов ElevenLabs."""
    path = _find_cached(text)
    if path:
        print(f"[Atlas]: {text}  (из кэша)")
        try:
            shared_state["state"] = "speaking"
            shared_state["text"] = text
            _play_file(path, interruptible)
            shared_state["state"] = "idle"
            return
        except Exception as e:
            print(f"[cache play error]: {e}")
    speak(text, interruptible=interruptible, cache_as=text)


def _split_sentences(text: str):
    """Режет ответ на фразы, чтобы начать говорить первую, пока
    генерируются остальные."""
    parts = re.split(r"(?<=[.!?\u2026])\s+", text.strip())
    out, buf = [], ""
    for p in parts:
        if not p:
            continue
        if len(buf) + len(p) < 90:
            buf = (buf + " " + p).strip()
        else:
            if buf:
                out.append(buf)
            buf = p
    if buf:
        out.append(buf)
    return out or [text]


def _generate_any(text: str, filename: str) -> None:
    """Генерация речи в указанный файл — тот же каскад, что в speak()."""
    if _response_language["lang"] == "ru":
        try:
            _generate_ru_primary(text, filename)
            return
        except Exception as e:
            print(f"[ElevenLabs error]: {e}, пробую Silero")
        try:
            wav = filename.rsplit(".", 1)[0] + ".wav"
            _generate_speech_silero(text, wav)
            if wav != filename:
                _shutil.move(wav, filename)
            return
        except Exception as e:
            print(f"[Silero error]: {e}, пробую Edge-TTS")
        async def _gen():
            communicate = edge_tts.Communicate(text, RUSSIAN_TTS_VOICE)
            await communicate.save(filename)
        asyncio.run(_gen())
        return

    try:
        _generate_speech(text, filename)
    except Exception as e:
        print(f"[TTS] Groq недоступен ({e}), пробую Edge-TTS")
        _generate_speech_fallback(text, filename)


def speak_streaming(text: str, interruptible: bool = True) -> None:
    """Длинный ответ: озвучиваем первое предложение сразу, остальные
    готовим параллельно. Начало слышно на секунды раньше."""
    chunks = _split_sentences(text)
    if len(chunks) < 2:
        speak(text, interruptible=interruptible)
        return

    print(f"[Atlas]: {text}")
    _stop_speaking.clear()
    _maybe_play_ambient(text)
    ready = {}
    lock = threading.Lock()
    ext = ".mp3" if _response_language["lang"] == "ru" else ".wav"

    def _gen(i, chunk):
        fn = f"temp_stream_{i}{ext}"
        try:
            _generate_any(chunk, fn)
            with lock:
                ready[i] = fn
        except Exception as e:
            print(f"[stream gen {i}]: {e}")
            with lock:
                ready[i] = None

    threads = [threading.Thread(target=_gen, args=(i, c), daemon=True)
               for i, c in enumerate(chunks)]
    for t in threads:
        t.start()

    for i in range(len(chunks)):
        # прервали на предыдущем куске — остальное не произносим
        if _stop_speaking.is_set():
            break
        threads[i].join(timeout=30)
        fn = ready.get(i)
        if not fn or not os.path.exists(fn):
            continue
        try:
            _vary_pitch(fn)
            _play_file(fn, interruptible)
        except Exception as e:
            print(f"[stream play {i}]: {e}")
        finally:
            try:
                os.remove(fn)
            except Exception:
                pass

    # убираем куски, которые уже сгенерировались, но озвучивать их не нужно
    for fn in list(ready.values()):
        if fn and os.path.exists(fn):
            try:
                os.remove(fn)
            except Exception:
                pass


def speak(text: str, interruptible: bool = True, cache_as: str = None) -> None:
    _stop_speaking.clear()
    print(f"[Atlas]: {text}")
    _maybe_play_ambient(text)

    if _response_language["lang"] == "ru":
        filename = "temp_speech.mp3"
        try:
            _generate_ru_primary(text, filename)
        except Exception as e:
            print(f"[ElevenLabs error]: {e}, falling back to Silero")
            filename = "temp_speech.wav"
            try:
                _generate_speech_silero(text, filename)
            except Exception as e2:
                print(f"[Silero error]: {e2}, falling back to Edge-TTS")
                filename = "temp_speech.mp3"
                try:
                    async def _gen():
                        communicate = edge_tts.Communicate(text, RUSSIAN_TTS_VOICE)
                        await communicate.save(filename)
                    asyncio.run(_gen())
                except Exception as e3:
                    print(f"[TTS error, speaking skipped]: {e3}")
                    return

        _vary_pitch(filename)
        pygame.mixer.music.load(filename)
        pygame.mixer.music.play()
        duration = pygame.mixer.Sound(filename).get_length()
        shared_state["speech_envelope"] = _compute_envelope(filename, duration)
        shared_state["speech_duration"] = duration
        shared_state["speech_start_time"] = time.time()

        stop_event = threading.Event()
        listener = None
        if interruptible:
            listener = threading.Thread(target=_watch_for_interrupt, args=(stop_event,), daemon=True)
            listener.start()
        while pygame.mixer.music.get_busy():
            pygame.time.wait(100)
        stop_event.set()
        if listener is not None:
            listener.join(timeout=2)
        pygame.mixer.music.unload()
        if cache_as:
            try:
                _shutil.copy(filename, _cache_path(cache_as, os.path.splitext(filename)[1]))
            except Exception as e:
                print(f"[cache save]: {e}")
        os.remove(filename)
        return

    filename = "temp_speech.wav"

    try:
        _generate_speech(text, filename)
    except Exception as e:
        print(f"[TTS] Groq unavailable ({e}), falling back to Edge-TTS")
        try:
            filename = "temp_speech.mp3"
            _generate_speech_fallback(text, filename)
        except Exception as e2:
            print(f"[TTS error, speaking skipped]: {e2}")
            return

    _vary_pitch(filename)
    pygame.mixer.music.load(filename)
    pygame.mixer.music.play()
    duration = pygame.mixer.Sound(filename).get_length()
    shared_state["speech_envelope"] = _compute_envelope(filename, duration)
    shared_state["speech_duration"] = duration
    shared_state["speech_start_time"] = time.time()

    stop_event = threading.Event()
    listener = None
    if interruptible:
        listener = threading.Thread(target=_watch_for_interrupt, args=(stop_event,), daemon=True)
        listener.start()

    while pygame.mixer.music.get_busy():
        pygame.time.wait(100)

    stop_event.set()
    if listener is not None:
        listener.join(timeout=2)

    pygame.mixer.music.unload()
    if cache_as:
        try:
            _shutil.copy(filename, _cache_path(cache_as, os.path.splitext(filename)[1]))
        except Exception as e:
            print(f"[cache save]: {e}")
    os.remove(filename)

LISTEN_START_TIMEOUT = 8.0     # сколько ждать начала речи после нажатия / имени


def _listen_once(max_duration: float, silence_limit: float,
                 start_timeout: float = LISTEN_START_TIMEOUT):
    """Одна фраза. Начало и конец речи определяет Silero VAD, а не порог
    громкости, — поэтому работает и с тихим микрофоном, и при шуме.
    Возвращает аудио или None, если за start_timeout никто не заговорил."""
    import torch
    from collections import deque
    from silero_vad import VADIterator

    block = 512                                   # 32 мс — размер, который ждёт VAD
    vad = VADIterator(_get_vad(), sampling_rate=SAMPLE_RATE, threshold=0.5,
                      min_silence_duration_ms=int(silence_limit * 1000),
                      speech_pad_ms=100)
    preroll = deque(maxlen=10)                    # ~0.3 с до начала речи — не теряем первый слог
    frames, started = [], False
    t0 = time.time()

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=block)
    stream.start()
    try:
        while True:
            chunk, _overflow = stream.read(block)
            if started:
                frames.append(chunk)
            else:
                preroll.append(chunk)
            ev = vad(torch.from_numpy(chunk.flatten().astype("float32") / 32768.0))
            if ev and "start" in ev and not started:
                started = True
                frames.extend(preroll)
            if ev and "end" in ev and started:
                break                              # пауза дольше silence_limit — фраза закончена
            if not started and time.time() - t0 > start_timeout:
                break                              # так и не заговорили
            if started and len(frames) * block / SAMPLE_RATE > max_duration:
                break
    finally:
        stream.stop()
        stream.close()
        vad.reset_states()

    return np.concatenate(frames) if started else None


def _wait_for_continuation(window: float = 0.9):
    """Короткое окно после паузы: если человек продолжил мысль, ловим её.
    Именно это мешало договорить — Atlas считал первую же паузу концом
    фразы и убегал выполнять."""
    chunk_duration = 0.1
    chunk_size = int(SAMPLE_RATE * chunk_duration)
    checks = int(window / chunk_duration)

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16")
    stream.start()
    try:
        for _ in range(checks):
            chunk, _overflow = stream.read(chunk_size)
            volume = np.sqrt(np.mean(chunk.astype(np.float32) ** 2))
            if volume > 320:          # заговорил снова
                return True
    finally:
        stream.stop()
        stream.close()
    return False


def listen(max_duration: int = 10, silence_limit: float = 1.6) -> str:
    """Слушает команду. После паузы недолго ждёт продолжения — чтобы можно
    было договорить мысль, а не выпаливать её на одном дыхании."""
    print("Listening...")

    audio = _listen_once(max_duration, silence_limit)
    if audio is None:
        return ""

    parts = [audio]
    # до двух продолжений: хватает на длинную мысль, но не даёт слушать вечно
    for _ in range(2):
        if not _wait_for_continuation():
            break
        print("   (продолжаешь — слушаю дальше)")
        more = _listen_once(max_duration, silence_limit)
        if more is None:
            break
        parts.append(more)

    recording = np.concatenate(parts) if len(parts) > 1 else parts[0]
    with _T("распознавание"):
        text = _transcribe_audio(recording)

    if text == "":
        print("Didn't catch that, try again.")
        return ""

    print(f"[You]: {text}")
    return text
try:
    from pygame._sdl2 import audio as sdl2_audio
except ImportError:
    sdl2_audio = None

VOICE_OPTIONS = {
    "male": ["troy", "daniel", "austin"],
    "female": ["autumn", "diana", "hannah"],
}


def list_voices() -> str:
    """Lists available TTS voices, grouped by gender."""
    male = ", ".join(VOICE_OPTIONS["male"])
    female = ", ".join(VOICE_OPTIONS["female"])
    return f"Male voices: {male}. Female voices: {female}. Current voice: {TTS_VOICE}."


def set_voice(name: str) -> str:
    """Switches the TTS voice."""
    global TTS_VOICE
    name = name.lower().strip()
    all_voices = VOICE_OPTIONS["male"] + VOICE_OPTIONS["female"]
    if name not in all_voices:
        return f"Unknown voice '{name}'. Available: {', '.join(all_voices)}."
    TTS_VOICE = name
    return f"Voice switched to {name}."


def list_audio_devices() -> str:
    """Lists available speaker (output) and microphone (input) devices."""
    parts = []
    if sdl2_audio is not None:
        try:
            outputs = sdl2_audio.get_audio_device_names(False)
            parts.append("Speakers: " + ", ".join(outputs))
        except Exception as e:
            parts.append(f"Couldn't list speakers: {e}")

    inputs = [d['name'] for d in sd.query_devices()
                if d['max_input_channels'] > 0 and "переназначение" not in d['name'].lower()]
    parts.append("Microphones: " + ", ".join(inputs))
    return " | ".join(parts)


def set_microphone(name: str) -> str:
    """Switches which microphone Atlas listens through, by partial name match."""
    name_lower = name.lower().strip()
    for i, dev in enumerate(sd.query_devices()):
        if dev['max_input_channels'] > 0 and name_lower in dev['name'].lower():
            current = sd.default.device
            out_idx = current[1] if isinstance(current, (list, tuple)) else None
            sd.default.device = (i, out_idx)
            return f"Microphone switched to {dev['name']}."
    return f"Couldn't find a microphone matching '{name}'."


def set_speaker(name: str) -> str:
    """Switches which speaker/headphones Atlas talks through, by partial name match."""
    if sdl2_audio is None:
        return "Speaker switching isn't supported on this pygame version."
    try:
        outputs = sdl2_audio.get_audio_device_names(False)
    except Exception as e:
        return f"Couldn't list speakers: {e}"

    match = next((o for o in outputs if name.lower().strip() in o.lower()), None)
    if not match:
        return f"Couldn't find a speaker matching '{name}'."

    pygame.mixer.quit()
    pygame.mixer.init(devicename=match)
    return f"Speaker switched to {match}."

def _compute_envelope(filename: str, duration: float, buckets: int = 40) -> list:
    """
    Считает реальную амплитудную огибающую из WAV (Groq/Silero).
    Для MP3 (ElevenLabs/Edge-TTS) честного декодера нет без ffmpeg —
    генерируем правдоподобную, но не настоящую огибающую взамен.
    """
    try:
        if filename.lower().endswith(".wav"):
            with wave.open(filename, 'rb') as wf:
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)
                samples = np.frombuffer(raw, dtype=np.int16)
                if wf.getnchannels() == 2:
                    samples = samples[::2]
                chunk_size = max(1, len(samples) // buckets)
                envelope = []
                for i in range(buckets):
                    chunk = samples[i*chunk_size:(i+1)*chunk_size]
                    if len(chunk) == 0:
                        envelope.append(0.0)
                        continue
                    rms = np.sqrt(np.mean(chunk.astype(np.float32) ** 2))
                    envelope.append(float(min(rms / 8000, 1.0)))
                return envelope
    except Exception as e:
        print(f"[envelope error]: {e}")

    random.seed(len(filename) + int(duration * 100))
    return [round(0.25 + 0.65 * abs(random.random() - 0.5) * 2, 2) for _ in range(buckets)]

# ---------------------------------------------------------------------------
# Потоковая речь: фразы поступают по мере генерации ответа моделью
# ---------------------------------------------------------------------------
import queue as _queue


class SpeechStream:
    """Принимает текст кусками, режет на фразы и озвучивает по порядку:
    пока играет фраза N, синтезируется N+1."""
    MIN_CHARS = 40          # короче не режем — иначе речь рваная
    _END = object()

    def __init__(self, interruptible: bool = True):
        self.interruptible = interruptible
        self.buf = ""
        self.spoken_any = False
        self.n = 0
        self.texts = _queue.Queue()
        self.files = _queue.Queue()
        self.ext = ".mp3" if _response_language["lang"] == "ru" else ".wav"
        _stop_speaking.clear()
        self.play_t = threading.Thread(target=self._play_loop, daemon=True)
        threading.Thread(target=self._gen_loop, daemon=True).start()
        self.play_t.start()

    def feed(self, delta: str) -> None:
        """Новый кусок текста от модели."""
        self.buf += delta
        while True:
            m = re.search(r"[.!?\u2026](?=\s)", self.buf[self.MIN_CHARS:])
            if not m:
                break
            cut = self.MIN_CHARS + m.end()
            self._push(self.buf[:cut])
            self.buf = self.buf[cut:]

    def finish(self) -> None:
        """Отдать остаток и дождаться конца речи."""
        self._push(self.buf)
        self.buf = ""
        self.texts.put(self._END)
        self.play_t.join()

    def _push(self, text: str) -> None:
        text = re.sub(r"^\s*(?:[-*•]|#+|\d+\.)\s+", "", text, flags=re.M)
        text = text.replace("**", "").strip()
        if text and not _stop_speaking.is_set():
            self.spoken_any = True
            self.texts.put(text)

    def _gen_loop(self):
        while True:
            text = self.texts.get()
            if text is self._END or _stop_speaking.is_set():
                self.files.put(self._END)
                return
            fn = f"temp_live_{self.n}{self.ext}"
            self.n += 1
            try:
                _generate_any(text, fn)
                self.files.put((text, fn))
            except Exception as e:
                print(f"[live tts]: {e}")

    def _play_loop(self):
        while True:
            item = self.files.get()
            if item is self._END:
                shared_state["state"] = "idle"
                return
            text, fn = item
            try:
                if not _stop_speaking.is_set():
                    print(f"[Atlas ▶] {text}")
                    shared_state["state"] = "speaking"
                    shared_state["text"] = text
                    _vary_pitch(fn)
                    _play_file(fn, self.interruptible)
            except Exception as e:
                print(f"[live play]: {e}")
            finally:
                try:
                    os.remove(fn)
                except Exception:
                    pass

# ---------------------------------------------------------------------------
# Выбор голоса: Kokoro / ElevenLabs + голоса Fish Audio из .env
# ---------------------------------------------------------------------------
KOKORO_VOICES = ["bm_lewis", "bm_george", "bm_daniel", "bm_fable", "am_michael", "am_adam"]
FISH_API_KEY = os.getenv("FISH_API_KEY")
VOICE_PREFS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_prefs.json")


def _parse_fish(env_name: str) -> dict:
    """'Jarvis:id1,Other:id2' → {'Fish · Jarvis': 'id1', ...}"""
    out = {}
    for item in (os.getenv(env_name) or "").split(","):
        if ":" in item:
            name, vid = item.split(":", 1)
            out[f"Fish · {name.strip()}"] = vid.strip()
    return out


FISH_VOICES = {"en": _parse_fish("FISH_VOICES_EN"), "ru": _parse_fish("FISH_VOICES_RU")}
_fish_choice = {"en": None, "ru": None}   # ID выбранного Fish-голоса или None
_en_engine = {"name": "groq"}             # "groq" (основной) или "kokoro"
_groq_tts_until = {"t": 0.0}              # до какого времени лимит голоса Groq исчерпан
_fish = None


def _generate_speech_fish(text: str, filename: str, voice_id: str) -> None:
    global _fish
    from fishaudio import FishAudio
    from fishaudio.types import TTSConfig
    if _fish is None:
        _fish = FishAudio(api_key=FISH_API_KEY)
    fmt = "mp3" if filename.endswith(".mp3") else "wav"
    audio = _fish.tts.convert(text=text, config=TTSConfig(reference_id=voice_id, format=fmt))
    data = audio if isinstance(audio, (bytes, bytearray)) else b"".join(audio)
    with open(filename, "wb") as f:
        f.write(data)


def _generate_ru_primary(text: str, filename: str) -> None:
    """Русский: Fish, если выбран в настройках, иначе ElevenLabs."""
    vid = _fish_choice["ru"]
    if vid and FISH_API_KEY:
        try:
            _generate_speech_fish(text, filename, vid)
            return
        except Exception as e:
            print(f"[Fish error]: {e}, пробую ElevenLabs")
    _generate_speech_elevenlabs(text=text, filename=filename)


def list_voice_choices(lang: str) -> list:
    if lang == "ru":
        base = (list(ELEVENLABS_VOICE_OPTIONS["male"].keys())
                + list(ELEVENLABS_VOICE_OPTIONS["female"].keys()))
    else:
        base = list(KOKORO_VOICES) + VOICE_OPTIONS["male"] + VOICE_OPTIONS["female"]
    return base + list(FISH_VOICES[lang].keys())


def current_voice_choice(lang: str) -> str:
    vid = _fish_choice[lang]
    if vid:
        for name, v in FISH_VOICES[lang].items():
            if v == vid:
                return name
    if lang == "ru":
        return _elevenlabs_voice["name"]
    return TTS_VOICE if _en_engine["name"] == "groq" else KOKORO_VOICE


def choose_voice(name: str) -> str:
    """Выбор голоса из настроек для текущего языка."""
    global KOKORO_VOICE
    lang = _response_language["lang"]
    if name in FISH_VOICES[lang]:
        _fish_choice[lang] = FISH_VOICES[lang][name]
        result = f"Voice switched to {name}."
    else:
        _fish_choice[lang] = None
        if lang == "ru":
            result = set_elevenlabs_voice(name)
        elif name in KOKORO_VOICES:
            KOKORO_VOICE = name
            _en_engine["name"] = "kokoro"
            result = f"Voice switched to {name}."
        else:
            result = set_voice(name)          # голоса Groq Orpheus
            _en_engine["name"] = "groq"
    _save_voice_prefs()
    return result


def _save_voice_prefs() -> None:
    import json
    try:
        with open(VOICE_PREFS, "w", encoding="utf-8") as f:
            json.dump({"kokoro": KOKORO_VOICE, "fish": _fish_choice,
                       "eleven": _elevenlabs_voice.get("name"),
                       "en_engine": _en_engine["name"], "groq_voice": TTS_VOICE},
                      f, ensure_ascii=False)
    except Exception as e:
        print(f"[voice prefs] {e}")


def _load_voice_prefs() -> None:
    global KOKORO_VOICE
    import json
    try:
        with open(VOICE_PREFS, encoding="utf-8") as f:
            p = json.load(f)
    except Exception:
        return
    KOKORO_VOICE = p.get("kokoro", KOKORO_VOICE)
    _en_engine["name"] = p.get("en_engine", "kokoro")
    if p.get("groq_voice"):
        try:
            set_voice(p["groq_voice"])
        except Exception:
            pass
    for lang in ("en", "ru"):
        vid = (p.get("fish") or {}).get(lang)
        if vid in FISH_VOICES[lang].values():
            _fish_choice[lang] = vid
    if p.get("eleven"):
        try:
            set_elevenlabs_voice(p["eleven"])
        except Exception:
            pass

_load_voice_prefs()