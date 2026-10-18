"""
Экономия и офлайн: счётчик расхода, предел в месяц, кэш служебных ответов, локальная модель без интернета.

    python tests/test_economy.py
"""
import os
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ATLAS_TESTING", "1")
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TMP = tempfile.mkdtemp(prefix="atlas_eco_")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def usage_counts_and_describes():
    from core import usage as U
    p = os.path.join(TMP, "u1.jsonl")
    U.record("groq", "openai/gpt-oss-120b", 1800, 200, 900, path=p)
    U.record("groq", "openai/gpt-oss-20b", 900, 100, 400, path=p)
    U.record("cerebras", "gpt-oss-120b", 0, 0, 50, ok=False, err="Error code: 429 rate limit", path=p)
    U.record("cache", "", kind="quick", cached=True, path=p)
    U.record("groq", "openai/gpt-oss-120b", 2000, 50, 300, path=p,
             cached_prompt=1600)                                   # кэш промпта: 1600 из 2000 входных
    s = U.summary(1, p)
    assert s["n"] == 5 and s["cached"] == 1 and s["usd"] == 0, s
    assert s["prompt_cached"] == 1600 and s["cache_hit"] == round(100 * 1600 / s["prompt_in"]), s
    from types import SimpleNamespace as _NS
    assert U.cached_prompt_tokens(_NS(prompt_tokens_details=_NS(cached_tokens=42))) == 42
    assert U.cached_prompt_tokens({"prompt_tokens_details": {"cached_tokens": 7}}) == 7
    assert U.cached_prompt_tokens(_NS(prompt_tokens=10)) == 0 and U.cached_prompt_tokens(None) == 0
    assert s["by"]["groq"]["n"] == 3 and s["by"]["cerebras"]["limits"] == 1
    d = U.describe(True, p)
    assert d.startswith("Сегодня 5 запросов к моделям (Groq 3") and "Из кэша — 1" in d and "лимиты упирался 1" in d, d
    assert "Кэш промпта" in d, d
    assert "0 $" in d
    assert U.provider_of("openai/gpt-oss-120b") == "groq" and U.provider_of("gem:gemini-3.8-flash") == "gem"
    assert U.provider_of("gh:openai/gpt-4.1-mini") == "gh" and U.provider_of("ollama:qwen2.5:7b") == "ollama"
    assert U.tokens_of({"prompt_tokens": 5, "completion_tokens": 7}) == (5, 7)
    assert U.tokens_of(NS(prompt_tokens=3, completion_tokens=4)) == (3, 4) and U.tokens_of(None) == (0, 0)
    U.record("groq", "x", 1, 1)                                  # без пути в тестах — в настоящий журнал не пишет
    assert not os.path.exists(U._DEFAULT_LOG) or "\"m\": \"x\"" not in open(U._DEFAULT_LOG, encoding="utf-8").read()


@test
def monthly_budget_warns_once_and_blocks_paid():
    from core import usage as U
    p = os.path.join(TMP, "u2.jsonl")
    os.environ["PRICE_GEM"] = "0.3"
    os.environ["SPEND_LIMIT_USD"] = "3"
    try:
        U._s["warned"] = ""
        for _ in range(9):
            U.record("gem", "gemini", 900_000, 100_000, 1000, path=p)     # 1 млн токенов × 0,3 $ = 0,30 $
        assert abs(U.month_spend(p) - 2.7) < 0.01 and not U.over_budget(p)
        w = U.budget_warning(p)
        assert w and "80 %" in w, w
        assert U.budget_warning(p) is None, "второй раз не повторяет"
        U.record("gem", "gemini", 1_000_000, 0, 1000, path=p)
        assert U.over_budget(p)
        w = U.budget_warning(p)
        assert w and "только бесплатные" in w, w
        assert "Потрачено за месяц 3.00 $ из 3" in U.describe(True, p)
    finally:
        os.environ.pop("PRICE_GEM", None)
        os.environ.pop("SPEND_LIMIT_USD", None)


