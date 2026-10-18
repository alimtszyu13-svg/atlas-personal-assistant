"""
Скорость: раннее распознавание речи, раннее окончание фразы, секундомер ответа — без микрофона и сети.

    python tests/test_speed.py
"""
import json
import os
import sys
import tempfile
import threading
import time
import traceback

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ATLAS_TESTING", "1")
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TMP = tempfile.mkdtemp(prefix="atlas_speed_")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class Mic:
    """Сценарий: список (секунды, речь?) → блоки по 32 мс; часы идут вместе с блоками."""

    def __init__(self, script):
        from core import listen_loop as L
        self.blocks = []
        for sec, speech in script:
            self.blocks += [speech] * int(round(sec / (L.BLOCK / L.SR)))
        self.i, self.t = 0, 0.0
        self.dt = L.BLOCK / L.SR

    def read(self):
        speech = self.blocks[self.i] if self.i < len(self.blocks) else False
        self.i += 1
        self.t += self.dt
        time.sleep(0.002)
        return np.full(512, 3000 if speech else 0, dtype=np.int16)

    def prob(self, chunk):
        return 0.9 if chunk[0] else 0.05

    def clock(self):
        return self.t


def _slow(text, delay=0.02):
    calls = []

    def tr(audio):
        calls.append(len(audio))
        time.sleep(delay)
        return text
    return tr, calls


@test
def early_text_reused_at_end():
    from core import listen_loop as L
    mic = Mic([(0.3, False), (1.2, True), (2.0, False)])
    tr, calls = _slow("какая завтра погода в оше")
    audio, text, note = L.run(mic.read, mic.prob, 15, 1.2, 8, transcribe=tr, is_complete=lambda t: False,
                              clock=mic.clock)
    assert note == "hit" and text == "какая завтра погода в оше" and len(calls) == 1, (note, text, calls)
    assert len(audio) >= 512 * 30


@test
def stale_when_speech_resumes():
    from core import listen_loop as L
    mic = Mic([(0.2, False), (1.0, True), (0.6, False), (1.0, True), (2.0, False)])
    tr, calls = _slow("часть фразы")
    audio, text, note = L.run(mic.read, mic.prob, 15, 1.2, 8, transcribe=tr, is_complete=lambda t: False,
                              clock=mic.clock)
    assert len(calls) == 2, "вторая пауза — новая попытка"
    assert note == "hit" and len(audio) > 512 * 80, (note, len(audio))
    mic = Mic([(0.2, False), (0.8, True), (0.6, False), (0.8, True), (0.6, False), (0.8, True), (2.0, False)])
    tr, calls = _slow("x")
    _a, text, note = L.run(mic.read, mic.prob, 15, 1.2, 8, transcribe=tr, is_complete=lambda t: False, clock=mic.clock)
    assert len(calls) == 2 and text is None and note == "stale", "не больше 2 ранних попыток за фразу"


@test
def early_end_for_complete_command():
    from core import listen_loop as L
    mic = Mic([(0.2, False), (1.0, True), (3.0, False)])
    tr, _ = _slow("поставь хром слева", delay=0)
    audio, text, note = L.run(mic.read, mic.prob, 15, 1.2, 8, transcribe=tr,
                              is_complete=lambda t: t.startswith("поставь"), clock=mic.clock)
    assert note == "early" and text == "поставь хром слева", (note, text)
    used = mic.i * mic.dt
    assert used < 0.2 + 1.0 + 1.0, f"закончил раньше полной паузы: {used:.2f} с"
    mic = Mic([(0.2, False), (1.0, True), (3.0, False)])
    _a, text, note = L.run(mic.read, mic.prob, 15, 1.2, 8, transcribe=tr, is_complete=None, clock=mic.clock)
    assert note == "hit", "диктовка: без раннего окончания, но готовый текст берём"


@test
def timeout_off_switch_and_errors():
    from core import listen_loop as L
    mic = Mic([(9.0, False)])
    assert L.run(mic.read, mic.prob, 15, 1.2, 1.0, clock=mic.clock) == (None, None, "none")
    os.environ["SPECULATIVE_STT"] = "off"
    try:
        mic = Mic([(0.2, False), (1.0, True), (2.0, False)])
        tr, calls = _slow("x")
        _a, text, note = L.run(mic.read, mic.prob, 15, 1.2, 8, transcribe=tr, clock=mic.clock)
        assert not calls and text is None and note == "none"
    finally:
        os.environ.pop("SPECULATIVE_STT")

    def boom(a):
        raise RuntimeError("сеть")
    mic = Mic([(0.2, False), (1.0, True), (2.0, False)])
    _a, text, note = L.run(mic.read, mic.prob, 15, 1.2, 8, transcribe=boom, clock=mic.clock)
    assert text is None, "ошибка раннего распознавания — обычный путь"
    assert L.complete_command("поставь хром слева") and not L.complete_command("поставь хром слева и")
    assert not L.complete_command("расскажи про чёрные дыры")


@test
def turn_trace_logs_and_stats():
    from core import turn_trace as T
    log = os.path.join(TMP, "turns.jsonl")
    t0 = time.time()
    T.begin(t0)
    T.mark("stt", t0 + 0.6)
    T.mark("route", t0 + 0.62)
    T.mark("first_token", t0 + 1.4)
    T.mark("first_audio", t0 + 1.9)
    T.mark("stt", t0 + 9)                        # повторная отметка не перезаписывает
    T.note(path="brain", spec="hit")
    T.mark("done", t0 + 3.1)
    s = T.end(log)
    assert s.startswith("[ход] речь→текст 0,60 · разбор 0,02 · модель 0,78 · голос 0,50 · до звука 1,90 · всего 3,1"), s
    row = json.loads(open(log, encoding="utf-8").read().splitlines()[0])
    assert row["marks"]["stt"] == 0.6 and row["notes"]["spec"] == "hit"
    T.begin(t0)
    T.mark("first_audio", t0 + 0.3)
    T.note(path="fast")
    T.end(log)
    st = T.stats(1, log)
    assert st["n"] == 2 and st["fast_share"] == 50 and st["spec_hits"] == 1, st
    assert T.end(log) == "", "хода нет — ничего"
    orig = T.LOG
    T.LOG = log
    try:
        d = T.handle("насколько ты быстрый?")
        assert d.startswith("Сегодня 2 ответов: до первого звука в среднем") and "Без модели (мгновенно) — 50 %" in d, d
    finally:
        T.LOG = orig
    assert T.handle("включи музыку") is None


@test
def speed_wiring():
    v = open(os.path.join(ROOT, "voice.py"), encoding="utf-8").read()
    assert "listen_loop.run(" in v and "FIRST_MIN_CHARS = 12" in v and "def warm()" in v and '_mark("first_audio")' in v
    assert "temp_stt_{threading.get_ident()}" in v, "ранний и обычный разбор речи не делят один файл"
    m = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert "_vw.warm()" in m and "early=False) if trigger == \"dictation\"" in m and "turn_trace.end()" in m
    sid = open(os.path.join(ROOT, "core", "speaker_id.py"), encoding="utf-8").read()
    assert "audio, _ready = _listen_once(" in sid
    from core import intent_router
    assert intent_router.route("насколько ты быстрый").label == "speed_stats"


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
