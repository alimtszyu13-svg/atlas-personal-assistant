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
    _mod("core.memory", log_turn=lambda who, t: LOG.append(("log", who)))
    _mod("core.speaker_id", handle_command=lambda c, say: False, check_last=lambda: (True, None))
    _mod("core.study", active=lambda: False, stop=lambda: "стоп тренировки")
    _mod("fast_commands", try_fast_command=lambda c: "быстро" if c.startswith("открой") else None)
    _mod("hotkeys", start_push_to_talk_hotkey=lambda k: None)
    _mod("reminders", start_reminder_thread=lambda f: None)
    _mod("selection_hotkey", start_selection_hotkeys=lambda: None)
    _mod("ui_state", shared_state={"chat_history": [], "manual_queue": [], "intro_done": True})
    _mod("voice", speak=lambda t, interruptible=True: LOG.append(("speak", t)), listen=lambda: LISTEN.pop(0),
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
def voice_loop_stop_then_command_then_shutdown():
    reset()
    LISTEN[:] = ["стоп", "расскажи что-нибудь", "Атлас, выключись"]
    M._voice_loop()
    asks = [e for e in LOG if isinstance(e, tuple) and e[0] == "ask_ai"]
    assert asks == [("ask_ai", "(Respond in Russian.) расскажи что-нибудь")], asks
    assert M.shared_state["should_quit"] is True
    assert M.shared_state["chat_history"][-1][1] in M.SHUTDOWN_RESPONSES["ru"]


@test
def short_noise_is_ignored():
    reset()
    LISTEN[:] = ["ээ", "выключись"]
    M._voice_loop()
    assert not any(isinstance(e, tuple) and e[0] == "ask_ai" for e in LOG)


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
