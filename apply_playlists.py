"""
Своя музыка в Spotify: любимые треки и свои плейлисты.

  • «Включи мои любимые треки», «поставь любимое», «play my liked songs» — Atlas открывает
    «Любимые треки» напрямую (spotify:collection:tracks) и жмёт большую кнопку Play слева под
    заголовком. Если они уже играли и кнопка поставила на паузу — нажимает ещё раз.
  • «Включи мой плейлист для учёбы» — поиск твоего плейлиста по названию и запуск.
  • Модель знает об этом (инструмент spotify_library) и в разговоре о музыке он всегда под рукой.

    python apply_playlists.py
    python apply_playlists.py --rollback
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
PATCHES = [('system_control.py', 'Spotify: любимые треки и свои плейлисты', 'SPOTIFY_SEEK_STEP = float(os.getenv("SPOTIFY_SEEK_STEP") or 5)', '_LIKED_WORDS = ("liked", "liked songs", "favorites", "favourites", "любимые", "любимые треки", "любимые песни",\n                "понравившиеся", "сохранённые", "сохраненные", "моя музыка", "")\n\n\ndef spotify_play_library(which: str = "liked") -> str:\n    """Твоя библиотека Spotify: which=\'liked\' — «Любимые треки» (открываются напрямую, без поиска);\n    иначе — плейлист по названию (Spotify показывает твои плейлисты в поиске)."""\n    w = (which or "").strip()\n    if w.lower() in _LIKED_WORDS:\n        return _spotify_play_page("spotify:collection:tracks", "любимые треки")\n    return play_on_spotify(w)\n\n\ndef _spotify_play_page(uri: str, label: str, timeout: float = 12.0) -> str:\n    """Открыть страницу (плейлист, «Любимые треки») и нажать её большую кнопку Play — она слева под\n    заголовком. Если эта музыка уже играла, нажатие ставит на паузу — тогда жмём ещё раз."""\n    import time as _time\n    try:\n        import pyautogui\n    except ImportError:\n        return "Не удалось: нет pyautogui."\n    wins, before = _spotify_windows(), None\n    if wins:\n        _bring_to_front(wins[0][0])\n        _time.sleep(0.35)\n        before = _results_signature(wins[0][2])\n    subprocess.Popen(f"start {uri}", shell=True)\n    deadline = _time.time() + timeout\n    while _time.time() < deadline:\n        wins = _spotify_windows()\n        if wins:\n            break\n        _time.sleep(0.3)\n    if not wins:\n        return "Окно Spotify не появилось."\n    hwnd, title_before, rect = wins[0]\n    _bring_to_front(hwnd)\n    if before is None:\n        _time.sleep(2.0)\n    _wait_results_changed(before, rect, timeout=min(4.0, max(1.0, deadline - _time.time() - 2)))\n    point = None\n    while _time.time() < deadline:\n        point = _find_green_play(rect, x_from=0.07)\n        if point:\n            break\n        _time.sleep(0.4)\n    if not point:\n        _save_spotify_snapshot(rect, "library")\n        return f"Открыл {label} в Spotify, но не нашёл кнопку Play."\n    old = pyautogui.position()\n\n    def click_and_watch(seconds: float):\n        pyautogui.click(*point)\n        pyautogui.moveTo(*old)\n        until = _time.time() + seconds\n        title = ""\n        while _time.time() < until:\n            title = _spotify_title()\n            if _is_playing(title) and title != title_before:\n                return "new", title\n            if not _is_playing(title) and _is_playing(title_before):\n                return "paused", title\n            _time.sleep(0.25)\n        return ("playing" if _is_playing(title) else "unknown"), title\n\n    status, title = click_and_watch(4.0)\n    if status == "new":\n        return f"Включил {label}: «{title}»."\n    if status == "paused":                        # это уже играло — кнопка сработала как пауза\n        status, title = click_and_watch(3.0)\n        if _is_playing(_spotify_title()):\n            return f"{label.capitalize()} уже играли — продолжил: «{_spotify_title()}»."\n    return f"Нажал Play в «{label}», но не уверен, что заиграло."\n\n\nSPOTIFY_SEEK_STEP = float(os.getenv("SPOTIFY_SEEK_STEP") or 5)'), ('fast_commands.py', 'быстрый путь: «включи мои любимые треки», «включи мой плейлист …»', '    # ---------- музыка: сразу в приложение ----------', '    # ---------- моя библиотека Spotify: любимые треки и свои плейлисты ----------\n    if not multi and re.fullmatch(r"(?:включи|поставь|запусти|врубай|play)\\s+(?:мне\\s+)?(?:мои\\s+|мою\\s+)?"\n                                  r"(?:любимые|понравившиеся|сохран[её]нные)\\s*(?:треки|песни|музыку)?|"\n                                  r"(?:включи|поставь|запусти|play)\\s+(?:мне\\s+)?(?:любимое|мою музыку|my liked songs|"\n                                  r"liked songs|my favorites|my favourites)", t):\n        from system_control import spotify_play_library\n        return _ok(spotify_play_library("liked"))\n    m = re.fullmatch(r"(?:включи|поставь|запусти|play)\\s+(?:мне\\s+)?(?:мой|my)\\s+(?:плейлист|playlist)\\s+(.+)", t)\n    if m and not multi:\n        from system_control import spotify_play_library\n        return _ok(spotify_play_library(m.group(1).strip(" «»\\"\'")))\n\n    # ---------- музыка: сразу в приложение ----------'), ('brain/tools.py', 'в разговоре о музыке — и библиотека Spotify', '      "volume_up", "volume_down", "spotify_seek"}),', '      "volume_up", "volume_down", "spotify_seek", "spotify_library"}),'), ('brain/planner.py', 'короткий ответ после запуска своей музыки', '    "search_file_content", "open_found_file", "open_search_result", "spotify_seek",', '    "search_file_content", "open_found_file", "open_search_result", "spotify_seek", "spotify_library",'), ('brain/prompt.py', 'инструкция: «мои любимые треки», «мой плейлист» — spotify_library', '    "Netflix). If play_on_spotify says it played something, trust it and say "', '    "Netflix). The user\'s own music (\'мои любимые треки\', \'мой плейлист …\', \'my liked songs\') → "\n    "spotify_library(which=\'liked\' or the playlist name). If play_on_spotify says it played something, trust it and say "'), ('brain/features.py', 'инструмент spotify_library', '    # --- видео-инструменты браузера: при закрытом браузере не запускают его, а честно говорят «не открыт»', '    if hasattr(_sc, "spotify_play_library"):\n        register("spotify_library", _sc.spotify_play_library, "Plays the user\'s own Spotify library: which=\'liked\' "\n                 "for their Liked Songs (\'мои любимые треки\', \'любимое\'), or the name of one of their playlists.",\n                 {"which": {"type": "string", "description": "\'liked\' or a playlist name"}}, ["which"], group="media")\n\n    # --- видео-инструменты браузера: при закрытом браузере не запускают его, а честно говорят «не открыт»'), ('tests/test_spotify.py', 'тестовый экран: страница плейлиста (кнопка слева под заголовком)', '    if S["button"] == "genre":', '    if S["button"] == "playlist_page" and S["popen"]:            # «Любимые треки»: заголовок и кнопка слева\n        d.rectangle((int(0.07 * w), int(0.10 * h), int(0.98 * w), int(0.38 * h)), fill=(80, 60, 160))\n        cx, cy = int(0.13 * w), int(0.47 * h)\n        d.ellipse((cx - 28, cy - 28, cx + 28, cy + 28), fill=(30, 215, 96))\n    if S["button"] == "genre":'), ('tests/test_spotify.py', 'тестовый клик: кнопка играющего плейлиста ставит на паузу', '    if _page_old():                                  # кнопка прошлого (играющего) результата = пауза', '    if S.get("toggle"):                              # кнопка того, что уже играет, — пауза / снова играть\n        S["title"] = "Spotify Premium" if not S["title"].lower().startswith("spotify") else S["title_on_click"]\n        return\n    if _page_old():                                  # кнопка прошлого (играющего) результата = пауза'), ('tests/test_spotify.py', 'любимые треки и свои плейлисты', '@test\ndef green_cover_on_the_left_is_not_a_button():', '@test\ndef liked_songs_open_directly_and_play():\n    reset(button="playlist_page", title_on_click="Imagine Dragons - Believer")\n    r = M.spotify_play_library("liked")\n    x, y = S["clicks"][0]\n    assert S["popen"] == ["start spotify:collection:tracks"] and "Believer" in r, (r, S["popen"])\n    assert abs(x - (RECT[0] + 0.13 * 1200)) < 15 and abs(y - (RECT[1] + 0.47 * 800)) < 15, S["clicks"]\n\n\n@test\ndef liked_songs_already_playing_are_not_left_paused():\n    reset(button="playlist_page", title="Imagine Dragons - Believer", title_on_click="Imagine Dragons - Believer")\n    S["toggle"] = True\n    r = M.spotify_play_library("любимые треки")\n    S["toggle"] = False\n    assert len(S["clicks"]) == 2 and "уже играли" in r and S["title"] == "Imagine Dragons - Believer", (r, S["title"])\n\n\n@test\ndef own_playlist_by_name_is_searched():\n    reset()\n    M.spotify_play_library("для учёбы")\n    assert S["popen"][0].startswith("start spotify:search:") and "%D1%83%D1%87" in S["popen"][0], S["popen"]\n\n\n@test\ndef green_cover_on_the_left_is_not_a_button():'), ('tests/test_commands.py', 'заглушка библиотеки Spotify', 'spotify_seek=_rec("spotify_seek"),', 'spotify_seek=_rec("spotify_seek"), spotify_play_library=_rec("spotify_play_library"),'), ('tests/test_commands.py', 'фразы про свою музыку', '@test\ndef unknown_goes_to_brain():', '@test\ndef my_music_phrases():\n    for p in ("включи мои любимые треки", "Атлас, поставь мне любимые песни", "включи любимое",\n              "play my liked songs", "включи понравившиеся"):\n        assert fast(p)[2][0][:2] == ("spotify_play_library", ("liked",)), p\n    assert fast("включи мой плейлист для учёбы")[2][0][:2] == ("spotify_play_library", ("для учёбы",))\n    assert fast("включи мой плейлист «вечер»")[2][0][:2] == ("spotify_play_library", ("вечер",))\n    assert fast("включи спокойную музыку")[2][0][0] == "play_on_spotify", "обычная музыка — как раньше"\n    assert fast("включи любимые треки и сделай громче")[0] is None, "составная — мозгу"\n\n\n@test\ndef unknown_goes_to_brain():'), ('tests/test_brain.py', 'своя музыка — у модели есть spotify_library', '    assert "media_* tools are ONLY for videos" in ai_brain.SYSTEM_PROMPT', '    assert "media_* tools are ONLY for videos" in ai_brain.SYSTEM_PROMPT\n    s = fresh([{"content": "ок"}])\n    ai_brain.ask_ai(RU + "поставь мой плейлист с тренировки")\n    assert "spotify_library" in {t["function"]["name"] for t in s.requests[0]["tools"]}\n    assert "spotify_library(which=\'liked\'" in ai_brain.SYSTEM_PROMPT')]
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
    cands = sorted(d for d in os.listdir(ROOT) if d.startswith("backup_playlists_"))
    if not cands:
        sys.exit("Резервной копии нет.")
    restore(p(cands[-1]))
    sys.exit(0)
if "def spotify_seek" not in open(p("system_control.py"), encoding="utf-8").read():
    sys.exit("Сначала поставь apply_music9.py.")

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

backup = p(time.strftime("backup_playlists_%Y%m%d_%H%M%S"))
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
        with open(p("playlists_test_output.txt"), "w", encoding="utf-8") as f:
            f.write(r.stdout + "\n" + r.stderr)
        restore(backup)
        sys.exit("✗ Проверка не прошла — всё возвращено. Пришли playlists_test_output.txt.")
print("\nГотово. Проверь: «включи мои любимые треки» и «включи мой плейлист …» (название своего плейлиста).")
