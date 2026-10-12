"""
Суфлёр на созвоне и секретарь звонков — без звука, окон и сети.

    python tests/test_calls.py
"""
import os
import sys
import tempfile
import time
import traceback

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TMP = tempfile.mkdtemp(prefix="atlas_calls_")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class FakeCard:
    def __init__(self):
        self.shown = []

    def show(self, title, text, seconds=35):
        self.shown.append((title, text))

    def close(self):
        self.shown.append(("closed", ""))


def _fresh_prompter(names=("Алим",), mode="me"):
    from core import prompter as P
    P.CFG = os.path.join(TMP, "prompter.json")
    if os.path.exists(P.CFG):
        os.remove(P.CFG)
    if names:
        P.set_names(", ".join(names))
    c = P.cfg()
    c["mode"] = mode
    P._save_cfg(c)
    P._s.update(on=False, lines=[], last_q="", shown=[], ask_name=False)
    return P


@test
def prompter_who_is_asked():
    P = _fresh_prompter()
    my = P._stems(["Алим"])
    assert my == ["алим", "alim"], my
    cases = {"Алим, а ты что думаешь про сроки?": "me", "What do you think, Alim?": "me",
             "Алимчик, ты где?": "me", "Дана, а ты что думаешь?": "other", "Кто-нибудь знает, когда дедлайн?": "group",
             "А ты как считаешь?": "maybe", "Сегодня хорошая погода.": None, "Алим, привет.": None,
             "Итак, какой у нас план на неделю?": None, "Слушай, Бекзат, ты закончил отчёт?": "other"}
    for t, want in cases.items():
        assert P.addressed(t, my) == want, (t, P.addressed(t, my))
    assert P.translit("Алим") == "alim" and P.translit("Alim") == "алим"


@test
def prompter_shows_only_when_asked_me():
    P = _fresh_prompter()
    card, asked = FakeCard(), []

    def ask(system, user):
        asked.append(user)
        return '{"to_user": true, "answer": "Думаю, успеем к пятнице, если сегодня закроем дизайн."}'
    t0 = 1000.0
    assert P.on_line("Давайте начнём, сегодня обсуждаем сроки проекта.", card, ask, now=t0) == ""
    assert P.on_line("Дана, а ты что думаешь?", card, ask, now=t0 + 5) == "" and not asked, "чужое имя — молчим"
    assert P.on_line("А ты как считаешь?", card, ask, now=t0 + 8) == "" and not asked, "«ты» без имени — не мне"
    out = P.on_line("Алим, а ты что думаешь про сроки?", card, ask, now=t0 + 12)
    assert out.startswith("Думаю, успеем к пятнице") and card.shown[-1][0].startswith("Алим, а ты"), card.shown
    assert "обсуждаем сроки проекта" in asked[0] and "Question just asked: Алим" in asked[0], asked[0]
    out = P.on_line("А ты уверен, что дизайн успеем?", card, ask, now=t0 + 20)
    assert out, "только что спрашивали тебя по имени — «ты» теперь про тебя"
    assert P.on_line("Кто-нибудь знает, когда дедлайн?", card, ask, now=t0 + 80) == "", "общий вопрос — молчим"
    no = lambda s, u: '{"to_user": false, "answer": ""}'
    assert P.on_line("А ты что скажешь?", card, no, now=t0 + 25) == "", "модель сказала: не тебе"
    n = len(card.shown)
    out = P.hint_now(card, ask)
    assert out and len(card.shown) == n + 1 and card.shown[-1][0] == "А ты что скажешь?", "Ctrl+Alt+H — на последний"
    _fresh_prompter(mode="all")
    assert P.on_line("Кто-нибудь знает, когда дедлайн?", card, ask, now=t0 + 90), "режим «на все вопросы»"


@test
def prompter_answer_parsing_and_prompt():
    P = _fresh_prompter()
    seen = []
    ok, text = P.answer("Алим, сколько будет стоить сервер?", ["Обсуждаем бюджет."], ["Алим"], "me",
                        ask=lambda s, u: seen.append(s) or 'Вот: ```json\n{"to_user": true, "answer": "Около 20 долларов в месяц."}\n```')
    assert ok and text == "Около 20 долларов в месяц.", text
    assert "READ your text aloud" in seen[0] and "No preambles" in seen[0] and "Russian" in seen[0]
    ok, text = P.answer("Алим, ты тут?", [], ["Алим"], "me", ask=lambda s, u: "Да, я здесь, слышу вас.")
    assert ok and text == "Да, я здесь, слышу вас.", "не JSON — берём текст как ответ"


