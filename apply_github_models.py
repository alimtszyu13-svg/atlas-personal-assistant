"""
Запасной провайдер моделей — GitHub Models (бесплатно для любого аккаунта GitHub).

Модель по умолчанию — openai/gpt-4.1-mini: умеет вызывать инструменты, формат
запросов как у OpenAI. Бесплатные лимиты: около 15 запросов в минуту и 150 в день,
до 8 тысяч токенов на вход в одном запросе (запросы Atlas ужаты до 4500 — влезают).

Стоит в очереди ПОСЛЕ двух моделей Groq: когда обе заняты, запрос уходит сюда
вместо ожидания. Ошибка или лимит — Atlas сам вернётся к Groq (минутный лимит —
на 30 с, дневной — на час, другая ошибка — на 10 минут).

Нужен токен GitHub с доступом к Models (только чтение) в .env:
    GITHUB_MODELS_TOKEN=github_pat_...
Модель можно сменить:  GITHUB_MODELS_MODEL=openai/gpt-4.1-mini

Запуск из корня проекта:  python apply_github_models.py
"""
import ast
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(ROOT, "ai_brain.py")
if not os.path.exists(P):
    sys.exit("Запусти из корня проекта Atlas (рядом с ai_brain.py).")
src = open(P, encoding="utf-8").read()
MARK = "# === Провайдер GitHub Models (бесплатно) ==="
if MARK in src:
    print("  ✓ GitHub Models уже подключён — ничего не меняю.")
    sys.exit(0)
backup = os.path.join(ROOT, time.strftime("backup_ghmodels_%Y%m%d_%H%M%S"))
os.makedirs(backup, exist_ok=True)
shutil.copy2(P, os.path.join(backup, "ai_brain.py"))
print(f"Резервная копия: {backup}")

BLOCK = '''

''' + MARK + '''
GH_MODEL = "gh:" + (os.getenv("GITHUB_MODELS_MODEL") or "openai/gpt-4.1-mini")
gh_client = (OpenAI(api_key=os.getenv("GITHUB_MODELS_TOKEN"), base_url="https://models.github.ai/inference",
                    max_retries=0) if os.getenv("GITHUB_MODELS_TOKEN") else None)
_gh_down = {"until": 0.0}
try:
    llm_gateway.MODEL_TPM[GH_MODEL] = 100000
except Exception:
    pass
print(f"[github models] подключён ({GH_MODEL.split(':', 1)[1]}) — подменяет Groq, когда тот занят"
      if gh_client else "[github models] токена нет (GITHUB_MODELS_TOKEN) — работаю без него")

_client_for_prev_gh = globals().get("_client_for")


def _client_for(kwargs: dict):
    m = str(kwargs.get("model", ""))
    if m.startswith("gh:") and gh_client is not None:
        kw = {k: v for k, v in kwargs.items() if k != "reasoning_effort"}
        kw["model"] = m.split(":", 1)[1]
        kw.setdefault("max_tokens", 4000)          # лимит бесплатного тарифа на ответ
        return gh_client, kw
    return _client_for_prev_gh(kwargs) if _client_for_prev_gh else (client, kwargs)


_candidates_prev_gh = globals().get("_candidates")


def _candidates(model: str, other: str) -> list:
    base = list(_candidates_prev_gh(model, other)) if _candidates_prev_gh else [model, other]
    if gh_client is not None and time.time() >= _gh_down["until"] and GH_MODEL not in base:
        idx = [base.index(x) for x in (model, other) if x in base]
        base.insert(max(idx) + 1 if idx else len(base), GH_MODEL)    # после двух моделей Groq
    return base


_call_model_stream_prev_gh = _call_model_stream


def _call_model_stream(*args, **kwargs):
    if not str(kwargs.get("model", "")).startswith("gh:"):
        return _call_model_stream_prev_gh(*args, **kwargs)
    try:
        return _call_model_stream_prev_gh(*args, **kwargs)
    except TaskCancelled:
        raise
    except Exception as e:
        msg = str(e)
        if "attempted to call tool" in msg:
            raise
        low = msg.lower()
        daily = "per day" in low or "daily" in low or "UserByDay" in msg
        limit = daily or "429" in msg or "rate" in low
        pause = 3600 if daily else (30 if limit else 600)
        _gh_down["until"] = time.time() + pause
        print(f"[github models] {'дневной лимит' if daily else 'лимит' if limit else 'ошибка'} "
              f"({msg[:140]}) — {pause // 60 if pause >= 60 else pause} {'мин' if pause >= 60 else 'с'} работаю через Groq")
        kwargs = dict(kwargs)
        kwargs["model"] = MODEL_SMART
        return _call_model_stream_prev_gh(*args, **kwargs)
'''
src = src.rstrip() + "\n" + BLOCK
ast.parse(src, filename="ai_brain.py")
open(P, "w", encoding="utf-8", newline="\n").write(src)
print("  ✓ ai_brain.py: провайдер GitHub Models в пуле моделей")
try:
    has = "GITHUB_MODELS_TOKEN" in open(os.path.join(ROOT, ".env"), "rb").read().decode("utf-8", "ignore")
except OSError:
    has = False
if not has:
    print("\nДобавь в .env строку  GITHUB_MODELS_TOKEN=github_pat_...  (как получить — в сообщении в чате).")
print("\nГотово. Запускай: python main.py")
