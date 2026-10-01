"""
LLM-гейтвей: единая точка учёта токенов Groq.

- estimate() — оценка размера запроса до отправки.
- fit()      — ужимает запрос, если он не влезает в лимит (вместо ошибки 413).
- reserve()  — учёт расхода за последние 60 с по каждой модели; если лимит
               почти исчерпан — ждёт нужное время (вместо ошибки 429).
"""
import json
import threading
import time
from collections import deque

TPM_LIMIT = 8000          # токенов в минуту на модель (бесплатный тариф Groq)
SAFETY = 0.95             # оценка уже подстраивается по реальности — запас можно уменьшить
MAX_REQUEST = 4500        # один запрос не больше этого — остальное остаётся на ответ
CHARS_PER_TOKEN = 4.0     # стартовая оценка; дальше подстраивается по реальному расходу
MODEL_TPM = {"cerebras:gpt-oss-120b": 60000}   # у Cerebras запас намного больше


def _budget(model: str) -> int:
    return int(MODEL_TPM.get(model, TPM_LIMIT) * SAFETY)

MODEL_PENALTY = {}   # модель → секунды «цены»: медленную берём, только если быструю ждать дольше
_lock = threading.Lock()
_windows = {}             # модель → deque[(время, токены)]


def estimate(obj) -> int:
    """Примерное число токенов в сообщениях/схеме/ответе."""
    return int(len(json.dumps(obj, ensure_ascii=False, default=str)) / CHARS_PER_TOKEN) + 1


def _used(win: deque, now: float) -> int:
    """Расход за последние 60 с. Истёкшие записи удаляются, ГДЕ БЫ они ни стояли:
    отметка «занята N секунд» бывает старше соседних записей, и раньше она застревала."""
    if any(now - t > 60 for t, _ in win):
        keep = sorted((t, n) for t, n in win if now - t <= 60)
        win.clear()
        win.extend(keep)
    return sum(t for _, t in win)


def _wait_for(win: deque, now: float, tokens: int, budget: int) -> float:
    """Сколько секунд ждать, пока в окне освободится место именно под tokens:
    идём от старых записей к новым, пока не наберётся нужный объём."""
    used = _used(win, now)
    if not win or used + tokens <= budget:
        return 0.0
    need = used + tokens - budget
    freed = 0
    for t, n in sorted(win):                    # по времени, даже если записи добавлены не по порядку
        freed += n
        if freed >= need:
            return max(0.0, 60 - (now - t) + 0.3)
    return max(0.0, 60 - (now - win[-1][0]) + 0.3)


def plan(models: list, tokens: int):
    """Для каждой модели — сколько ждать под этот запрос; возвращает
    (модель, ожидание в секундах, свободное место в окне) с минимальным ожиданием.
    При равенстве предпочитается модель, стоящая в списке раньше."""
    budget = int(TPM_LIMIT * SAFETY)
    best, best_eff = None, 0.0
    with _lock:
        now = time.time()
        for m in models:
            win = _windows.setdefault(m, deque())
            budget = _budget(m)
            wait = _wait_for(win, now, tokens, budget)
            room = budget - _used(win, now)
            eff = wait + MODEL_PENALTY.get(m, 0.0)
            if best is None or eff < best_eff - 0.05:
                best, best_eff = (m, wait, room), eff
    return best


def reserve(model: str, tokens: int, cancel_check=None) -> None:
    """Ждёт, пока в минутном окне модели хватит места, и записывает расход."""
    budget = _budget(model)
    short = model.split("/")[-1]
    while True:
        with _lock:
            win = _windows.setdefault(model, deque())
            now = time.time()
            used = _used(win, now)
            if used + tokens <= budget or not win:
                win.append((now, tokens))
                print(f"[gateway] {short}: запрос ~{tokens} ток., за минуту {used + tokens}/{budget}")
                return
            wait = _wait_for(win, now, tokens, budget)   # точно под этот запрос
            wait = max(wait, 0.25)                        # никогда не крутимся вхолостую
        print(f"[gateway] {short}: лимит минуты ({used}/{budget}), жду {wait:.1f}с")
        end = time.time() + wait
        while time.time() < end:
            if cancel_check:
                cancel_check()          # F8 прерывает и ожидание
            time.sleep(0.2)


def add(model: str, tokens: int) -> None:
    """Дописать расход (ответ модели) в окно."""
    with _lock:
        _windows.setdefault(model, deque()).append((time.time(), tokens))


