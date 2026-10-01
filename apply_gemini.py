"""
Запасной провайдер моделей — Gemini от Google (бесплатный тариф).

Проверено на твоём ключе: gemini-3.8-flash отвечает (200). Модель умеет вызывать
инструменты. Стоит в очереди ПОСЛЕ двух моделей Groq: когда обе заняты, запрос
уходит в Gemini вместо ожидания.

Особенности Gemini 3, которые учтены:
  • «подпись мысли» (thought_signature) у каждого вызова инструмента сохраняется
    в истории и возвращается Gemini в следующих запросах — без неё он отказывает;
  • вызовам инструментов, которые сделал Groq (у них подписи нет), ставится
    служебная заглушка, которую Gemini принимает;
  • при отправке истории в Groq эти поля убираются — Groq лишних полей не любит;
  • системные сообщения сливаются в одно в начале;
  • запрос идёт целиком, без потока.
Лимит или ошибка — Atlas сам вернётся к Groq (лимит в минуту — 60 с,
дневной — час, другая ошибка — 5 минут).

Нужно в .env:  GEMINI_API_KEY=...
Модель можно сменить:  GEMINI_MODEL=gemini-3.8-flash

Запуск из корня проекта:  python apply_gemini.py
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
MARK = "# === Провайдер Gemini (бесплатный тариф Google) ==="
if MARK in src:
    print("  ✓ Gemini уже подключён — ничего не меняю.")
    sys.exit(0)
backup = os.path.join(ROOT, time.strftime("backup_gemini_%Y%m%d_%H%M%S"))
os.makedirs(backup, exist_ok=True)
shutil.copy2(P, os.path.join(backup, "ai_brain.py"))
print(f"Резервная копия: {backup}")

BLOCK = '''

''' + MARK + '''
GEM_MODEL = "gem:" + (os.getenv("GEMINI_MODEL") or "gemini-3.8-flash")
gem_client = (OpenAI(api_key=os.getenv("GEMINI_API_KEY"),
                     base_url="https://generativelanguage.googleapis.com/v1beta/openai/", max_retries=0)
              if os.getenv("GEMINI_API_KEY") else None)
_gem_down = {"until": 0.0}
_GEM_DUMMY_SIG = {"google": {"thought_signature": "skip_thought_signature_validator"}}
try:
    llm_gateway.MODEL_TPM[GEM_MODEL] = 250000
except Exception:
    pass
print(f"[gemini] подключён ({GEM_MODEL.split(':', 1)[1]}) — подменяет Groq, когда тот занят"
      if gem_client else "[gemini] ключа нет (GEMINI_API_KEY) — работаю без него")


def _strip_gem_fields(msgs):
    """Для Groq и остальных: убрать подписи мысли Gemini из истории."""
    if not any(isinstance(m, dict) and any(isinstance(tc, dict) and "extra_content" in tc
                                           for tc in (m.get("tool_calls") or [])) for m in (msgs or [])):
        return msgs
    out = []
    for m in msgs:
        if isinstance(m, dict) and m.get("tool_calls"):
            m = dict(m)
            m["tool_calls"] = [{k: v for k, v in tc.items() if k != "extra_content"} if isinstance(tc, dict) else tc
                               for tc in m["tool_calls"]]
        out.append(m)
    return out


def _gem_messages(msgs):
    """Для Gemini: системные — в одно в начале; у каждого вызова инструмента есть подпись мысли."""
    sys_parts, out = [], []
    for m in msgs or []:
        if not isinstance(m, dict):
            out.append(m)
            continue
        m = dict(m)
        if m.get("role") == "system":
            if m.get("content"):
                sys_parts.append(str(m["content"]))
            continue
        if m.get("role") == "assistant" and m.get("tool_calls"):
            m["tool_calls"] = [dict(tc, extra_content=tc.get("extra_content") or _GEM_DUMMY_SIG)
                               for tc in m["tool_calls"]]
            if m.get("content") is None:
                m["content"] = ""
        out.append(m)
    return ([{"role": "system", "content": "\\n\\n".join(sys_parts)}] if sys_parts else []) + out


_client_for_prev_gem = globals().get("_client_for")


def _client_for(kwargs: dict):
    m = str(kwargs.get("model", ""))
    if m.startswith("gem:") and gem_client is not None:
        kw = {k: v for k, v in kwargs.items() if k != "reasoning_effort"}
        kw["model"] = m.split(":", 1)[1]
        kw["messages"] = _gem_messages(kwargs.get("messages"))
        kw["reasoning_effort"] = "low"            # быстрее и дешевле по лимиту
        return gem_client, kw
    cl, kw = _client_for_prev_gem(kwargs) if _client_for_prev_gem else (client, kwargs)
    if isinstance(kw, dict) and kw.get("messages"):
        stripped = _strip_gem_fields(kw["messages"])
        if stripped is not kw["messages"]:
            kw = dict(kw, messages=stripped)
    return cl, kw


_candidates_prev_gem = globals().get("_candidates")


def _candidates(model: str, other: str) -> list:
    base = list(_candidates_prev_gem(model, other)) if _candidates_prev_gem else [model, other]
    if gem_client is not None and time.time() >= _gem_down["until"] and GEM_MODEL not in base:
        idx = [base.index(x) for x in (model, other) if x in base]
        base.insert(max(idx) + 1 if idx else len(base), GEM_MODEL)     # сразу после двух моделей Groq
    return base


def _gem_call(on_text, **kwargs):
    cl, kw = _client_for(kwargs)
    kw.pop("stream", None)
    r = cl.chat.completions.create(**kw)
    ch = r.choices[0] if getattr(r, "choices", None) else None
    if ch is None:
        raise RuntimeError(f"ответ без choices: {str(r)[:200]}")
    m = ch.message
    content = (getattr(m, "content", None) or "").strip()
    calls = []
    for tc in (getattr(m, "tool_calls", None) or []):
        extra = (getattr(tc, "model_extra", None) or {}).get("extra_content")
        c = {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
        if extra:
            c["extra_content"] = extra               # подпись мысли — вернём её Gemini в следующем шаге
        calls.append(c)
    if not content and not calls:
        raise RuntimeError(f"пустой ответ (finish_reason={getattr(ch, 'finish_reason', '?')})")
    if content:
        on_text(content)
    msg = {"role": "assistant", "content": content or None}
    if calls:
        msg["tool_calls"] = calls
    return msg, getattr(r, "usage", None)


_call_model_stream_prev_gem = _call_model_stream


def _call_model_stream(*args, **kwargs):
    if not str(kwargs.get("model", "")).startswith("gem:") or gem_client is None:
        return _call_model_stream_prev_gem(*args, **kwargs)
    on_text = args[0] if args else kwargs.pop("on_text", lambda d: None)
    try:
        return _gem_call(on_text, **kwargs)
    except TaskCancelled:
        raise
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        daily = "perday" in low.replace("_", "").replace(" ", "") or "per day" in low
        limit = daily or "429" in msg or "resource_exhausted" in low or "quota" in low
        pause = 3600 if daily else (60 if limit else 300)
        _gem_down["until"] = time.time() + pause
        print(f"[gemini] {msg[:160]} — {pause} с работаю через Groq")
        kwargs = dict(kwargs)
        kwargs["model"] = MODEL_SMART
        return _call_model_stream_prev_gem(*args, **kwargs)
'''
src = src.rstrip() + "\n" + BLOCK
ast.parse(src, filename="ai_brain.py")
open(P, "w", encoding="utf-8", newline="\n").write(src)
print("  ✓ ai_brain.py: провайдер Gemini в пуле моделей")
print("\nГотово. Запускай: python main.py")
