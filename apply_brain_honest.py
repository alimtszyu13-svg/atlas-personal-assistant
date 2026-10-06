"""
Мозг: честнее и собраннее.

  • О себе — только правду: возможности описывает по своим инструментам и не выдумывает
    приложения, загрузки и сайты (было: «скачай APK с официального сайта» — такого нет).
  • Делает только то, о чём просили: память и портрет пользователя — фон для понимания,
    а не повод начинать действия (было: после «установи себя» полез смотреть курс доллара).
  • «Опыт» из прошлых разговоров — не больше трёх инструментов, и без desktop_* в разговоре
    о музыке (было: в музыкальные запросы подмешивались инструменты окон Windows).

    python apply_brain_honest.py
    python apply_brain_honest.py --rollback
"""
import os
import re
import shutil
import subprocess
import sys
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
PATCHES = [('brain/prompt.py', 'правило: о себе — только правду; делать только то, о чём просили', 'SYSTEM_PROMPT = _CORE + _HANDS + _THINK_EXAMPLES + _HONEST_MEMORY\n', '_STAY_TRUE = (" ABOUT YOURSELF: describe what you can do only from your tools — never invent apps, downloads, websites, "\n              "settings or features; if no tool covers it, say so plainly. STAY ON TASK: do only what the user asked in "\n              "this turn; never start another task on your own because of something in memory, the user\'s profile or "\n              "the Situation note (e.g. don\'t look up an exchange rate nobody asked for).")\nSYSTEM_PROMPT = _CORE + _HANDS + _THINK_EXAMPLES + _HONEST_MEMORY + _STAY_TRUE\n'), ('brain/planner.py', 'портрет из памяти — для понимания, не повод действовать', '                    text = ("About the user (from memory — use it to understand what they want and why; don\'t recite "\n                            "it unless asked): " + "; ".join(facts))', '                    text = ("About the user (from memory — use it only to understand what they want and why; don\'t "\n                            "recite it unless asked and never start actions because of it): " + "; ".join(facts))'), ('core/memory.py', 'воспоминания — фон, а не повод действовать', '    return ("Memory about the user (use it if helpful; don\'t recite it unprompted):\\n"', '    return ("Memory about the user (background only: use it to understand the request; don\'t recite it "\n            "unprompted and never start actions because of it):\\n"'), ('brain/tools.py', '«опыт» — не больше трёх инструментов, и без desktop_* в разговоре о музыке', '    extra = [t for t in TOOLS_SCHEMA if t["function"]["name"] in set(names) - have]', '    q = state.turn.get("question", "")\n    if _INTENT_PINS[0][0].search(q) or _music_context():     # музыка: «опыт» лазить в окно Spotify — плохой опыт\n        names = [n for n in names if not str(n).startswith("desktop_")]\n    names = list(dict.fromkeys(names))[:3]                     # не больше трёх «из опыта»\n    extra = [t for t in TOOLS_SCHEMA if t["function"]["name"] in set(names) - have]'), ('tests/test_brain.py', 'тесты: честность о себе, не отвлекаться, «опыт» без мусора', '@test\ndef region_blocked_music_service_is_hidden():', '@test\ndef honest_about_itself_and_stays_on_task():\n    p = ai_brain.SYSTEM_PROMPT\n    assert "ABOUT YOURSELF" in p and "never invent apps, downloads" in p\n    assert "STAY ON TASK" in p and "never start another task on your own" in p\n    assert "never start actions because of it" in open(os.path.join(ROOT, "brain", "planner.py"), encoding="utf-8").read()\n\n\n@test\ndef learned_tools_are_few_and_no_desktop_for_music():\n    les = sys.modules["core.lessons"]\n    les.LEARNED[:] = ["desktop_look", "desktop_switch", "desktop_windows", "open_app", "next_track", "add_note"]\n    try:\n        s = fresh([{"content": "ок"}])\n        state.conversation_history.append({"role": "assistant", "content": "Включил «Eminem - Stan» в Spotify."})\n        ai_brain.ask_ai(RU + "поставь что-нибудь повеселее")\n        names = {t["function"]["name"] for t in s.requests[0]["tools"]}\n        assert not any(n.startswith("desktop_") for n in names), sorted(names)\n        odd = ["kill_process", "empty_recycle_bin", "lock_screen", "get_uptime", "word_count", "generate_qr_code"]\n        les.LEARNED[:] = odd\n        s = fresh([{"content": "ок"}])\n        ai_brain.ask_ai(RU + "расскажи что-нибудь интересное про космос")\n        names = {t["function"]["name"] for t in s.requests[0]["tools"]}\n        assert len(set(odd) & names) <= 3, sorted(set(odd) & names)\n    finally:\n        les.LEARNED[:] = []\n\n\n@test\ndef region_blocked_music_service_is_hidden():')]
TOUCHED = sorted({rel for rel, *_ in PATCHES})


