"""
Больше слов для обращения к Atlas.

  • Имя: «атлас», «атласик», «атласа», «атласу», «атласом», «атласе» и звучания
    английского Atlas («этлас», «атлэс», «атлес») — плюс любые свои через .env:
        WAKE_WORDS_EXTRA=джарвис,компьютер
  • Остановка речи: «стоп», «хватит», «тихо», «замолчи», «стой», «прекрати»,
    «перестань», «остановись», «замолкни», «довольно», «отмена», «тише» — плюс свои:
        STOP_WORDS_EXTRA=умолкни
  • Отмена задачи текстом и голосом: те же слова + «не надо», «отбой», «wait», «pause».
  • Выключение: «выключись», «отключись», «заверши работу», «закончи работу»,
    «закройся», «иди спать», «shut down», «turn off», «go to sleep», «exit», «quit».

Локальный распознаватель (Vosk) знает не все слова: при запуске Atlas проверит
каждое и напишет в лог, какие реально работают, а какие модель не знает.

Запуск из корня проекта:  python apply_words.py
Резервная копия: backup_words_<время>/
"""
import ast
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
FILES = ["voice.py", "main.py", "web_gui.py"]
for f in FILES:
    if not os.path.exists(os.path.join(ROOT, f)):
        sys.exit(f"Не найден {f} — запусти из корня проекта Atlas.")
backup = os.path.join(ROOT, time.strftime("backup_words_%Y%m%d_%H%M%S"))
os.makedirs(backup, exist_ok=True)
for f in FILES:
    shutil.copy2(os.path.join(ROOT, f), os.path.join(backup, f))
print(f"Резервная копия: {backup}")
src = {f: open(os.path.join(ROOT, f), encoding="utf-8").read() for f in FILES}
report = []


def rep_all(f, old, new, what):
    n = src[f].count(old)
    if n:
        src[f] = src[f].replace(old, new)
        report.append(f"  ✓ {f}: {what} (мест: {n})")
    elif new in src[f]:
        report.append(f"  ✓ {f}: {what} (уже было)")
    else:
        report.append(f"  ! {f}: {what} — фрагмент не найден, пропущено")


def _extra(env):
    return f'tuple(w.strip().lower() for w in os.getenv("{env}", "").split(",") if w.strip())'


# ---------------------------------------------------------------------------
# voice.py
# ---------------------------------------------------------------------------
V = "voice.py"
WAKE = ('WAKE_WORDS_LOCAL = tuple(dict.fromkeys(\n'
        '    ("атлас", "атласик", "атласа", "атласу", "атласом", "атласе", "этлас", "атлэс", "атлес")\n'
        f'    + {_extra("WAKE_WORDS_EXTRA")}))   # имя и его формы + свои из .env')
STOP = ('INTERRUPT_WORDS_LOCAL = tuple(dict.fromkeys(\n'
        '    ("стоп", "хватит", "тихо", "замолчи", "стой", "прекрати", "перестань", "остановись",\n'
        '     "замолкни", "довольно", "отмена", "тише")\n'
        f'    + {_extra("STOP_WORDS_EXTRA")}))   # слова остановки + свои из .env')
rep_all(V, 'WAKE_WORDS_LOCAL = ("атлас",)', WAKE, "формы имени и свои слова из .env")
rep_all(V, 'INTERRUPT_WORDS_LOCAL = ("стоп", "хватит", "тихо", "замолчи")', STOP, "больше слов остановки")
rep_all(V, 'INTERRUPT_WORDS = ("stop", "enough", "quiet", "стоп", "хватит", "тихо")',
        'INTERRUPT_WORDS = ("stop", "enough", "quiet", "wait", "pause", "стоп", "хватит", "тихо", "стой",\n'
        '                   "прекрати", "перестань", "остановись", "довольно", "отмена")',
        "слова остановки для распознанного текста")

