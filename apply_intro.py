"""
Заставка запуска и финал: правки Python-части.

Положи этот файл и новый atlas_ui.html в корень проекта (рядом с main.py) и запусти:
    python apply_intro.py

Что делает (с резервной копией в backup_intro_<время>/):
  • web_gui.py  — проверка систем для заставки (get_boot_status, ping_groq_ui),
                  сигнал «звезда вспыхнула» (intro_done_ui), разрешение звука без клика;
  • ui_state.py — флаг intro_done;
  • main.py     — приветствие ждёт вспышки звезды, чтобы голос совпал с заставкой.
Повторный запуск безопасен: уже внесённые правки пропускаются.
"""
import ast
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
report = []


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return f.read()


def write(p, s):
    with open(os.path.join(ROOT, p), "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


if not os.path.exists(os.path.join(ROOT, "main.py")):
    sys.exit("Запусти скрипт из корня проекта Atlas (рядом с main.py).")
html = read("atlas_ui.html")
if "startIntro" not in html:
    sys.exit("atlas_ui.html в корне проекта — старый. Сначала положи туда новый файл (в нём есть заставка).")

backup = os.path.join(ROOT, time.strftime("backup_intro_%Y%m%d_%H%M%S"))
os.makedirs(backup, exist_ok=True)
for f in ("web_gui.py", "ui_state.py", "main.py"):
    shutil.copy2(os.path.join(ROOT, f), os.path.join(backup, f))
print(f"Резервная копия: {backup}")

# ---------------------------------------------------------------------------
# web_gui.py
# ---------------------------------------------------------------------------
report.append("web_gui.py:")
w = read("web_gui.py")
METHODS = '''    # ------------------------------------------------------------------
    # Заставка запуска: проверка систем и сигнал «звезда вспыхнула»
    # ------------------------------------------------------------------
    def get_boot_status(self) -> list:
        """Быстрые проверки для журнала заставки. Каждая — ключ, успех, значение."""
        out = []

        def add(key, ok, value=""):
            out.append({"key": key, "ok": bool(ok), "value": value})

        add("core", True)
        try:
            import voice
            add("vosk", os.path.isdir(voice.VOSK_MODEL_PATH))
        except Exception as e:
            add("vosk", False, str(e)[:40])
        try:
            from voice import get_response_language, current_voice_choice
            add("voice", True, current_voice_choice(get_response_language()))
        except Exception as e:
            add("voice", False, str(e)[:40])
        try:
            import sounddevice as sd
            add("mic", True, sd.query_devices(kind="input")["name"])
        except Exception as e:
            add("mic", False, str(e)[:40])
        try:
            conn = self._memory_conn()
            try:
                n = conn.execute("SELECT COUNT(*) FROM edges WHERE active = 1").fetchone()[0]
            except sqlite3.OperationalError:
                n = 0                              # графа ещё нет — это не ошибка
            conn.close()
            add("memory", True, n)
        except Exception as e:
            add("memory", False, str(e)[:40])
        try:
            import file_search
            db = getattr(file_search, "INDEX_DB", None) or os.path.join(_ROOT, "file_index2.db")
            db = db if os.path.isabs(db) else os.path.join(_ROOT, db)
            conn = sqlite3.connect(db)
            n = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            conn.close()
            add("index", True, n)
        except Exception as e:
            add("index", False, str(e)[:40])
        add("missions", True, self._missions_running())
        return out

    def ping_groq_ui(self) -> dict:
        """Связь с Groq: бесплатный запрос списка моделей, без расхода токенов."""
        try:
            from voice import groq_client
            t0 = time.time()
            groq_client.models.list()
            return {"key": "groq", "ok": True, "value": int((time.time() - t0) * 1000)}
        except Exception as e:
            return {"key": "groq", "ok": False, "value": str(e)[:40]}

    def intro_done_ui(self) -> None:
        shared_state["intro_done"] = True


'''
if "def get_boot_status" not in w:
    if "class WebGUI:" not in w:
        sys.exit("В web_gui.py не найден class WebGUI — пришли файл, разберёмся.")
    w = w.replace("class WebGUI:", METHODS + "class WebGUI:", 1)
    report.append("  ✓ проверка систем и сигнал заставки")
else:
    report.append("  ✓ уже есть")
if "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS" not in w:
    a = "        webview.start(debug=True)"
    if a in w:
        w = w.replace(a, "        # звуки заставки должны играть сразу, без первого клика по окну\n"
                         "        os.environ.setdefault(\"WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS\",\n"
                         "                              \"--autoplay-policy=no-user-gesture-required\")\n" + a, 1)
        report.append("  ✓ звук заставки без клика")
    else:
        report.append("  ! строка webview.start не найдена — звук может включиться только после первого клика")
for need in ("import sqlite3", "import time", "_ROOT"):
    if need not in w:
        report.append(f"  ! в файле нет «{need}» — пришли web_gui.py, поправлю")
write("web_gui.py", w)

# ---------------------------------------------------------------------------
# ui_state.py
# ---------------------------------------------------------------------------
report.append("ui_state.py:")
u = read("ui_state.py")
if '"intro_done"' not in u:
    m = re.search(r'^(\s*)"notif_seq":[^\n]*\n', u, re.M)
    if m:
        u = u[:m.end()] + f'{m.group(1)}"intro_done": False,       # звезда вспыхнула — можно приветствовать\n' + u[m.end():]
        report.append("  ✓ флаг intro_done")
    else:
        u = u.replace("shared_state = {", 'shared_state = {\n    "intro_done": False,', 1)
        report.append("  ✓ флаг intro_done (в начало словаря)")
else:
    report.append("  ✓ уже есть")
write("ui_state.py", u)

# ---------------------------------------------------------------------------
# main.py — приветствие в момент вспышки звезды
# ---------------------------------------------------------------------------
report.append("main.py:")
mn = read("main.py")
if "intro_done" not in mn:
    a = "    start_reminder_thread(_speak_and_update)\n"
    if a in mn:
        mn = mn.replace(a, a +
            "    # приветствие — в момент вспышки звезды в заставке (не дольше 15 с ожидания)\n"
            "    _t0 = time.time()\n"
            "    while not shared_state.get(\"intro_done\") and time.time() - _t0 < 15:\n"
            "        time.sleep(0.1)\n", 1)
        report.append("  ✓ приветствие синхронизировано с заставкой")
    else:
        report.append("  ! начало _voice_loop не найдено — приветствие прозвучит без синхронизации")
else:
    report.append("  ✓ уже есть")
write("main.py", mn)

# ---------------------------------------------------------------------------
report.append("Проверка синтаксиса:")
bad = False
for f in ("web_gui.py", "ui_state.py", "main.py"):
    try:
        ast.parse(read(f), filename=f)
        report.append(f"  ✓ {f}")
    except SyntaxError as e:
        bad = True
        report.append(f"  ! {f}: {e}")
print("\n".join(report))
print(f"\nЕсть ошибка — верни файлы из {backup} и пришли вывод." if bad else "\nГотово. Запускай: python main.py")
