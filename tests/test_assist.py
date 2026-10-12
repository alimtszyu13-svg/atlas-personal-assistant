"""
«Отчёт», проверка перед отправкой, заполнение форм, магазины (цена и вежливые сообщения).

    python tests/test_assist.py
"""
import os
import sys
import tempfile
import time
import traceback
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TMP = tempfile.mkdtemp(prefix="atlas_assist_")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def report_compose_and_speed():
    from core import report as R
    d = {"weather": "+12°, ясно", "events": [("19:00", "созвон по проекту")],
         "reminders": [{"when": "сегодня, 20:30", "text": "позвонить бабушке"}], "mail": 3,
         "pc": {"battery": (24, False), "disk": 12.4, "cpu": 85, "hog": "chrome", "ram": 60},
         "watch": ["загрузка"], "modes": [("rec", "Лекция", 12)], "waiting": [("msg", "Мама ❤")],
         "notes": {"tasks": 4, "ideas": 2, "buy": 0}}
    parts = R.compose(d, now=datetime(2026, 10, 12, 17, 40), ru=True)
    text = " ".join(p for _, p in parts)
    assert text.startswith("Сэр, отчёт на 17:40. На улице +12°, ясно. В календаре: 19:00 — созвон по проекту."), text
    for piece in ("Напоминания: 20:30 — позвонить бабушке.", "В почте 3 непрочитанных письма.",
                  "заряд 24 % — стоит подключить зарядку", "на диске C осталось всего 12 ГБ",
                  "процессор загружен на 85 %, больше всех — chrome", "Слежу: загрузка.", "Идёт запись «Лекция», 12 мин.",
                  "Ждёт твоего «да»: сообщение для Мама ❤.", "У тебя 4 открытые задачи, 2 новые идеи."):
        assert piece in text, (piece, text)
    quiet = " ".join(p for _, p in R.compose({"pc": {"cpu": 5, "ram": 30}}, now=datetime(2026, 10, 12, 9, 5)))
    assert quiet == "Сэр, отчёт на 09:05. Компьютер в порядке. Больше ничего важного.", quiet
    slow = [("weather", lambda: "+1°"), ("mail", lambda: time.sleep(3) or 9)]
    t0 = time.time()
    got = R.collect(slow, timeout=0.5)
    assert got == {"weather": "+1°"} and time.time() - t0 < 1.0, "медленный источник не держит отчёт"
    boom = [("events", lambda: 1 / 0), ("watch", lambda: ["x"])]
    assert R.collect(boom) == {"watch": ["x"]}
    for t in ("отчёт", "Атлас, отчёт", "доложи обстановку", "статус", "report", "status report"):
        assert R._ASK.match(t), t
    assert not R._ASK.match("отчёт по физике") and R.handle("отчёт по физике") is None


class PEnv:
    def __init__(self, sel="", doc=None):
        self.sel, self.doc, self.pasted = sel, doc, []

    def selected(self):
        return self.sel, 42

    def word(self):
        return self.doc

    def paste(self, hwnd, text):
        self.pasted.append((hwnd, text))
        return True


class FakeRange:
    def __init__(self, para):
        self.p = para

    @property
    def Text(self):
        return self.p.text + "\r"

    @Text.setter
    def Text(self, v):
        self.p.text = v

    def MoveEnd(self, unit, n):
        pass


class FakePara:
    def __init__(self, t):
        self.text = t

    @property
    def Range(self):
        return FakeRange(self)


class FakeDoc:
    def __init__(self, paras):
        self._p = [FakePara(t) for t in paras]
        self.TrackRevisions = False

    @property
    def Paragraphs(self):
        doc = self

        class P:
            def __iter__(self):
                return iter(doc._p)

            def __call__(self, i):
                return doc._p[i - 1]
        return P()


