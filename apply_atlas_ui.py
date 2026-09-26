"""
Перенос нового интерфейса в Atlas.

Запуск из корня проекта (там, где main.py), с активированным venv:
    python apply_atlas_ui.py

Что делает:
  1. Копирует все файлы, которые будет менять, в папку backup_ui_<время>/.
  2. Ставит новые atlas_ui.html, web_gui.py, ui_state.py (лежат рядом со скриптом
     в папке atlas_ui_update/).
  3. Вносит маленькие правки в voice.py, main.py, core/proactive.py,
     core/missions.py, core/llm_gateway.py. Каждая правка сначала ищет точный
     фрагмент; если его нет (файл уже поменян руками), правка пропускается
     и об этом пишется в отчёте — ничего не ломается молча.
  4. Проверяет синтаксис всех изменённых .py файлов.

Откат: скопировать файлы из backup_ui_<время>/ обратно.
"""
import ast
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "atlas_ui_update")
report = []


def ok(msg):
    report.append("  ✓ " + msg)


def warn(msg):
    report.append("  ! " + msg)


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(os.path.join(ROOT, path), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# 0. Проверки и резервная копия
# ---------------------------------------------------------------------------
if not os.path.exists(os.path.join(ROOT, "main.py")):
    sys.exit("Запусти скрипт из корня проекта Atlas (рядом с main.py).")
for f in ("atlas_ui.html", "web_gui.py", "ui_state.py"):
    if not os.path.exists(os.path.join(SRC, f)):
        sys.exit(f"Не найден {os.path.join('atlas_ui_update', f)} — распакуй папку рядом со скриптом.")

TOUCHED = ["atlas_ui.html", "web_gui.py", "ui_state.py", "voice.py", "main.py",
           "core/proactive.py", "core/missions.py", "core/llm_gateway.py"]
backup = os.path.join(ROOT, time.strftime("backup_ui_%Y%m%d_%H%M%S"))
os.makedirs(backup, exist_ok=True)
for f in TOUCHED:
    p = os.path.join(ROOT, f)
    if os.path.exists(p):
        dst = os.path.join(backup, f)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(p, dst)
print(f"Резервная копия: {backup}")

# ---------------------------------------------------------------------------
# 1. Новые файлы интерфейса
# ---------------------------------------------------------------------------
report.append("Новые файлы:")
for f in ("atlas_ui.html", "web_gui.py", "ui_state.py"):
    shutil.copy2(os.path.join(SRC, f), os.path.join(ROOT, f))
    ok(f)

# ---------------------------------------------------------------------------
# 2. voice.py
# ---------------------------------------------------------------------------
report.append("voice.py:")
v = read("voice.py")

# 2.1 audioop удалён из Python 3.13+, а rms нигде не используется
if "from audioop import rms\n" in v:
    v = v.replace("from audioop import rms\n", "", 1)
    ok("убран неиспользуемый импорт audioop (его нет в Python 3.13+)")

# 2.2 _transcribe_audio: в блоке except было обращение к result, которого при ошибке
#     сети не существует — это роняло голосовой цикл. Заменяем функцию целиком.
NEW_TRANSCRIBE = '''def _transcribe_audio(recording: np.ndarray) -> str:
    """
    Распознаёт фразу через Groq Whisper. В русском режиме — с подсказкой языка,
    в английском Whisper определяет язык сам. Тишину и фразы, которые Whisper
    выдумывает на шуме («Продолжение следует», «Thanks for watching»), отбрасываем.
    """
    if not _has_speech(recording):
        print("[VAD] речи нет — Whisper не вызываю")
        return ""

    temp_path = "temp_stt.wav"
    with wave.open(temp_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(recording.tobytes())

    try:
        with open(temp_path, "rb") as f:
            data = f.read()
        if _response_language["lang"] == "ru":
            result = groq_client.audio.transcriptions.create(
                file=(temp_path, data), model="whisper-large-v3-turbo", language="ru")
        else:
            result = groq_client.audio.transcriptions.create(
                file=(temp_path, data), model="whisper-large-v3-turbo")
        text = (result.text or "").strip()
        if not re.search(r"[^\\W_]", text):         # ни буквы, ни цифры: «...», «?!»
            print(f"[Whisper] пустая фраза отброшена: {text!r}")
            return ""
        if re.sub(r"[^\\w\\s]", "", text.lower()).strip() in _WHISPER_JUNK:
            print(f"[Whisper] выдуманная фраза отброшена: {text!r}")
            return ""
        return text
    except Exception as e:
        print(f"[STT error]: {e}")
        return ""
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


'''
m = re.search(r"^def _transcribe_audio\(.*?(?=^WAKE_CONF\s*=)", v, re.S | re.M)
if m:
    v = v[:m.start()] + NEW_TRANSCRIBE + v[m.end():]
    ok("_transcribe_audio: исправлен блок ошибок (раньше падал при сбое сети)")
else:
    warn("_transcribe_audio не найден в ожидаемом виде — пропущено")

# 2.3 уровень микрофона для звезды, пока Atlas слушает
if 'shared_state["mic_level"]' not in v:
    a = "            chunk, _overflow = stream.read(block)\n"
    if v.count(a) == 1:
        v = v.replace(a, a + '            shared_state["mic_level"] = min(1.0, float(np.sqrt(np.mean(\n'
                             '                chunk.astype(np.float32) ** 2))) / 32768 * 9)   # для звезды в интерфейсе\n', 1)
        ok("_listen_once: передаёт громкость микрофона в интерфейс")
    else:
        warn("строка чтения микрофона в _listen_once не найдена — реакция на голос будет без микрофона")
    b = "        vad.reset_states()\n"
    if v.count(b) == 1:
        v = v.replace(b, b + '        shared_state["mic_level"] = 0.0\n', 1)
        ok("_listen_once: сбрасывает громкость после фразы")
else:
    ok("уровень микрофона уже передаётся")
write("voice.py", v)

# ---------------------------------------------------------------------------
# 3. main.py
# ---------------------------------------------------------------------------
report.append("main.py:")
mn = read("main.py")
if "from click import command\n" in mn:
    mn = mn.replace("from click import command\n", "", 1)
    ok("убран случайный импорт click (не использовался)")
else:
    ok("правок не нужно")
write("main.py", mn)

# ---------------------------------------------------------------------------
# 4. core/proactive.py — каждое событие попадает в «Уведомления»
# ---------------------------------------------------------------------------
report.append("core/proactive.py:")
pr = read("core/proactive.py")
if "from ui_state import notify" not in pr:
    a = "def _deliver(rule: Rule, text: str, speak) -> None:\n"
    if a in pr:
        pr = pr.replace(a, a +
            "    try:                                   # панель уведомлений в интерфейсе\n"
            "        from ui_state import notify\n"
            "        _k = {\"rule_traceback\": \"clipboard\"}.get(rule.check.__name__,\n"
            "                                                   rule.check.__name__.replace(\"rule_\", \"\"))\n"
            "        notify(\"info\" if _k == \"break\" else \"warn\", _k, text)\n"
            "    except Exception as e:\n"
            "        print(f\"[проактивность] уведомление интерфейсу: {e}\")\n", 1)
        ok("события проактивности попадают в уведомления интерфейса")
    else:
        warn("функция _deliver не найдена — уведомления проактивности в панель не попадут")
else:
    ok("уже подключено")
write("core/proactive.py", pr)

# ---------------------------------------------------------------------------
# 5. core/missions.py — готовые и неудачные миссии в «Уведомления»
# ---------------------------------------------------------------------------
report.append("core/missions.py:")
ms = read("core/missions.py")
if "from ui_state import notify" not in ms:
    a = "def _notify(text: str) -> None:\n"
    if a in ms:
        ms = ms.replace(a,
            "def _notify(text: str, kind: str = \"ok\", key: str = \"mission_done\") -> None:\n"
            "    try:                                   # панель уведомлений в интерфейсе\n"
            "        from ui_state import notify\n"
            "        notify(kind, key, text)\n"
            "    except Exception as e:\n"
            "        print(f\"[миссии] уведомление интерфейсу: {e}\")\n", 1)
        ok("готовые миссии попадают в уведомления интерфейса")
        ms, n = re.subn(r'(_notify\(f"Миссия не удалась.*?)\)(\s*\n)', lambda mo: mo.group(1) + ', kind="warn", key="mission_failed")' + mo.group(2), ms, count=1)
        if n:
            ok("неудачные миссии помечаются предупреждением")
        else:
            warn("строка «Миссия не удалась» не найдена — неудачи будут показаны как обычные уведомления")
    else:
        warn("функция _notify не найдена — уведомления миссий в панель не попадут")
else:
    ok("уже подключено")
write("core/missions.py", ms)

# ---------------------------------------------------------------------------
# 6. core/llm_gateway.py — снимок лимитов для панели «Система»
# ---------------------------------------------------------------------------
report.append("core/llm_gateway.py:")
gw = read("core/llm_gateway.py")
if "def snapshot(" not in gw:
    gw = gw.rstrip() + '''


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
        models = {m.split("/")[-1]: {"used": int(_used(w, now)), "budget": budget}
                  for m, w in _windows.items()}
    last = dict(_last)
    if last:
        last["ago"] = time.time() - last.pop("at")
    return {"models": models, "last": last}
'''
    ok("добавлен snapshot() для панели «Система»")
else:
    ok("уже подключено")
write("core/llm_gateway.py", gw)

# ---------------------------------------------------------------------------
# 7. Проверка синтаксиса
# ---------------------------------------------------------------------------
report.append("Проверка синтаксиса:")
bad = False
for f in ("web_gui.py", "ui_state.py", "voice.py", "main.py",
          "core/proactive.py", "core/missions.py", "core/llm_gateway.py"):
    try:
        ast.parse(read(f), filename=f)
        ok(f)
    except SyntaxError as e:
        bad = True
        warn(f"{f}: {e}")

print("\n".join(report))
if bad:
    print(f"\nЕсть синтаксическая ошибка — верни файлы из {backup} и пришли этот вывод.")
else:
    print("\nГотово. Запускай: python main.py")
