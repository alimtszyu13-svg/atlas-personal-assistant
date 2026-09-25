"""
Починка voice.py.

Что случилось: apply_speed.py проверял «уже пропатчен?» по наличию
speak_cached. Этот кусок был вставлен руками раньше, скрипт его увидел и
пропустил файл целиком — поэтому speak_streaming, _play_file и _generate_any
так и не появились.

Этот скрипт убирает старый ручной блок и вставляет полный.
Запусти из папки проекта:   python fix_voice.py
"""

import ast
import os
import shutil
import sys

VOICE_BLOCK = '''
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
    key = hashlib.md5(f"{lang}:{TTS_VOICE}:{text}".encode("utf-8")).hexdigest()[:16]
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
    parts = re.split(r"(?<=[.!?\\u2026])\\s+", text.strip())
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
            _generate_speech_elevenlabs(text, filename)
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
        threads[i].join(timeout=30)
        fn = ready.get(i)
        if not fn or not os.path.exists(fn):
            continue
        try:
            _vary_pitch(fn)
            _play_file(fn, interruptible and i == 0)
        except Exception as e:
            print(f"[stream play {i}]: {e}")
        finally:
            try:
                os.remove(fn)
            except Exception:
                pass


'''


def main():
    path = "voice.py"
    if not os.path.exists(path):
        print("Не нашёл voice.py — запусти скрипт из папки проекта Atlas.")
        sys.exit(1)

    src = open(path, encoding="utf-8").read()
    shutil.copy(path, path + ".bak2")
    print(f"копия: {path}.bak2")

    if "def speak_streaming" in src:
        print("voice.py уже содержит полный блок — чинить нечего.")
        return

    # 1) убираем старый ручной блок, если он есть
    if "_cached_phrase_path" in src:
        start = src.find("# Кэш готовых фраз")
        if start == -1:
            start = src.find("import hashlib")
        end = src.find("def speak(text:", start if start != -1 else 0)
        if start != -1 and end != -1 and end > start:
            removed = end - start
            src = src[:start] + src[end:]
            print(f"убран старый ручной блок ({removed} символов)")
        else:
            print("! старый блок найден, но границы неясны — проверь файл руками")
            sys.exit(1)

    # 2) вставляем полный блок и расширяем сигнатуру speak
    marker_plain = "def speak(text: str, interruptible: bool = True) -> None:"
    marker_cached = "def speak(text: str, interruptible: bool = True, cache_as: str = None) -> None:"
    new_sig = marker_cached

    if marker_plain in src:
        src = src.replace(marker_plain, VOICE_BLOCK + new_sig, 1)
    elif marker_cached in src:
        src = src.replace(marker_cached, VOICE_BLOCK + new_sig, 1)
    else:
        print("! не нашёл определение speak() — пришли мне voice.py")
        sys.exit(1)
    print("вставлен полный блок")

    # 3) сохранение в кэш перед удалением временного файла (обе ветки)
    if "_cache_path(cache_as" not in src:
        src = src.replace('''        pygame.mixer.music.unload()
        os.remove(filename)
        return''', '''        pygame.mixer.music.unload()
        if cache_as:
            try:
                _shutil.copy(filename, _cache_path(cache_as, os.path.splitext(filename)[1]))
            except Exception as e:
                print(f"[cache save]: {e}")
        os.remove(filename)
        return''')

        src = src.replace('''    pygame.mixer.music.unload()
    os.remove(filename)

def listen''', '''    pygame.mixer.music.unload()
    if cache_as:
        try:
            _shutil.copy(filename, _cache_path(cache_as, os.path.splitext(filename)[1]))
        except Exception as e:
            print(f"[cache save]: {e}")
    os.remove(filename)

def listen''')
        print(f"сохранений в кэш добавлено: {src.count('_cache_path(cache_as')}")

    # 4) пауза перед обработкой и тайминг распознавания
    src = src.replace(
        "def listen(max_duration: int = 8, silence_limit: float = 1.2) -> str:",
        "def listen(max_duration: int = 8, silence_limit: float = 0.7) -> str:")

    if 'with _T("распознавание")' not in src:
        src = src.replace('''    recording = np.concatenate(frames)
    text = _transcribe_audio(recording)''', '''    recording = np.concatenate(frames)
    with _T("распознавание"):
        text = _transcribe_audio(recording)''')

    if "\nimport re\n" not in src:
        src = src.replace("import threading\nimport wave",
                          "import threading\nimport re\nimport wave")

    # 5) проверка синтаксиса до записи
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"СИНТАКСИС СЛОМАЛСЯ бы на строке {e.lineno}: {e.msg}")
        print("Файл не тронут. Пришли мне voice.py.")
        sys.exit(1)

    open(path, "w", encoding="utf-8").write(src)

    check = open(path, encoding="utf-8").read()
    print("\nпроверка:")
    for m in ("def _play_file", "def speak_cached", "def _split_sentences",
              "def _generate_any", "def speak_streaming",
              "silence_limit: float = 0.7", "cache_as: str = None"):
        print(f"  {'OK ' if m in check else 'НЕТ'}  {m}")

    print("\nТеперь верни в main.py импорт speak_streaming:")
    print("  from voice import speak, speak_cached, speak_streaming, listen, "
          "wait_for_wake_word, _push_to_talk_event, get_response_language")
    print("и верни в _speak_and_update блок с speak_streaming, если убирал.")


if __name__ == "__main__":
    main()