@test
def quick_answers_cached():
    from core import llm_cache as C, quick_llm as Q
    C.DB = os.path.join(TMP, "cache.db")
    C.force(True)
    calls = []

    class Cl:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    calls.append(kw["model"])
                    return NS(choices=[NS(message=NS(content="Конспект: всё хорошо."))],
                              usage=NS(prompt_tokens=10, completion_tokens=5))
    orig = Q._clients
    Q._clients = lambda: [(Cl, "m1", {})]
    try:
        a = Q.ask("Сделай конспект", "длинный текст лекции")
        b = Q.ask("Сделай конспект", "длинный текст лекции")
        assert a == b == "Конспект: всё хорошо." and calls == ["m1"], calls
        Q.ask("Сделай конспект", "другой текст")
        assert calls == ["m1", "m1"]
        Q.ask("Сделай конспект", "длинный текст лекции", cache=False)
        assert len(calls) == 3, "cache=False — всегда свежий ответ"
        assert C.stats()["hits"] >= 1
        C.put("s", "t", 0, "старое", ttl=-1)
        assert C.get("s", "t", 0) is None, "просроченное не отдаём"
    finally:
        Q._clients = orig
        C.force(None)
    assert not C.enabled(), "в тестах по умолчанию выключен"


@test
def semantic_canonical_uses_cache():
    from core import llm_cache as C, semantic_router as S
    C.DB = os.path.join(TMP, "cache2.db")
    C.force(True)
    n = {"asked": 0}
    orig = S._ask_fast

    def fake(system, user):
        n["asked"] += 1
        return "разверни телеграм"
    S._ask_fast = fake
    try:
        assert S.canonical("растяни телегу", "window_layout") == "разверни телеграм"
        assert S.canonical("растяни телегу", "window_layout") == "разверни телеграм"
        assert n["asked"] == 1, n
    finally:
        S._ask_fast = orig
        C.force(None)


@test
def offline_falls_back_to_local_model():          # последним: подменяет модули ядра
    from tests import stubs
    stubs.install()
    from brain import providers as P, planner
    P.ollama_client, P.OLLAMA_NAME, P.OLLAMA = object(), "qwen2.5:7b", "ollama:qwen2.5:7b"
    P._ollama["ready"] = True
    P._net_down["until"] = 0
    try:
        assert P.local_ready() and P._candidates(P.MODEL_SMART, P.MODEL_FAST)[-1] == P.OLLAMA, "своя — последней"
        asked = []

        def fake(on_text, **kw):
            asked.append(kw["model"])
            if not kw["model"].startswith("ollama:"):
                raise RuntimeError("Connection error: getaddrinfo failed")
            on_text("Без интернета, но отвечаю.")
            return {"role": "assistant", "content": "Без интернета, но отвечаю."}, None
        orig = P._call_model_stream
        P._call_model_stream = fake
        try:
            resp, model, _dt = planner._ask_model(lambda d: None, [{"role": "user", "content": "привет"}], [],
                                                  P.MODEL_SMART, "low")
            assert model == P.OLLAMA and resp["content"] == "Без интернета, но отвечаю." and asked[-1] == P.OLLAMA, asked
            assert P._candidates(P.MODEL_SMART, P.MODEL_FAST) == [P.OLLAMA], "минуту сразу к своей модели"
            cl, kw = P._client_for({"model": P.OLLAMA, "messages": [], "reasoning_effort": "low"})
            assert cl is P.ollama_client and kw["model"] == "qwen2.5:7b" and "reasoning_effort" not in kw
        finally:
            P._call_model_stream = orig
        assert P.is_offline_error("Connection error.") and not P.is_offline_error("Error code: 429 rate limit")
        # месячный предел: платный провайдер уходит из списка
        os.environ["PRICE_GEM"] = "0.3"
        from core import usage as U
        orig_over = U.over_budget
        U.over_budget = lambda *a, **k: True
        P._net_down["until"] = 0
        P.gem_client = object()
        try:
            assert P.GEM_MODEL not in P._candidates(P.MODEL_SMART, P.MODEL_FAST)
        finally:
            U.over_budget = orig_over
            os.environ.pop("PRICE_GEM", None)
            P.gem_client = None
    finally:
        P.ollama_client, P._ollama["ready"], P._net_down["until"] = None, False, 0


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
