"""
Смысловой слой маршрутизатора: Atlas понимает перефразировки, под которые нет правил.

    «растяни телегу на весь экран»  → по смыслу похоже на «разверни телеграм» (окна)
                                     → быстрая модель переписывает в стандартную команду
                                     → обычный быстрый путь выполняет её мгновенно

Как:
  1. Примеры фраз для каждой функции: evals/routing.tsv + выученные исправления (atlas_route_examples.json).
  2. Фраза → вектор смысла (та же MiniLM, что в поиске файлов и выборе инструментов, локально, ~10 мс).
  3. Ближайшие примеры: если сходство ≥ THRESHOLD и лидер явно впереди — кандидат найден.
  4. Быстрая модель (gpt-oss-20b, ~0,3 с) получает фразу и 6 примеров этой функции и пишет стандартную
     команду — или NONE, если фраза не про это. Стандартная команда снова идёт через правила: выполняется,
     только если правило её узнало. Так смысловой слой не может «выдумать» действие.

Обучение на исправлениях: Atlas выполнил фразу не так, ты сразу говоришь «нет, я имел в виду …» —
пара «фраза → правильная функция» запоминается и в следующий раз находится по смыслу.
SEMANTIC_ROUTER=off в .env — выключить.
"""
import json
import os
import re
import threading
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEARNED = os.path.join(ROOT, "atlas_route_examples.json")
DATA = os.path.join(ROOT, "evals", "routing.tsv")
THRESHOLD = 0.62
MARGIN = 0.04
K = 5
_s = {"labels": None, "texts": None, "mat": None, "embed": None, "dirty": True}
_lock = threading.Lock()
_SKIP = {"pending_disk", "pending_proofread", "pending_where", "call_secretary", "scenarios"}
_NO_RESCUE = {"other", "instant"}          # «other» — отрицательные примеры: ближе к ним — фраза не наша


def enabled() -> bool:
    if os.getenv("ATLAS_TESTING") == "1" and _s.get("embed") is None:
        return False                                  # в тестах — без настоящей модели и сети
    return (os.getenv("SEMANTIC_ROUTER") or "on").strip().lower() not in ("off", "0", "false", "no")


def _embedder():
    if _s["embed"] is None:
        from file_search import _embed
        _s["embed"] = _embed
    return _s["embed"]


def set_embedder(fn) -> None:
    """Для тестов: своя функция «тексты → нормированные векторы»."""
    _s["embed"] = fn
    _s["dirty"] = True


def examples() -> list:
    out = []
    try:
        with open(DATA, encoding="utf-8") as f:
            for line in f:
                if "\t" in line and not line.startswith("#"):
                    t, lab = line.rstrip("\n").split("\t", 1)
                    if lab.strip() not in _SKIP:
                        out.append((t.strip(), lab.strip()))
    except Exception:
        pass
    try:
        with open(LEARNED, encoding="utf-8") as f:
            out += [(e["text"], e["label"]) for e in json.load(f)]
    except Exception:
        pass
    return out


def _index():
    with _lock:
        if _s["dirty"] or _s["mat"] is None:
            ex = examples()
            _s["texts"] = [t for t, _ in ex]
            _s["labels"] = [lab for _, lab in ex]
            _s["mat"] = _embedder()(_s["texts"]) if ex else np.zeros((0, 1), dtype=np.float32)
            _s["dirty"] = False
        return _s["texts"], _s["labels"], _s["mat"]


def nearest(text: str, k: int = K) -> list:
    """[(метка, сходство, пример)] — ближайшие примеры по смыслу."""
    texts, labels, mat = _index()
    if not texts:
        return []
    q = _embedder()([text])[0]
    sims = mat @ q
    order = np.argsort(-sims)[:k]
    return [(labels[i], float(sims[i]), texts[i]) for i in order]


def guess(text: str):
    """→ (метка, сходство, примеры этой метки) или None, если уверенности нет."""
    near = nearest(text, k=K * 2)
    if not near or near[0][1] < THRESHOLD:
        return None
    top = near[0][0]
    if top in _NO_RESCUE:
        return None
    other = next((s for lab, s, _ in near if lab != top), 0.0)
    if near[0][1] - other < MARGIN:
        return None
    same = [ex for lab, _, ex in near if lab == top]
    return top, near[0][1], same


def _label_examples(label: str, n: int = 6) -> list:
    return [t for t, lab in examples() if lab == label][:n]


def canonical(text: str, label: str, ask=None) -> str:
    """Быстрая модель: фраза → стандартная команда этой функции (или '' — не про это)."""
    ex = _label_examples(label)
    system = ("Rewrite the user's request as ONE standard command in the same language, in the style of these "
              "examples (keep every specific detail: names, numbers, apps, files, places):\n- " + "\n- ".join(ex) +
              "\nIf the request is NOT this kind of command, answer exactly NONE. Output only the command or NONE.")
    q = ask or _ask_fast
    out = (q(system, text) or "").strip().strip("«»\"'.")
    if not out or out.upper().startswith("NONE") or len(out) > 200:
        return ""
    return out


def _ask_fast(system: str, user: str) -> str:
    from core import prompter
    return prompter._ask_fast(system, user)


def rescue(text: str, ctx: dict = None, ask=None):
    """Правила не узнали фразу → (стандартная команда, метка) или None."""
    if not enabled() or len(text) > 160:
        return None
    from core import intent_router
    g = guess(text)
    if not g:
        return None
    label = g[0]
    if intent_router.OWNER.get(label) not in ("pc_fast", "main"):
        return None
    try:
        cmd = canonical(text, label, ask)
    except Exception as e:
        print(f"[смысл] модель: {e}")
        return None
    if not cmd:
        return None
    r = intent_router.route(cmd, ctx)
    if label not in r.claims:
        print(f"[смысл] «{text}» → «{cmd}» — правила не узнали, не выполняю")
        return None
    print(f"[смысл] «{text}» → {label} ({g[1]:.2f}) → «{cmd}»")
    return cmd, label


# --- обучение на исправлениях ---------------------------------------------------------------------------------
_CORR = re.compile(r"^(?:нет|не то|не так|не это|неправильно|я не (?:это|так) (?:имел|имела) в виду|no|not that|wrong)"
                   r"[,.!\s—–-]*(?:я\s+)?(?:имел[аи]?\s+в\s+виду|хотел[аи]?|просил[аи]?|i meant|i wanted)?[,:\s—–-]*(.*)$", re.I)


def correction(text: str):
    """«нет, я имел в виду поставь хром слева» → «поставь хром слева» (или '' если просто «нет»)."""
    m = _CORR.match(" ".join((text or "").split()))
    if not m:
        return None
    return m.group(1).strip(" .")


def learn(phrase: str, label: str) -> bool:
    if not phrase or label in _SKIP or label in _NO_RESCUE or len(phrase) > 160:
        return False
    with _lock:
        try:
            with open(LEARNED, encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            items = []
        if any(e["text"].lower() == phrase.lower() and e["label"] == label for e in items):
            return False
        items = [e for e in items if e["text"].lower() != phrase.lower()]
        items.append({"text": phrase, "label": label, "ts": time.time()})
        with open(LEARNED, "w", encoding="utf-8") as f:
            json.dump(items[-500:], f, ensure_ascii=False, indent=1)
        _s["dirty"] = True
    print(f"[смысл] запомнил: «{phrase}» → {label}")
    return True
