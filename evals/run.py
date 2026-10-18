"""
Экзамен для Atlas: точность маршрутизатора, конфликты, скорость — на наборе реальных фраз.

    python -m evals.run              — экзамен маршрутизатора (без сети и модели, ~1 с)
    python -m evals.run --brain      — плюс экзамен мозга через bench.py (нужны ключи и интернет)
    python -m evals.run --save       — записать результат как новую планку (evals/baseline.json)

Набор — evals/routing.tsv: «фраза <TAB> кто должен взять». Результаты — evals/results/<время>.json,
сравнение с планкой; если точность упала больше чем на 1 пункт — выход с ошибкой (так это ловит
проверка на GitHub).
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "routing.tsv")
PARA = os.path.join(HERE, "paraphrases.tsv")
BASE = os.path.join(HERE, "baseline.json")
OUT = os.path.join(HERE, "results")


def load(path: str = DATA) -> list:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#") or "\t" not in line:
                continue
            text, want = line.split("\t", 1)
            rows.append((text.strip(), want.strip()))
    return rows


def _isolate() -> None:
    """Экзамен не должен зависеть от твоих файлов (режимы, «кто в каком мессенджере») — временные пустые."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="atlas_eval_")
    from core import messengers, scenarios
    messengers.FILE = os.path.join(tmp, "m.json")
    scenarios.FILE = os.path.join(tmp, "s.json")


def run_routing(rows: list, semantic: bool = False) -> dict:
    """semantic=True — промахи правил («other») пробует смысловой слой (нужны модель MiniLM и интернет)."""
    from core import intent_router
    _isolate()
    rescue = None
    if semantic:
        os.environ["ATLAS_TESTING"] = "0"
        from core import semantic_router
        rescue = semantic_router.rescue
    for t, _ in rows[:3]:
        intent_router.route(t)                          # прогрев: импорт модулей не считаем в скорость
    wrong, conflicts, times = [], [], []
    per = {}
    for text, want in rows:
        r = intent_router.route(text)
        times.append(r.ms)
        got = r.label
        if got == "other" and rescue is not None:
            try:
                res = rescue(text)
            except Exception as e:
                print(f"  [смысл] {e}")
                res = None
            if res:
                got = res[1]
        p = per.setdefault(want, {"n": 0, "ok": 0})
        p["n"] += 1
        if got == want:
            p["ok"] += 1
        else:
            wrong.append({"text": text, "want": want, "got": got, "claims": r.claims})
        if r.ambiguous:
            conflicts.append({"text": text, "claims": r.claims})
    n = len(rows)
    ok = n - len(wrong)
    times.sort()
    return {"n": n, "ok": ok, "accuracy": round(100 * ok / max(1, n), 1), "wrong": wrong, "conflicts": conflicts,
            "per_label": per, "ms_p50": round(statistics.median(times), 2) if times else 0,
            "ms_p95": round(times[int(len(times) * 0.95) - 1], 2) if times else 0}


def report(res: dict, base: dict, max_wrong: int = 25) -> str:
    lines = [f"Маршрутизатор: {res['ok']} из {res['n']} верно — {res['accuracy']}%  "
             f"(скорость p50 {res['ms_p50']} мс, p95 {res['ms_p95']} мс)"]
    if base:
        d = round(res["accuracy"] - base.get("accuracy", 0), 1)
        lines.append(f"Планка: {base.get('accuracy')}% → {'+' if d >= 0 else ''}{d} пункта")
    weak = sorted(((k, v) for k, v in res["per_label"].items() if v["ok"] < v["n"]),
                  key=lambda kv: kv[1]["ok"] / kv[1]["n"])
    if weak:
        lines.append("Слабые места: " + ", ".join(f"{k} {v['ok']}/{v['n']}" for k, v in weak[:8]))
    for w in res["wrong"][:max_wrong]:
        lines.append(f"  ✗ «{w['text']}» → {w['got']} (нужно {w['want']})" +
                     (f", заявки: {', '.join(w['claims'])}" if len(w["claims"]) > 1 else ""))
    if res["conflicts"]:
        lines.append(f"Конфликты (несколько функций хотят одну фразу): {len(res['conflicts'])}")
        for c in res["conflicts"][:10]:
            lines.append(f"  ⚔ «{c['text']}»: {', '.join(c['claims'])}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", action="store_true", help="ещё и экзамен мозга (bench.py)")
    ap.add_argument("--save", action="store_true", help="сделать результат новой планкой")
    ap.add_argument("--semantic", action="store_true", help="перефразировки — со смысловым слоем (модель + интернет)")
    a = ap.parse_args(argv)
    res = run_routing(load())
    base = {}
    try:
        with open(BASE, encoding="utf-8") as f:
            base = json.load(f)
    except Exception:
        pass
    print(report(res, base))
    para = run_routing(load(PARA), semantic=a.semantic) if os.path.exists(PARA) else None
    if para:
        how = "со смысловым слоем" if a.semantic else "только правила; со смысловым слоем: --semantic"
        print(f"\nПерефразировки (честная проверка, {how}):")
        print(report(para, {"accuracy": base["para"]} if base.get("para") is not None and not a.semantic else {},
                     max_wrong=25 if a.semantic else 5).replace("Маршрутизатор:", "Понял"))
        res["para"] = para
    os.makedirs(OUT, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    with open(os.path.join(OUT, f"{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    if a.save:
        with open(BASE, "w", encoding="utf-8") as f:
            json.dump({"accuracy": res["accuracy"], "n": res["n"], "para": para["accuracy"] if para else None,
                       "saved": stamp}, f, ensure_ascii=False, indent=1)
        print("Сохранил как новую планку.")
    if a.brain:
        print("\nМозг (bench.py):")
        subprocess.run([sys.executable, os.path.join(ROOT, "bench.py")], cwd=ROOT)
    if base and res["accuracy"] < base.get("accuracy", 0) - 1.0:
        print("✗ Точность упала больше чем на 1 пункт.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
