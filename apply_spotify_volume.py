"""
Громкость самого Spotify.

  • «Громче» / «тише», когда играет Spotify, — двигается ползунок Spotify (Ctrl+↑ / Ctrl+↓),
    а не громкость всего компьютера. Spotify не играет — громкость компьютера, как раньше.
  • «Громкость Spotify на 80», «сделай звук в Spotify погромче, скажем на 80» — выставить уровень.
    Текущую громкость Spotify узнать неоткуда, поэтому сначала до нуля, потом вверх: звук на миг проседает.
    Шаг одного нажатия — SPOTIFY_VOLUME_STEP в .env (по умолчанию 10%).
  • Модель знает об этом (инструмент spotify_volume).

    python apply_spotify_volume.py
    python apply_spotify_volume.py --rollback
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
PATCHES = [('system_control.py', 'громкость самого Spotify (Ctrl+↑/↓)', 'SPOTIFY_SEEK_STEP = float(os.getenv("SPOTIFY_SEEK_STEP") or 5)', 'SPOTIFY_VOLUME_STEP = float(os.getenv("SPOTIFY_VOLUME_STEP") or 10)   # на сколько % Ctrl+↑/↓ двигает ползунок Spotify\n\n\ndef spotify_is_playing() -> bool:\n    """Играет ли сейчас Spotify (по заголовку его окна)."""\n    return _is_playing(_spotify_title())\n\n\ndef spotify_volume(action: str = "up", percent: int = 0, steps: int = 1) -> str:\n    """Громкость самого Spotify — его ползунок, а не громкость компьютера.\n    action=\'up\' / \'down\' — на steps шагов (Ctrl+↑ / Ctrl+↓); action=\'set\' — выставить percent %\n    (текущий уровень Spotify узнать неоткуда: сначала до нуля, потом вверх — звук на миг проседает)."""\n    import time as _time\n    wins = _spotify_windows()\n    if not wins:\n        return "Spotify не открыт."\n    try:\n        import pyautogui\n    except ImportError:\n        return "Не удалось: нет pyautogui."\n    _bring_to_front(wins[0][0])\n    _time.sleep(0.2)\n\n    def press(key, n):\n        for _ in range(n):\n            pyautogui.hotkey("ctrl", key)\n            _time.sleep(0.04)\n    a = (action or "up").strip().lower()\n    if a == "set":\n        p = max(0, min(100, int(percent or 0)))\n        press("down", int(100 / SPOTIFY_VOLUME_STEP) + 2)\n        n = round(p / SPOTIFY_VOLUME_STEP)\n        press("up", n)\n        return f"Громкость Spotify — {int(n * SPOTIFY_VOLUME_STEP)}%."\n    down = a in ("down", "тише", "quieter")\n    press("down" if down else "up", max(1, int(steps or 1)))\n    return f"Сделал Spotify {\'тише\' if down else \'громче\'}."\n\n\nSPOTIFY_SEEK_STEP = float(os.getenv("SPOTIFY_SEEK_STEP") or 5)'), ('fast_commands.py', '«сделай звук в Spotify погромче» тоже понимается', '_LOUDER = re.compile(r"(?:(?:сделай(?:те)?|сделаем|давай)\\s+)?(?:(?:музыку|звук|громкость)\\s+)?"', '_SPOTIFY_WORD = r"(?:(?:в|на)\\s+(?:spotify|спотифа[йе]|спотике)\\s+)?"\n_LOUDER = re.compile(r"(?:(?:сделай(?:те)?|сделаем|давай)\\s+)?(?:(?:музыку|звук|громкость)\\s+)?" + _SPOTIFY_WORD +'), ('fast_commands.py', '«сделай звук в Spotify потише» тоже понимается', '_QUIETER = re.compile(r"(?:(?:сделай(?:те)?|сделаем|давай)\\s+)?(?:(?:музыку|звук|громкость)\\s+)?"', '_QUIETER = re.compile(r"(?:(?:сделай(?:те)?|сделаем|давай)\\s+)?(?:(?:музыку|звук|громкость)\\s+)?" + _SPOTIFY_WORD +'), ('fast_commands.py', 'громче: если играет Spotify — его ползунок', '        from system_advanced import volume_up\n        return _ok(volume_up())', '        from system_control import spotify_is_playing, spotify_volume\n        if spotify_is_playing():                   # играет Spotify — двигаем его ползунок\n            return _ok(spotify_volume("up"))\n        from system_advanced import volume_up\n        return _ok(volume_up())'), ('fast_commands.py', 'тише: если играет Spotify — его ползунок', '        from system_advanced import volume_down\n        return _ok(volume_down())', '        from system_control import spotify_is_playing, spotify_volume\n        if spotify_is_playing():\n            return _ok(spotify_volume("down"))\n        from system_advanced import volume_down\n        return _ok(volume_down())'), ('fast_commands.py', '«громкость Spotify на 80», «сделай звук в спотифае на 80»', '    m = re.match(r"^(?:громкость|volume)\\s+(?:на\\s+)?(\\d{1,3})\\s*%?$", t)', '    m = re.fullmatch(r"(?:(?:сделай|поставь|выставь)\\s+)?(?:громкость|звук)\\s+(?:в\\s+|у\\s+)?(?:spotify|спотифа[йея]|музыки)"\n                     r"[\\s,]+(?:(?:погромче|потише|скажем|где-то|примерно)[\\s,]+)*(?:на\\s+)?(\\d{1,3})"\n                     r"(?:\\s*(?:%|процент\\w*))?", t)\n    if m:\n        from system_control import spotify_volume\n        return _ok(spotify_volume("set", int(m.group(1))))\n    m = re.match(r"^(?:громкость|volume)\\s+(?:на\\s+)?(\\d{1,3})\\s*%?$", t)'), ('brain/tools.py', 'в разговоре о музыке — и громкость Spotify', '      "volume_up", "volume_down", "spotify_seek", "spotify_library"}),', '      "volume_up", "volume_down", "spotify_seek", "spotify_library", "spotify_volume"}),'), ('brain/planner.py', 'короткий ответ после громкости Spotify', '"spotify_seek", "spotify_library",', '"spotify_seek", "spotify_library", "spotify_volume",'), ('brain/prompt.py', 'инструкция: громкость Spotify — spotify_volume, компьютера — volume_up/down', '"Pause and skip → play_pause_media, next_track; louder/quieter → volume_up / volume_down; seek in a song → "', '"Pause and skip → play_pause_media, next_track; louder/quieter while Spotify plays → spotify_volume(\'up\'/\'down\'), "\n    "a set Spotify level (\'Spotify на 80\') → spotify_volume(\'set\', percent); the whole computer → volume_up / "\n    "volume_down / set_volume; seek in a song → "'), ('brain/features.py', 'инструмент spotify_volume', '    if hasattr(_sc, "spotify_play_library"):', '    if hasattr(_sc, "spotify_volume"):\n        register("spotify_volume", _sc.spotify_volume, "Spotify\'s own volume slider (not the computer volume): "\n                 "action=\'up\'/\'down\' (steps = how many notches), or action=\'set\' with percent 0-100.",\n                 {"action": {"type": "string", "description": "\'up\', \'down\' or \'set\'"}, "percent": {"type": "integer"},\n                  "steps": {"type": "integer"}}, ["action"], group="media")\n    if hasattr(_sc, "spotify_play_library"):'), ('tests/test_commands.py', 'заглушки: играет ли Spotify, громкость Spotify', 'spotify_play_library=_rec("spotify_play_library"),', 'spotify_play_library=_rec("spotify_play_library"),\n         spotify_is_playing=lambda: STATE.get("spotify_playing", False), spotify_volume=_rec("spotify_volume"),'), ('tests/test_commands.py', 'громкость Spotify — фразы из живого лога', '@test\ndef unknown_goes_to_brain():', '@test\ndef spotify_volume_phrases():\n    STATE["spotify_playing"] = True\n    try:\n        assert fast("Сделай звук погромче.")[2][0][:2] == ("spotify_volume", ("up",)), "играет Spotify — его ползунок"\n        assert fast("сделай чуть тише")[2][0][:2] == ("spotify_volume", ("down",))\n        assert fast("Сделай звук в Spotify погромче, скажем на 80.")[2][0][:2] == ("spotify_volume", ("set", 80))\n        assert fast("громкость спотифая на 50 процентов")[2][0][:2] == ("spotify_volume", ("set", 50))\n    finally:\n        STATE["spotify_playing"] = False\n    assert fast("Сделай звук погромче.")[1] == ["volume_up"], "Spotify не играет — громкость компьютера"\n    assert fast("громкость 40")[2][0][:2] == ("set_volume", (40,)), "системная громкость числом — как раньше"\n\n\n@test\ndef unknown_goes_to_brain():'), ('tests/test_spotify.py', 'громкость самого Spotify', '@test\ndef green_cover_on_the_left_is_not_a_button():', '@test\ndef spotify_volume_keys():\n    reset()\n    S["front"] = False\n    r = M.spotify_volume("up")\n    assert S["keys"] == ["ctrl+up"] and S["front"] and "громче" in r, (r, S["keys"])\n    reset()\n    r = M.spotify_volume("down", steps=2)\n    assert S["keys"] == ["ctrl+down"] * 2 and "тише" in r, S["keys"]\n    reset()\n    r = M.spotify_volume("set", 80)\n    assert S["keys"] == ["ctrl+down"] * 12 + ["ctrl+up"] * 8 and "80%" in r, (r, S["keys"])\n    reset(hidden=True)\n    assert "не открыт" in M.spotify_volume("up") and not S["keys"]\n\n\n@test\ndef green_cover_on_the_left_is_not_a_button():'), ('tests/test_brain.py', 'в разговоре о музыке есть громкость Spotify', '    assert "spotify_library(which=\'liked\'" in ai_brain.SYSTEM_PROMPT', '    assert "spotify_library(which=\'liked\'" in ai_brain.SYSTEM_PROMPT\n    s = fresh([{"content": "ок"}])\n    state.conversation_history.append({"role": "assistant", "content": "Включил любимые треки: «Imagine Dragons - Believer»."})\n    ai_brain.ask_ai(RU + "Сделай звук в Spotify погромче, скажем на 80.")\n    assert "spotify_volume" in {t["function"]["name"] for t in s.requests[0]["tools"]}\n    assert "spotify_volume(\'set\', percent)" in ai_brain.SYSTEM_PROMPT')]
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
    cands = sorted(d for d in os.listdir(ROOT) if d.startswith("backup_spvolume_"))
    if not cands:
        sys.exit("Резервной копии нет.")
    restore(p(cands[-1]))
    sys.exit(0)
if "def spotify_play_library" not in open(p("system_control.py"), encoding="utf-8").read():
    sys.exit("Сначала поставь apply_playlists.py.")

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

backup = p(time.strftime("backup_spvolume_%Y%m%d_%H%M%S"))
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
        with open(p("spvolume_test_output.txt"), "w", encoding="utf-8") as f:
            f.write(r.stdout + "\n" + r.stderr)
        restore(backup)
        sys.exit("✗ Проверка не прошла — всё возвращено. Пришли spvolume_test_output.txt.")
print("\nГотово. Проверь при играющем Spotify: «сделай погромче», «громкость Spotify на 50».")
