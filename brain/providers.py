"""
Модели: кто отвечает, в каком порядке и что делать при ошибке.

Раньше это были пять слоёв обёрток вокруг _client_for, _candidates и _call_model_stream,
дописанных в разное время. Здесь то же поведение, собранное в три функции:

    _candidates(model, other) — по каким моделям пробовать запрос и в каком порядке
    _client_for(kwargs)       — какой клиент и какие параметры нужны выбранной модели
    _call_model_stream(...)   — сам запрос, с починкой и запасным путём при ошибке

Порядок моделей: Cerebras (если есть ключ и он не на паузе) → две модели Groq →
Gemini → GitHub Models → запасные модели Groq. Шлюз (core/llm_gateway) по этому списку
выбирает ту, у которой есть место в минутном лимите; Gemini «дорогой» (отвечает долго),
поэтому берётся, только если остальных ждать дольше 15 с.
"""
import collections
import concurrent.futures
import os
import re
import threading
import time
from types import SimpleNamespace as _NS

from dotenv import load_dotenv
from openai import OpenAI

from core import llm_gateway
from brain.state import TaskCancelled, _check_cancel

load_dotenv()

# =============================================================================
# Клиенты
# =============================================================================
client = OpenAI(api_key=os.getenv("GROQ_API_KEY"), base_url="https://api.groq.com/openai/v1",
                max_retries=0)       # повторы и ожидания делает шлюз и наш цикл — прозрачно, с логом

MODEL_SMART = "openai/gpt-oss-120b"   # первый шаг — понять задачу и спланировать
MODEL_FAST = "openai/gpt-oss-20b"     # дальше — исполнять шаги
MODEL = MODEL_FAST                    # совместимость со старым кодом

CEREBRAS_MODEL = "cerebras:gpt-oss-120b"
cerebras_client = (OpenAI(api_key=os.getenv("CEREBRAS_API_KEY"), base_url="https://api.cerebras.ai/v1", max_retries=0)
                   if os.getenv("CEREBRAS_API_KEY") else None)
_cerebras_down = {"until": 0.0}
_cer_calls = collections.deque(maxlen=50)          # пробный тариф: 5 запросов в минуту
_cer_cool = {"until": 0.0}

GH_MODEL = "gh:" + (os.getenv("GITHUB_MODELS_MODEL") or "openai/gpt-4.1-mini")
gh_client = (OpenAI(api_key=os.getenv("GITHUB_MODELS_TOKEN"), base_url="https://models.github.ai/inference", max_retries=0)
             if os.getenv("GITHUB_MODELS_TOKEN") else None)
_gh_down = {"until": 0.0}

GEM_MODEL = "gem:" + (os.getenv("GEMINI_MODEL") or "gemini-3.8-flash")
gem_client = (OpenAI(api_key=os.getenv("GEMINI_API_KEY"),
                     base_url="https://generativelanguage.googleapis.com/v1beta/openai/", max_retries=0)
              if os.getenv("GEMINI_API_KEY") else None)
_gem_down = {"until": 0.0}
_GEM_EFFORT = {"v": os.getenv("GEMINI_EFFORT") or "none"}       # без размышлений — быстро
_GEM_DUMMY_SIG = {"google": {"thought_signature": "skip_thought_signature_validator"}}

EXTRA_GROQ = [("moonshotai/kimi-k2-instruct-0905", 10000), ("llama-3.3-70b-versatile", 12000)]
_extra_models = []

try:                                         # лимиты и «цена» для шлюза
    llm_gateway.MODEL_TPM[CEREBRAS_MODEL] = int(os.getenv("CEREBRAS_TPM") or 30000)
    llm_gateway.MODEL_TPM[GH_MODEL] = 100000
    llm_gateway.MODEL_TPM[GEM_MODEL] = 250000
    llm_gateway.MODEL_PENALTY[GEM_MODEL] = float(os.getenv("GEMINI_PENALTY") or 15.0)
except Exception:
    pass

print("[cerebras] подключён — лимиты Groq больше не узкое место" if cerebras_client
      else "[cerebras] ключа нет (CEREBRAS_API_KEY) — работаю только через Groq")
print(f"[github models] подключён ({GH_MODEL.split(':', 1)[1]}) — подменяет Groq, когда тот занят"
      if gh_client else "[github models] токена нет (GITHUB_MODELS_TOKEN) — работаю без него")
