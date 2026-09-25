# onnxruntime должен загрузиться первым: его DLL конфликтуют,
# если раньше успели загрузиться WinRT (OCR) или .NET (pywebview)
from click import command
import onnxruntime  # noqa: F401
import threading
import time
from datetime import datetime
from ai_brain import ask_ai
from core.bus import bus
from fast_commands import try_fast_command
from reminders import start_reminder_thread
from web_gui import WebGUI
from window_hotkey import start_window_toggle_hotkey
from ui_state import shared_state
import random
import re
from selection_hotkey import start_selection_hotkeys
from hotkeys import start_push_to_talk_hotkey
from voice import speak, speak_cached, speak_streaming, listen, wait_for_wake_word, _push_to_talk_event, get_response_language
from database import init_db
init_db()

from file_search import build_index_background
build_index_background()   # индекс строится в фоне, не задерживая запуск

WAKE_RESPONSES = {
    "en": [
        "Yes, sir?", "Always ready, sir.", "What do you want, sir?",
        "At your service.", "Waiting for your command.", "I'm here, sir.",
        "I thought you are sleeping, sir.", "Ready when you are.",
        "Something happened, sir?",
    ],
    "ru": [
        "Да, сэр?", "Всегда готов, сэр.", "Что вам угодно, сэр?",
        "К вашим услугам.", "Жду вашей команды.", "Я здесь, сэр.",
        "Я думал, вы спите, сэр.", "Готов, как только скажете.",
        "Что-то случилось, сэр?",
    ],
}

GREETING_TIME = {
    "en": {
        "morning": ["Good morning, sir.", "Morning, sir.", "Up early, sir?"],
        "afternoon": ["Good afternoon, sir.", "Afternoon, sir."],
        "evening": ["Good evening, sir.", "Evening, sir."],
        "night": ["Working late, sir?", "Still up, sir?", "Burning the midnight oil, sir?"],
    },
    "ru": {
        "morning": ["Доброе утро, сэр.", "С добрым утром, сэр.", "Рано встали, сэр?"],
        "afternoon": ["Добрый день, сэр.", "Добрый день."],
        "evening": ["Добрый вечер, сэр.", "Вечер добрый, сэр."],
        "night": ["Что, опять допоздна, сэр?", "Ещё не спите, сэр?", "Полуночничаем, сэр?"],
    },
}

GREETING_TAIL = {
    "en": ["Atlas is online and ready.", "Atlas online.", "Systems up, ready when you are.", "Atlas here, all systems go."],
    "ru": ["Атлас на связи, готов к работе.", "Атлас в сети.", "Всё запущено, готов слушать.", "Атлас на месте."],
}


def _time_greeting() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 12:
        bucket = "morning"
    elif 12 <= hour < 18:
        bucket = "afternoon"
    elif 18 <= hour < 23:
        bucket = "evening"
    else:
        bucket = "night"
    lang = get_response_language()
    return random.choice(GREETING_TIME[lang][bucket])


def _speak_and_update(text: str, interruptible: bool = True) -> None:
    shared_state["state"] = "speaking"
    shared_state["text"] = text
    shared_state["chat_history"].append(("Atlas", text))
    spoken = re.sub(r"^\s*(?:[-*•]|#+|\d+\.)\s+", "", text, flags=re.M).replace("**", "")
    speak(spoken, interruptible=interruptible)
    shared_state["state"] = "idle"


def _process_command(command: str) -> None:
    import traceback
    print(f"[cmd] {command!r} ← {traceback.extract_stack()[-2].name}")
    shared_state["chat_history"].append(("You", command))
    shared_state["text"] = command

    # Быстрый путь: простая команда выполняется сразу, без LLM и без TTS
    fast = try_fast_command(command)
    if fast is not None:
        spoken = isinstance(fast, tuple)      # ("speak", текст) — результат озвучить
        if spoken:
            fast = fast[1]
        print(f"[FAST] {command} -> {fast}")







        from ai_brain import remember_exchange
        import tool_router
        remember_exchange(command, fast)
        tool_router.note_topic(command)
        if spoken:
            _speak_and_update(fast)           # сам добавит в чат и вернёт idle
        else:
            shared_state["chat_history"].append(("Atlas", fast))
            shared_state["state"] = "idle"
        shared_state["text"] = ""
        return

    shared_state["state"] = "thinking"

    # Явно подсказываем модели язык ответа на каждом ходу — не полагаемся
    # на то, что она "запомнит" переключение из истории диалога, особенно
    # если язык менялся через интерфейс, минуя саму беседу
    lang_hint = "(Respond in Russian.) " if get_response_language() == "ru" else "(Respond in English.) "
    from voice import SpeechStream
    speech = SpeechStream()
    response = ask_ai(lang_hint + command, speech=speech)
    shared_state["chat_history"].append(("Atlas", response))   # текст — сразу, речь догоняет
    speech.finish()
    if not speech.spoken_any:            # запасной путь (например, после rate limit)
        shared_state["state"] = "speaking"
        speak(response)
    shared_state["state"] = "idle"


def _manual_queue_watcher():
    while True:
        if shared_state["manual_queue"]:
            command = shared_state["manual_queue"].pop(0)
            _process_command(command)
        time.sleep(0.2)


