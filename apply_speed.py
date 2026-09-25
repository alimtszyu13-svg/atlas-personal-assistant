"""
Патч блока «скорость» для Atlas.

Запусти один раз из папки проекта:   python apply_speed.py

Что делает:
  voice.py    — кэш повторяющихся фраз, потоковая озвучка длинных ответов,
                пауза перед обработкой 1.2 -> 0.7 сек, замеры времени
  ai_brain.py — чинит отступ в блоке extra, параллельный запуск независимых
                инструментов, тайминги шагов модели
  main.py     — использует кэш для отклика/приветствия и потоковую озвучку

Перед правкой каждый файл копируется в *.bak — если что-то пойдёт не так,
просто верни копию.
"""

import os
import re
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
    готовим параллельно. Пользователь слышит начало на секунды раньше."""
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

AI_PARALLEL = '''
# Инструменты, которые только читают и ничего не меняют — их безопасно
# запускать одновременно. Когда модель просит два поиска сразу, это экономит
# несколько секунд. Всё остальное (клики, удаление, запуск) — строго по
# очереди, чтобы не поломать порядок действий.
READ_ONLY_TOOLS = {
    "search_web", "get_weather", "get_news", "recall_memories",
    "get_cpu_usage", "get_memory_usage", "get_battery_status",
    "get_disk_usage", "get_uptime", "get_volume", "get_brightness",
    "list_notes", "list_todos", "list_timers", "list_today_events",
    "list_upcoming_events", "get_recent_emails", "get_unread_count",
    "get_my_ip", "get_local_ip", "is_website_up", "ping_host",
    "list_steam_games", "locate_file", "calculate", "convert_units",
    "word_count", "translate_text", "list_voices", "list_audio_devices",
}


def _run_one_tool(func_name, func_args):
    """Выполняет один инструмент, возвращает (результат, успех, мс)."""
    func = AVAILABLE_FUNCTIONS.get(func_name)
    start = time.time()
    if not func:
        return f"Функция {func_name} не найдена.", False, 0
    try:
        valid = set(inspect.signature(func).parameters.keys())
        args = {k: v for k, v in func_args.items() if k in valid}
        result = func(**args)
        success = True
    except Exception as tool_err:
        print(f"[Tool error in {func_name}]: {tool_err}")
        result = f"Something went wrong running {func_name}: {tool_err}"
        success = False
    ms = int((time.time() - start) * 1000)
    print(f"[время] {func_name}: {ms / 1000:.2f}с")
    return result, success, ms


'''


def patch_voice(path):
    src = open(path, encoding="utf-8").read()
    if "def speak_cached" in src:
        return "уже пропатчен"

    src = src.replace(
        "def listen(max_duration: int = 8, silence_limit: float = 1.2) -> str:",
        "def listen(max_duration: int = 8, silence_limit: float = 0.7) -> str:")

    src = src.replace(
        "def speak(text: str, interruptible: bool = True) -> None:",
        VOICE_BLOCK + "def speak(text: str, interruptible: bool = True, cache_as: str = None) -> None:")

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

    src = src.replace('''    recording = np.concatenate(frames)
    text = _transcribe_audio(recording)''', '''    recording = np.concatenate(frames)
    with _T("распознавание"):
        text = _transcribe_audio(recording)''')

    if "\nimport re\n" not in src:
        src = src.replace("import threading\nimport wave",
                          "import threading\nimport re\nimport wave")

    open(path, "w", encoding="utf-8").write(src)
    return "ok"


def patch_ai_brain(path):
    src = open(path, encoding="utf-8").read()
    if "READ_ONLY_TOOLS" in src:
        return "уже пропатчен"

    # 1) чиним съехавший отступ блока extra
    broken = '''                })
            
                extra = [context_msg]
            if current_plan:
                extra.append({"role": "system", "content":
                    "Active plan: " + json.dumps(current_plan, ensure_ascii=False)})
            model = MODEL_SMART if step_index == 0 else MODEL_FAST
            extra = [context_msg]'''
    fixed = '''                })

            model = MODEL_SMART if step_index == 0 else MODEL_FAST
            extra = [context_msg]'''
    if broken in src:
        src = src.replace(broken, fixed)
    else:
        print("  ! блок extra выглядит иначе — проверь его руками")

    # 2) добавляем список безопасных инструментов и помощник
    src = src.replace("def ask_ai(question: str) -> str:",
                      AI_PARALLEL + "def ask_ai(question: str) -> str:")

    # 3) параллельный запуск, когда модель просит несколько чтений сразу
    old_loop = '''            for tool_call in message.tool_calls:
                func_name = tool_call.function.name'''
    new_loop = '''            names = [tc.function.name for tc in message.tool_calls]
            if len(names) > 1 and all(n in READ_ONLY_TOOLS for n in names):
                import concurrent.futures
                print(f"[параллельно] {names}")
                parsed = []
                for tc in message.tool_calls:
                    a = json.loads(tc.function.arguments)
                    parsed.append((tc, tc.function.name, {k: v for k, v in a.items() if k}))
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                    futures = [ex.submit(_run_one_tool, n, a) for _tc, n, a in parsed]
                    outcomes = [f.result() for f in futures]
                for (tc, n, a), (result, success, ms) in zip(parsed, outcomes):
                    step_index += 1
                    g = tool_router.group_of_tool(n)
                    if g and g not in active_groups:
                        active_groups.add(g)
                        active_schema = tool_router.filter_schema(TOOLS_SCHEMA, active_groups)
                    threading.Thread(target=log_task, args=(n, a, result, success, ms),
                                     daemon=True).start()
                    rs = str(result)
                    if len(rs) > MAX_TOOL_RESULT_CHARS:
                        rs = rs[:MAX_TOOL_RESULT_CHARS] + "\\n...[обрезано]"
                    conversation_history.append({"role": "tool",
                                                 "tool_call_id": tc.id, "content": rs})
                continue

            for tool_call in message.tool_calls:
                func_name = tool_call.function.name'''
    if old_loop in src:
        src = src.replace(old_loop, new_loop, 1)
    else:
        print("  ! цикл tool_calls выглядит иначе — параллельность не вставлена")

    # 4) тайминг каждого шага модели
    src = src.replace('''                    response = client.chat.completions.create(
                        model=model,''', '''                    _t0 = time.time()
                    response = client.chat.completions.create(
                        model=model,''')
    src = src.replace('''                        reasoning_effort=reasoning_effort,
                    )
                    break''', '''                        reasoning_effort=reasoning_effort,
                    )
                    print(f"[время] модель ({model.split('/')[-1]}, шаг {step_index}): "
                          f"{time.time() - _t0:.2f}с")
                    break''')

    open(path, "w", encoding="utf-8").write(src)
    return "ok"


def patch_main(path):
    src = open(path, encoding="utf-8").read()
    if "speak_cached" in src:
        return "уже пропатчен"

    src = src.replace(
        "from voice import speak, listen, wait_for_wake_word, _push_to_talk_event, get_response_language",
        "from voice import speak, speak_cached, speak_streaming, listen, wait_for_wake_word, _push_to_talk_event, get_response_language")

    # длинные ответы озвучиваем потоково
    src = src.replace('''    shared_state["chat_history"].append(("Atlas", text))
    speak(text, interruptible=interruptible)''', '''    shared_state["chat_history"].append(("Atlas", text))
    if interruptible and len(text) > 110:
        speak_streaming(text, interruptible=True)
    else:
        speak(text, interruptible=interruptible)''')

    # отклик на имя — из кэша
    src = src.replace('''            _speak_and_update(random.choice(WAKE_RESPONSES[lang]), interruptible=False)''',
                      '''            phrase = random.choice(WAKE_RESPONSES[lang])
            shared_state["chat_history"].append(("Atlas", phrase))
            speak_cached(phrase)''')

    # приветствие и прощание — тоже
    src = src.replace('''    _speak_and_update(f"{_time_greeting()} {random.choice(GREETING_TAIL[lang])}", interruptible=False)''',
                      '''    greeting = f"{_time_greeting()} {random.choice(GREETING_TAIL[lang])}"
    shared_state["chat_history"].append(("Atlas", greeting))
    speak_cached(greeting)''')

    src = src.replace('''            shutdown_msg = random.choice(SHUTDOWN_RESPONSES[get_response_language()])
            _speak_and_update(shutdown_msg)''',
                      '''            shutdown_msg = random.choice(SHUTDOWN_RESPONSES[get_response_language()])
            shared_state["chat_history"].append(("Atlas", shutdown_msg))
            speak_cached(shutdown_msg)''')

    open(path, "w", encoding="utf-8").write(src)
    return "ok"


def main():
    targets = [("voice.py", patch_voice),
               ("ai_brain.py", patch_ai_brain),
               ("main.py", patch_main)]

    missing = [n for n, _ in targets if not os.path.exists(n)]
    if missing:
        print("Не нашёл файлы:", ", ".join(missing))
        print("Запусти скрипт из папки проекта Atlas.")
        sys.exit(1)

    for name, fn in targets:
        shutil.copy(name, name + ".bak")
        try:
            status = fn(name)
            print(f"{name}: {status}  (копия в {name}.bak)")
        except Exception as e:
            shutil.copy(name + ".bak", name)
            print(f"{name}: ОШИБКА {e} — файл восстановлен из копии")

    print("\nПроверяю синтаксис...")
    import ast
    ok = True
    for name, _ in targets:
        try:
            ast.parse(open(name, encoding="utf-8").read())
            print(f"  {name}: OK")
        except SyntaxError as e:
            ok = False
            print(f"  {name}: СИНТАКСИС СЛОМАН строка {e.lineno}: {e.msg}")
    if ok:
        print("\nГотово. Запускай: python main.py")
    else:
        print("\nВосстанови файлы из .bak и пришли мне вывод.")


if __name__ == "__main__":
    main()