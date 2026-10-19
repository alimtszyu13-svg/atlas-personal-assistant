"""
Все тесты Atlas одной командой:  python tests/run_all.py
Каждый набор — отдельным процессом (как в установщиках); в конце — итог. Код выхода 1, если что-то упало.
"""
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for _s in (sys.stdout, sys.stderr):                      # ✓/✗ и кириллица в файл/перенаправление — всегда UTF-8
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main() -> int:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", ATLAS_TESTING="1")
    suites = sorted(f for f in os.listdir(os.path.join(ROOT, "tests")) if f.startswith("test_") and f.endswith(".py"))
    failed, total_ok, total, t0 = [], 0, 0, time.time()
    for s in suites:
        t1 = time.time()
        try:
            r = subprocess.run([sys.executable, os.path.join(ROOT, "tests", s)], cwd=ROOT, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=600, env=env)
            out, code = r.stdout + r.stderr, r.returncode
        except subprocess.TimeoutExpired:
            out, code = "тесты не закончились за 10 минут", 1
        m = re.search(r"Тестов пройдено: (\d+) из (\d+)", out)
        good = code == 0 and m and m.group(1) == m.group(2)
        if m:
            total_ok += int(m.group(1))
            total += int(m.group(2))
        print(f"{'✓' if good else '✗'} {s}: {m.group(0) if m else 'итоговой строки нет'} ({time.time() - t1:.1f} с)")
        if not good:
            failed.append(s)
            print("\n".join("    " + ln for ln in out.splitlines()[-40:]))
    print(f"\nИтого: {total_ok} из {total} тестов, {len(suites) - len(failed)} из {len(suites)} наборов, "
          f"{time.time() - t0:.0f} с")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
