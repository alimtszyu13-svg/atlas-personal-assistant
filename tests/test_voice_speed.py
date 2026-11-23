"""
Голос: длинная фраза начинает звучать после короткого первого куска, а не после синтеза всего текста.
Настоящий voice.py, но звук, сеть и синтез подменены: время синтеза ∝ длине куска (как у Fish по журналу).

    python tests/test_voice_speed.py
"""
import os
import sys
import time
import traceback
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def _fake(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


class _Any:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, n):
        return _Any()

    def __call__(self, *a, **k):
        return _Any()


def load_voice():
    """voice.py без звуковой карты, pygame, Groq, ElevenLabs и сети."""
    if "voice" in sys.modules:
        return sys.modules["voice"]
    for name in ("sounddevice", "edge_tts", "pygame", "elevenlabs", "elevenlabs.client", "groq", "dotenv"):
        if name not in sys.modules or name in ("pygame", "sounddevice", "edge_tts", "elevenlabs", "elevenlabs.client", "groq"):
            _fake(name)
    sys.modules["groq"].Groq = _Any
    sys.modules["elevenlabs.client"].ElevenLabs = _Any
    sys.modules.setdefault("dotenv", _fake("dotenv")).load_dotenv = lambda *a, **k: None
    sys.modules["pygame"].mixer = _Any()
    sys.modules["pygame"].time = _Any()
    import voice
    return voice


@test
def long_reply_starts_with_a_short_piece():
    v = load_voice()
    made, played, t0 = [], [], {}

    def gen(text, fn):                                    # синтез: 0,02 с на символ (масштаб 1:1 к реальным 2 с на 100)
        time.sleep(len(text) * 0.0005)
        made.append(text)
        return fn

    def play(fn, interruptible=False):
        played.append((time.time() - t0["t"], fn))

    v._generate_any, v._play_file, v._vary_pitch = gen, play, lambda fn: None
    v.os.remove = lambda fn: None
    v._maybe_play_ambient = lambda t: None
    v._response_language["lang"] = "ru"
    text = "Мысль записана: дедлайн курсовой 20 ноября, идея сделать видео про проект, и ещё напомнить завтра утром."
    t0["t"] = time.time()
    v.speak(text, interruptible=False)
    assert len(made) >= 2, made
    assert len(made[0]) <= 48, f"первый кусок длинный: {made[0]!r}"
    assert " ".join(made).replace("  ", " ").strip() == text, made
    first_sound = played[0][0]
    whole = len(text) * 0.0005
    assert first_sound < whole * 0.6, (first_sound, whole)


@test
def short_and_cached_phrases_unchanged():
    v = load_voice()
    calls = []
    orig = v._speak_unlocked
    v._speak_unlocked = lambda text, interruptible=True, cache_as=None: calls.append((text, cache_as))
    try:
        v.speak("Готово, сэр.")
        v.speak("Добрый вечер, сэр. Атлас в сети и готов работать над вашими задачами сегодня.", cache_as="greet")
    finally:
        v._speak_unlocked = orig
    assert calls == [("Готово, сэр.", None),
                     ("Добрый вечер, сэр. Атлас в сети и готов работать над вашими задачами сегодня.", "greet")], calls


def main():
    ok = 0
    for t in TESTS:
        try:
            t()
            ok += 1
            print(f"  ✓ {t.__name__}")
        except Exception:
            print(f"  ✗ {t.__name__}")
            traceback.print_exc()
    print(f"\nТестов пройдено: {ok} из {len(TESTS)}")
    sys.stdout.flush()
    os._exit(0 if ok == len(TESTS) else 1)


if __name__ == "__main__":
    main()
