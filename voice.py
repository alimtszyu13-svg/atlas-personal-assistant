import sounddevice as sd
import numpy as np
import os
import threading
import wave
import pygame
from groq import Groq
from dotenv import load_dotenv
import asyncio
import edge_tts
from elevenlabs.client import ElevenLabs

load_dotenv()

pygame.mixer.init()

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

TTS_MODEL = "canopylabs/orpheus-v1-english"
TTS_VOICE = "troy"
STT_MODEL = "whisper-large-v3"

SAMPLE_RATE = 16000
INTERRUPT_WORDS = ("stop", "enough", "quiet", "стоп", "хватит", "тихо")
WAKE_WORD = "atlas"
_push_to_talk_event = threading.Event()

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
    _push_to_talk_event.set()


def _generate_speech(text: str, filename: str) -> None:
    """Генерирует аудио через Groq TTS (Orpheus)."""
    response = groq_client.audio.speech.create(
        model=TTS_MODEL,
        voice=TTS_VOICE,
        input=text,
        response_format="wav"
    )
    response.write_to_file(filename)


def _transcribe_audio(recording: np.ndarray) -> str:
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
                    model=STT_MODEL,
                    language="ru"
                )
            else:
                result = groq_client.audio.translations.create(
                    file=(temp_path, f.read()),
                    model=STT_MODEL
                )
        return result.text.strip()
    except Exception as e:
        print(f"[STT error]: {e}")
        return ""
    finally:
        os.remove(temp_path)


def wait_for_wake_word() -> str:
    """Возвращает 'manual', если разбудили push-to-talk'ом, или 'voice', если услышал имя."""
    print("(waiting for wake word 'Atlas'...)")
    while True:
        if _push_to_talk_event.is_set():
            _push_to_talk_event.clear()
            return "manual"

        recording = sd.rec(int(2.5 * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                            channels=1, dtype='int16')
        sd.wait()

        if _push_to_talk_event.is_set():
            _push_to_talk_event.clear()
            return "manual"

        volume = np.sqrt(np.mean(recording.astype(np.float32) ** 2))
        if volume < 250:
            continue

        text = _transcribe_audio(recording).lower()
        if WAKE_WORD in text:
            return "voice"


def _watch_for_interrupt(stop_event: threading.Event) -> None:
    while pygame.mixer.music.get_busy() and not stop_event.is_set():
        chunk = sd.rec(int(1.5 * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                        channels=1, dtype='int16')
        sd.wait()

        if stop_event.is_set():
            break

        volume = np.sqrt(np.mean(chunk.astype(np.float32) ** 2))
        if volume < 250:
            continue

        text = _transcribe_audio(chunk).lower()
        if any(word in text for word in INTERRUPT_WORDS):
            print("[Atlas]: (interrupted)")
            pygame.mixer.music.stop()


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

def speak(text: str, interruptible: bool = True) -> None:
    print(f"[Atlas]: {text}")

    if _response_language["lang"] == "ru":
        filename = "temp_speech.mp3"
        try:
            _generate_speech_elevenlabs(text, filename)
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
        pygame.mixer.music.load(filename)
        pygame.mixer.music.play()

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

    pygame.mixer.music.load(filename)
    pygame.mixer.music.play()

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
    os.remove(filename)

def listen(max_duration: int = 8, silence_limit: float = 1.2) -> str:
    print("Listening...")
    chunk_duration = 0.1
    chunk_size = int(SAMPLE_RATE * chunk_duration)
    silence_threshold = 300

    frames = []
    silent_chunks = 0
    max_silent_chunks = int(silence_limit / chunk_duration)
    total_chunks = int(max_duration / chunk_duration)

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='int16')
    stream.start()

    started_speaking = False
    for _ in range(total_chunks):
        chunk, _ = stream.read(chunk_size)
        frames.append(chunk)
        volume = np.sqrt(np.mean(chunk.astype(np.float32) ** 2))
        if volume > silence_threshold:
            started_speaking = True
            silent_chunks = 0
        elif started_speaking:
            silent_chunks += 1
        if started_speaking and silent_chunks > max_silent_chunks:
            break

    stream.stop()
    stream.close()

    recording = np.concatenate(frames)
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