print(f"[gemini] подключён ({GEM_MODEL.split(':', 1)[1]}) — подменяет Groq, когда тот занят"
      if gem_client else "[gemini] ключа нет (GEMINI_API_KEY) — работаю без него")


def _detect_extra_models() -> None:
    """Запасные модели Groq берутся, только если есть на аккаунте (проверка в фоне при запуске)."""
    try:
        have = {m.id for m in client.models.list().data}
        for mid, tpm in EXTRA_GROQ:
            if mid in have and mid not in _extra_models:
                _extra_models.append(mid)
                llm_gateway.MODEL_TPM[mid] = tpm
        print(f"[модели] запасные модели Groq: {', '.join(m.split('/')[-1] for m in _extra_models) or 'на аккаунте нет'}")
    except Exception as e:
        print(f"[модели] список моделей Groq недоступен: {e}")


def start() -> None:
    threading.Thread(target=_detect_extra_models, daemon=True).start()


# =============================================================================
# Порядок моделей
# =============================================================================
def _insert_after(base: list, model: str, other: str, item: str) -> None:
    idx = [base.index(x) for x in (model, other) if x in base]
    base.insert(max(idx) + 1 if idx else len(base), item)


def _candidates(model: str, other: str) -> list:
    now = time.time()
    base = ([CEREBRAS_MODEL, model, other] if cerebras_client is not None and now >= _cerebras_down["until"]
            else [model, other])
    base += [m for m in _extra_models if m not in base]
    if gh_client is not None and now >= _gh_down["until"] and GH_MODEL not in base:
        _insert_after(base, model, other, GH_MODEL)
    if gem_client is not None and now >= _gem_down["until"] and GEM_MODEL not in base:
        _insert_after(base, model, other, GEM_MODEL)         # Gemini — сразу после Groq, перед GitHub
    if CEREBRAS_MODEL in base:
        # 5 запросов за минуту уже было — шлюз узнаёт, через сколько освободится место,
        # и сам решает: подождать пару секунд или взять другую модель
        rpm = int(os.getenv("CEREBRAS_RPM") or 5)
        recent = sorted(t for t in _cer_calls if now - t < 60)
        if len(recent) >= rpm:
            free_at = recent[-rpm] + 60
            if free_at > _cer_cool["until"]:
                llm_gateway.cooldown(CEREBRAS_MODEL, free_at - now)
                _cer_cool["until"] = free_at
    return base


# =============================================================================
# Клиент и параметры для выбранной модели
# =============================================================================
def _strip_gem_fields(msgs):
    """Для всех, кроме Gemini: убрать «подписи мысли» Gemini из истории."""
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
    """Для Gemini: системные сообщения — одним в начале; у каждого вызова инструмента есть подпись мысли."""
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
            m["tool_calls"] = [dict(tc, extra_content=tc.get("extra_content") or _GEM_DUMMY_SIG) for tc in m["tool_calls"]]
            if m.get("content") is None:
                m["content"] = ""
        out.append(m)
    return ([{"role": "system", "content": "\n\n".join(sys_parts)}] if sys_parts else []) + out


def _client_for(kwargs: dict):
    m = str(kwargs.get("model", ""))
    if m.startswith("gem:") and gem_client is not None:
        kw = {k: v for k, v in kwargs.items() if k != "reasoning_effort"}
        kw["model"] = m.split(":", 1)[1]
        kw["messages"] = _gem_messages(kwargs.get("messages"))
        kw["reasoning_effort"] = _GEM_EFFORT["v"]          # без размышлений: 1–2 с вместо 3–11 с
        return gem_client, kw
    if m.startswith("gh:") and gh_client is not None:
        cl = gh_client
        kw = {k: v for k, v in kwargs.items() if k != "reasoning_effort"}
        kw["model"] = m.split(":", 1)[1]
        kw.setdefault("max_tokens", 4000)                  # лимит бесплатного тарифа на ответ
    elif m.startswith("cerebras:") and cerebras_client is not None:
        cl, kw = cerebras_client, dict(kwargs, model=m.split(":", 1)[1])
    else:
        cl, kw = client, kwargs
    if "reasoning_effort" in kw and "gpt-oss" not in str(kw.get("model", "")):
        kw = {k: v for k, v in kw.items() if k != "reasoning_effort"}     # у kimi и llama такого параметра нет
    if isinstance(kw, dict) and kw.get("messages"):
        stripped = _strip_gem_fields(kw["messages"])
        if stripped is not kw["messages"]:
            kw = dict(kw, messages=stripped)
    return cl, kw


