"""
Распознавание: «выключись» не превращается в «Официанты», а случайная короткая фраза не запускает поиск.

    python tests/test_stt_guard.py
"""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np  # noqa: E402

from core import stt_guard as G  # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def vosk(*words, conf=1.0):
    return [{"word": w, "conf": conf} for w in words]


@test
def waiters_become_shutdown_when_whisper_is_unsure():
    cmd = G.local_command(vosk("выключись"))
    assert cmd == "выключись"
    r = G.pick([("Афащин.", -1.11, 0.1), ("Официанты.", -0.75, 0.1)], cmd, seconds=0.8)
    assert r["text"] == "выключись" and r["source"] == "vosk", r


@test
def confident_whisper_is_not_overridden_by_vosk():
    r = G.pick([("Открой загрузки.", -0.2, 0.0), ("Открой загрузки", -0.25, 0.0)], "громче", seconds=1.2)
    assert r["text"] == "Открой загрузки." and r["source"] == "whisper" and not r["doubt"], r


@test
def shutdown_needs_real_doubt_not_just_a_short_phrase():
    r = G.pick([("Выключи свет.", -0.4, 0.0), ("Выключи свет.", -0.42, 0.0)], "выключись", seconds=0.9)
    assert r["text"] == "Выключи свет." and r["source"] == "whisper", "выключение — только при явном сомнении"
    r = G.pick([("Дальше.", -0.4, 0.0), ("Дальше.", -0.4, 0.0)], "громче", seconds=0.9)
    assert r["text"] == "громче", "обычная короткая команда: Vosk уверен, Whisper — так себе"


@test
def unsure_vosk_or_unknown_words_are_ignored():
    assert G.local_command(vosk("выключись", conf=0.7)) == ""
    assert G.local_command(vosk("[unk]")) == ""
    assert G.local_command(vosk("сделай", "громче")) == "сделай громче"
    assert G.local_command(vosk("громче", "[unk]")) == ""


@test
def unclear_short_phrase_is_asked_again():
    r = G.pick([("Афащин.", -1.11, 0.1), ("Официанты.", -0.75, 0.1)], "", seconds=0.8)
    assert r["text"] == "Официанты." and r["doubt"], r
    r = G.pick([("Розен.", -1.2, 0.0), ("Розен.", -1.1, 0.0)], "", seconds=0.6)
    assert r["doubt"], "даже одинаковые варианты, но очень неуверенные — переспросить"


@test
def long_or_clear_phrases_are_never_questioned():
    r = G.pick([("Найди документ про путешествия на компьютере.", -0.95, 0.0),
                ("Найди документ о путешествиях на компьютере.", -0.97, 0.0)], "", seconds=2.8)
    assert not r["doubt"], "длинную фразу выполняем — смысл понятен и с ошибкой в слове"
    r = G.pick([("Какая погода?", -0.3, 0.0), ("Какая погода?", -0.3, 0.0)], "", seconds=0.9)
    assert not r["doubt"]


@test
def silence_like_result_is_doubtful():
    r = G.pick([("Спасибо.", -0.5, 0.9), ("Спасибо.", -0.5, 0.9)], "", seconds=0.5)
    assert r["doubt"], r


@test
def audio_is_trimmed_leveled_and_padded():
    sr = G.SAMPLE_RATE
    quiet = np.zeros(sr * 3, dtype=np.int16)
    quiet[sr:sr + sr // 2] = (np.sin(np.arange(sr // 2) / 5) * 2000).astype(np.int16)
    out = G.prepare(quiet, [{"start": sr, "end": sr + sr // 2}], pad_s=0.4)
    assert len(out) < len(quiet), "тишина по краям обрезана"
    assert abs(len(out) - (int(0.4 * sr) * 2 + sr // 2 + int(0.25 * sr) + int(0.3 * sr))) <= 2, len(out)
    assert np.max(np.abs(out)) > 10000, "тихая речь стала громче"
    assert not np.any(out[:int(0.4 * sr)]), "в начале — тишина для Whisper"
    loud = (np.sin(np.arange(sr) / 5) * 30000).astype(np.int16)
    assert np.max(np.abs(G.prepare(loud))) <= 30001, "громкую не перегружаем"


@test
def grammar_keeps_only_words_the_model_knows():
    g = G.grammar(lambda w: w != "отключись")
    assert "отключись" not in g and "выключись" in g and g[-1] == "[unk]"


@test
def early_ears_hand_over_wake_and_command_audio():
    from core import early_ears as E
    import threading
    E._s.update(thread=threading.Thread(target=lambda: None), woke=None, after=[], loud=0.0)
    E._s["thread"].start()
    E._s["stop"].clear()
    assert E.take() == (False, None), "имени не было — обычный запуск"
    E._s.update(thread=threading.Thread(target=lambda: None), woke=1.0, after=[b"\x01\x00" * 8000], loud=0.0)
    E._s["thread"].start()
    E._s["stop"].clear()
    woke, audio = E.take()
    assert woke and audio.shape == (8000, 1), (woke, None if audio is None else audio.shape)
    assert E.take() == (False, None), "отдаёт один раз"


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
    sys.exit(0 if ok == len(TESTS) else 1)


if __name__ == "__main__":
    main()