def fit(messages: list, tools: list, limit: int = MAX_REQUEST) -> list:
    """Ужимает запрос до limit токенов. Текущий вопрос пользователя и всё, что
    сделано по нему (вызовы инструментов, результаты), не удаляется никогда —
    иначе модель забывает, о чём её спросили. Порядок: сначала сокращаются
    старые результаты инструментов, потом удаляются реплики ДО текущего вопроса.
    Историю в памяти не меняет — работает с копией."""
    msgs = [dict(m) if isinstance(m, dict) else m for m in messages]
    tools_cost = estimate(tools)

    def total():
        return estimate(msgs) + tools_cost

    def role(m):
        return m.get("role") if isinstance(m, dict) else getattr(m, "role", None)

    def last_user():
        for i in range(len(msgs) - 1, -1, -1):
            if role(msgs[i]) == "user":
                return i
        return len(msgs)

    tool_idx = [i for i, m in enumerate(msgs) if role(m) == "tool"]
    for i in tool_idx[:-1]:                       # самый свежий результат — целиком
        if total() <= limit:
            break
        m = msgs[i]
        if isinstance(m, dict) and len(str(m.get("content", ""))) > 300:
            m["content"] = str(m["content"])[:300] + " …[сокращено]"

    dropped = 0
    while total() > limit and last_user() > 1:
        del msgs[1]
        dropped += 1
        # не оставляем результаты инструментов без вызова, который их породил
        while last_user() > 1 and role(msgs[1]) == "tool":
            del msgs[1]
            dropped += 1
    if dropped:
        print(f"[gateway] запрос ужат: убрано {dropped} старых сообщений, ~{total()} ток.")
    return msgs


def chars(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False, default=str))


def record(model: str, reserved: int, usage, prompt_chars: int, fallback: int) -> None:
    """Заменяет оценку реальным расходом из ответа Groq и подстраивает
    CHARS_PER_TOKEN, чтобы следующие оценки были точнее."""
    global CHARS_PER_TOKEN
    if usage is None:
        add(model, fallback)
        return
    get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
    total, prompt = get("total_tokens"), get("prompt_tokens")
    if not total:
        add(model, fallback)
        return
    details = get("prompt_tokens_details")
    cached = (details.get("cached_tokens") if isinstance(details, dict)
              else getattr(details, "cached_tokens", 0)) or 0
    add(model, total - cached - reserved)  # кэш Groq в лимит не входит — вычитаем
    if prompt:
        CHARS_PER_TOKEN = 0.7 * CHARS_PER_TOKEN + 0.3 * (prompt_chars / prompt)
    print(f"[gateway] реально: {total} ток. (из них кэш {cached}, в лимит {total - cached}; оценка {reserved}), chars/token → {CHARS_PER_TOKEN:.2f}")

def reserve_any(models: list, tokens: int, cancel_check=None) -> str:
    """Берёт первую модель из списка, у которой прямо сейчас есть место.
    Если места нет ни у одной — ждёт ту, что освободится раньше. Возвращает модель."""
    chosen = plan(models, tokens)[0]      # модель, где ждать меньше всего (часто — ноль)
    if chosen != models[0]:
        print(f"[gateway] {models[0].split('/')[-1]} занята — беру {chosen.split('/')[-1]}")
    reserve(chosen, tokens, cancel_check)
    return chosen

def busy(model: str, frac: float = 0.5) -> bool:
    """Занято ли больше frac минутного лимита — фоновые задачи тогда ждут."""
    with _lock:
        win = _windows.setdefault(model, deque())
        return _used(win, time.time()) > _budget(model) * frac


# ---------------------------------------------------------------------------
# Для панели «Система» в интерфейсе: расход за минуту и последний ответ
# ---------------------------------------------------------------------------
_last = {}
_record_original = record


def record(model, reserved, usage, prompt_chars, fallback):
    _record_original(model, reserved, usage, prompt_chars, fallback)
    total = None
    if usage is not None:
        total = usage.get("total_tokens") if isinstance(usage, dict) else getattr(usage, "total_tokens", None)
    _last.update(model=model.split("/")[-1], tokens=int(total or fallback), at=time.time())


def snapshot() -> dict:
    budget = int(TPM_LIMIT * SAFETY)
    with _lock:
        now = time.time()
        models = {m.split("/")[-1]: {"used": int(_used(w, now)), "budget": _budget(m)}
                  for m, w in _windows.items()}
    last = dict(_last)
    if last:
        last["ago"] = time.time() - last.pop("at")
    return {"models": models, "last": last}


def cooldown(model: str, seconds: float) -> None:
    """Groq сказал «попробуйте через N секунд» — считаем модель занятой ровно до этого момента."""
    with _lock:
        _windows.setdefault(model, deque()).append((time.time() - 60 + seconds, _budget(model)))
