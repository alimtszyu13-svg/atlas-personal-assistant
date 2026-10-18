"""
Счётчик расхода: сколько запросов и токенов Atlas тратит у каждого провайдера, где упирается в лимиты,
сколько ответов взято из кэша и сколько это стоит.

    record("groq", "openai/gpt-oss-120b", tokens_in=1800, tokens_out=120, ms=900)
    «сколько ты потратил сегодня?» / «сколько запросов к моделям?» → короткая сводка

Бесплатные тарифы (Groq, Cerebras, Gemini, GitHub Models, Ollama) стоят 0. Если у тебя платный ключ —
цена за миллион токенов в .env: PRICE_GROQ=0.15, PRICE_GEMINI=0.3 … (вход и выход считаются вместе).
Для голоса: PRICE_FISH_PER_1K_CHARS. Предел в месяц: SPEND_LIMIT_USD (по умолчанию 3) — при 80 % Atlas
предупредит один раз, при 100 % — перейдёт на бесплатные модели и скажет об этом.
"""
import json
import os
import re
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "usage.jsonl")
_DEFAULT_LOG = LOG
_lock = threading.Lock()
_s = {"warned": "", "rows": None}


def provider_of(model: str) -> str:
    m = str(model or "")
    if ":" in m.split("/")[0]:
        return m.split(":", 1)[0]
    return "groq"


def price(provider: str) -> float:
    try:
        return float(os.getenv(f"PRICE_{provider.upper()}") or 0)
    except ValueError:
        return 0.0


def record(provider: str, model: str = "", tokens_in: int = 0, tokens_out: int = 0, ms: float = 0, ok: bool = True,
           kind: str = "chat", cached: bool = False, chars: int = 0, err: str = "", path: str = None,
           cached_prompt: int = 0) -> dict:
    cost = 0.0
    if not cached:
        cost = (tokens_in + tokens_out) / 1e6 * price(provider)
        if kind == "tts" and chars:
            try:
                cost += chars / 1000 * float(os.getenv("PRICE_FISH_PER_1K_CHARS") or 0)
            except ValueError:
                pass
    row = {"ts": time.time(), "p": provider, "m": str(model).split("/")[-1], "in": int(tokens_in or 0),
           "out": int(tokens_out or 0), "ms": int(ms or 0), "ok": bool(ok), "k": kind, "c": bool(cached),
           "ch": int(chars or 0), "usd": round(cost, 6), "pc": int(cached_prompt or 0)}
    if err:
        row["err"] = "limit" if re.search(r"429|rate|quota|limit", err, re.I) else "offline" if re.search(
            r"connect|getaddrinfo|timed? ?out|unreachable|name resolution", err, re.I) else "error"
    if path is None and LOG == _DEFAULT_LOG and os.getenv("ATLAS_TESTING") == "1":
        return row                                      # тесты не пишут в твой настоящий журнал
    try:
        with _lock:
            p = path or LOG
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return row


def tokens_of(usage) -> tuple:
    """usage из ответа SDK (объект или dict) → (вход, выход)."""
    if usage is None:
        return 0, 0
    g = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
    return int(g("prompt_tokens") or g("input_tokens") or 0), int(g("completion_tokens") or g("output_tokens") or 0)


def cached_prompt_tokens(usage) -> int:
    """Сколько токенов промпта Groq/OpenAI взяли из кэша (prompt_tokens_details.cached_tokens)."""
    if usage is None:
        return 0
    det = usage.get("prompt_tokens_details") if isinstance(usage, dict) else getattr(usage, "prompt_tokens_details", None)
    if det is None:
        return 0
    return int((det.get("cached_tokens") if isinstance(det, dict) else getattr(det, "cached_tokens", 0)) or 0)


