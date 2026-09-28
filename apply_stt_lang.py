"""
«Podtwierdza jej sprawienie» вместо «Подтверждаю исправление».

В английском режиме Whisper сам угадывает язык и иногда принимает русскую речь
за польскую. Теперь, если он определил что-то кроме английского и русского,
фраза перераспознаётся как русская.

Запуск из корня проекта:  python apply_stt_lang.py
Резервная копия: backup_stt_<время>/voice.py
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
backup = os.path.join(ROOT, time.strftime("backup_stt_%Y%m%d_%H%M%S"))
os.makedirs(backup, exist_ok=True)
shutil.copy2(P, os.path.join(backup, "voice.py"))
print(f"Резервная копия: {backup}")

OLD = '''        else:
            result = groq_client.audio.transcriptions.create(
                file=(temp_path, data), model="whisper-large-v3-turbo")
        text = (result.text or "").strip()'''
NEW = '''        else:
            result = groq_client.audio.transcriptions.create(
                file=(temp_path, data), model="whisper-large-v3-turbo",
                response_format="verbose_json")
            lang = (getattr(result, "language", "") or "").lower()
            if lang and lang not in ("en", "english", "ru", "russian"):
                # Whisper иногда принимает русскую речь за польскую — переслушиваем как русскую
                print(f"[Whisper] язык «{lang}» — перераспознаю как русский")
                result = groq_client.audio.transcriptions.create(
                    file=(temp_path, data), model="whisper-large-v3-turbo", language="ru")
        text = (result.text or "").strip()'''

if NEW in src:
    print("  ✓ voice.py: уже исправлено")
elif src.count(OLD) == 1:
    src = src.replace(OLD, NEW, 1)
    ast.parse(src, filename="voice.py")
    open(P, "w", encoding="utf-8", newline="\n").write(src)
    print("  ✓ voice.py: чужой язык → перераспознавание по-русски\n\nГотово. Запускай: python main.py")
else:
    print("  ! voice.py: фрагмент распознавания не найден — пришли функцию _transcribe_audio")
