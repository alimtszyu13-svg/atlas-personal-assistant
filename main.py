import threading
import time
from datetime import datetime
from voice import speak, listen, wait_for_wake_word
from ai_brain import ask_ai
from reminders import start_reminder_thread
from web_gui import WebGUI
from window_hotkey import start_window_toggle_hotkey
from ui_state import shared_state
import random
from selection_hotkey import start_selection_hotkeys
from hotkeys import start_push_to_talk_hotkey
from voice import speak, listen, wait_for_wake_word, _push_to_talk_event

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

def _time_greeting() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "Good morning, sir."
    elif 12 <= hour < 18:
        return "Good afternoon, sir."
    elif 18 <= hour < 23:
        return "Good evening, sir."
    else:
        return "Working late, sir?"


def _speak_and_update(text: str, interruptible: bool = True) -> None:
    shared_state["state"] = "speaking"
    shared_state["text"] = text
    shared_state["chat_history"].append(("Atlas", text))
    speak(text, interruptible=interruptible)
    shared_state["state"] = "idle"


from voice import get_response_language

def _process_command(command: str) -> None:
    shared_state["chat_history"].append(("You", command))
    shared_state["state"] = "thinking"
    shared_state["text"] = command

    # Явно подсказываем модели язык ответа на каждом ходу — не полагаемся
    # на то, что она "запомнит" переключение из истории диалога, особенно
    # если язык менялся через интерфейс, минуя саму беседу
    lang_hint = "(Respond in Russian.) " if get_response_language() == "ru" else "(Respond in English.) "
    response = ask_ai(lang_hint + command)
    _speak_and_update(response)


def _manual_queue_watcher():
    while True:
        if shared_state["manual_queue"]:
            command = shared_state["manual_queue"].pop(0)
            _process_command(command)
        time.sleep(0.2)


MIN_COMMAND_LENGTH = 3  # отсекаем случайный шум/мусор вроде "." или "uh"

def _voice_loop():
    start_reminder_thread(_speak_and_update)
    _speak_and_update(f"{_time_greeting()} Atlas is online and ready.", interruptible=False)

    while True:
        _push_to_talk_event.clear()  # страхуемся от "призрачного" события,
                                       # унаследованного от системного хука клавиатуры
        shared_state["state"] = "idle"
        shared_state["text"] = ""

        print(f"[DEBUG] always_listening={shared_state.get('always_listening')}")
        if shared_state.get("always_listening"):
            trigger = "always"
        else:
            trigger = wait_for_wake_word()
        print(f"[DEBUG] trigger={trigger}")

        # Короткий отклик ("Yes, sir?") уместен только когда Atlas реально
        # услышал своё имя вслух. Push-to-talk и always-listening — уже
        # осознанные действия пользователя, лишняя реплика тут была бы
        # той самой "повторяющейся" болтовнёй, которая надоедала.
        if trigger == "voice":
            lang = get_response_language()
            _speak_and_update(random.choice(WAKE_RESPONSES[lang]), interruptible=False)

        shared_state["state"] = "listening"
        command = listen()

        if len(command.strip()) < MIN_COMMAND_LENGTH:
            continue  # мусорное/пустое распознавание — не тратим вызов ask_ai

        STOP_WORDS = ("stop", "стоп", "выключись")
        if any(word in command.lower() for word in STOP_WORDS):
            shutdown_msg = "До свидания, сэр." if get_response_language() == "ru" else "Shutting down."
            _speak_and_update(shutdown_msg)
            shared_state["should_quit"] = True
            break
        _process_command(command)
        
start_push_to_talk_hotkey("f9")
start_selection_hotkeys()

voice_thread = threading.Thread(target=_voice_loop, daemon=True)
voice_thread.start()

manual_thread = threading.Thread(target=_manual_queue_watcher, daemon=True)
manual_thread.start()

gui = WebGUI(shared_state)
start_window_toggle_hotkey(gui, "f10")
gui.run()