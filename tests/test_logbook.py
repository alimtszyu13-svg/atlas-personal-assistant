"""
Тесты журнала (core/logbook.py): всё напечатанное попадает в файл, ошибки помечаются,
повторное подключение не дублирует строки.

    python tests/test_logbook.py
"""
import os
import sys
import tempfile

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main():
    import importlib.util
    spec = importlib.util.spec_from_file_location("logbook", os.path.join(ROOT, "core", "logbook.py"))
    lb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lb)
    out, err = sys.stdout, sys.stderr
    d = tempfile.mkdtemp()
    ok, total, problems = 0, 4, []
    try:
        path = lb.start(log_dir=d)
        same = lb.start(log_dir=d)
        print("[мозг] проверка журнала — по-русски")
        print("частичная ", end="")
        print("строка")
        sys.stderr.write("Traceback: что-то сломалось\n")
        sys.stdout.flush()
        text = open(path, encoding="utf-8").read()
    finally:
        sys.stdout, sys.stderr = out, err
    checks = [
        ("строки попадают в файл с временем", "[мозг] проверка журнала — по-русски" in text and text[2] == ":"),
        ("строка из частей пишется целиком", "частичная строка" in text),
        ("ошибки помечены [stderr]", "[stderr] Traceback: что-то сломалось" in text),
        ("повторное подключение не дублирует", same == path and text.count("проверка журнала") == 1),
    ]
    for name, good in checks:
        print(f"  {'✓' if good else '✗'} {name}")
        ok += bool(good)
    print(f"\nТестов пройдено: {ok} из {total}")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
