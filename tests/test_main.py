"""
Тесты main.py: обработка команды (быстрый путь / мозг), голосовой цикл со «стоп» и «выключись»,
команды из чата. Всё вокруг заменено подставными модулями — окно, микрофон и модели не нужны.

    python tests/test_main.py
"""
import os
import sys
import traceback
import types

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LOG = []


def _mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


class Speech:
    def __init__(self):
        self.spoken_any = False

    def feed(self, d):
        self.spoken_any = True

    def finish(self):
        LOG.append("speech.finish")


def install():
    _mod("onnxruntime")
    _mod("ai_brain", ask_ai=lambda q, speech=None: (LOG.append(("ask_ai", q)), "ответ мозга")[1],
         cancel_current_task=lambda: None, remember_exchange=lambda u, a: LOG.append(("remember", u, a)),
         set_announcer=lambda f: None)
    core = sys.modules.get("core") or _mod("core")
    core.__path__ = [os.path.join(ROOT, "core")]
    _mod("core.bus", bus=None)
    _mod("core.logbook", start=lambda: "logs/atlas.log")
    _mod("core.memory", log_turn=lambda who, t: LOG.append(("log", who)))
    _mod("core.speaker_id", handle_command=lambda c, say: False, check_last=lambda: (True, None))
    _mod("core.study", active=lambda: False, stop=lambda: "стоп тренировки")
    _mod("fast_commands", try_fast_command=lambda c: "быстро" if c.startswith("открой") else None)
    _mod("hotkeys", start_push_to_talk_hotkey=lambda k: None)
    _mod("reminders", start_reminder_thread=lambda f: None)
    _mod("selection_hotkey", start_selection_hotkeys=lambda: None)
    _mod("ui_state", shared_state={"chat_history": [], "manual_queue": [], "intro_done": True})
    _mod("voice", speak=lambda t, interruptible=True: LOG.append(("speak", t)), listen=lambda **kw: LISTEN.pop(0),
         wait_for_wake_word=lambda: "voice", _push_to_talk_event=types.SimpleNamespace(clear=lambda: None),
         get_response_language=lambda: "ru", SpeechStream=Speech)
    _mod("web_gui", WebGUI=object)
    _mod("window_hotkey", start_window_toggle_hotkey=lambda g, k: None)
    _mod("database", init_db=lambda: None)
    _mod("file_search", build_index_background=lambda: None)
    _mod("tool_router", note_topic=lambda c: None)
    _mod("skills").__path__ = []
    _mod("skills.fun", game_active=lambda: False)


LISTEN = []
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def reset():
    LOG.clear()
    M.shared_state.update(chat_history=[], manual_queue=[], intro_done=True, should_quit=False)


@test
def fast_path_shows_result_without_brain():
    reset()
    M._process_command("открой блокнот")
    assert ("remember", "открой блокнот", "быстро") in LOG and not any(e[0] == "ask_ai" for e in LOG if isinstance(e, tuple))
    assert M.shared_state["chat_history"][-1] == ("Atlas", "быстро") and M.shared_state["state"] == "idle"


@test
def brain_gets_language_hint_and_fallback_voice():
    reset()
    M._process_command("какая сегодня погода")
    assert ("ask_ai", "(Respond in Russian.) какая сегодня погода") in LOG
    assert ("speak", "ответ мозга") in LOG, "речь не прозвучала потоком — должна прозвучать целиком"


@test
def emotion_tags_go_to_voice_not_to_chat():
    reset()
    saved = M.ask_ai
    M.ask_ai = lambda q, speech=None: "[warm] Отличный выбор, сэр. [amused] Классика."
    try:
        M._process_command("поставь что-нибудь хорошее")
    finally:
        M.ask_ai = saved
    assert M.shared_state["chat_history"][-1] == ("Atlas", "Отличный выбор, сэр. Классика."), M.shared_state["chat_history"]
    assert ("speak", "[warm] Отличный выбор, сэр. [amused] Классика.") in LOG, "голосу метки нужны"
    M._speak_and_update("[calm] Готово.")
    assert M.shared_state["chat_history"][-1] == ("Atlas", "Готово.") and M.shared_state["text"] == "Готово."


