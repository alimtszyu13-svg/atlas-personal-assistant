"""
Три починки по логу.

1. Обрывает на полуслове. Я поставил паузу 0.7с — для команды хватает, для
   мысли нет. Теперь 1.1с плюс «продолжение»: после паузы Atlas ещё коротко
   слушает, и если ты заговорил дальше, дописывает сказанное к той же фразе.
   Короткие команды при этом не тормозят — продолжение ждёт всего ~0.9с.

2. Поиск по содержимому не использовался: в наборе инструментов его не было,
   модель брала locate_file, который ищет по имени файла. Добавляем.

3. Потеря 35 секунд: предупреждение на третьем шаге велело модели взять
   browser_screenshot_describe, которого нет в отфильтрованном наборе —
   два провальных вызова подряд. Теперь предупреждение появляется только
   если браузерные инструменты действительно доступны.

Запусти из папки проекта:   python fix_listening.py
"""

import ast
import os
import shutil
import sys

CONTINUATION = '''

def _listen_once(max_duration: float, silence_limit: float):
    """Один заход записи: ждём речь, пишем до паузы. Возвращает аудио или
    None, если человек так и не заговорил."""
    chunk_duration = 0.1
    chunk_size = int(SAMPLE_RATE * chunk_duration)
    silence_threshold = 300

    frames = []
    silent_chunks = 0
    max_silent = int(silence_limit / chunk_duration)
    total_chunks = int(max_duration / chunk_duration)

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16")
    stream.start()
    started = False
    try:
        for _ in range(total_chunks):
            chunk, _overflow = stream.read(chunk_size)
            frames.append(chunk)
            volume = np.sqrt(np.mean(chunk.astype(np.float32) ** 2))
            if volume > silence_threshold:
                started = True
                silent_chunks = 0
            elif started:
                silent_chunks += 1
            if started and silent_chunks > max_silent:
                break
    finally:
        stream.stop()
        stream.close()

    if not started:
        return None
    return np.concatenate(frames)


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

'''

NEW_LISTEN = '''def listen(max_duration: int = 10, silence_limit: float = 1.1) -> str:
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
'''


def _end_of_function(src: str, start: int) -> int:
    """Конец функции — первая следующая строка без отступа. Надёжнее, чем
    искать конкретный якорь: у разных версий файла он свой."""
    lines = src[start:].split("\n")
    for i, line in enumerate(lines[1:], 1):
        if line.strip() and not line[0].isspace():
            return start + sum(len(l) + 1 for l in lines[:i])
    return len(src)


def patch_voice():
    path = "voice.py"
    src = open(path, encoding="utf-8").read()
    if "_wait_for_continuation" in src:
        return "уже пропатчен"

    shutil.copy(path, path + ".bak4")

    start = src.find("def listen(max_duration")
    if start == -1:
        return "! не нашёл listen()"
    end = _end_of_function(src, start)

    src = src[:start] + CONTINUATION.lstrip("\n") + "\n" + NEW_LISTEN + src[end:]

    ast.parse(src)
    open(path, "w", encoding="utf-8").write(src)
    return "ok — пауза 1.1с + продолжение фразы"


def patch_router():
    path = "tool_router.py"
    src = open(path, encoding="utf-8").read()
    if "search_file_content" in src:
        return "уже пропатчен"

    shutil.copy(path, path + ".bak2")

    src = src.replace('''        "rename_file", "copy_file", "move_file",
    },''', '''        "rename_file", "copy_file", "move_file",
        "search_file_content", "open_found_file",
    },''')

    src = src.replace('''        "rename", "copy", "move", "delete", "desktop", "download",
    ),''', '''        "rename", "copy", "move", "delete", "desktop", "download",
        "где файл", "где лежит", "содержим", "внутри файла", "писал про",
        "написал про", "текстом", "wrote about", "talked about", "mentioned",
        "содержит", "где я", "забыл название",
    ),''')

    ast.parse(src)
    open(path, "w", encoding="utf-8").write(src)
    return "ok — поиск по содержимому в группе files"


def patch_brain():
    path = "ai_brain.py"
    src = open(path, encoding="utf-8").read()
    changed = []

    shutil.copy(path, path + ".bak4")

    # предупреждение про скриншот — только если браузер реально доступен
    old = '''            if step_index == 2:
                conversation_history.append({
                    "role": "system",
                    "content": "WARNING: You have made multiple unsuccessful attempts using DOM/HTML tools. Stop guessing. IMMEDIATELY use the `browser_screenshot_describe` tool to visually analyze the screen and click the required element."
                })'''
    new = '''            # Подсказываем про скриншот только если браузерные инструменты
            # реально в наборе. Иначе модель пытается вызвать недоступный
            # инструмент, получает отказ и теряет десятки секунд.
            if step_index == 2 and "browser" in active_groups:
                conversation_history.append({
                    "role": "system",
                    "content": "WARNING: You have made multiple unsuccessful attempts using DOM/HTML tools. Stop guessing. IMMEDIATELY use the `browser_screenshot_describe` tool to visually analyze the screen and click the required element."
                })'''
    if old in src:
        src = src.replace(old, new, 1)
        changed.append("предупреждение про скриншот — только при доступном браузере")

    # подсказка: для поиска по содержимому не годится locate_file
    hint = '''    "When the user is looking for a file but describes what is INSIDE it "
    "rather than its name ('the file where I wrote about my algebra "
    "textbook'), use search_file_content — locate_file only matches "
    "filenames and will not find it. "
    ""
'''
    anchor = '''    "You have a large toolkit: apps, files, weather, news, timers, system "'''
    if "search_file_content — locate_file only matches" not in src and anchor in src:
        src = src.replace(anchor, hint + anchor, 1)
        changed.append("подсказка в промпте про поиск по содержимому")

    ast.parse(src)
    open(path, "w", encoding="utf-8").write(src)
    return "ok — " + "; ".join(changed) if changed else "нечего менять"


def main():
    for f in ("voice.py", "tool_router.py", "ai_brain.py"):
        if not os.path.exists(f):
            print(f"Не нашёл {f} — запусти из папки проекта Atlas.")
            sys.exit(1)

    for name, fn in (("voice.py", patch_voice),
                     ("tool_router.py", patch_router),
                     ("ai_brain.py", patch_brain)):
        try:
            print(f"{name}: {fn()}")
        except SyntaxError as e:
            print(f"{name}: СИНТАКСИС строка {e.lineno}: {e.msg} — файл не тронут")
        except Exception as e:
            print(f"{name}: ошибка {e}")

    print("\nПроверяю синтаксис...")
    ok = True
    for f in ("voice.py", "tool_router.py", "ai_brain.py"):
        try:
            ast.parse(open(f, encoding="utf-8").read())
            print(f"  {f}: OK")
        except SyntaxError as e:
            ok = False
            print(f"  {f}: СЛОМАН строка {e.lineno}: {e.msg}")
    print("\nГотово. Запускай: python main.py" if ok
          else "\nВерни файлы из .bak и пришли вывод.")


if __name__ == "__main__":
    main()