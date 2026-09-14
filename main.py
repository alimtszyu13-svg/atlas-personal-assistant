import threading
import time
from datetime import datetime
from voice import speak, listen
from ai_brain import ask_ai
from reminders import start_reminder_thread
from web_gui import WebGUI
from ui_state import shared_state


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


def _process_command(command: str) -> None:
    shared_state["chat_history"].append(("You", command))
    shared_state["state"] = "thinking"
    shared_state["text"] = command
    response = ask_ai(command)
    _speak_and_update(response)


def _manual_queue_watcher():
    while True:
        if shared_state["manual_queue"]:
            command = shared_state["manual_queue"].pop(0)
            _process_command(command)
        time.sleep(0.2)


def _voice_loop():
    start_reminder_thread(_speak_and_update)
    _speak_and_update(f"{_time_greeting()} Atlas is online and ready.", interruptible=False)

    while True:
        shared_state["state"] = "listening"
        shared_state["text"] = ""
        command = listen()

        if command == "":
            continue
        if "stop" in command.lower():
            _speak_and_update("Shutting down.")
            shared_state["should_quit"] = True
            break

        _process_command(command)


voice_thread = threading.Thread(target=_voice_loop, daemon=True)
voice_thread.start()

manual_thread = threading.Thread(target=_manual_queue_watcher, daemon=True)
manual_thread.start()

gui = WebGUI(shared_state)
gui.run()