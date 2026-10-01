"""
GitHub Models отвечал пустотой → Atlas говорил «Done.» + «Восстановить страницы?» в браузере.

1) Пустые ответы GitHub Models. Потоковый режим у GitHub (Azure) отдаёт ответ
   иначе, чем Groq, и текст терялся. Теперь к GitHub Models запрос идёт БЕЗ
   потока — ответ приходит целиком (для модели gpt-4.1-mini это 1–2 секунды).
   Если ответ всё-таки пустой — в лог пишется причина, а запрос тут же уходит в Groq.
2) «Done.» вместо ответа больше не звучит: если модель вернула пустоту, Atlas
   говорит честно, что не получил ответа.
3) «Восстановить страницы? Работа Chromium была завершена некорректно» —
   Atlas закрывается быстро, и браузер не успевает отметить корректный выход.
   Теперь перед запуском браузера эта отметка ставится сама, а всплывашка
   восстановления отключена флагом запуска.

Запуск из корня проекта:  python apply_gh_fix.py
"""
import ast
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
FILES = ["ai_brain.py", "browser_agent.py"]
for f in FILES:
    if not os.path.exists(os.path.join(ROOT, f)):
        sys.exit(f"Не найден {f} — запусти из корня проекта Atlas.")
backup = os.path.join(ROOT, time.strftime("backup_ghfix_%Y%m%d_%H%M%S"))
os.makedirs(backup, exist_ok=True)
for f in FILES:
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


A = "ai_brain.py"
if "GH_MODEL" not in src[A]:
    sys.exit("В ai_brain.py нет подключения GitHub Models — сначала запусти apply_github_models.py.")
BLOCK = '''

# === GitHub Models без потока ===
# У GitHub (Azure) потоковый ответ устроен иначе, и текст терялся — запрос целиком.
def _gh_call(on_text, **kwargs):
    cl, kw = _client_for(kwargs)
    kw.pop("stream", None)
    r = cl.chat.completions.create(**kw)
    ch = r.choices[0] if getattr(r, "choices", None) else None
    if ch is None:
        raise RuntimeError("ответ без choices")
    m = ch.message
    content = (getattr(m, "content", None) or getattr(m, "refusal", None) or "").strip()
    calls = [{"id": tc.id, "type": "function",
              "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
             for tc in (getattr(m, "tool_calls", None) or [])]
    if not content and not calls:
        raise RuntimeError(f"пустой ответ (finish_reason={getattr(ch, 'finish_reason', '?')})")
    if content:
        on_text(content)
    msg = {"role": "assistant", "content": content or None}
    if calls:
        msg["tool_calls"] = calls
    return msg, getattr(r, "usage", None)


_call_model_stream_prev_ghfix = _call_model_stream


def _call_model_stream(*args, **kwargs):
    if not str(kwargs.get("model", "")).startswith("gh:") or gh_client is None:
        return _call_model_stream_prev_ghfix(*args, **kwargs)
    on_text = args[0] if args else kwargs.pop("on_text", lambda d: None)
    try:
        return _gh_call(on_text, **kwargs)
    except TaskCancelled:
        raise
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        daily = "per day" in low or "daily" in low or "UserByDay" in msg
        limit = daily or "429" in msg or "rate" in low
        pause = 3600 if daily else (30 if limit else 120)
        _gh_down["until"] = time.time() + pause
        print(f"[github models] {msg[:160]} — {pause} с работаю через Groq")
        kwargs = dict(kwargs)
        kwargs["model"] = MODEL_SMART
        return _call_model_stream_prev_ghfix(*args, **kwargs)
'''
if "# === GitHub Models без потока ===" in src[A]:
    report.append(f"  ✓ {A}: GitHub Models без потока (уже было)")
else:
    src[A] = src[A].rstrip() + "\n" + BLOCK
    report.append(f"  ✓ {A}: GitHub Models — ответ целиком, пустой ответ → Groq")
rep(A, '''                reply = message.content or "Done."''',
    '''                reply = message.content or ("Модель вернула пустой ответ — повторите, пожалуйста, сэр."
                                            if "(Respond in Russian.)" in question
                                            else "The model returned an empty answer — please say that again, sir.")''',
    "вместо «Done.» — честное сообщение о пустом ответе")

B = "browser_agent.py"
rep(B, "def _ensure_browser():", '''def _mark_clean_exit() -> None:
    """Atlas закрывается быстро, и Chromium не успевает отметить корректный выход —
    при следующем запуске он спрашивает «Восстановить страницы?». Ставим отметку сами."""
    p = os.path.join(NETFLIX_PROFILE_DIR, "Default", "Preferences")
    try:
        with open(p, encoding="utf-8") as f:
            prefs = json.load(f)
        prof = prefs.setdefault("profile", {})
        if prof.get("exit_type") != "Normal" or prof.get("exited_cleanly") is False:
            prof["exit_type"] = "Normal"
            prof["exited_cleanly"] = True
            with open(p, "w", encoding="utf-8") as f:
                json.dump(prefs, f)
    except Exception:
        pass


def _ensure_browser():''', "отметка корректного выхода перед запуском")
rep(B, "        _playwright = sync_playwright().start()\n",
    "        _mark_clean_exit()\n        _playwright = sync_playwright().start()\n", "отметка ставится перед каждым запуском")
rep(B, 'args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-infobars"],',
    'args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-infobars",\n'
    '                  "--hide-crash-restore-bubble", "--disable-session-crashed-bubble"],',
    "всплывашка «Восстановить страницы?» отключена")

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
