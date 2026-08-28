import sounddevice as sd
import numpy as np
import speech_recognition as sr
import pyttsx3

def speak(text: str) -> None:
    print(f"[Atlas]: {text}")
    local_engine = pyttsx3.init()   # создаём заново каждый раз
    local_engine.setProperty('rate', 180)
    local_engine.say(text)
    local_engine.runAndWait()
    local_engine.stop()


recognizer = sr.Recognizer()
SAMPLE_RATE = 16000

def listen(max_duration: int = 8, silence_limit: float = 1.2) -> str:
    """
    Слушает микрофон и сама решает, когда человек договорил —
    по тишине, а не по жёсткому таймеру.
    """
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