def p(rel):
    return os.path.join(ROOT, *rel.split("/"))


def restore(backup):
    for rel in TOUCHED:
        b = os.path.join(backup, *rel.split("/"))
        if os.path.exists(b):
            shutil.copy2(b, p(rel))
    print(f"Вернул прежние файлы из {backup}.")


if "--rollback" in sys.argv:
    cands = sorted(d for d in os.listdir(ROOT) if d.startswith("backup_honest_"))
    if not cands:
        sys.exit("Резервной копии нет.")
    restore(p(cands[-1]))
    sys.exit(0)
if "_music_context" not in open(p("brain/tools.py"), encoding="utf-8").read():
    sys.exit("Сначала поставь apply_music5.py.")

new, status = {}, []
for rel, what, old, repl in PATCHES:
    s = new.get(rel) or open(p(rel), encoding="utf-8").read()
    if repl in s:
        status.append((what, "уже было"))
    elif s.count(old) == 1:
        s = s.replace(old, repl, 1)
        status.append((what, "ok"))
    else:
        status.append((what, "НЕ НАЙДЕНО"))
    new[rel] = s
for what, st in status:
    print(f"  {'!' if st == 'НЕ НАЙДЕНО' else '✓'} {what}" + ("" if st == "ok" else f" ({st})"))
if any(st == "НЕ НАЙДЕНО" for _, st in status):
    sys.exit("Код выглядит иначе, чем я ожидал — ничего не изменено. Пришли вывод.")
if all(st == "уже было" for _, st in status):
    sys.exit("Уже установлено.")

backup = p(time.strftime("backup_honest_%Y%m%d_%H%M%S"))
for rel in TOUCHED:
    os.makedirs(os.path.dirname(os.path.join(backup, *rel.split("/"))), exist_ok=True)
    shutil.copy2(p(rel), os.path.join(backup, *rel.split("/")))
print(f"Резервная копия: {backup}")
for rel, s in new.items():
    with open(p(rel), "w", encoding="utf-8", newline="\n") as f:
        f.write(s)

env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
suites = [os.path.join("tests", f) for f in sorted(os.listdir(p("tests"))) if f.startswith("test_") and f.endswith(".py")]
for suite in suites:
    r = subprocess.run([sys.executable, p(suite)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300, env=env)
    m = re.search(r"Тестов пройдено: (\d+) из (\d+)", r.stdout + r.stderr)
    good = r.returncode == 0 and m and m.group(1) == m.group(2)
    print(f"  {'✓' if good else '✗'} {suite}: {m.group(0) if m else 'итоговой строки нет'}")
    if not good:
        out = (r.stdout + "\n" + r.stderr).splitlines()
        for i, ln in enumerate(out):
            if ln.strip().startswith("✗"):
                print("      " + "\n      ".join(x.rstrip() for x in out[i:i + 7]))
        with open(p("honest_test_output.txt"), "w", encoding="utf-8") as f:
            f.write(r.stdout + "\n" + r.stderr)
        restore(backup)
        sys.exit("✗ Проверка не прошла — всё возвращено. Пришли honest_test_output.txt.")
print("\nГотово. Перезапусти Atlas: python main.py")
