"""
Короткий запрос к модели для служебных задач: переписать текст диктовки, сделать конспект, ответить
по записи. Без инструментов и без истории разговора. Cerebras → Groq → Gemini — кто первым ответит.

    ask(system, text, max_tokens=800) → строка
"""
import re


def _clients():
    from brain import providers as P
    out = []
    if getattr(P, "cerebras_client", None) is not None:
        out.append((P.cerebras_client, P.CEREBRAS_MODEL.split(":", 1)[1], {"reasoning_effort": "low"}))
    out.append((P.client, P.MODEL_SMART, {"reasoning_effort": "low"}))
    if getattr(P, "gem_client", None) is not None:
        out.append((P.gem_client, P.GEM_MODEL.split(":", 1)[1], {"reasoning_effort": "none"}))
    return out


def ask(system: str, text: str, max_tokens: int = 800, clients=None) -> str:
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": text}]
    last = None
    for cl, model, extra in (clients if clients is not None else _clients()):
        try:
            r = cl.chat.completions.create(model=model, messages=msgs, max_tokens=max_tokens, temperature=0.3, **extra)
            out = re.sub(r"<think>.*?</think>", "", r.choices[0].message.content or "", flags=re.S).strip()
            if out:
                return out
        except Exception as e:
            last = e
            print(f"[модель] {model}: {str(e)[:100]} — пробую следующую")
    raise RuntimeError(f"модель не ответила ({last})" if last else "модель вернула пустой ответ")
