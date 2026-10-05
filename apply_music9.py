"""
Громкость и перемотка музыки — правильными инструментами.

Что было: «сделаем музыку погромче» и «перемотай на 15 секунд вперёд» Atlas выполнял
инструментами для ВИДЕО во встроенном браузере (media_volume, media_seek). Браузер был закрыт —
эти инструменты его запускали (3,7 с) и ничего не меняли, а Atlas говорил «сделал».

  • Видео-инструменты браузера больше не запускают закрытый браузер: честно отвечают
    «браузер не открыт — для музыки volume_up / spotify_seek…». Это касается и быстрого пути.
  • Новый инструмент spotify_seek(секунды): перемотка трека клавишами Spotify (Shift+→/←).
    Шаг одного нажатия — SPOTIFY_SEEK_STEP в .env (по умолчанию 5 с).
  • Быстрый путь понимает «сделаем музыку погромче», «чуть-чуть тише»,
    «перемотай на 15 секунд вперёд», «перемотай назад на 30 секунд».
  • В разговоре о музыке у модели всегда есть громкость и перемотка.

    python apply_music9.py
    python apply_music9.py --rollback
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
PATCHES = [('system_control.py', 'перемотка Spotify: spotify_seek(секунды)', 'def play_on_spotify(query: str = "", autoplay: bool = True) -> str:', 'SPOTIFY_SEEK_STEP = float(os.getenv("SPOTIFY_SEEK_STEP") or 5)     # на сколько секунд Spotify перематывает за одно Shift+стрелка\n\n\ndef spotify_seek(seconds: int = 15) -> str:\n    """Перематывает трек в Spotify вперёд (seconds > 0) или назад (seconds < 0) клавишами самого\n    Spotify — Shift+→ / Shift+←, по SPOTIFY_SEEK_STEP секунд за нажатие."""\n    import time as _time\n    wins = _spotify_windows()\n    if not wins:\n        return "Spotify не открыт — перематывать нечего."\n    try:\n        import pyautogui\n    except ImportError:\n        return "Не удалось перемотать: нет pyautogui."\n    s = int(seconds or 15)\n    n = max(1, round(abs(s) / SPOTIFY_SEEK_STEP))\n    _bring_to_front(wins[0][0])\n    _time.sleep(0.2)\n    for _ in range(n):\n        pyautogui.hotkey("shift", "right" if s > 0 else "left")\n        _time.sleep(0.05)\n    return f"Перемотал Spotify {\'вперёд\' if s > 0 else \'назад\'} на {int(n * SPOTIFY_SEEK_STEP)} секунд."\n\n\ndef play_on_spotify(query: str = "", autoplay: bool = True) -> str:'), ('fast_commands.py', 'быстрый путь: видео в браузере — только если браузер уже открыт (не запускать его)', '        import browser_agent\n        fn = getattr(browser_agent, fn_name, None)\n        if fn is None:\n            return None', '        import browser_agent\n        if not getattr(browser_agent, "_browser_alive", lambda: True)():\n            return None                        # браузер закрыт — не запускаем его ради громкости/паузы\n        fn = getattr(browser_agent, fn_name, None)\n        if fn is None:\n            return None'), ('fast_commands.py', 'быстрый путь: «сделаем музыку погромче», «чуть тише» и т.п.', 'def _match_app(target: str):', '_LOUDER = re.compile(r"(?:(?:сделай(?:те)?|сделаем|давай)\\s+)?(?:(?:музыку|звук|громкость)\\s+)?"\n                     r"(?:(?:немного|чуть|чуть-чуть|ещё|еще)\\s+)?(?:по)?громче(?:\\s+(?:музыку|звук|пожалуйста))?")\n_QUIETER = re.compile(r"(?:(?:сделай(?:те)?|сделаем|давай)\\s+)?(?:(?:музыку|звук|громкость)\\s+)?"\n                      r"(?:(?:немного|чуть|чуть-чуть|ещё|еще)\\s+)?(?:по)?тише(?:\\s+(?:музыку|звук|пожалуйста))?")\n_SEEK = re.compile(r"(?:перемотай|промотай|отмотай)(?:\\s+(?:на\\s+)?(?P<n1>\\d+)\\s*(?:секунд\\w*|сек))?\\s+"\n                   r"(?P<dir>вперёд|вперед|назад)(?:\\s+(?:на\\s+)?(?P<n2>\\d+)\\s*(?:секунд\\w*|сек))?")\n\n\ndef _match_app(target: str):'), ('fast_commands.py', 'быстрый путь: громче — и в длинных формулировках', '    if t in ("громче", "сделай громче", "погромче", "louder", "volume up"):', '    if t in ("громче", "сделай громче", "погромче", "louder", "volume up") or _LOUDER.fullmatch(t):'), ('fast_commands.py', 'быстрый путь: тише — и в длинных формулировках', '    if t in ("тише", "сделай тише", "потише", "quieter", "volume down"):', '    if t in ("тише", "сделай тише", "потише", "quieter", "volume down") or _QUIETER.fullmatch(t):'), ('fast_commands.py', 'быстрый путь: «перемотай на 15 секунд вперёд» — видео в браузере или Spotify', '    if t in ("перемотай вперёд", "перемотай вперед", "вперёд", "вперед", "forward", "seek forward"):', '    m = _SEEK.fullmatch(t)\n    if m:\n        back = m.group("dir") == "назад"\n        result = _ok(_browser_media("media_seek", direction="backward" if back else "forward"))\n        if result:\n            return result                          # видео в браузере Atlas\n        n = int(m.group("n1") or m.group("n2") or 15)\n        from system_control import spotify_seek\n        return _ok(spotify_seek(-n if back else n))\n\n    if t in ("перемотай вперёд", "перемотай вперед", "вперёд", "вперед", "forward", "seek forward"):'), ('brain/tools.py', 'в разговоре о музыке у модели есть громкость и перемотка', '     {"play_on_spotify", "play_pause_media", "next_track", "previous_track", "play_on_rezka"}),', '     {"play_on_spotify", "play_pause_media", "next_track", "previous_track", "play_on_rezka",\n      "volume_up", "volume_down", "spotify_seek"}),'), ('brain/planner.py', 'короткий ответ после перемотки Spotify', '    "search_file_content", "open_found_file", "open_search_result",', '    "search_file_content", "open_found_file", "open_search_result", "spotify_seek",'), ('brain/prompt.py', 'инструкция: громкость и перемотка музыки — не видео-инструментами браузера', '"Pause and skip → play_pause_media, next_track. If play_on_spotify says it played something, trust it and say "', '"Pause and skip → play_pause_media, next_track; louder/quieter → volume_up / volume_down; seek in a song → "\n    "spotify_seek(seconds, negative = back). media_* tools are ONLY for videos in Atlas\'s own browser (Rezka, "\n    "Netflix). If play_on_spotify says it played something, trust it and say "'), ('brain/features.py', 'перемотка Spotify — инструмент; видео-инструменты не запускают закрытый браузер', '    # Сервисы, недоступные в регионе пользователя, модели не показываются (по умолчанию —', '    # --- перемотка Spotify\n    import system_control as _sc\n    if hasattr(_sc, "spotify_seek"):\n        register("spotify_seek", _sc.spotify_seek, "Seeks the current Spotify track forward (positive seconds) or back "\n                 "(negative), e.g. 15 or -30. For music in Spotify — not for videos in the browser.",\n                 {"seconds": {"type": "integer"}}, ["seconds"], group="media")\n\n    # --- видео-инструменты браузера: при закрытом браузере не запускают его, а честно говорят «не открыт»\n    import functools as _ft\n    import browser_agent as _ba2\n\n    def _video_only(name, fn):\n        @_ft.wraps(fn)\n        def wrapper(*a, **kw):\n            if not getattr(_ba2, "_browser_alive", lambda: True)():\n                return (f"Браузер не открыт: {name} управляет только видео в браузере Atlas. Для музыки — "\n                        "volume_up / volume_down, play_pause_media, next_track, spotify_seek.")\n            return fn(*a, **kw)\n        return wrapper\n    for _n in ("media_play_pause", "media_seek", "media_volume", "media_player_fullscreen", "next_episode", "skip_intro"):\n        if _n in AVAILABLE_FUNCTIONS:\n            AVAILABLE_FUNCTIONS[_n] = _video_only(_n, getattr(AVAILABLE_FUNCTIONS[_n], "__wrapped__", AVAILABLE_FUNCTIONS[_n]))\n\n    # Сервисы, недоступные в регионе пользователя, модели не показываются (по умолчанию —'), ('tests/stubs.py', 'тестовые заглушки: браузер Atlas закрыт', '    sys.modules["database"].log_task = lambda *a: None', '    sys.modules["database"].log_task = lambda *a: None\n    sys.modules["browser_agent"]._browser_alive = lambda: False'), ('tests/test_commands.py', 'заглушка браузера знает, открыт ли он', '    _mod("browser_agent", **{n: _browser(n) for n in ("media_volume", "media_play_pause", "skip_intro", "next_episode",\n                                                      "media_player_fullscreen", "media_seek")})', '    _mod("browser_agent", _browser_alive=lambda: STATE["browser_open"],\n         **{n: _browser(n) for n in ("media_volume", "media_play_pause", "skip_intro", "next_episode",\n                                     "media_player_fullscreen", "media_seek")})'), ('tests/test_commands.py', 'заглушка перемотки Spotify', '    _mod("system_control", open_app=_rec("open_app"), close_app=_rec("close_app"),', '    _mod("system_control", open_app=_rec("open_app"), close_app=_rec("close_app"), spotify_seek=_rec("spotify_seek"),'), ('tests/test_commands.py', 'браузер закрыт — видео-команды его не трогают', '    assert fast("тише")[1] == ["media_volume", "volume_down"], "браузер закрыт → системная громкость"\n    assert fast("тише", browser=True)[1] == ["media_volume"]\n    assert fast("пауза")[1] == ["media_play_pause", "play_pause_media"]', '    assert fast("тише")[1] == ["volume_down"], "браузер закрыт → системная громкость, браузер не запускаем"\n    assert fast("тише", browser=True)[1] == ["media_volume"]\n    assert fast("пауза")[1] == ["play_pause_media"]'), ('tests/test_commands.py', 'громкость и перемотка — фразы из живого лога', '@test\ndef unknown_goes_to_brain():', '@test\ndef volume_and_seek_in_natural_phrases():\n    assert fast("сделаем музыку погромче")[1] == ["volume_up"]\n    assert fast("Атлас, сделай чуть-чуть тише")[1] == ["volume_down"]\n    assert fast("перемотай на 15 секунд вперёд")[2][0][:2] == ("spotify_seek", (15,))\n    assert fast("перемотай назад на 30 секунд")[2][0][:2] == ("spotify_seek", (-30,))\n    assert fast("перемотай вперёд")[2][0][:2] == ("spotify_seek", (15,))\n    assert fast("перемотай вперёд", browser=True)[1] == ["media_seek"], "видео в браузере — его плеер"\n    assert fast("громче музыка у соседей")[0] is None, "не команда громкости"\n\n\n@test\ndef unknown_goes_to_brain():'), ('tests/test_brain.py', 'в разговоре о музыке — громкость и перемотка; видео-инструменты не запускают браузер', '@test\ndef region_blocked_music_service_is_hidden():', '@test\ndef music_talk_brings_volume_and_seek_and_video_tools_stay_honest():\n    s = fresh([{"content": "ок"}])\n    state.conversation_history.append({"role": "assistant", "content": "Включил «Eminem - Stan» в Spotify."})\n    ai_brain.ask_ai(RU + "Сделаем музыку погромче")\n    names = {t["function"]["name"] for t in s.requests[0]["tools"]}\n    assert {"volume_up", "volume_down", "spotify_seek"} <= names, sorted(names)\n    res, ok, _ = tools._run_one_tool("media_volume", {"action": "up"})\n    assert "не открыт" in str(res) and tools._TRACE_FAIL.search(str(res)), res\n    assert "media_* tools are ONLY for videos" in ai_brain.SYSTEM_PROMPT\n\n\n@test\ndef region_blocked_music_service_is_hidden():'), ('tests/test_spotify.py', 'заглушка клавиатуры: сочетания клавиш', '        press=lambda k: (S["keys"].append(k), S.update(title="Artist - Song") if S["space_works"] else None))', '        press=lambda k: (S["keys"].append(k), S.update(title="Artist - Song") if S["space_works"] else None),\n        hotkey=lambda *k: S["keys"].append("+".join(k)))'), ('tests/test_spotify.py', 'перемотка Spotify', '@test\ndef green_cover_on_the_left_is_not_a_button():', '@test\ndef spotify_seek_forward_and_back():\n    reset()\n    S["front"] = False\n    r = M.spotify_seek(15)\n    assert S["keys"] == ["shift+right"] * 3 and S["front"] and "вперёд на 15" in r, (r, S["keys"])\n    reset()\n    r = M.spotify_seek(-30)\n    assert S["keys"] == ["shift+left"] * 6 and "назад на 30" in r, (r, S["keys"])\n    reset(hidden=True)\n    assert "не открыт" in M.spotify_seek(15) and not S["keys"]\n\n\n@test\ndef green_cover_on_the_left_is_not_a_button():')]
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
    cands = sorted(d for d in os.listdir(ROOT) if d.startswith("backup_music9_"))
    if not cands:
        sys.exit("Резервной копии нет.")
    restore(p(cands[-1]))
    sys.exit(0)
if "> 0.6" not in open(p("system_control.py"), encoding="utf-8").read():
    sys.exit("Сначала поставь apply_music8.py.")

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

backup = p(time.strftime("backup_music9_%Y%m%d_%H%M%S"))
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
        with open(p("music9_test_output.txt"), "w", encoding="utf-8") as f:
            f.write(r.stdout + "\n" + r.stderr)
        restore(backup)
        sys.exit("✗ Проверка не прошла — всё возвращено. Пришли music9_test_output.txt.")
print("\nГотово. Проверь: «сделаем музыку погромче», «перемотай на 15 секунд вперёд».")
