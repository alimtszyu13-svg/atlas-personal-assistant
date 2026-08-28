from voice import speak, listen
from commands import route_command

speak("Добрый день, сэр! Чем могу помочь?")

while True:
    command = listen()
    if command == "":
        continue
    if "стоп" in command.lower():
        speak("До свидания!")
        break

    response = route_command(command)
    speak(response)