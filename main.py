from voice import speak, listen

speak("Атлас на связи. Слушаю тебя.")

while True:
    command = listen()
    if command == "":
        continue
    if "стоп" in command.lower():
        speak("Выключаюсь.")
        break
    speak(f"Ты сказал: {command}")