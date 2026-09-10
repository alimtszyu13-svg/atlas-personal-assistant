from voice import speak, listen
from commands import route_command

speak("Атлас на связи.")

while True:
    command = listen()
    if command == "":
        continue
    if "стоп" in command.lower():
        speak("Выключаюсь.")
        break

    response = route_command(command)
    speak(response)