MIN_COMMAND_LENGTH = 3  # отсекаем случайный шум/мусор вроде "." или "uh"
SHUTDOWN_RESPONSES = {
    "en": ["Shutting down.", "Signing off, sir.", "Going dark. See you soon, sir.", "Powering down now."],
    "ru": ["До свидания, сэр.", "Отключаюсь, сэр.", "Ухожу в тень. До скорого, сэр.", "Выключаюсь."],
}


def _wake_chime() -> None:
    """Короткий сигнал «слушаю»."""
    try:
        import winsound
        winsound.Beep(880, 90)
    except Exception:
        pass


def _cache_phrase(phrase: str) -> None:
    """Генерирует фразу в кеш, не проигрывая её."""
    import shutil
    from voice import _generate_any, _cache_path
    ext = ".mp3" if get_response_language() == "ru" else ".wav"
    tmp = f"temp_wake{ext}"
    try:
        _generate_any(phrase, tmp)
        shutil.move(tmp, _cache_path(phrase, ext))
    except Exception as e:
        print(f"[wake cache] {e}")


def _warm_wake_phrases() -> None:
    """При старте заранее кешируем отклики на имя, чтобы они играли мгновенно."""
    from voice import _find_cached
    for p in WAKE_RESPONSES[get_response_language()]:
        if not _find_cached(p):
            _cache_phrase(p)


def _wake_reply_async() -> None:
    """Отклик на имя голосом, но без ожидания: фраза играет, а микрофон уже слушает."""
    import pygame
    from voice import _find_cached
    phrase = random.choice(WAKE_RESPONSES[get_response_language()])
    path = _find_cached(phrase)
    if path:
        print(f"[Atlas]: {phrase}")
        pygame.mixer.Sound(path).play()           # отдельный канал, не блокирует
    else:
        _wake_chime()                             # фразы ещё нет в кеше — сигнал и кешируем
        threading.Thread(target=_cache_phrase, args=(phrase,), daemon=True).start()

def _voice_loop():
    start_reminder_thread(_speak_and_update)
    lang = get_response_language()
    _speak_and_update(f"{_time_greeting()} {random.choice(GREETING_TAIL[lang])}", interruptible=False)

    while True:
        _push_to_talk_event.clear()  # страхуемся от "призрачного" события,
                                       # унаследованного от системного хука клавиатуры
        shared_state["state"] = "idle"
        shared_state["text"] = ""

        print(f"[DEBUG] always_listening={shared_state.get('always_listening')}")
        follow_up = shared_state.pop("follow_up", False)
        if shared_state.get("always_listening"):
            trigger = "always"
        elif follow_up:
            trigger = "followup"          # Atlas задал вопрос / идёт игра — слушаем без имени
            print("(жду ответа без имени)")
        else:
            trigger = wait_for_wake_word()
        print(f"[DEBUG] trigger={trigger}")

        # Короткий отклик ("Yes, sir?") уместен только когда Atlas реально
        # услышал своё имя вслух. Push-to-talk и always-listening — уже
        # осознанные действия пользователя, лишняя реплика тут была бы
        # той самой "повторяющейся" болтовнёй, которая надоедала.
        from voice import wake_has_command
        if trigger == "voice" and not wake_has_command():
            from voice import output_is_headphones
            if output_is_headphones():
                _wake_reply_async()          # наушники: говорим и сразу слушаем
            else:
                lang = get_response_language()
                speak_cached(random.choice(WAKE_RESPONSES[lang]))   # колонки: сначала договорить
            
        shared_state["state"] = "listening"
        command = listen()

        if len(command.strip()) < MIN_COMMAND_LENGTH and not re.search(r"\d", command):
            continue  # мусорное/пустое распознавание — не тратим вызов ask_ai

        SHUTDOWN_PHRASES = {
            "выключись", "отключись", "выключайся", "выключи себя",
            "shut down", "turn off", "power off", "turn yourself off",
        }
        cmd = command.lower().strip(" .!?,")
        cmd = re.sub(r"^(?:atlas|атлас)[,\s]+", "", cmd)
        cmd = re.sub(r"[,\s]+(?:please|пожалуйста)$", "", cmd).strip()
        if cmd in {"stop", "стоп", "хватит", "cancel", "отмена", "enough"}:
            continue          # прерывать нечего — не тратим запрос к модели
        if cmd in SHUTDOWN_PHRASES:
            shutdown_msg = random.choice(SHUTDOWN_RESPONSES[get_response_language()])
            _speak_and_update(shutdown_msg)
            shared_state["should_quit"] = True
            break
        _process_command(command)

        # Режим продолжения: вопрос в конце ответа или активная игра
        from skills.fun import game_active
        last = shared_state["chat_history"][-1][1] if shared_state["chat_history"] else ""
        shared_state["follow_up"] = str(last).rstrip().endswith("?") or game_active()
        
start_push_to_talk_hotkey("f9")
import keyboard
from ai_brain import cancel_current_task
keyboard.add_hotkey("f8", cancel_current_task)
print("(cancel hotkey active: f8)")
start_selection_hotkeys()


from voice import output_device_name, output_is_headphones
print(f"[audio] вывод: {output_device_name()} → "
      f"{'наушники' if output_is_headphones() else 'колонки'}")
threading.Thread(target=_warm_wake_phrases, daemon=True).start()

voice_thread = threading.Thread(target=_voice_loop, daemon=True)
voice_thread.start()

manual_thread = threading.Thread(target=_manual_queue_watcher, daemon=True)
manual_thread.start()

gui = WebGUI(shared_state)
start_window_toggle_hotkey(gui, "f10")
gui.run()