@test
def voice_loop_stop_then_command_then_shutdown():
    reset()
    LISTEN[:] = ["стоп", "расскажи что-нибудь", "Атлас, выключись"]
    M._voice_loop()
    asks = [e for e in LOG if isinstance(e, tuple) and e[0] == "ask_ai"]
    assert asks == [("ask_ai", "(Respond in Russian.) расскажи что-нибудь")], asks
    assert M.shared_state["should_quit"] is True
    assert M.shared_state["chat_history"][-1][1] in M.SHUTDOWN_RESPONSES["ru"]


@test
def pc_does_not_wake_while_the_phone_is_talking():
    import time as _t
    reset()
    calls = {"n": 0}
    saved = M.wait_for_wake_word

    def wake():
        calls["n"] += 1
        if calls["n"] == 2:
            M.shared_state["phone_active_until"] = 0       # разговор с телефона закончился
        return "voice"
    M.wait_for_wake_word = wake
    M.shared_state["phone_active_until"] = _t.time() + 30
    LISTEN[:] = ["выключись"]
    try:
        M._voice_loop()
    finally:
        M.wait_for_wake_word = saved
        M.shared_state["phone_active_until"] = 0
    assert calls["n"] == 2 and M.shared_state["should_quit"], "первое «Атлас» во время разговора с телефона пропущено"


@test
def an_error_in_one_conversation_does_not_stop_listening():
    reset()
    calls = {"n": 0}
    saved_process, saved_speak = M._process_command, M.speak

    def boom(cmd):
        calls["n"] += 1
        raise FileNotFoundError("temp_speech.wav")          # как в живом логе

    def speak_boom(text, *a, **k):
        if any(text.endswith(t) for t in M.GREETING_TAIL["ru"]):   # ломается только приветствие, как в логе
            raise PermissionError("temp_speech.wav")
        return saved_speak(text, *a, **k)
    M._process_command, M.speak = boom, speak_boom
    LISTEN[:] = ["какая погода", "выключись"]
    try:
        M._voice_loop()                                       # раньше падал уже на приветствии
    finally:
        M._process_command, M.speak = saved_process, saved_speak
    assert calls["n"] == 1 and M.shared_state["should_quit"], "после ошибки Atlas слушал дальше и выключился по команде"


@test
def listening_starts_right_away_while_greeting_plays():
    import threading
    import time as _t
    reset()
    M.shared_state["intro_done"] = False                     # заставка ещё идёт — приветствие ждёт её
    playing, hushed = threading.Event(), []
    saved_speak, saved_wake = M.speak, M.wait_for_wake_word

    def slow_greeting(text, *a, **k):
        if any(text.endswith(t) for t in M.GREETING_TAIL["ru"]):
            playing.set()
            for _ in range(40):                                # «говорит» до 2 с, пока не оборвут
                if hushed:
                    return
                _t.sleep(0.05)
            return
        return saved_speak(text, *a, **k)
    sys.modules["voice"].hush = lambda: hushed.append(1)
    woke = []
    M.speak = slow_greeting
    M.wait_for_wake_word = lambda: (woke.append(_t.time()), "voice")[1]
    LISTEN[:] = ["выключись"]
    t0 = _t.time()
    threading.Timer(0.3, lambda: M.shared_state.update(intro_done=True)).start()
    try:
        M._voice_loop()
    finally:
        M.speak, M.wait_for_wake_word = saved_speak, saved_wake
        del sys.modules["voice"].hush
    assert woke and woke[0] - t0 < 0.2, "слушает «Атлас» сразу, не дожидаясь заставки и приветствия"
    assert M.shared_state["chat_history"][0][1].endswith(tuple(M.GREETING_TAIL["ru"])), "приветствие видно в чате"
    for _ in range(40):
        if hushed or not M._greeting["thread"].is_alive():
            break
        _t.sleep(0.05)
    assert not M._greeting["thread"].is_alive() or hushed


@test
def short_noise_is_ignored():
    reset()
    LISTEN[:] = ["ээ", "выключись"]
    M._voice_loop()
    assert not any(isinstance(e, tuple) and e[0] == "ask_ai" for e in LOG)


