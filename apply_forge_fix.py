"""
Мастерская навыков + диагностика GitHub Models.

1) Навык «курс валют» не прошёл тесты: модель выбирала API, которые требуют ключ
   или переехали (httpx по умолчанию НЕ идёт по перенаправлениям — ответ 301
   выглядел как «неуспешный»). Теперь:
     • httpx.get(..., follow_redirects=True) — требование в инструкции;
     • модели подсказан список проверенных бесплатных сервисов без ключей:
       курсы валют (в том числе сом) — open.er-api.com, погода — open-meteo,
       Википедия, время, страны и т.д.;
     • перед тестами модель видит, какой именно ответ пришёл от сервиса.
2) Мастерская мешала разговору: писала навык в фоне и съедала минутный лимит,
   пока ты задавал вопросы (паузы по 30–50 с). Теперь она ждёт тишины —
   пока Atlas не занят и лимит свободен — и пользуется всем пулем моделей.
3) GitHub Models: «ответ без choices». В лог теперь пишется, что именно пришло,
   чтобы найти причину.

Запуск из корня проекта:  python apply_forge_fix.py
"""
import ast
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
FILES = ["core/skill_forge.py", "ai_brain.py"]
for f in FILES:
    if not os.path.exists(os.path.join(ROOT, f)):
        sys.exit(f"Не найден {f} — запусти из корня проекта Atlas.")
backup = os.path.join(ROOT, time.strftime("backup_forgefix_%Y%m%d_%H%M%S"))
for f in FILES:
    os.makedirs(os.path.dirname(os.path.join(backup, f)), exist_ok=True)
    shutil.copy2(os.path.join(ROOT, f), os.path.join(backup, f))
print(f"Резервная копия: {backup}")
src = {f: open(os.path.join(ROOT, f), encoding="utf-8").read() for f in FILES}
report = []


def rep(f, old, new, what):
    s = src[f]
    if new in s:
        report.append(f"  ✓ {f}: {what} (уже было)")
    elif s.count(old) == 1:
        src[f] = s.replace(old, new, 1)
        report.append(f"  ✓ {f}: {what}")
    else:
        report.append(f"  ! {f}: {what} — фрагмент не найден, пропущено")


F = "core/skill_forge.py"
rep(F, '''    ". No file, process, OS or shell access; no eval/exec; network ONLY via httpx.get(url, timeout=10) to "
    "free public APIs without keys. ''', '''    ". No file, process, OS or shell access; no eval/exec; network ONLY via "
    "httpx.get(url, timeout=10, follow_redirects=True) to free public APIs WITHOUT keys — check the HTTP status "
    "and JSON fields you rely on. Known good keyless APIs: currency rates incl. KGS/RUB/KZT — "
    "https://open.er-api.com/v6/latest/USD (JSON: result, rates{CODE: rate}); weather — "
    "https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&current=temperature_2m; geocoding — "
    "https://geocoding-api.open-meteo.com/v1/search?name=..; Wikipedia summary — "
    "https://ru.wikipedia.org/api/rest_v1/page/summary/<title>; countries — https://restcountries.com/v3.1/name/<name>; "
    "public holidays — https://date.nager.at/api/v3/PublicHolidays/<year>/<CC>. Prefer these over APIs that need keys. ''',
    "проверенные бесплатные сервисы и переходы по ссылкам")
rep(F, '''def _ask(messages: list) -> dict:
    from ai_brain import client, MODEL_SMART, MODEL_FAST
    from core import llm_gateway
    est = sum(len(m["content"]) for m in messages) // 3 + 2500
    model = llm_gateway.reserve_any([MODEL_SMART, MODEL_FAST], est)
    kw = dict(model=model, messages=messages, max_tokens=3000, response_format={"type": "json_object"})
    if "gpt-oss" in model:
        kw["reasoning_effort"] = "medium"
    r = client.chat.completions.create(**kw)
    raw = r.choices[0].message.content or ""''', '''def _wait_quiet(max_wait: float = 300) -> None:
    """Мастерская работает в тишине: пока Atlas не занят разговором и минутный лимит не забит."""
    try:
        from ui_state import shared_state
        from ai_brain import MODEL_SMART
        from core import llm_gateway
    except Exception:
        return
    t0 = time.time()
    while time.time() - t0 < max_wait:
        if shared_state.get("state") == "idle" and not llm_gateway.busy(MODEL_SMART, 0.4):
            return
        time.sleep(3)


def _ask(messages: list) -> dict:
    import ai_brain as _ab
    from ai_brain import client, MODEL_SMART, MODEL_FAST
    from core import llm_gateway
    est = sum(len(m["content"]) for m in messages) // 3 + 2500
    cands = _ab._candidates(MODEL_SMART, MODEL_FAST) if hasattr(_ab, "_candidates") else [MODEL_SMART, MODEL_FAST]
    model = llm_gateway.reserve_any(cands, est)
    kw = dict(model=model, messages=messages, max_tokens=3000, response_format={"type": "json_object"})
    if "gpt-oss" in model:
        kw["reasoning_effort"] = "medium"
    try:
        cl, kw2 = _ab._client_for(kw) if hasattr(_ab, "_client_for") else (client, kw)
        r = cl.chat.completions.create(**kw2)
        raw = r.choices[0].message.content or ""
    except Exception as e:
        if model == MODEL_FAST:
            raise
        print(f"[навыки] {model} не ответил ({str(e)[:100]}) — пробую {MODEL_FAST.split('/')[-1]}")
        llm_gateway.reserve(MODEL_FAST, est)
        r = client.chat.completions.create(**dict(kw, model=MODEL_FAST, reasoning_effort="medium"))
        raw = r.choices[0].message.content or ""''', "мастерская пользуется всем пулом моделей")
rep(F, '''    for rnd in range(MAX_FIX_ROUNDS + 1):
        data = _ask(msgs)''', '''    for rnd in range(MAX_FIX_ROUNDS + 1):
        _wait_quiet()                           # не отнимаем лимит у разговора
        data = _ask(msgs)''', "мастерская ждёт тишины перед каждым шагом")

A = "ai_brain.py"
rep(A, '''    if ch is None:
        raise RuntimeError("ответ без choices")''', '''    if ch is None:
        raw = r if isinstance(r, str) else (getattr(r, "model_dump", lambda: r)())
        raise RuntimeError(f"ответ без choices: {str(raw)[:300]}")''', "диагностика ответа GitHub Models")

ok = True
for f in FILES:
    try:
        ast.parse(src[f], filename=f)
    except SyntaxError as e:
        ok = False
        report.append(f"  ! {f}: {e} — файл НЕ изменён")
        continue
    open(os.path.join(ROOT, f), "w", encoding="utf-8", newline="\n").write(src[f])
    report.append(f"  ✓ синтаксис {f}")
print("\n".join(report))
print("\nГотово. Запускай: python main.py" if ok else f"\nЕсть ошибка — пришли вывод (копия: {backup}).")
