import sounddevice as sd
import numpy as np
import os
import threading
import wave
import pygame
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

pygame.mixer.init()

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

TTS_MODEL = "canopylabs/orpheus-v1-english"
TTS_VOICE = "troy"
STT_MODEL = "whisper-large-v3"

SAMPLE_RATE = 16000
INTERRUPT_WORDS = ("stop", "enough", "quiet")
WAKE_WORD = "atlas"


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
    Сохраняет numpy-запись во временный wav-файл и отправляет
    в Groq Whisper Translation — распознаёт речь на ЛЮБОМ языке
    и переводит результат в английский текст. Официальный механизм
    (endpoint /audio/translations), а не побочный эффект несовпадения language.
    """
    temp_path = "temp_stt.wav"

    with wave.open(temp_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(recording.tobytes())

    try:
        with open(temp_path, "rb") as f:
            translation = groq_client.audio.translations.create(
                file=(temp_path, f.read()),
                model=STT_MODEL
            )
        return translation.text.strip()
    except Exception as e:
        print(f"[STT error]: {e}")
        return ""
    finally:
        os.remove(temp_path)


def wait_for_wake_word() -> None:
    print("(waiting for wake word 'Atlas'...)")
    while True:
        recording = sd.rec(int(2.5 * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                            channels=1, dtype='int16')
        sd.wait()

        volume = np.sqrt(np.mean(recording.astype(np.float32) ** 2))
        if volume < 250:
            continue

        text = _transcribe_audio(recording).lower()
        if WAKE_WORD in text:
            return


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


def speak(text: str, interruptible: bool = True) -> None:
    print(f"[Atlas]: {text}")
    filename = "temp_speech.wav"

    try:
        _generate_speech(text, filename)
    except Exception as e:
        print(f"[TTS error, speaking skipped]: {e}")
        return  # не можем озвучить — просто продолжаем работу без голоса в этот раз

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