@test
def proofread_selection_and_word():
    from core import proofread as P
    P._s.update(pending=None, said=None)
    ask = lambda s, u, max_tokens=0: ('{"fixed": "Привет, извини, что поздно. Я думаю, что успею.", '
                                       '"issues": ["«извени» → «извини»", "запятая перед «что»"]}')
    env = PEnv(sel="Привет извени что поздно. Я думаю что успею.")
    said = P.handle("проверь перед отправкой", env=env, ask=ask)
    assert said == "Нашёл 2 ошибки: «извени» → «извини»; запятая перед «что». Исправить?", said
    assert P.handle("да", last="другое", env=env) is None, "«да» на другой вопрос — не наше"
    assert P.handle("да", last=said, env=env) == "Исправил." and env.pasted == [(42, "Привет, извини, что поздно. Я думаю, что успею.")]
    same = lambda s, u, max_tokens=0: '{"fixed": "Всё хорошо.", "issues": []}'
    assert P.handle("проверь", env=PEnv(sel="Всё хорошо."), ask=same) == "Ошибок не нашёл — можно отправлять."
    assert P.handle("проверь", env=PEnv()).startswith("Выдели текст")
    doc = FakeDoc(["Первый абзац без ошибок.", "Второй абзац с ашибкой"])
    fix = lambda s, u, max_tokens=0: ('{"fixed": "Первый абзац без ошибок.\\nВторой абзац с ошибкой", '
                                      '"issues": ["«ашибкой» → «ошибкой»"]}')
    said = P.handle("проверь документ", env=PEnv(doc=doc), ask=fix)
    assert said == "Нашёл 1 ошибку: «ашибкой» → «ошибкой». Исправить?", said
    out = P.handle("да", last=said, env=PEnv(doc=doc))
    assert doc.TrackRevisions and doc._p[1].text == "Второй абзац с ошибкой" and doc._p[0].text == "Первый абзац без ошибок."
    assert out.startswith("Исправил 1 абзац") and "режиме исправлений" in out, out
    assert P.handle("проверь почту", env=env) is None


class FEnv:
    def __init__(self, fields):
        self.f, self.typed = fields, []

    def fields(self):
        return self.f

    def type_into(self, i, text):
        self.typed.append((i, text))
        return True


@test
def form_fill_profile_and_fill():
    from core import form_fill as F
    F.FILE = os.path.join(TMP, "form.json")
    ask = lambda s, u, max_tokens=0: '{"full_name": "Алим Иванов", "first_name": "Алим", "last_name": "Иванов", "city": "Бишкек"}'
    out = F.handle("для анкет: меня зовут Алим Иванов, почта alim@mail.com, телефон +996 555 123 456, город Бишкек", ask=ask)
    assert out.startswith("Запомнил для анкет: почта, телефон"), out
    d = F.load()
    assert d["email"] == "alim@mail.com" and d["phone"] == "+996555123456" and d["first_name"] == "Алим", d
    fields = [{"i": 0, "kind": "edit", "name": "Имя"}, {"i": 1, "kind": "edit", "name": "Фамилия"},
              {"i": 2, "kind": "edit", "name": "Email", "value": "old@x.com"},
              {"i": 3, "kind": "edit", "name": "Пароль", "password": True}, {"i": 4, "kind": "edit", "name": "Номер карты"},
              {"i": 5, "kind": "edit", "name": "Телефон"}, {"i": 6, "kind": "edit", "name": "ИНН"},
              {"i": 7, "kind": "button", "name": "Отправить"}]
    seen = []

    def plan_ask(system, user, max_tokens=0):
        seen.append(user)
        return '{"0": "Алим", "1": "Иванов", "5": "+996555123456", "3": "hack", "6": ""}'
    env = FEnv(fields)
    out = F.fill(env, plan_ask, True)
    assert env.typed == [(0, "Алим"), (1, "Иванов"), (5, "+996555123456")], env.typed
    assert "Пароль" not in seen[0] and "карты" not in seen[0] and "Email" not in seen[0], "в модель — только пустые поля"
    assert out == "Заполнил 3 поля: Имя, Фамилия, Телефон. Не знаю: ИНН. Отправлять не стал — проверь и отправь сам.", out
    assert F.handle("что ты знаешь для анкет").startswith("Для анкет знаю: почта — alim@mail.com")
    assert F.handle("забудь мой телефон для анкет") == "Забыл: телефон." and "phone" not in F.load()
    F._save({})
    assert F.fill(FEnv(fields), plan_ask).startswith("Я ещё ничего не знаю для анкет")
    assert F.handle("заполни эту форму", env=FEnv([])).startswith("Я ещё ничего") and F.handle("включи музыку") is None


