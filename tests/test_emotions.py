"""
Тесты эмоций в голосе: метки уходят только в Fish Audio, а все остальные голоса, субтитры,
чат и журнал получают чистый текст.

    python tests/test_emotions.py

Сами голоса подставные (ничего не синтезируется, интернет не нужен) — проверяется, какой
текст получает каждый из них.
"""
import asyncio
import os
import queue
import sys
import tempfile
import traceback
import types

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
GOT = {}
TAGGED = "[sympathetic] Тяжёлый день, сэр. [warm] Поставлю что-нибудь мягкое."
CLEAN = "Тяжёлый день, сэр. Поставлю что-нибудь мягкое."


def _mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


class _Communicate:
    def __init__(self, text, voice):
        GOT["edge"] = text

    async def save(self, filename):
        open(filename, "wb").close()


def install():
    _mod("sounddevice", RawInputStream=object, query_devices=lambda *a, **k: [], default=types.SimpleNamespace(device=(0, 0)))
    music = types.SimpleNamespace(load=lambda *a: None, play=lambda *a: None, get_busy=lambda: False,
                                  unload=lambda: None, stop=lambda: None)
    _mod("pygame", mixer=types.SimpleNamespace(init=lambda *a, **k: None, music=music, Sound=lambda p: None,
                                               quit=lambda: None),
         time=types.SimpleNamespace(wait=lambda ms: None))
    _mod("groq", Groq=lambda **k: types.SimpleNamespace())
    _mod("dotenv", load_dotenv=lambda *a, **k: None)
    _mod("edge_tts", Communicate=_Communicate)
    _mod("elevenlabs")
    _mod("elevenlabs.client", ElevenLabs=lambda **k: types.SimpleNamespace())
    _mod("ui_state", shared_state={})
    _mod("soundfile", write=lambda fn, samples, sr: GOT.__setitem__("kokoro_written", True))

    class Resp:
        status_code, content, text = 200, b"audio", ""
    _mod("httpx", post=lambda url, headers=None, json=None, timeout=None: (GOT.__setitem__("fish", json["text"]), Resp())[1])


def load_voice():
    import importlib.util
    spec = importlib.util.spec_from_file_location("voice", os.path.join(ROOT, "voice.py"))
    v = importlib.util.module_from_spec(spec)
    sys.modules["voice"] = v
    spec.loader.exec_module(v)
    v.FISH_API_KEY = "test"
    v.elevenlabs_client = types.SimpleNamespace(text_to_speech=types.SimpleNamespace(
        convert=lambda **kw: (GOT.__setitem__("elevenlabs", kw["text"]), [b"x"])[1]))
    v._silero_model = types.SimpleNamespace(save_wav=lambda **kw: GOT.__setitem__("silero", kw["text"]))
    v._kokoro = types.SimpleNamespace(create=lambda text, **kw: (GOT.__setitem__("kokoro", text), ([], 24000))[1])

    class _Speech:
        @staticmethod
        def create(**kw):
            GOT["groq"] = kw["input"]
            return types.SimpleNamespace(write_to_file=lambda fn: None)
    v.groq_client = types.SimpleNamespace(audio=types.SimpleNamespace(speech=_Speech))
    return v


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def tmp(ext=".mp3"):
    return os.path.join(tempfile.mkdtemp(), "t" + ext)


@test
def tags_module_strip_and_fish_versions():
    from core import emotions as e
    assert e.strip(TAGGED) == CLEAN
    assert e.strip("[мгновенно] и [1] — не метки") == "[мгновенно] и [1] — не метки"
    assert e.for_fish(TAGGED, "s2.1-pro-free") == TAGGED
    assert e.for_fish(TAGGED, "s1") == "(empathetic) Тяжёлый день, сэр. (soft tone) Поставлю что-нибудь мягкое."
    os.environ["VOICE_EMOTIONS"] = "off"
    try:
        assert e.for_fish(TAGGED, "s2.1-pro-free") == CLEAN
    finally:
        os.environ.pop("VOICE_EMOTIONS", None)


@test
def fish_hears_the_emotions():
    GOT.clear()
    V.FISH_MODEL = "s2.1-pro-free"
    V._generate_speech_fish(TAGGED, tmp(), "voice-id")
    assert GOT["fish"] == TAGGED, GOT
    V.FISH_MODEL = "s1"
    V._generate_speech_fish(TAGGED, tmp(), "voice-id")
    assert GOT["fish"].startswith("(empathetic)"), GOT
    V.FISH_MODEL = "s2.1-pro-free"


@test
def russian_voices_never_read_tags_aloud():
    GOT.clear()
    V._generate_speech_elevenlabs(TAGGED, tmp())
    V._generate_speech_silero(TAGGED, tmp(".wav"))
    V._response_language["lang"] = "ru"
    V._fish_choice["ru"] = None
    V._generate_ru_primary = lambda t, f: (_ for _ in ()).throw(RuntimeError("нет квоты"))
    V._generate_speech_silero = lambda t, f: (_ for _ in ()).throw(RuntimeError("нет silero"))
    V._generate_any_impl(TAGGED, tmp())                  # каскад дошёл до Edge
    assert GOT["elevenlabs"] == CLEAN and GOT["silero"] == CLEAN and GOT["edge"] == CLEAN, GOT


@test
def english_voices_never_read_tags_aloud():
    GOT.clear()
    V._response_language["lang"] = "en"
    V._fish_choice["en"] = None
    V._en_engine["name"] = "groq"
    V._groq_tts_until["t"] = 0
    V._generate_speech(TAGGED, tmp(".wav"))
    V._generate_speech_kokoro(TAGGED, tmp(".wav"))
    V._generate_speech_fallback(TAGGED, tmp())
    assert GOT["groq"] == CLEAN and GOT["kokoro"] == CLEAN and GOT["edge"] == CLEAN, GOT


@test
def subtitles_of_streaming_speech_are_clean():
    st = V.SpeechStream.__new__(V.SpeechStream)
    st.interruptible = False
    st.files = queue.Queue()
    st.files.put((TAGGED, tmp()))
    st.files.put(V.SpeechStream._END)
    V._play_file = lambda fn, interruptible=False: None
    V._vary_pitch = lambda fn: None
    V._stop_speaking.clear()
    seen = []
    orig = V.shared_state

    class Watch(dict):
        def __setitem__(self, k, val):
            if k == "text":
                seen.append(val)
            super().__setitem__(k, val)
    V.shared_state = Watch()
    try:
        st._play_loop()
    finally:
        V.shared_state = orig
    assert seen == [CLEAN], seen


def main():
    global V
    install()
    V = load_voice()
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