def _retry_after(err: str) -> float:
    """«try again in 7m12.5s» / «in 2.3s» / «in 1h2m» → секунды."""
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", err)
    if m and any(m.groups()):
        h, mi, s = (float(x) if x else 0.0 for x in m.groups())
        return h * 3600 + mi * 60 + s + 0.5
    return 2.0


# =============================================================================
# Запрос
# =============================================================================
def _stream_raw(on_text, **kwargs):
    """Потоковый запрос: текст отдаётся в on_text по мере генерации, вызовы инструментов
    собираются из кусков. Возвращает (сообщение-dict, расход токенов)."""
    cl, kw = _client_for(kwargs)
    stream = cl.chat.completions.create(stream=True, **kw)
    content, calls, usage = "", {}, None
    for chunk in stream:
        _check_cancel()
        u = getattr(chunk, "usage", None)          # Groq кладёт расход в последний кусок: usage или x_groq.usage
        if u is None:
            xg = (getattr(chunk, "model_extra", None) or {}).get("x_groq")
            u = xg.get("usage") if isinstance(xg, dict) else getattr(xg, "usage", None)
        if u is not None:
            usage = u
        if not chunk.choices:
            continue
        d = chunk.choices[0].delta
        if d.content:
            content += d.content
            on_text(d.content)
        for tc in (d.tool_calls or []):
            c = calls.setdefault(tc.index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            if tc.id:
                c["id"] = tc.id
            if tc.function and tc.function.name:
                c["function"]["name"] += tc.function.name
            if tc.function and tc.function.arguments:
                c["function"]["arguments"] += tc.function.arguments
    msg = {"role": "assistant", "content": content or None}
    if calls:
        for c in calls.values():
            c["function"]["arguments"] = c["function"]["arguments"] or "{}"
        msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    return msg, usage


_UNKNOWN_TOOL_RE = re.compile(r"attempted to call tool '([^']+)'")


def _stream_fixing(on_text, **kwargs):
    """Модель попросила инструмент, которого нет в наборе этого запроса: добавляем его,
    подсказываем или, в крайнем случае, отвечаем без инструментов."""
    from brain import tools
    fixes = 0
    while True:
        try:
            return _stream_raw(on_text, **kwargs)
        except Exception as e:
            m = _UNKNOWN_TOOL_RE.search(str(e))
            if not m or fixes >= 2 or not kwargs.get("tools"):
                raise
            fixes += 1
            raw = m.group(1)
            name = raw.split("<|")[0].strip()          # сбой разметки: 'search_web<|channel|>commentary'
            known = {t["function"]["name"] for t in tools.TOOLS_SCHEMA}
            if fixes == 1 and name in known:
                kwargs["tools"] = tools._with_tool(kwargs["tools"], name)
                print(f"[инструменты] модель попросила «{name}» — добавляю в набор и повторяю")
            elif fixes == 1:
                kwargs["messages"] = list(kwargs.get("messages") or []) + [{
                    "role": "system", "content": f"Tool '{raw}' does not exist. Use only the tools provided, "
                                                 f"or answer directly from what you already know."}]
                print(f"[инструменты] модель попросила несуществующий «{raw}» — подсказываю и повторяю")
            else:
                kwargs["tool_choice"] = "none"
                print("[инструменты] повтор не помог — отвечаю без инструментов, по уже известным данным")


def _whole_call(on_text, **kwargs):
    """Запрос целиком, без потока (GitHub и Gemini отдают поток иначе, и текст терялся)."""
    m = str(kwargs.get("model", ""))
    cl, kw = _client_for(kwargs)
    kw.pop("stream", None)
    r = None
    for _try in range(3):
        try:
            r = cl.chat.completions.create(**kw)
            break
        except Exception as e:
            bad = str(e).lower()
            nxt = {"none": "minimal", "minimal": "low"}.get(kw.get("reasoning_effort")) if m.startswith("gem:") else None
            if nxt and ("reasoning" in bad or "thinking" in bad or "400" in bad):
                print(f"[gemini] уровень размышлений «{kw['reasoning_effort']}» не принят — пробую «{nxt}»")
                _GEM_EFFORT["v"] = kw["reasoning_effort"] = nxt
                continue
            raise
    ch = r.choices[0] if getattr(r, "choices", None) else None
    if ch is None:
        raw = r if isinstance(r, str) else (getattr(r, "model_dump", lambda: r)())
        raise RuntimeError(f"ответ без choices: {str(raw)[:300]}")
    msg_obj = ch.message
    content = (getattr(msg_obj, "content", None) or getattr(msg_obj, "refusal", None) or "").strip()
    calls = []
    for tc in (getattr(msg_obj, "tool_calls", None) or []):
        c = {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
        extra = (getattr(tc, "model_extra", None) or {}).get("extra_content")
        if extra:
            c["extra_content"] = extra               # подпись мысли Gemini — вернём её в следующем шаге
        calls.append(c)
    if not content and not calls:
        raise RuntimeError(f"пустой ответ (finish_reason={getattr(ch, 'finish_reason', '?')})")
    if content:
        on_text(content)
    msg = {"role": "assistant", "content": content or None}
    if calls:
        msg["tool_calls"] = calls
    return msg, getattr(r, "usage", None)


def _pause_after(provider: str, err: Exception) -> float:
    """Сколько секунд не трогать провайдера после ошибки."""
    msg, low = str(err), str(err).lower()
    if provider == "cerebras":
        return 20 if ("429" in msg or "rate" in low or "quota" in low) else 120
    if provider == "gem":
        daily = "perday" in low.replace("_", "").replace(" ", "") or "per day" in low
        limit = daily or "429" in msg or "resource_exhausted" in low or "quota" in low
        return 3600 if daily else (60 if limit else 300)
    daily = "per day" in low or "daily" in low or "UserByDay" in msg          # GitHub
    limit = daily or "429" in msg or "rate" in low
    return 3600 if daily else (30 if limit else 120)


def _call_model_stream(on_text, **kwargs):
    """Запрос к выбранной модели. Ошибка у Cerebras / Gemini / GitHub → пауза для этого
    провайдера и тот же запрос через Groq."""
    m = str(kwargs.get("model", ""))
    if m.startswith("cerebras:"):
        _cer_calls.append(time.time())
    provider = ("gem" if m.startswith("gem:") and gem_client is not None else
                "gh" if m.startswith("gh:") and gh_client is not None else
                "cerebras" if m.startswith("cerebras:") else None)
    if provider is None:
        return _stream_fixing(on_text, **kwargs)
    try:
        return _whole_call(on_text, **kwargs) if provider in ("gem", "gh") else _stream_fixing(on_text, **kwargs)
    except TaskCancelled:
        raise
    except Exception as e:
        if provider == "cerebras" and "attempted to call tool" in str(e):
            raise
        pause = _pause_after(provider, e)
        {"gem": _gem_down, "gh": _gh_down, "cerebras": _cerebras_down}[provider]["until"] = time.time() + pause
        label = {"gem": "[gemini]", "gh": "[github models]", "cerebras": "[cerebras]"}[provider]
        print(f"{label} {str(e)[:160]} — {pause:.0f} с работаю через Groq")
        return _stream_fixing(on_text, **dict(kwargs, model=MODEL_SMART))


# --- запрос без потока, который можно бросить (для модулей, которым не нужен поток) ---
_model_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)


def _call_model(**kwargs):
    """Ждём ответ и каждые 0.2 с проверяем отмену."""
    fut = _model_pool.submit(client.chat.completions.create, **kwargs)
    while True:
        try:
            return fut.result(timeout=0.2)
        except concurrent.futures.TimeoutError:
            _check_cancel()


def _as_message(d: dict):
    """dict → объект с теми же полями, что у ответа SDK."""
    tcs = [_NS(id=t["id"], function=_NS(name=t["function"]["name"], arguments=t["function"]["arguments"]))
           for t in d.get("tool_calls", [])]
    return _NS(content=d.get("content"), tool_calls=tcs or None, model_dump=lambda: dict(d))
