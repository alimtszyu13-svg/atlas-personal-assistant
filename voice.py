import sounddevice as sd
import numpy as np
import speech_recognition as sr
import asyncio
import edge_tts
import os
from playsound import playsound

# Список голосов на русском можно посмотреть командой:
# edge-tts --list-voices | findstr ru-RU
VOICE = "ru-RU-DmitryNeural"  # мужской, звучит уверенно — можно заменить на ru-RU-SvetlanaNeural (женский)

async def _generate_speech(text: str, filename: str) -> None:
    """Генерирует mp3-файл с озвучкой текста через Edge-TTS."""
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(filename)

def speak(text: str) -> None:
    print(f"[Atlas]: {text}")
    filename = "temp_speech.mp3"
    # edge_tts асинхронный, а наш код синхронный —
    # asyncio.run() запускает асинхронную функцию и ждёт её завершения,
    # "мостик" между двумя мирами
    asyncio.run(_generate_speech(text, filename))
    playsound(filename)
    os.remove(filename)  # чистим за собой, чтобы файлы не копились


recognizer = sr.Recognizer()
SAMPLE_RATE = 16000

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