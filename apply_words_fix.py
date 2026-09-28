"""
Срочное исправление после apply_words.py.

Проверка слов по словарю использовала Model.find_word, а в установленной у тебя
версии Vosk такой функции нет — голосовой цикл падал сразу после запуска.
Теперь: если функция есть — неизвестные слова отсеиваются и пишутся в лог;
если нет — все слова передаются как есть (Vosk сам молча пропускает незнакомые).

Запуск из корня проекта:  python apply_words_fix.py
"""
import ast
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(ROOT, "voice.py")
if not os.path.exists(P):
    sys.exit("Запусти из корня проекта Atlas (рядом с voice.py).")
src = open(P, encoding="utf-8").read()
OLD = "    known = [w for w in words if _vosk_model.find_word(w) >= 0] or list(words)[:1]\n"
NEW = ("    finder = getattr(_vosk_model, \"find_word\", None)      # есть не во всех версиях Vosk\n"
       "    known = ([w for w in words if finder(w) >= 0] if finder else list(words)) or list(words)[:1]\n")
n = src.count(OLD)
if not n:
    print("  ✓ voice.py: уже исправлено" if NEW in src else "  ! voice.py: строка не найдена — пришли функцию _vosk_recognizer")
    sys.exit(0)
backup = os.path.join(ROOT, time.strftime("backup_wordsfix_%Y%m%d_%H%M%S"))
os.makedirs(backup, exist_ok=True)
shutil.copy2(P, os.path.join(backup, "voice.py"))
src = src.replace(OLD, NEW)
ast.parse(src, filename="voice.py")
open(P, "w", encoding="utf-8", newline="\n").write(src)
print(f"  ✓ voice.py: исправлено мест: {n} (копия: {backup})\n\nГотово. Запускай: python main.py")
