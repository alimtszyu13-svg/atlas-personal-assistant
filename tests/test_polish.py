"""
Полировка: поиск в интернете быстрее, пересказ памяти без json_validate_failed, ответы без дежурных фраз.

    python tests/test_polish.py
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


@test
def no_closing_filler():
    from core import filler as F
    assert F.clean("Here's a classic travel essay: «The Art». Happy reading, sir.") == "Here's a classic travel essay: «The Art»."
    assert F.clean("Sent to your phone, sir. Let me know if you need anything else.") == "Sent to your phone, sir."
    assert F.clean("Готово, сэр. Чем ещё могу помочь?") == "Готово, сэр."
    assert F.clean("Сохранил. Если что-то понадобится — скажите.") == "Сохранил."
    assert F.clean("Погода 24 градуса. Удачи на экзамене!") == "Погода 24 градуса. Удачи на экзамене!", "тёплое пожелание — не дежурное"
    assert F.clean("Надеюсь, это поможет.") == "Надеюсь, это поможет.", "весь ответ — не обрезаем до пустоты"
    assert F.piece("Happy reading, sir.") == "" and F.piece("Нашёл 3 файла.") == "Нашёл 3 файла."


@test
def memory_summary_survives_strict_json_errors():
    from core import memory as M
    assert M.parse_json('Вот: ```json\n{"summary": "ok", "facts": []}\n```') == {"summary": "ok", "facts": []}
    cut = '{"summary": "Обсуждали SAT", "facts": [{"s": "User", "r": "prepares_for", "o": "SAT"}, {"s": "Us'
    assert M.parse_json(cut) == {"summary": "Обсуждали SAT", "facts": [{"s": "User", "r": "prepares_for", "o": "SAT"}]}
    assert M.parse_json("нет json") == {}
    tried = []

    class Cl:
        def __init__(self, reply):
            self.reply = reply
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        def create(self, **kw):
            tried.append(("json" if "response_format" in kw else "text", kw["model"]))
            if isinstance(self.reply, Exception):
                raise self.reply
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=self.reply))])
    bad = Cl(RuntimeError("Error code: 400 - json_validate_failed"))
    good = Cl('Конечно! {"summary": "Говорили про эссе", "facts": [{"s": "User", "r": "writes", "o": "essay"}]}')
    st, summary, facts = M._summarize([("user", "я пишу эссе"), ("assistant", "ок")],
                                      calls=[(bad, "fast", True), (good, "fast", False)])
    assert st == "ok" and summary == "Говорили про эссе" and facts[0]["o"] == "essay", (st, summary, facts)
    assert tried == [("json", "fast"), ("text", "fast")], tried
    st, *_ = M._summarize([("user", "x")], calls=[(bad, "a", True), (Cl(""), "b", False)])
    assert st == "error"


@test
def web_search_races_engines_and_caches():
    import web_search_tool as W
    calls = []

    def one(q, n, backend):
        calls.append(backend)
        if backend == "duckduckgo":
            time.sleep(1.5)                                    # медленный
            return [{"title": "медленный", "body": "", "href": "https://slow"}]
        if backend == "bing":
            raise RuntimeError("bing недоступен")
        time.sleep(0.1)
        return [{"title": "Погода в Бишкеке", "body": "Сегодня +24", "href": "https://pogoda.kg"}]
    W._cache.clear()
    t0 = time.time()
    out = W.search_web("погода бишкек", one=one)
    took = time.time() - t0
    assert "Погода в Бишкеке: Сегодня +24 (https://pogoda.kg)" == out and took < 1.0, (out, took)
    calls.clear()
    t0 = time.time()
    assert W.search_web("Погода Бишкек ", one=one) == out and time.time() - t0 < 0.05 and not calls, "из кэша"
    W._cache.clear()
    out = W.search_web("ничего", one=lambda q, n, b: [])
    assert "No results" in out
    out = W.search_web("ошибка", one=lambda q, n, b: (_ for _ in ()).throw(RuntimeError("нет сети")))
    assert "Couldn't search" in out


@test
def prompt_asks_for_no_filler():
    src = open(os.path.join(ROOT, "brain", "prompt.py"), encoding="utf-8").read()
    assert "no closing filler" in src


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