@test
def prompter_voice_and_pipeline():
    P = _fresh_prompter(names=())
    said = P.handle("включи суфлёр")
    assert said.startswith("Как тебя называют на созвонах?"), said
    lines = []

    def capture(feed, stop):
        speech = (np.sin(np.linspace(0, 400, 16000)) * 0.1).astype(np.float32)
        quiet = np.zeros(16000 // 5, dtype=np.float32)
        for _ in range(2):
            for k in range(0, len(speech), 3200):
                feed(speech[k:k + 3200])
            for _ in range(5):
                feed(quiet)
        while not stop.is_set():
            time.sleep(0.05)
    import core.prompter as PM
    orig_start = PM.start
    PM.start = lambda capture_=None, stt=None, card=None: orig_start(capture, lambda a: lines.append(len(a)) or "",
                                                                       FakeCard())
    try:
        out = P.handle("меня зовут Алим, Алимчик")
    finally:
        PM.start = orig_start
    assert out.startswith("Суфлёр включён — молча подсказываю только когда спрашивают тебя (Алим, Алимчик)"), out
    for _ in range(40):
        if len(lines) >= 2:
            break
        time.sleep(0.05)
    assert len(lines) == 2, lines
    assert P.active() and P.handle("выключи суфлёр").startswith("Суфлёр выключен") and not P.active()
    assert P.handle("суфлёр, отвечай на все вопросы") == "Хорошо, подсказываю на все вопросы." and P.cfg()["mode"] == "all"
    assert P.handle("включи музыку") is None


# --- секретарь ------------------------------------------------------------------------------------------------
class PhoneEnv:
    def __init__(self, headphones=False, labels=True):
        self.wins = [{"hwnd": 1, "app": "Telegram", "title": "Telegram", "min": False}]
        self.call = False
        self.log, self.said, self.hp, self.labels = [], [], headphones, labels
        self.t = 100.0

    def ring(self):
        self.call = True
        self.wins.append({"hwnd": 77, "app": "Telegram", "title": "Telegram", "min": False})

    def windows(self):
        return list(self.wins)

    def rect(self, hwnd):
        return (1500, 300, 1820, 700)

    def ocr(self, rect):
        if not self.call:
            return [{"text": "Чаты", "x": 0, "y": 0, "w": 10, "h": 12}]
        return [{"text": "Мама ❤", "x": 1600, "y": 420, "w": 120, "h": 28},
                {"text": "звонит в Telegram…", "x": 1590, "y": 460, "w": 150, "h": 14},
                {"text": "Принять", "x": 1560, "y": 640, "w": 50, "h": 12}]

    def buttons(self, hwnd):
        return [("Принять", 1560, 650), ("Отклонить", 1760, 650), ("Завершить", 1660, 650)] if self.labels else []

    def color_buttons(self, rect):
        return {"green": (1580, 655), "red": (1740, 655)}

    def click(self, x, y):
        self.log.append(("click", x, y))
        if self.call:
            self.wins = [w for w in self.wins if w["hwnd"] != 77] if (x, y) != (1560, 650) else self.wins

    def activate(self, h):
        return True

    def headphones(self):
        return self.hp

    def louder(self):
        self.log.append("louder")

    def say(self, text):
        self.said.append(text)

    def listen_now(self):
        self.log.append("listen")

    def sleep(self, s):
        self.t += s

    def now(self):
        return self.t


def _fresh_secretary():
    from core import call_secretary as C
    C._s.update(ring=None, seen=set(), handled=set())
    C.owner_name = lambda: "Алим"
    C._ru = lambda: True
    return C


@test
def secretary_announces_and_repeats():
    C = _fresh_secretary()
    env, said = PhoneEnv(), []
    C._s["seen"] = {1}
    assert C.tick(env, said.append) == "" and not said
    env.ring()
    assert C.tick(env, said.append) == "Сэр, вам звонит Мама ❤ в Telegram." and "listen" in env.log
    env.t += 3
    assert C.tick(env, said.append) == "", "не чаще раза в 9 секунд"
    for _ in range(5):
        env.t += 10
        C.tick(env, said.append)
    assert said == ["Сэр, вам звонит Мама ❤ в Telegram.", "Сэр, всё ещё звонит Мама ❤.", "Сэр, всё ещё звонит Мама ❤."], said
    env.wins = [w for w in env.wins if w["hwnd"] != 77]
    C.tick(env, said.append)
    assert C._s["ring"] is None, "окно звонка закрылось — звонок закончился"
    assert C.intent("возьми трубку") is None, "звонка нет — команды не наши"


@test
def secretary_take_self_drop():
    C = _fresh_secretary()
    env = PhoneEnv()
    C._s["seen"] = {1}
    env.ring()
    C.tick(env, lambda s: None)
    assert C.handle("сейчас возьму", env) == "Хорошо." and C._s["ring"] is None
    C.tick(env, lambda s: None)
    assert C._s["ring"] is None, "сам взял — больше не объявляем этот звонок"
    env = PhoneEnv()
    C._s.update(seen={1}, handled=set())
    env.ring()
    C.tick(env, lambda s: None)
    assert C.handle("возьми трубку и поставь на громкую", env) == "Принял, на громкой."
    assert ("click", 1560, 650) in env.log and "louder" in env.log
    env = PhoneEnv(labels=False)
    C._s.update(seen={1}, handled=set())
    env.ring()
    C.tick(env, lambda s: None)
    assert C.handle("сбрось", env) == "Сбросил." and ("click", 1740, 655) in env.log, "без названий — по цвету"


@test
def secretary_says_busy_or_texts_back():
    C = _fresh_secretary()
    env = PhoneEnv()
    C._s.update(seen={1}, handled=set())
    env.ring()
    C.tick(env, lambda s: None)
    out = C.handle("скажи, что я очень занят, перезвоню через 10 минут", env)
    assert out == "Сказал, что ты занят, и положил трубку.", out
    assert env.said == ["Здравствуйте! Это Atlas, ассистент Алима. Он сейчас занят и перезвонит вам через 10 минут."]
    clicks = [e for e in env.log if isinstance(e, tuple)]
    assert clicks == [("click", 1560, 650), ("click", 1660, 650)], env.log
    from core import messengers
    env = PhoneEnv(headphones=True)
    C._s.update(seen={1}, handled=set(), ring=None)
    env.ring()
    C.tick(env, lambda s: None)
    sent = []
    orig = messengers.open_chat, messengers.send
    messengers.open_chat = lambda app, who, ui=None: {"ok": True, "verified": True, "hwnd": 1, "name": "Мама ❤"}
    messengers.send = lambda ui=None: sent.append(messengers._s["pending"]["text"]) or "Отправил."
    try:
        out = C.handle("скажи, что занят", env)
    finally:
        messengers.open_chat, messengers.send = orig
    assert out == "Сбросил и написал Мама ❤: «Сейчас не могу говорить, перезвоню позже.»." and not env.said, out
    assert sent == ["Сейчас не могу говорить, перезвоню позже."]
    assert C.genitive("Алим") == "Алима" and C.genitive("Дана") == "Даны" and C.genitive("Ольга") == "Ольги"


@test
def calls_wiring():
    src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert "call_secretary.start(_announce)" in src and "prompter.active()" in src
    hk = open(os.path.join(ROOT, "selection_hotkey.py"), encoding="utf-8").read()
    assert 'add_hotkey("ctrl+alt+h"' in hk
    fast = open(os.path.join(ROOT, "core", "pc_fast.py"), encoding="utf-8").read()
    assert fast.index("call_secretary.handle") < fast.index("messengers.intent")
    import skills.pc_power  # noqa: F401
    from core.skills import REGISTRY
    assert "call_prompter" in REGISTRY
    for f in (".gitignore", ".dockerignore"):
        assert "atlas_prompter.json" in open(os.path.join(ROOT, f), encoding="utf-8").read()


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