@test
def unclear_phrase_is_asked_again_not_searched():
    reset()
    v = sys.modules["voice"]
    unclear = ["Официанты."]
    v.last_was_unclear = lambda: LISTEN_LAST[0] in unclear
    saved_listen = v.listen

    def listen():
        LISTEN_LAST[0] = LISTEN.pop(0)
        return LISTEN_LAST[0]
    M.listen = listen
    LISTEN[:] = ["Официанты.", "выключись"]
    try:
        M._voice_loop()
    finally:
        M.listen = saved_listen
        del v.last_was_unclear
    assert not any(isinstance(e, tuple) and e[0] == "ask_ai" for e in LOG), "мозг не искал официантов"
    asked = [e[1] for e in LOG if isinstance(e, tuple) and e[0] == "speak"]
    assert any("повторите" in t.lower() or "ещё раз" in t.lower() for t in asked), asked


LISTEN_LAST = [""]


@test
def dictation_types_without_the_name_until_enough():
    reset()
    from core import dictation
    typed = []
    io = types.SimpleNamespace(foreground=lambda: 42, is_atlas=lambda h: False, title_of=lambda h: "Telegram",
                               focus=lambda h: True, paste=typed.append, keys=lambda c, n=1: typed.append(c))
    dictation._s["io"] = io
    LISTEN[:] = ["Пиши за мной", "Привет, как дела?", "Хватит", "выключись"]
    try:
        M._voice_loop()
    finally:
        dictation._s["io"] = None
    assert typed == ["Привет, как дела?"], typed
    assert not any(isinstance(e, tuple) and e[0] == "ask_ai" for e in LOG), "диктовка не уходит в мозг"
    said = [e[1] for e in LOG if isinstance(e, tuple) and e[0] == "speak"]
    assert any("Пишу в «Telegram»" in t for t in said) and any("Диктовка закончена" in t for t in said), said


@test
def recording_by_voice_starts_and_stops_without_the_brain():
    reset()
    from core import meeting_notes as Mn
    calls, state = [], {"on": False}
    saved = Mn.start_recording, Mn.stop_recording, Mn.active, Mn.last_summary
    Mn.start_recording = lambda title, mic, announce=None, with_screen=None: (
        calls.append(("start", title, mic, with_screen)), state.update(on=True), "Recording started")[2]
    Mn.stop_recording = lambda: (calls.append(("stop",)), state.update(on=False), "saved")[2]
    Mn.active = lambda: state["on"]
    Mn.last_summary = lambda: "обсудили сроки"
    opened = []
    sys.modules["web_gui"].open_records_window = lambda: opened.append(1)
    LISTEN[:] = ["Атлас, запиши созвон с Машей", "Прекрати запись", "выключись"]
    try:
        M._voice_loop()
    finally:
        Mn.start_recording, Mn.stop_recording, Mn.active, Mn.last_summary = saved
        del sys.modules["web_gui"].open_records_window
    assert calls == [("start", "Созвон с Машей", True, False), ("stop",)], calls
    assert not any(isinstance(e, tuple) and e[0] == "ask_ai" for e in LOG), "мозг не нужен"
    said = [e[1] for e in LOG if isinstance(e, tuple) and e[0] == "speak"]
    assert any(t.startswith("Записываю") for t in said) and any("обсудили сроки" in t for t in said), said
    assert opened, "после конспекта открывается окно записей"


@test
def chat_shutdown_works_like_voice():
    reset()
    M.shared_state["manual_queue"].append("Выключись.")
    import threading
    threading.Thread(target=M._manual_queue_watcher, daemon=True).start()
    import time
    for _ in range(30):
        if M.shared_state.get("should_quit"):
            break
        time.sleep(0.05)
    assert M.shared_state["should_quit"] and M.shared_state["chat_history"][0] == ("You", "Выключись.")


def main():
    global M
    install()
    import importlib.util
    spec = importlib.util.spec_from_file_location("atlas_main", os.path.join(ROOT, "main.py"))
    M = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(M)                 # при импорте ничего не запускается — запуск только в main()
    ok = 0
    for t in TESTS:
        try:
            t()
            ok += 1
            print(f"  ✓ {t.__name__}")
        except Exception:
            print(f"  ✗ {t.__name__}\n" + "".join("      " + ln for ln in traceback.format_exc().splitlines(True)[-5:]))
    print(f"\nТестов пройдено: {ok} из {len(TESTS)}")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if ok == len(TESTS) else 1)


if __name__ == "__main__":
    main()
