"""
Маршрутизатор команд, экзамен, смысловой слой и обучение на исправлениях — без сети и модели.

    python tests/test_router.py
"""
import os
import sys
import tempfile
import traceback
import zlib

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ATLAS_TESTING", "1")
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TMP = tempfile.mkdtemp(prefix="atlas_router_")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def trigram_embed(texts):
    """Подставная «модель смысла»: триграммы букв → вектор. Похожие слова — похожие векторы."""
    out = []
    for t in texts:
        v = np.zeros(1024, dtype=np.float32)
        s = f"  {t.lower().replace('ё', 'е')}  "
        for i in range(len(s) - 2):
            v[zlib.crc32(s[i:i + 3].encode()) % 1024] += 1.0
        out.append(v / max(1e-9, float(np.linalg.norm(v))))
    return np.array(out)


@test
def router_claims_priority_and_purity():
    from core import intent_router as R, messengers, scenarios, wellbeing, prompter
    messengers.FILE = os.path.join(TMP, "m.json")
    scenarios.FILE = os.path.join(TMP, "s.json")
    wellbeing.CFG = os.path.join(TMP, "wb.json")
    prompter.CFG = os.path.join(TMP, "pr.json")
    r = R.route("поставь хром слева, а ворд справа")
    assert r.label == "window_layout" and r.claims == ["window_layout"] and not r.ambiguous, r
    r = R.route("сделай конспект этой статьи")
    assert r.label == "link_notes" and "media_import" in r.claims and r.ambiguous, r
    assert R.route("включи музыку").label == "other"
    assert R.route("распознай текст с экрана", {"local": False}).label == "other", "с телефона — не трогаем экран"
    for t in ("следи за осанкой", "включи суфлёр", "игровой режим", "отчёт", "запиши: купить хлеб"):
        R.route(t)
    assert not os.path.exists(wellbeing.CFG) and not os.path.exists(prompter.CFG), "проверка не должна ничего менять"
    assert R.LABELS.index("meeting_notes") < R.LABELS.index("messengers") < R.LABELS.index("file_ops")
    assert R.OWNER["link_notes"] == "main" and R.OWNER["window_layout"] == "pc_fast" and R.OWNER["instant"] == "brain"
    assert R.route("распознай текст с экрана").ms < 50


@test
def pc_fast_falls_through_to_next_claim():
    from core import pc_fast, intent_router as R
    orig = R.route
    R.route = lambda t, c=None: R.Route(label="window_layout", claims=["window_layout", "where_saved"])
    calls = []
    orig_h = pc_fast._handlers

    def fake(*a, **k):
        return {"window_layout": lambda: calls.append("win") or None,
                "where_saved": lambda: calls.append("where") or "«отчёт.docx» — в Загрузках. Открыть?"}
    pc_fast._handlers = fake
    try:
        out = pc_fast.handle("поставь отчёт слева")
    finally:
        R.route, pc_fast._handlers = orig, orig_h
    assert calls == ["win", "where"] and out.startswith("«отчёт.docx»"), (calls, out)
    R.route = lambda t, c=None: R.Route(label="squeeze", claims=["squeeze"])
    try:
        assert pc_fast.handle("сожми это") is None, "фразы main — не трогаем, main выполнит сам"
    finally:
        R.route = orig


@test
def evals_gate_on_routing_set():
    from evals import run as E
    rows = E.load()
    assert len(rows) >= 150, len(rows)
    res = E.run_routing(rows)
    assert res["accuracy"] >= 98.0, E.report(res, {})
    para = E.run_routing(E.load(E.PARA))
    assert para["n"] >= 50 and 0 < para["accuracy"] <= 100
    txt = E.report(res, {"accuracy": 90.0})
    assert txt.startswith("Маршрутизатор:") and "Планка: 90.0%" in txt


@test
def semantic_rescues_paraphrase_safely():
    from core import semantic_router as S, intent_router as R
    S.LEARNED = os.path.join(TMP, "learned.json")
    S.set_embedder(trigram_embed)
    S.THRESHOLD, S.MARGIN = 0.35, 0.0
    try:
        g = S.guess("растяни телеграм на весь экран")
        assert g and g[0] == "window_layout", g
        asked = []

        def ask(system, user):
            asked.append(system)
            return "разверни телеграм"
        res = S.rescue("растяни телегу на весь экран, плиз", {}, ask=ask)
        assert res == ("разверни телеграм", "window_layout"), res
        assert "разверни телеграм" in asked[0] or "Rewrite" in asked[0]
        assert S.rescue("растяни телеграм на весь экран", {}, ask=lambda s, u: "NONE") is None, "модель сказала: не про это"
        assert S.rescue("растяни телеграм на весь экран", {}, ask=lambda s, u: "сделай что-нибудь хорошее") is None, \
            "стандартную команду правила не узнали — не выполняем"
        g = S.guess("включи музыку погромче")
        assert g is None, f"ближе к отрицательным примерам (other) — не наше: {g}"
    finally:
        S.set_embedder(None)
        S._s["embed"] = None


@test
def corrections_are_learned():
    from core import semantic_router as S, pc_fast
    import time
    S.LEARNED = os.path.join(TMP, "learned2.json")
    S.set_embedder(trigram_embed)
    try:
        assert S.correction("нет, я имел в виду поставь хром слева") == "поставь хром слева"
        assert S.correction("не то, сверни телеграм") == "сверни телеграм"
        assert S.correction("нет") == "" and S.correction("включи музыку") is None
        pc_fast.LAST.update(text="закинь хром в левую часть", route="file_ops", said="…", ts=time.time())
        out = pc_fast.prepare("нет, я имел в виду поставь хром слева")
        assert out == "поставь хром слева", out
        ex = dict((t, lab) for t, lab in S.examples())
        assert ex.get("закинь хром в левую часть") == "window_layout", "исправление запомнилось"
        assert not S.learn("что-то", "other")
        pc_fast.LAST.update(ts=0)
        assert pc_fast.prepare("нет, я имел в виду включи музыку") == "включи музыку"
    finally:
        S.set_embedder(None)
        S._s["embed"] = None


@test
def semantic_off_in_tests_and_by_env():
    from core import semantic_router as S
    S._s["embed"] = None
    assert not S.enabled(), "в тестах без своей модели — выключен (никаких скачиваний и запросов)"
    os.environ["ATLAS_TESTING"] = "0"
    os.environ["SEMANTIC_ROUTER"] = "off"
    try:
        assert not S.enabled()
    finally:
        os.environ["ATLAS_TESTING"] = "1"
        os.environ.pop("SEMANTIC_ROUTER", None)


@test
def wiring():
    src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert "pc_fast.prepare(command" in src
    ph = open(os.path.join(ROOT, "phone", "server.py"), encoding="utf-8").read()
    assert "pc_fast.prepare(text, local=False)" in ph
    gi = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
    assert ".atlas_backups/" in gi and "atlas_route_examples.json" in gi
    ci = open(os.path.join(ROOT, ".github", "workflows", "tests.yml"), encoding="utf-8").read()
    assert "tests/run_all.py" in ci and "evals.run" in ci


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