# Vosk: оставить только слова, которые модель знает, и сказать об остальных
OLD_REC = '    return KaldiRecognizer(_vosk_model, SAMPLE_RATE,\n                           json.dumps(list(words) + ["[unk]"], ensure_ascii=False))'
NEW_REC = ('    known = [w for w in words if _vosk_model.find_word(w) >= 0] or list(words)[:1]\n'
           '    missing = [w for w in words if w not in known]\n'
           '    if missing and not _vosk_warned.get(tuple(words)):\n'
           '        _vosk_warned[tuple(words)] = True\n'
           '        print(f"[vosk] слышу: {\', \'.join(known)} | модель не знает: {\', \'.join(missing)}")\n'
           '    return KaldiRecognizer(_vosk_model, SAMPLE_RATE,\n'
           '                           json.dumps(known + ["[unk]"], ensure_ascii=False))')
rep_all(V, OLD_REC, NEW_REC, "проверка слов по словарю распознавателя")
if "_vosk_warned = {}" not in src[V] and "_vosk_warned.get" in src[V]:
    src[V] = src[V].replace("_vosk_model = None\n", "_vosk_model = None\n_vosk_warned = {}\n", 1)

# проверка имени: любое слово из списка, а не только «атлас»
rep_all(V, '                if w["word"] != "атлас":', '                if w["word"] not in WAKE_WORDS_LOCAL:',
        "имя узнаётся в любой форме")
rep_all(V, "print(f\"[wake] отклонено: атлас ({w['conf']:.2f})\")",
        "print(f\"[wake] отклонено: {w['word']} ({w['conf']:.2f})\")", "в логе видно, какое слово услышано")
rep_all(V, "print(f\"[wake] атлас ({w['conf']:.2f})\")",
        "print(f\"[wake] {w['word']} ({w['conf']:.2f})\")", "в логе видно, какое слово сработало")
rep_all(V, 'if n_stop and ("атлас" in sure or repeated or output_is_headphones()):',
        'if n_stop and (any(w in WAKE_WORDS_LOCAL for w in sure) or repeated or output_is_headphones()):',
        "«<имя>, стоп» работает с любой формой имени")

# ---------------------------------------------------------------------------
# main.py — отмена и выключение
# ---------------------------------------------------------------------------
M = "main.py"
rep_all(M, 'if cmd in {"stop", "стоп", "хватит", "cancel", "отмена", "enough"}:',
        'if cmd in {"stop", "стоп", "хватит", "cancel", "отмена", "enough", "стой", "прекрати", "перестань",\n'
        '                   "остановись", "довольно", "не надо", "отбой", "тихо", "замолчи", "wait", "pause"}:',
        "больше слов отмены голосом")
OLD_SD = r'(?:выключ\w*|отключ\w*|выключи себя|shut\s?down|turn off|power off|turn yourself off)'
NEW_SD = (r'(?:выключ\w*|отключ\w*|выключи себя|заверши работу|завершить работу|закончи работу|закройся|'
          r'иди спать|shut\s?down|turn off|power off|turn yourself off|go to sleep|exit|quit)')
rep_all(M, OLD_SD, NEW_SD, "больше фраз выключения (голосом и текстом)")

# ---------------------------------------------------------------------------
# web_gui.py — «стоп» из чата
# ---------------------------------------------------------------------------
W = "web_gui.py"
rep_all(W, 'if text.lower().strip(" .!") in ("стоп", "stop", "отмена", "cancel", "хватит"):',
        'if text.lower().strip(" .!") in ("стоп", "stop", "отмена", "cancel", "хватит", "стой", "прекрати",\n'
        '                                        "перестань", "остановись", "довольно", "не надо", "отбой",\n'
        '                                        "тихо", "замолчи", "wait", "pause"):',
        "больше слов отмены в чате")

# ---------------------------------------------------------------------------
ok = True
for f in FILES:
    try:
        ast.parse(src[f], filename=f)
    except SyntaxError as e:
        ok = False
        report.append(f"  ! {f}: {e} — файл НЕ изменён")
        continue
    open(os.path.join(ROOT, f), "w", encoding="utf-8", newline="\n").write(src[f])
    report.append(f"  ✓ синтаксис {f}")
print("\n".join(report))
print("\nГотово. Запускай: python main.py" if ok else f"\nЕсть ошибка — пришли вывод (копия: {backup}).")
