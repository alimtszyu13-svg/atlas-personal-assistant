"""
Секундомер каждого ответа: где Atlas тратит время.

Один «ход» = от конца твоей фразы до последнего звука ответа. Отметки:
    speech_end   ты замолчал (VAD)            stt          текст готов
    route        разобрал, кто выполняет        first_token  модель написала первое слово
    first_audio  зазвучал ответ                 done         ответ закончен
В лог одна строка: «[ход] речь→текст 0,62 · разбор 0,01 · модель 0,85 · голос 0,40 · до звука 1,88 · всего 3,1»,
и запись в logs/turns.jsonl. «Насколько ты быстрый?» — средние за сегодня и за неделю.

    begin() / mark("stt") / note(key=value) / end() — потокобезопасно, ошибки никогда не мешают ответу.
"""
import json
import os
import re
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "turns.jsonl")
ORDER = ["speech_end", "stt", "route", "first_token", "first_audio", "done"]
NAMES_RU = {"stt": "речь→текст", "route": "разбор", "first_token": "модель", "first_audio": "голос"}
_lock = threading.Lock()
_cur = {"turn": None}


def begin(t0: float = None) -> dict:
    with _lock:
        _cur["turn"] = {"t0": t0 or time.time(), "marks": {}, "notes": {}}
        return _cur["turn"]


def mark(name: str, t: float = None) -> None:
    try:
        with _lock:
            tr = _cur["turn"]
            if tr is not None and name not in tr["marks"]:
                tr["marks"][name] = (t or time.time()) - tr["t0"]
    except Exception:
        pass


def note(**kw) -> None:
    try:
        with _lock:
            if _cur["turn"] is not None:
                _cur["turn"]["notes"].update(kw)
    except Exception:
        pass


def current():
    return _cur["turn"]


def summary(tr: dict) -> str:
    m = tr["marks"]
    parts, prev = [], 0.0
    for k in ORDER[1:5]:
        if k in m:
            parts.append(f"{NAMES_RU[k]} {m[k] - prev:.2f}".replace(".", ","))
            prev = m[k]
    if "first_audio" in m:
        parts.append(f"до звука {m['first_audio']:.2f}".replace(".", ","))
    if "done" in m:
        parts.append(f"всего {m['done']:.1f}".replace(".", ","))
    extra = " ".join(f"{k}={v}" for k, v in tr["notes"].items())
    return "[ход] " + " · ".join(parts) + (f"  ({extra})" if extra else "")


def end(path: str = None) -> str:
    """Закрыть ход: строка в лог и запись в turns.jsonl. → строка сводки ('' — хода не было)."""
    try:
        with _lock:
            tr = _cur["turn"]
            _cur["turn"] = None
        if tr is None or not tr["marks"]:
            return ""
        tr["marks"].setdefault("done", time.time() - tr["t0"])
        s = summary(tr)
        print(s)
        p = path or LOG
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": tr["t0"], "marks": {k: round(v, 3) for k, v in tr["marks"].items()},
                                "notes": tr["notes"]}, ensure_ascii=False) + "\n")
        return s
    except Exception:
        return ""


def stats(days: float = 1.0, path: str = None, now: float = None) -> dict:
    now = now or time.time()
    rows = []
    try:
        with open(path or LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("ts", 0) >= now - days * 86400:
                    rows.append(r)
    except Exception:
        return {"n": 0}

    def med(key):
        v = sorted(r["marks"][key] for r in rows if key in r["marks"])
        return v[len(v) // 2] if v else None

    fast = [r for r in rows if r.get("notes", {}).get("path") == "fast"]
    return {"n": len(rows), "first_audio": med("first_audio"), "stt": med("stt"), "first_token": med("first_token"),
            "done": med("done"), "fast_share": round(100 * len(fast) / len(rows)) if rows else 0,
            "spec_hits": sum(1 for r in rows if r.get("notes", {}).get("spec") == "hit")}


def describe(ru: bool = True) -> str:
    d, w = stats(1), stats(7)
    if not w.get("n"):
        return "Пока нечего считать — поговори со мной немного." if ru else "Nothing measured yet."
    def f(x):
        return f"{x:.1f}".replace(".", ",") if x is not None else "—"
    base = d if d.get("n") else w
    when = "сегодня" if d.get("n") else "за неделю"
    s = (f"{when.capitalize()} {base['n']} ответов: до первого звука в среднем {f(base['first_audio'])} с, "
         f"распознавание речи {f(base['stt'])} с, модель до первого слова {f(base['first_token'])} с. "
         f"Без модели (мгновенно) — {base['fast_share']} % команд.")
    if base.get("spec_hits"):
        s += f" Раннее распознавание сработало {base['spec_hits']} раз."
    return s if ru else s


_ASK = re.compile(r"(?:насколько|как)\s+(?:ты\s+)?быстр\w*|скорость (?:ответа|ответов|работы)|сколько ты (?:думаешь|отвечаешь)|"
                  r"how fast are you|your (?:speed|latency)", re.I)


def handle(text: str):
    return describe(bool(re.search(r"[а-яё]", text or "", re.I))) if _ASK.search(text or "") else None
