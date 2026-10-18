"""
Короткий запрос к модели для служебных задач: переписать текст диктовки, сделать конспект, ответить
по записи. Без инструментов и без истории разговора. Cerebras → Groq → Gemini — кто первым ответит.

    ask(system, text, max_tokens=800) → строка
"""
import re
import time


def _clients():
    from brain import providers as P
    if P.local_ready() and time.time() < P._net_down["until"]:
        return [(P.ollama_client, P.OLLAMA_NAME, {})]          # интернета нет — сразу своя модель
    out = []
    if getattr(P, "cerebras_client", None) is not None:
        out.append((P.cerebras_client, P.CEREBRAS_MODEL.split(":", 1)[1], {"reasoning_effort": "low"}))
    out.append((P.client, P.MODEL_SMART, {"reasoning_effort": "low"}))
    if getattr(P, "gem_client", None) is not None:
        out.append((P.gem_client, P.GEM_MODEL.split(":", 1)[1], {"reasoning_effort": "none"}))
    if P.local_ready():
        out.append((P.ollama_client, P.OLLAMA_NAME, {}))
    return out


def _provider(cl, model: str) -> str:
    try:
        from brain import providers as P
        return {id(P.client): "groq", id(getattr(P, "cerebras_client", None)): "cerebras",
                id(getattr(P, "gem_client", None)): "gem", id(getattr(P, "gh_client", None)): "gh",
                id(getattr(P, "ollama_client", None)): "ollama"}.get(id(cl), "groq")
    except Exception:
        return "groq"


def ask(system: str, text: str, max_tokens: int = 800, clients=None, cache: bool = True, ttl: float = None) -> str:
    """cache=True: тот же system+text в течение ttl (6 ч) — ответ из кэша, без запроса."""
    from core import llm_cache, usage
    use_cache = cache and clients is None
    if use_cache:
        hit = llm_cache.get(system, text, max_tokens)
        if hit:
            usage.record("cache", "", kind="quick", cached=True)
            return hit
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": text}]
    last = None
    for cl, model, extra in (clients if clients is not None else _clients()):
        prov = _provider(cl, model)
        t0 = time.time()
        try:
            r = cl.chat.completions.create(model=model, messages=msgs, max_tokens=max_tokens, temperature=0.3, **extra)
            out = re.sub(r"<think>.*?</think>", "", r.choices[0].message.content or "", flags=re.S).strip()
            if out:
                tin, tout = usage.tokens_of(getattr(r, "usage", None))
                usage.record(prov, model, tin or len(system + text) // 4, tout or len(out) // 4,
                             (time.time() - t0) * 1000, kind="quick")
                if use_cache:
                    llm_cache.put(system, text, max_tokens, out, **({"ttl": ttl} if ttl else {}))
                return out
        except Exception as e:
            last = e
            usage.record(prov, model, ms=(time.time() - t0) * 1000, ok=False, kind="quick", err=str(e))
            try:
                from brain import providers as P
                if prov != "ollama" and P.is_offline_error(e):
                    P.mark_offline()
            except Exception:
                pass
            print(f"[модель] {model}: {str(e)[:100]} — пробую следующую")
    raise RuntimeError(f"модель не ответила ({last})" if last else "модель вернула пустой ответ")
