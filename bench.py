"""
Тест производительности и надёжности Atlas.

  python bench.py              — безопасный набор (ничего не открывает и не меняет)
  python bench.py --actions    — плюс команды с действиями (откроет Блокнот, поставит таймер)
  python bench.py --only 3,5   — только выбранные номера

Каждая команда идёт через настоящий мозг Atlas (ask_ai), без голоса. На время теста к нему
подключаются датчики: сколько раз он обратился к модели, сколько ждал в очереди из-за
лимитов, сколько работали инструменты, когда появилось первое слово ответа.
Результаты: таблица в консоли + bench_results/<время>.json; итог сравнивается с прошлым запуском.
"""
import argparse
import glob
import json
import os
import re
import statistics
import sys
import time

CASES = [
    # (запрос, какие инструменты считаются правильными, слова, которые должны быть в ответе, нужен ли --actions)
    ("Какая сейчас погода?", {"get_weather", "holo_weather"}, [], False),
    ("Сколько будет 17 умножить на 23?", {"calculate", "*"}, ["391"], False),
    ("Какие главные новости сегодня?", {"get_news", "search_web"}, [], False),
    ("Найди в интернете, кто написал «Евгения Онегина»", {"search_web", "read_webpage", "*"}, ["Пушкин"], False),
    ("Покажи погоду в Стамбуле", {"holo_weather"}, [], False),
    ("Покажи Эйфелеву башню", {"holo_show"}, [], False),
    ("Сколько свободного места на диске C?", {"get_disk_usage"}, [], False),
    ("Какая сейчас загрузка процессора?", {"get_cpu_usage"}, [], False),
    ("Переведи на английский: я готовлюсь к экзамену", {"translate_text", "*"}, ["exam"], False),
    ("Сконвертируй 5 миль в километры", {"convert_units", "calculate", "*"}, ["8"], False),
    ("Какие у меня задачи в списке дел?", {"list_todos"}, [], False),
    ("Что ты обо мне знаешь?", {"holo_graph", "recall_conversations", "recall_memories", "*"}, [], False),
    ("Найди файл, где я писал про SAT", {"search_file_content", "open_found_file"}, [], False),
    ("Курс доллара к сому", {"get_exchange_rate", "search_web"}, [], False),
    ("Открой блокнот и напиши: тест Atlas", {"execute_plan", "desktop_type"}, [], True),
    ("Поставь таймер на одну минуту с подписью тест", {"set_timer"}, [], True),
]
FAIL_WORDS = re.compile(r"не получилось|не могу|лимит|ошибк|couldn'?t|error|limit|не смог|слишком много шагов", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--actions", action="store_true", help="включить команды с действиями")
    ap.add_argument("--only", default="", help="номера через запятую")
    ap.add_argument("--lang", choices=["ru", "en"], default="ru",
                    help="язык ответов в тесте (по умолчанию ru — проверки ответов написаны по-русски)")
    args = ap.parse_args()
    only = {int(x) for x in args.only.split(",") if x.strip().isdigit()}

    print("Загружаю Atlas (без голоса и окна)…")
    import ai_brain as ab
    from core import llm_gateway as gw
    lang_hint = "(Respond in Russian.) " if args.lang == "ru" else "(Respond in English.) "
    try:
        from voice import get_response_language
        saved = get_response_language()
        note = "" if saved == args.lang else f" (в настройках Atlas сейчас «{saved}» — тест от неё не зависит)"
    except Exception:
        note = ""
    print(f"Язык ответов в тесте: {args.lang}{note}")

    cur = {}
    try:                                       # новое устройство мозга: brain/providers и brain/tools
        from brain import providers as _prov, tools as _tl
        stream_home, tool_home = _prov, _tl
    except ImportError:                        # старое: всё в ai_brain.py
        stream_home = tool_home = ab
    orig_stream, orig_reserve, orig_tool = stream_home._call_model_stream, gw.reserve_any, tool_home._run_one_tool

    def stream(*a, **k):
        t = time.time()
        try:
            return orig_stream(*a, **k)
        finally:
            cur["model_calls"] += 1
            cur["model_s"] += time.time() - t
            cur["models"].append(str(k.get("model", "?")).split("/")[-1])

    def reserve(*a, **k):
        t = time.time()
        try:
            return orig_reserve(*a, **k)
        finally:
            cur["wait_s"] += time.time() - t

    def tool(name, a):
        t = time.time()
        try:
            return orig_tool(name, a)
        finally:
            cur["tools"].append(name)
            cur["tool_s"] += time.time() - t

    stream_home._call_model_stream, gw.reserve_any, tool_home._run_one_tool = stream, reserve, tool
    # тест не должен попадать в самообучение и привычки (иначе «вы часто спрашиваете погоду»)
    for mod, names in (("core.lessons", ("record_turn", "learn_from_correction")), ("core.routines", ("log_tools",))):
        try:
            m = __import__(mod, fromlist=["x"])
            for n in names:
                if hasattr(m, n):
                    setattr(m, n, lambda *a, **k: None)
        except Exception:
            pass

    class Speech:
        def feed(self, d):
            if cur["first_s"] is None and d.strip():
                cur["first_s"] = time.time() - cur["t0"]

    rows = []
    for i, (q, want, must, needs_actions) in enumerate(CASES, 1):
        if only and i not in only:
            continue
        if needs_actions and not args.actions:
            continue
        try:
            ab.reset_conversation()
        except Exception:
            pass
        cur.update(t0=time.time(), model_calls=0, model_s=0.0, wait_s=0.0, tools=[], tool_s=0.0, first_s=None, models=[])
        print(f"\n[{i:>2}] {q}")
        try:
            reply = ab.ask_ai(lang_hint + q, Speech())
            err = ""
        except Exception as e:
            reply, err = "", f"{type(e).__name__}: {e}"
        total = time.time() - cur["t0"]
        first = cur["first_s"] if cur["first_s"] is not None else total
        used = set(cur["tools"])
        tool_ok = bool(used & want) or "*" in want          # «*» — годится любой путь, решает проверка текста
        text_ok = all(w.lower() in str(reply).lower() for w in must)
        ok = bool(reply) and not err and tool_ok and text_ok and not FAIL_WORDS.search(str(reply)[:120])
        rows.append({"n": i, "q": q, "ok": ok, "total": round(total, 2), "first": round(first, 2),
                     "model_calls": cur["model_calls"], "model_s": round(cur["model_s"], 2),
                     "wait_s": round(cur["wait_s"], 2), "tool_s": round(cur["tool_s"], 2),
                     "tools": cur["tools"], "models": cur["models"], "reply": str(reply)[:200], "error": err})
        r = rows[-1]
        print(f"     {'✓' if ok else '✗'} {r['total']:.1f} с (первое слово {r['first']:.1f}) | модель ×{r['model_calls']} "
              f"{r['model_s']:.1f} с | очередь {r['wait_s']:.1f} с | инструменты {r['tool_s']:.1f} с {r['tools']}")
        print(f"     → {r['reply'][:140]}" + (f"\n     ! {err}" if err else ""))

    if not rows:
        print("Нет команд для запуска.")
        return
    tot = [r["total"] for r in rows]
    first = [r["first"] for r in rows]
    summ = {
        "when": time.strftime("%Y-%m-%d %H:%M"), "cases": len(rows), "ok": sum(r["ok"] for r in rows),
        "median_total": round(statistics.median(tot), 2), "worst_total": round(max(tot), 2),
        "median_first": round(statistics.median(first), 2),
        "avg_model_calls": round(sum(r["model_calls"] for r in rows) / len(rows), 2),
        "share_wait": round(sum(r["wait_s"] for r in rows) / max(0.01, sum(tot)) * 100),
        "share_model": round(sum(r["model_s"] for r in rows) / max(0.01, sum(tot)) * 100),
        "share_tools": round(sum(r["tool_s"] for r in rows) / max(0.01, sum(tot)) * 100),
    }
    print("\n" + "=" * 78)
    print(f"{'№':>3}  {'итог':>6}  {'1-е слово':>9}  {'модель':>7}  {'очередь':>8}  {'инстр.':>7}  запрос")
    for r in rows:
        print(f"{r['n']:>3}  {r['total']:>5.1f}с  {r['first']:>8.1f}с  ×{r['model_calls']:<2}{r['model_s']:>4.1f}с  "
              f"{r['wait_s']:>7.1f}с  {r['tool_s']:>6.1f}с  {'✓' if r['ok'] else '✗'} {r['q'][:34]}")
    print("=" * 78)
    print(f"Успешно: {summ['ok']} из {summ['cases']} ({round(summ['ok'] / summ['cases'] * 100)}%)")
    print(f"Медиана: {summ['median_total']} с, до первого слова {summ['median_first']} с; худший случай {summ['worst_total']} с")
    print(f"Обращений к модели в среднем: {summ['avg_model_calls']}")
    print(f"Куда уходит время: очередь из-за лимитов {summ['share_wait']}% · модель {summ['share_model']}% · "
          f"инструменты {summ['share_tools']}% · остальное {100 - summ['share_wait'] - summ['share_model'] - summ['share_tools']}%")

    os.makedirs("bench_results", exist_ok=True)
    prev = sorted(glob.glob("bench_results/*.json"))
    path = os.path.join("bench_results", time.strftime("%Y%m%d_%H%M%S") + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"summary": summ, "rows": rows}, f, ensure_ascii=False, indent=1)
    if prev:
        try:
            p = json.load(open(prev[-1], encoding="utf-8"))["summary"]
            k = p["median_total"] / max(0.01, summ["median_total"])
            print(f"\nПо сравнению с прошлым запуском ({p['when']}): медиана {p['median_total']} → {summ['median_total']} с "
                  f"({'в ' + format(k, '.1f') + ' раза быстрее' if k >= 1 else 'медленнее в ' + format(1 / k, '.1f') + ' раза'}), "
                  f"успешно {p['ok']}/{p['cases']} → {summ['ok']}/{summ['cases']}")
        except Exception:
            pass
    print(f"\nПодробности: {path}")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)                                 # фоновые потоки Atlas (индекс, память) не держат консоль


if __name__ == "__main__":
    main()
