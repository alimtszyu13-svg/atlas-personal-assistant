import sounddevice as sd
import numpy as np
import speech_recognition as sr
import asyncio
import edge_tts
import os
import threading
import pygame

pygame.mixer.init()

VOICE = "ru-RU-DmitryNeural"

async def _generate_speech(text: str, filename: str) -> None:
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(filename)


recognizer = sr.Recognizer()
SAMPLE_RATE = 16000
INTERRUPT_WORDS = ("стоп", "хватит", "замолчи")

WAKE_WORD = "атлас"

def wait_for_wake_word() -> None:
    print("(жду команду 'Атлас'...)")
    while True:
        recording = sd.rec(int(2.5 * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                            channels=1, dtype='int16')
        sd.wait()

        volume = np.sqrt(np.mean(recording.astype(np.float32) ** 2))
        if volume < 250:
            continue

        audio = sr.AudioData(recording.tobytes(), SAMPLE_RATE, 2)
        try:
            text = recognizer.recognize_google(audio, language="ru-RU").lower()
            if WAKE_WORD in text:
                return
        except (sr.UnknownValueError, sr.RequestError):
            continue


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

        audio = sr.AudioData(chunk.tobytes(), SAMPLE_RATE, 2)
        try:
            text = recognizer.recognize_google(audio, language="ru-RU").lower()
            if any(word in text for word in INTERRUPT_WORDS):
                print("[Atlas]: (прерван)")
                pygame.mixer.music.stop()
        except (sr.UnknownValueError, sr.RequestError):
            pass

def speak(text: str, interruptible: bool = True) -> None:
    print(f"[Atlas]: {text}")
    filename = "temp_speech.mp3"
    asyncio.run(_generate_speech(text, filename))

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

    # ждём, пока поток-слушатель реально закончится (максимум 2 сек на это),
    # иначе он может ещё держать микрофон, когда мы уже начнём listen()
    if listener is not None:
        listener.join(timeout=2)

    pygame.mixer.music.unload()
    os.remove(filename)


def listen(max_duration: int = 8, silence_limit: float = 1.2) -> str:
    print("Слушаю...")
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
    audio = sr.AudioData(recording.tobytes(), SAMPLE_RATE, 2)

    try:
        text = recognizer.recognize_google(audio, language="ru-RU")
        print(f"[Ты]: {text}")
        return text
    except sr.UnknownValueError:
        print("Не расслышал, повтори.")
        return ""
    except sr.RequestError:
        print("Нет связи с сервисом распознавания.")
        return ""
    