def rows(days: float = 1.0, path: str = None, now: float = None) -> list:
    now = now or time.time()
    out = []
    try:
        with open(path or LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("ts", 0) >= now - days * 86400:
                    out.append(r)
    except Exception:
        pass
    return out


def summary(days: float = 1.0, path: str = None, now: float = None) -> dict:
    rs = rows(days, path, now)
    by = {}
    for r in rs:
        b = by.setdefault(r["p"], {"n": 0, "tokens": 0, "usd": 0.0, "limits": 0, "errors": 0, "cached": 0})
        b["n"] += 1
        b["tokens"] += r.get("in", 0) + r.get("out", 0)
        b["usd"] += r.get("usd", 0)
        b["cached"] += 1 if r.get("c") else 0
        if r.get("err") == "limit":
            b["limits"] += 1
        elif r.get("err"):
            b["errors"] += 1
    pin = sum(r.get("in", 0) for r in rs)
    pc = sum(r.get("pc", 0) for r in rs)
    return {"n": len(rs), "cached": sum(1 for r in rs if r.get("c")), "usd": round(sum(r.get("usd", 0) for r in rs), 4),
            "tokens": sum(r.get("in", 0) + r.get("out", 0) for r in rs), "by": by,
            "prompt_in": pin, "prompt_cached": pc, "cache_hit": round(100 * pc / pin) if pin else 0}


def month_spend(path: str = None, now: float = None) -> float:
    now = now or time.time()
    lt = time.localtime(now)
    start = time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    return round(sum(r.get("usd", 0) for r in rows((now - start) / 86400 + 0.001, path, now)), 4)


def limit_usd() -> float:
    try:
        return float(os.getenv("SPEND_LIMIT_USD") or 3)
    except ValueError:
        return 3.0


def over_budget(path: str = None, now: float = None) -> bool:
    """Платные модели — только пока месячный расход меньше предела."""
    return month_spend(path, now) >= limit_usd()


def budget_warning(path: str = None, now: float = None):
    """Один раз за месяц на 80 % и один раз на 100 % → текст для голоса или None."""
    if not any(k.startswith("PRICE_") and (v or "0").strip() not in ("", "0", "0.0") for k, v in os.environ.items()):
        return None                                     # все тарифы бесплатные — журнал даже не читаем
    spent, lim = month_spend(path, now), limit_usd()
    if lim <= 0 or spent <= 0:
        return None
    month = time.strftime("%Y-%m", time.localtime(now or time.time()))
    lvl = "100" if spent >= lim else "80" if spent >= 0.8 * lim else ""
    key = f"{month}:{lvl}"
    if not lvl or _s["warned"] == key or (lvl == "80" and _s["warned"] == f"{month}:100"):
        return None
    _s["warned"] = key
    if lvl == "100":
        return f"Сэр, расход на модели за месяц дошёл до {spent:.2f} $ из {lim:.0f} — дальше только бесплатные модели."
    return f"Сэр, за месяц на модели ушло {spent:.2f} $ — это 80 % от вашего предела в {lim:.0f} $."


_NAMES = {"groq": "Groq", "cerebras": "Cerebras", "gem": "Gemini", "gh": "GitHub", "ollama": "локальная модель",
          "fish": "голос Fish", "stt": "распознавание"}


def describe(ru: bool = True, path: str = None, now: float = None) -> str:
    d, m = summary(1, path, now), month_spend(path, now)
    if not d["n"]:
        return "Сегодня я ещё не обращался к моделям." if ru else "No model calls today yet."
    parts = sorted(d["by"].items(), key=lambda kv: -kv[1]["n"])
    who = ", ".join(f"{_NAMES.get(p, p)} {b['n']}" for p, b in parts[:4])
    s = (f"Сегодня {d['n']} запросов к моделям ({who}), около {d['tokens'] // 1000} тысяч токенов. "
         if ru else f"Today {d['n']} model calls ({who}), about {d['tokens'] // 1000}k tokens. ")
    if d["cached"]:
        s += f"Из кэша — {d['cached']}. " if ru else f"{d['cached']} from cache. "
    if d.get("cache_hit"):
        s += f"Кэш промпта — {d['cache_hit']} %. " if ru else f"Prompt cache hit {d['cache_hit']}%. "
    lim = sum(b["limits"] for b in d["by"].values())
    if lim:
        s += f"В лимиты упирался {lim} раз. " if ru else f"Hit rate limits {lim} times. "
    s += (f"Потрачено за месяц {m:.2f} $ из {limit_usd():.0f}." if m else "Всё на бесплатных тарифах — 0 $.") if ru else \
        (f"Spent this month: ${m:.2f} of {limit_usd():.0f}." if m else "All on free tiers — $0.")
    return s


_ASK = re.compile(r"сколько (?:ты )?(?:потратил|тратишь|стоишь|стоит твоя работа|запросов|токенов)|"
                  r"(?:расход|траты) (?:на )?(?:модел|ии|токен)|лимиты? (?:моделей|groq|грок)|"
                  r"how much (?:did you|do you) (?:spend|cost)|how many (?:requests|tokens)|token usage", re.I)


def handle(text: str):
    return describe(bool(re.search(r"[а-яё]", text or "", re.I))) if _ASK.search(text or "") else None
