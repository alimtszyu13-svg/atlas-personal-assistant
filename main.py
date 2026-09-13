from voice import speak, listen
from ai_brain import ask_ai
from reminders import start_reminder_thread

start_reminder_thread(speak)

speak("Atlas online.")

while True:
    command = listen()
    if command == "":
        continue
    if "stop" in command.lower():
        speak("Shutting down.")
        break

    response = ask_ai(command)
    speak(response)