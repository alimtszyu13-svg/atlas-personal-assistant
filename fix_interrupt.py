"""
Починка прерывания речи ("стоп" / "хватит" во время ответа).

Что сломалось: в потоковой озвучке слушатель прерывания запускался только
для первого куска, а остальные играли без него. И даже если первый кусок
обрывался, следующие всё равно проигрывались — цикл ничего не проверял.

Решение: общий флаг _stop_speaking. Слушатель ставит его, когда слышит
стоп-слово; каждый кусок слушает; цикл проверяет флаг между кусками и
выбрасывает недоигранное.

Запусти из папки проекта:   python fix_interrupt.py
"""

import ast
import os
import shutil
import sys


def main():
    path = "voice.py"
    if not os.path.exists(path):
        print("Не нашёл voice.py — запусти из папки проекта Atlas.")
        sys.exit(1)

    src = open(path, encoding="utf-8").read()

    if "_stop_speaking" in src:
        print("voice.py уже починен.")
        return

    shutil.copy(path, path + ".bak3")
    print(f"копия: {path}.bak3")
    changes = []

    # 1) общий флаг остановки — рядом с остальными флагами модуля
    if "_push_to_talk_event = threading.Event()" in src:
        src = src.replace(
            "_push_to_talk_event = threading.Event()",
            "_push_to_talk_event = threading.Event()\n"
            "# Ставится, когда пользователь сказал стоп-слово во время речи.\n"
            "# Нужен, чтобы потоковая озвучка выбросила недоигранные куски,\n"
            "# а не продолжала болтать после прерывания.\n"
            "_stop_speaking = threading.Event()", 1)
        changes.append("добавлен флаг _stop_speaking")
    else:
        print("! не нашёл _push_to_talk_event — пришли voice.py")
        sys.exit(1)

    # 2) слушатель выставляет флаг
    old_listener = '''        text = _transcribe_audio(chunk).lower()
        if any(word in text for word in INTERRUPT_WORDS):
            print("[Atlas]: (interrupted)")
            pygame.mixer.music.stop()'''
    new_listener = '''        text = _transcribe_audio(chunk).lower()
        if any(word in text for word in INTERRUPT_WORDS):
            print("[Atlas]: (прервано)")
            _stop_speaking.set()
            pygame.mixer.music.stop()
            return'''
    if old_listener in src:
        src = src.replace(old_listener, new_listener, 1)
        changes.append("слушатель теперь ставит флаг и выходит")
    else:
        print("! _watch_for_interrupt выглядит иначе — проверь руками")

    # 3) сбрасываем флаг в начале каждой новой реплики
    src = src.replace(
        '''def speak(text: str, interruptible: bool = True, cache_as: str = None) -> None:
    print(f"[Atlas]: {text}")''',
        '''def speak(text: str, interruptible: bool = True, cache_as: str = None) -> None:
    _stop_speaking.clear()
    print(f"[Atlas]: {text}")''', 1)
    changes.append("флаг сбрасывается в speak()")

    # 4) все куски потоковой озвучки слушают прерывание, цикл его уважает
    old_stream = '''    print(f"[Atlas]: {text}")
    _maybe_play_ambient(text)
    ready = {}'''
    new_stream = '''    print(f"[Atlas]: {text}")
    _stop_speaking.clear()
    _maybe_play_ambient(text)
    ready = {}'''
    if old_stream in src:
        src = src.replace(old_stream, new_stream, 1)
        changes.append("флаг сбрасывается в speak_streaming()")

    old_play_loop = '''    for i in range(len(chunks)):
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
                pass'''
    new_play_loop = '''    for i in range(len(chunks)):
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
                pass'''
    if old_play_loop in src:
        src = src.replace(old_play_loop, new_play_loop, 1)
        changes.append("каждый кусок слушает прерывание, цикл его уважает")
    else:
        print("! цикл воспроизведения выглядит иначе — проверь руками")

    # 5) проверка синтаксиса до записи
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"СИНТАКСИС СЛОМАЛСЯ бы на строке {e.lineno}: {e.msg}")
        print("Файл не тронут.")
        sys.exit(1)

    open(path, "w", encoding="utf-8").write(src)

    print()
    for c in changes:
        print(f"  {c}")

    check = open(path, encoding="utf-8").read()
    print("\nпроверка:")
    for m in ("_stop_speaking = threading.Event()",
              "_stop_speaking.set()",
              "if _stop_speaking.is_set():",
              "_play_file(fn, interruptible)"):
        print(f"  {'OK ' if m in check else 'НЕТ'}  {m}")
    print("\nГотово. Запускай: python main.py")


if __name__ == "__main__":
    main()