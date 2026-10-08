"""
Atlas — точка входа.

    фразы          приветствия, отклики, прощания
    речь           _speak_and_update — сказать вслух и показать в интерфейсе
    команды        _process_command — быстрый путь → мозг (ai_brain.ask_ai)
    голосовой цикл ждём «Атлас» → слушаем → стоп / выключись / команда
    запуск         main() — службы в нужном порядке, затем окно интерфейса
"""
# onnxruntime должен загрузиться первым: его DLL конфликтуют,
# если раньше успели загрузиться WinRT (OCR) или .NET (pywebview)
import onnxruntime  # noqa: F401
import time as _time

_T0 = _time.time()
from core import logbook  # noqa: E402

print(f"[журнал] {logbook.start()}")
import random
import re
import threading
import time
from datetime import datetime

from ai_brain import ask_ai, cancel_current_task, remember_exchange
from core.bus import bus  # noqa: F401  (шина событий — подключается при импорте)
from core.control_words import is_shutdown, is_stop
from core import emotions as _emo                    # метки эмоций — только в голос, не в чат
from fast_commands import try_fast_command
from hotkeys import start_push_to_talk_hotkey
from reminders import start_reminder_thread
from selection_hotkey import start_selection_hotkeys
from ui_state import shared_state
from voice import speak, listen, wait_for_wake_word, _push_to_talk_event, get_response_language
from web_gui import WebGUI
from window_hotkey import start_window_toggle_hotkey
from database import init_db

init_db()
from file_search import build_index_background  # noqa: E402

build_index_background()   # индекс строится в фоне, не задерживая запуск
from core import memory  # noqa: E402

print(f"[запуск] модули загружены за {_time.time() - _T0:.1f} с")
MIN_COMMAND_LENGTH = 3     # отсекаем случайный шум вроде "." или "uh"

# =============================================================================
# Фразы
# =============================================================================
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
SHUTDOWN_RESPONSES = {
    "en": ["Shutting down.", "Signing off, sir.", "Going dark. See you soon, sir.", "Powering down now."],
    "ru": ["До свидания, сэр.", "Отключаюсь, сэр.", "Ухожу в тень. До скорого, сэр.", "Выключаюсь."],
}


def _time_greeting() -> str:
    hour = datetime.now().hour
    bucket = ("morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 18
              else "evening" if 18 <= hour < 23 else "night")
    return random.choice(GREETING_TIME[get_response_language()][bucket])


# =============================================================================
# Речь
# =============================================================================
def _speak_and_update(text: str, interruptible: bool = True) -> None:
    """Сказать вслух и показать в интерфейсе."""
    shared_state["state"] = "speaking"
    shared_state["text"] = _emo.strip(text)
    shared_state["chat_history"].append(("Atlas", _emo.strip(text)))
    spoken = re.sub(r"^\s*(?:[-*•]|#+|\d+\.)\s+", "", text, flags=re.M).replace("**", "")
    speak(spoken, interruptible=interruptible)
    shared_state["state"] = "idle"


def _announce(text: str) -> None:
    """Для фоновых служб: сказать, не прерываясь на голос пользователя."""
    _speak_and_update(text, interruptible=False)


# =============================================================================
# Команды
# =============================================================================
def _process_command(command: str) -> None:
    print(f"[cmd] {command!r}")
    shared_state["chat_history"].append(("You", command))
    memory.log_turn("user", command)
    shared_state["text"] = command

    from core import speaker_id                  # «запомни мой голос», «отвечай только мне / всем»
    if speaker_id.handle_command(command, _announce):
        shared_state["state"] = "idle"
        shared_state["text"] = ""
        return

    # Быстрый путь: простая команда выполняется сразу, без модели и без голоса
    fast = try_fast_command(command)
    if fast is not None:
        spoken = isinstance(fast, tuple)          # ("speak", текст) — результат озвучить
        if spoken:
            fast = fast[1]
        print(f"[FAST] {command} -> {fast}")
        memory.log_turn("assistant", fast)
        import tool_router
        remember_exchange(command, fast)
        tool_router.note_topic(command)
        if spoken:
            _speak_and_update(fast)               # сам добавит в чат и вернёт idle
        else:
            shared_state["chat_history"].append(("Atlas", fast))
            shared_state["state"] = "idle"
        shared_state["text"] = ""
        return

    # Мозг. Язык ответа подсказываем на каждом ходу — не полагаемся на то, что модель
    # «запомнит» переключение (язык мог смениться через интерфейс, минуя разговор)
    shared_state["state"] = "thinking"
    lang_hint = "(Respond in Russian.) " if get_response_language() == "ru" else "(Respond in English.) "
    from voice import SpeechStream
    speech = SpeechStream()
    response = ask_ai(lang_hint + command, speech=speech)
    shown = _emo.strip(response)                  # метки эмоций нужны голосу, а не чату
    memory.log_turn("assistant", shown)
    shared_state["chat_history"].append(("Atlas", shown))      # текст — сразу, речь догоняет
    speech.finish()
    if not speech.spoken_any:                     # запасной путь (например, после лимита)
        shared_state["state"] = "speaking"
        speak(response)
    shared_state["state"] = "idle"


def _shutdown() -> None:
    _speak_and_update(random.choice(SHUTDOWN_RESPONSES[get_response_language()]))
    shared_state["should_quit"] = True


