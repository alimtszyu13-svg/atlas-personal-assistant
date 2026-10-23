"""
Ошибки из тестовой сессии 23.10 — каждая стала проверкой, чтобы не вернуться.

    python tests/test_log_2310.py
"""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ATLAS_TESTING", "1")
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
def open_app_understands_english_and_exe_names():
    """Модель трижды просила «notepad», «notepad.exe», полный путь — и слышала «Не знаю приложения»."""
    import system_control as S
    for name in ("notepad", "Notepad.exe", r"C:\Windows\System32\notepad.exe", "Блокнот", "«блокнот»"):
        assert S._canon_app(name) == "блокнот", (name, S._canon_app(name))
    assert S._canon_app("chrome") == "хром" and S._canon_app("WhatsApp") == "whatsapp"
    assert S._canon_app("какая-то программа") == "какая-то программа"     # неизвестное — дальше в ярлыки как есть


@test
def app_word_inside_a_request_is_not_an_app():
    """«открой новую вкладку в блокноте, нажми на плюсик» заново открывал Блокнот вместо вкладки."""
    import fast_commands as F
    assert F._match_app("новую вкладку в блокноте, нажми на плюсик сверху") is None
    assert F._match_app("блокнот") == "блокнот"
    assert F._match_app("мне гугл хром") == "хром"
    assert F._match_app("программу блокнот пожалуйста") == "блокнот"
    assert F._match_app("диск d") is None                     # «диск» — дискорд, но «диск d» — не он
    for t in ("новую вкладку в блокноте, нажми на плюсик сверху", "карту парижа в гугле"):
        assert any(w in t for w in F.NOT_AN_APP), t


@test
def name_this_is_not_rename():
    """«Назови три слова на «зо»» записалось как новая команда."""
    from core import aliases as A
    assert A.parse_add("Назови три слова в русском языке, которые заканчиваются на «зо».") is None
    assert A.parse_add("назови столицы европы в алфавитном порядке") is None
    assert A.parse_add("назови команду включи резку как кино") == ("кино", "включи резку")
    assert A.parse_add("переименуй запиши экран в запись") == ("запись", "запиши экран")


@test
def edit_request_allows_typing():
    """«Подправь текст по своим рекомендациям» — четыре отказа «не печатаю: не просил»."""
    import re
    src = open(os.path.join(ROOT, "brain", "tools.py"), encoding="utf-8").read()     # tools.py тянет сеть — берём только regex
    block = src[src.index("_TYPE_INTENT = re.compile("):]
    T = type("T", (), {"_TYPE_INTENT": eval(block[len("_TYPE_INTENT = "):block.index("re.I)") + 5], {"re": re})})
    for q in ("Подправь текст по своим рекомендациям.", "исправь ошибки в тексте", "допиши мысль",
              "отредактируй абзац", "fix the text"):
        assert T._TYPE_INTENT.search(q), q
    for q in ("открой блокнот", "что на экране?"):
        assert not T._TYPE_INTENT.search(q), q


@test
def contact_answer_with_punctuation():
    """«Чат записан. Назар.» — точка после «записан» сбивала разбор."""
    from core import messengers as M
    assert M._answer_name("Чат записан. Назар.") == "Назар"
    assert M._answer_name("Чат записан Назар.") == "Назар"
    assert M._answer_name("он записан как Ана") == "Ана"


@test
def exact_search_result_is_not_thrown_away():
    """Поиск «Назар» → строка «Назар» в результатах совпадала с запросом и выбрасывалась как «поле поиска»."""
    from core import messengers as M
    rg = {"list_x": 400, "top": 65, "chat_x": 404, "head_y": 160, "foot_y": 950, "W": 1900, "H": 1050}
    uia = [{"text": "Назар", "x": 135, "y": 260, "w": 50, "h": 19, "uia": "TextControl"},
           {"text": "Назар 20:31", "x": 135, "y": 259, "w": 233, "h": 23, "uia": "DataItemControl"}]
    res = M._choose(uia, rg, M.stems("Назару"), False, None, "назар")
    assert res and res[0] == "click" and res[1]["y"] >= 259, res
    # OCR: строка поиска сверху с тем же текстом — её не жмём, жмём результат
    ocr = [{"text": "Назар", "x": 120, "y": 100, "w": 50, "h": 18}, {"text": "Назар", "x": 135, "y": 260, "w": 50, "h": 18}]
    res = M._choose(ocr, rg, M.stems("Назару"), False, None, "назар")
    assert res and res[0] == "click" and res[1]["y"] == 260, res


@test
def hologram_keeps_the_kind_of_place():
    """«голограмму города Париж» → модель искала «Paris» → на экране Пэрис Хилтон."""
    import re
    src = open(os.path.join(ROOT, "brain", "features.py"), encoding="utf-8").read()
    ns = {"re": re}
    exec(src[src.index("_HOLO_KIND = ("):src.index("def holo_show(query: str)")], ns)
    k = ns["holo_kind"]
    assert k("Paris", "Итак, Атлас, выпейте мне голограмму города Париж.") == "Paris city"
    assert k("Париж", "покажи город Париж") == "Париж город"
    assert k("Paris city", "покажи город Париж") == "Paris city"
    assert k("Eiffel Tower", "покажи Эйфелеву башню") == "Eiffel Tower"
    assert k("горячий шоколад", "покажи горячий шоколад") == "горячий шоколад"


def main():
    ok = 0
    for fn in TESTS:
        try:
            fn()
            ok += 1
            print(f"  ✓ {fn.__name__}")
        except Exception:
            print(f"  ✗ {fn.__name__}")
            traceback.print_exc()
    print(f"Тестов пройдено: {ok} из {len(TESTS)}")
    sys.exit(0 if ok == len(TESTS) else 1)


if __name__ == "__main__":
    main()
