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
MAX_REQUEST = 6000        # один запрос не больше этого — остальное остаётся на ответ
CHARS_PER_TOKEN = 4.0     # стартовая оценка; дальше подстраивается по реальному расходу

_lock = threading.Lock()
_windows = {}             # модель → deque[(время, токены)]


def estimate(obj) -> int:
    """Примерное число токенов в сообщениях/схеме/ответе."""
    return int(len(json.dumps(obj, ensure_ascii=False, default=str)) / CHARS_PER_TOKEN) + 1


def _used(win: deque, now: float) -> int:
    while win and now - win[0][0] > 60:
        win.popleft()
    return sum(t for _, t in win)


def reserve(model: str, tokens: int, cancel_check=None) -> None:
    """Ждёт, пока в минутном окне модели хватит места, и записывает расход."""
    budget = int(TPM_LIMIT * SAFETY)
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
            wait = 60 - (now - win[0][0]) + 0.3
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
    """Ужимает запрос до limit токенов. Сначала сокращает старые результаты
    инструментов, потом убирает самые старые реплики. Системный промпт
    и последние сообщения не трогает. Историю в памяти не меняет — работает с копией."""
    msgs = [dict(m) if isinstance(m, dict) else m for m in messages]
    tools_cost = estimate(tools)

    def total():
        return estimate(msgs) + tools_cost

    for m in msgs[1:-4]:
        if total() <= limit:
            break
        if isinstance(m, dict) and m.get("role") == "tool" and len(str(m.get("content", ""))) > 300:
            m["content"] = str(m["content"])[:300] + " …[сокращено]"

    dropped = 0
    while total() > limit and len(msgs) > 5:
        del msgs[1]
        dropped += 1
        # не оставляем результаты инструментов без вызова, который их породил
        while len(msgs) > 5 and isinstance(msgs[1], dict) and msgs[1].get("role") == "tool":
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
    add(model, total - reserved)          # поправка: в окне теперь реальный расход
    if prompt:
        CHARS_PER_TOKEN = 0.7 * CHARS_PER_TOKEN + 0.3 * (prompt_chars / prompt)
    print(f"[gateway] реально: {total} ток. (оценка {reserved}), chars/token → {CHARS_PER_TOKEN:.2f}")

def reserve_any(models: list, tokens: int, cancel_check=None) -> str:
    """Берёт первую модель из списка, у которой прямо сейчас есть место.
    Если места нет ни у одной — ждёт ту, что освободится раньше. Возвращает модель."""
    budget = int(TPM_LIMIT * SAFETY)
    with _lock:
        now = time.time()
        chosen = None
        for m in models:
            win = _windows.setdefault(m, deque())
            if not win or _used(win, now) + tokens <= budget:
                chosen = m
                break
        if chosen is None:
            chosen = min(models, key=lambda m: _windows[m][0][0])
    if chosen != models[0]:
        print(f"[gateway] {models[0].split('/')[-1]} занята — беру {chosen.split('/')[-1]}")
    reserve(chosen, tokens, cancel_check)
    return chosen