def _manual_queue_watcher() -> None:
    """Команды, набранные в чате интерфейса."""
    while True:
        if shared_state["manual_queue"]:
            command = shared_state["manual_queue"].pop(0)
            if is_shutdown(command):              # «выключись» в чате — как голосом
                shared_state["chat_history"].append(("You", command))
                _shutdown()
                continue
            _process_command(command)
        time.sleep(0.2)


# =============================================================================
# Голосовой цикл
# =============================================================================
def _voice_loop() -> None:
    start_reminder_thread(_speak_and_update)
    # приветствие — в момент вспышки звезды в заставке (не дольше 15 с ожидания)
    t0 = time.time()
    while not shared_state.get("intro_done") and time.time() - t0 < 15:
        time.sleep(0.1)
    lang = get_response_language()
    print(f"[запуск] Atlas готов за {time.time() - _T0:.1f} с")
    try:
        _speak_and_update(f"{_time_greeting()} {random.choice(GREETING_TAIL[lang])}", interruptible=False)
    except Exception as e:
        print(f"[голос] приветствие не прозвучало: {e}")

    while True:
        try:
            if _voice_turn() == "quit":
                break
        except Exception as e:                    # раньше любая ошибка здесь навсегда останавливала прослушивание
            import traceback
            print(f"[голос] ошибка в разговоре: {e!r} — слушаю дальше")
            traceback.print_exc()
            time.sleep(1)


def _voice_turn():
    """Один разговор: ждём имя → слушаем → выполняем. → "quit", если Atlas выключают."""
    if True:
        _push_to_talk_event.clear()               # «призрачное» нажатие от системного хука клавиатуры
        shared_state["state"] = "idle"
        shared_state["text"] = ""

        print(f"[DEBUG] always_listening={shared_state.get('always_listening')}")
        follow_up = shared_state.pop("follow_up", False)
        if shared_state.get("always_listening"):
            trigger = "always"
        elif follow_up:
            trigger = "followup"                  # Atlas задал вопрос / идёт игра — слушаем без имени
            print("(жду ответа без имени)")
        else:
            trigger = wait_for_wake_word()
        print(f"[DEBUG] trigger={trigger}")
        if trigger == "voice" and time.time() < shared_state.get("phone_active_until", 0):
            print("[голос] идёт разговор с телефона — компьютер не перебивает")
            return

        shared_state["state"] = "listening"
        command = listen()
        if len(command.strip()) < MIN_COMMAND_LENGTH and not re.search(r"\d", command):
            return                              # пустое распознавание — не тратим запрос к модели

        if trigger != "manual":                   # F9 и текст не проверяем — это запасной путь
            from core import speaker_id
            ok, score = speaker_id.check_last()
            if score is not None:
                print(f"[голос] сходство с вашим голосом: {score:.2f}")
            if not ok:
                print("[голос] это не ваш голос — команда пропущена")
                shared_state["text"] = ""
                return

        if is_stop(command):
            from core import study                # «хватит» во время тренировки — закончить её
            if study.active():
                _speak_and_update(study.stop(), interruptible=False)
            return                              # прерывать нечего — не тратим запрос к модели
        if is_shutdown(command):
            _shutdown()
            return "quit"
        _process_command(command)

        # Режим продолжения: вопрос в конце ответа или активная игра
        from skills.fun import game_active
        last = shared_state["chat_history"][-1][1] if shared_state["chat_history"] else ""
        shared_state["follow_up"] = str(last).rstrip().endswith("?") or game_active()


# =============================================================================
# Запуск
# =============================================================================
def main() -> None:
    import keyboard
    import ai_brain
    from voice import output_device_name, output_is_headphones
    from core import proactive, missions, healer, routines, skill_forge, day_report, gestures

    start_push_to_talk_hotkey("f9")
    keyboard.add_hotkey("f8", cancel_current_task)
    print("(cancel hotkey active: f8)")
    start_selection_hotkeys()
    print(f"[audio] вывод: {output_device_name()} → {'наушники' if output_is_headphones() else 'колонки'}")

    memory.start_sleep_cycle()
    proactive.start(_announce)
    missions.init(_announce)
    healer.start(_announce)                       # самолечение
    routines.start(_announce)                     # ритуалы и привычки
    skill_forge.start(_announce)                  # мастерская навыков
    day_report.start()                            # учёт времени для итогов
    gestures.start()                              # хлопки (жесты — в интерфейсе)
    ai_brain.set_announcer(_announce)             # итоги фоновых задач — вслух
    ai_brain.features.start_memory_autoreview()   # раз в 3 дня — проверка памяти, удаление только после «да»
    try:
        from core import cloud_sync                 # одна память на все устройства (если настроен Supabase)
        cloud_sync.start()
    except Exception as e:
        print(f"[облако] синхронизация не запустилась: {e}")
    try:
        from phone import server as _phone        # телефон уже сопряжён — приложение на нём просто работает
        _phone.start_if_paired()
    except Exception as e:
        print(f"[телефон] не запустился: {e}")
    try:
        from core import pc_link                    # телефон через облако управляет компьютером
        pc_link.start_from_env()
    except Exception as e:
        print(f"[руки] связь с облаком не запустилась: {e}")

    threading.Thread(target=_voice_loop, daemon=True).start()
    threading.Thread(target=_manual_queue_watcher, daemon=True).start()

    gui = WebGUI(shared_state)
    start_window_toggle_hotkey(gui, "f10")
    gui.run()


if __name__ == "__main__":
    main()
