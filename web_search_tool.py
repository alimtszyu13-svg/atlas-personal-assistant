"""
Поиск в интернете без ключей (DuckDuckGo и другие через ddgs).

Быстрее, чем раньше (было 3–5 с):
  • несколько поисковиков спрашиваются параллельно — берётся тот, кто ответил первым;
  • тайм-аут 4 с: медленный поисковик не держит ответ;
  • тот же запрос в течение 10 минут — из памяти, мгновенно.
В ответе для модели есть адреса страниц: если нужно прочитать страницу (read_webpage), модель берёт
настоящий адрес, а не придумывает его (отсюда были ошибки 404).
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait

BACKENDS = ("duckduckgo", "bing", "brave")
TIMEOUT = 4.0
CACHE_S = 600
_cache, _lock = {}, threading.Lock()
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="web-search")


def _one(query: str, n: int, backend: str):
    from ddgs import DDGS
    try:
        with DDGS(timeout=TIMEOUT) as d:
            return list(d.text(query, max_results=n, backend=backend))
    except TypeError:                                  # старая версия ddgs — без выбора поисковика
        with DDGS() as d:
            return list(d.text(query, max_results=n))


def _race(query: str, n: int, backends=BACKENDS, one=None) -> list:
    """Первый непустой ответ из параллельных поисковиков."""
    one = one or _one
    futs = {_pool.submit(one, query, n, b): b for b in backends}
    deadline = time.time() + TIMEOUT + 1
    pending = set(futs)
    errors = []
    while pending and time.time() < deadline:
        done, pending = wait(pending, timeout=max(0.05, deadline - time.time()), return_when=FIRST_COMPLETED)
        for f in done:
            try:
                res = f.result()
            except Exception as e:
                errors.append(f"{futs[f]}: {e}")
                continue
            if res:
                return res
    if errors and not pending:
        raise RuntimeError("; ".join(errors)[:300])
    return []


def search_web(query: str, num_results: int = 3, one=None) -> str:
    """Ищет в интернете и возвращает короткую сводку первых результатов (заголовок, суть, адрес)."""
    key = (query.strip().lower(), num_results)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_S:
            return hit[1]
    try:
        results = _race(query, num_results, one=one)
    except Exception as e:
        print(f"[Ошибка search_web]: {e}")
        return "Couldn't search the web right now, there might be a connection issue."
    if not results:
        return f"No results found for '{query}'."
    parts = []
    for r in results[:num_results]:
        title, body, url = r.get("title", ""), r.get("body", ""), r.get("href") or r.get("url") or ""
        parts.append(f"{title}: {body}" + (f" ({url})" if url else ""))
    out = " | ".join(parts)
    with _lock:
        _cache[key] = (time.time(), out)
        if len(_cache) > 200:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:50]:
                _cache.pop(k, None)
    return out