class WEnv:
    def __init__(self):
        self.t, self.page = 1000.0, "Наушники Sony WH — 3 499 сом. Похожие: 2 100 сом"

    def fetch(self, url):
        return self.page

    def current_page(self):
        return "https://shop.kg/sony", "Наушники Sony — Shop.kg"

    def now(self):
        return self.t

    def procs(self):
        return []

    def idle(self):
        return 0


@test
def shop_price_watch():
    from core import watchers as W
    W.FILE = os.path.join(TMP, "w.json")
    W._s["items"] = None
    env, said = WEnv(), []
    assert W.prices("Цена 3 499 сом, было 4 000 сом") == [(3499.0, "сом"), (4000.0, "сом")]
    assert W.main_price("Price: $1,299.99 was $1,499") == (1299.99, "$")
    out = W.handle("скажи, когда эти наушники подешевеют ниже 3000", env)
    assert out.startswith("Сейчас 3499 сом. Проверяю раз в 10 минут, две недели — скажу, когда станет ниже 3000"), out
    env.t += 700
    env.page = "Наушники Sony WH — 3 199 сом"
    W.check_all(env, lambda w, m, e: said.append(m))
    assert not said, "3199 — ещё не ниже 3000"
    env.t += 700
    env.page = "Наушники Sony WH — 2 899 сом (скидка)"
    W.check_all(env, lambda w, m, e: said.append(m))
    assert said == ["Подешевело: «Наушники Sony — Shop.kg» — 2899 сом (было 3499)."], said
    W.handle("дай знать, когда станет дешевле", env)
    env.t += 700
    env.page = "Наушники — 2 850 сом"
    W.check_all(env, lambda w, m, e: said.append(m))
    assert said[-1].startswith("Подешевело") and "было 2899" in said[-1], said


@test
def shop_messages_polite():
    from core import messengers as M
    M.known_app = lambda c: None
    it = M.intent("напиши магазину Mobi в инстаграме: есть ли iPhone 15 в синем")
    assert it == ("reply", "instagram", "магазину Mobi", "есть ли iPhone 15 в синем", None), it
    it = M.intent("спроси у магазина Dodo в ватсапе есть ли доставка до 9 вечера")
    assert it[1:3] == ("whatsapp", "магазина Dodo") and M.shop_contact(it[2]) == "Dodo", it
    assert M.intent("напиши маме в телеграме: привет")[4] == "привет", "не магазину — дословно"
    seen = []
    out = M.draft("есть ли iPhone 15 в синем", [], "Mobi", "instagram", True,
                  ask=lambda s, u, max_tokens=0: seen.append(s) or "«Здравствуйте! Подскажите, пожалуйста, есть ли iPhone 15 в синем цвете? Спасибо!»",
                  shop=True)
    assert out == "Здравствуйте! Подскажите, пожалуйста, есть ли iPhone 15 в синем цвете? Спасибо!"
    assert "Polite, literate" in seen[0] and "«Здравствуйте!»" in seen[0] and "Do not invent" in seen[0]
    assert M.is_shop("поддержку Yandex") and not M.is_shop("маме")


@test
def assist_wiring():
    from core import pc_fast
    fast = open(os.path.join(ROOT, "core", "pc_fast.py"), encoding="utf-8").read()
    assert "report.handle(text)" in fast and "proofread.handle" in fast and "form_fill.handle" in fast
    import skills.pc_power  # noqa: F401
    from core.skills import REGISTRY
    for n in ("status_report", "proofread_selection", "fill_form", "remember_for_forms"):
        assert n in REGISTRY, n
    for f in (".gitignore", ".dockerignore"):
        assert "atlas_form_profile.json" in open(os.path.join(ROOT, f), encoding="utf-8").read()
    assert pc_fast.handle("заполни эту форму", local=False) is None, "с телефона — не трогаем поля на экране"


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
