"""
Spotify: страница жанра («включи lo-fi»).

На твоём снимке верхний результат — «Жанр» без кнопки Play, а ниже ряд карточек плейлистов,
у которых кнопка появляется только при наведении и меньше обычной.

  • При наведении Atlas проходит именно ряд карточек (раньше точки были рассчитаны на крупную
    карточку и попадали в промежутки).
  • Кнопку карточки он принимает от 30 px, но только рядом с курсором — маленькие зелёные
    значки в других местах окна не считаются.
  • Боковая библиотека (обложки слева) не просматривается.

    python apply_music7.py
    python apply_music7.py --rollback
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
P7 = [('system_control.py', 'поиск кнопки: размер и «рядом с курсором» задаются параметрами', 'def _find_green_play(rect, x_from: float = 0.45):', 'def _find_green_play(rect, x_from: float = 0.45, min_size: int = 40, near=None):'), ('system_control.py', 'поиск кнопки: порог размера и, при наведении, только кнопка рядом с курсором', '        if 40 <= bw <= 90 and 40 <= bh <= 90 and 0.7 <= bw / bh <= 1.4 \\\n                and len(c["pts"]) >= 60:\n            good.append(c)', '        if min_size <= bw <= 90 and min_size <= bh <= 90 and 0.7 <= bw / bh <= 1.4 \\\n                and len(c["pts"]) >= 60 \\\n                and (near is None or (abs(l + c["cx"] - near[0]) <= near[2] and abs(t + c["cy"] - near[1]) <= near[3])):\n            good.append(c)'), ('system_control.py', 'наведение: проходим ряд карточек плейлистов (страница жанра), кнопка — на той, что под курсором', '        for fy in (0.30, 0.38, 0.46):\n            for fx in (0.30, 0.40, 0.50, 0.60):\n                pyautogui.moveTo(l + int(w * fx), t + int(h * fy))\n                _time.sleep(0.15)\n                point = _find_green_play(rect, x_from=0.2)', '        # верхний результат (крупная карточка) и ряд карточек плейлистов под ним: на странице жанра\n        # у «Жанра» кнопки нет, а у карточек она появляется только при наведении и меньше обычной.\n        # Берём только кнопку рядом с курсором — на той карточке, над которой мышь, а не значок где-то ещё.\n        for fy in (0.38, 0.44, 0.30, 0.50):\n            for fx in (0.11, 0.19, 0.28, 0.36, 0.45, 0.55, 0.65):\n                mx, my = l + int(w * fx), t + int(h * fy)\n                pyautogui.moveTo(mx, my)\n                _time.sleep(0.15)\n                point = _find_green_play(rect, x_from=0.06, min_size=30,           # боковую библиотеку не смотрим\n                                         near=(mx, my, int(w * 0.09), int(h * 0.12)))')]
TEST_SPOTIFY = '"""\nТесты музыки в Spotify (system_control.py): продолжить воспроизведение, найти кнопку Play\n(в том числе видимую только при наведении), снимок окна при неудаче.\nОкно Spotify, экран и клавиши — подставные: Windows и сам Spotify не нужны.\n\n    python tests/test_spotify.py\n"""\nimport functools\nimport os\nimport shutil\nimport sys\nimport tempfile\nimport traceback\nimport types\n\nfor _stream in (sys.stdout, sys.stderr):\n    try:\n        _stream.reconfigure(encoding="utf-8", errors="replace")\n    except Exception:\n        pass\n\nROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\nsys.path.insert(0, ROOT)\nfrom PIL import Image, ImageDraw  # noqa: E402\n\nRECT = (100, 50, 1300, 850)                  # окно Spotify на экране\nS = {}\n\n\ndef reset(title="Spotify Premium", button="visible", key_works=True, space_works=True, avatar=True,\n          stale=False, title_on_click="Linkin Park - Numb", hidden=False, behind=False):\n    S.update(hidden=hidden, behind=behind, front=not behind, title=title, button=button, key_works=key_works, space_works=space_works, mouse=(5, 5),\n             clicks=[], keys=[], popen=[], hovered=False, avatar=avatar, stale=stale, switch_at=0.0,\n             title_on_click=title_on_click, old_clicks=0)\n\n\ndef _page_old():\n    import time\n    return S["stale"] and time.time() < S["switch_at"]\n\n\ndef _popen(cmd, shell=True):\n    import time\n    S["popen"].append(cmd)\n    if S["stale"]:\n        S["switch_at"] = time.time() + 1.0          # ещё секунду на экране прошлые результаты\n\n\ndef _click(x, y):\n    S["clicks"].append((x, y))\n    if _page_old():                                  # кнопка прошлого (играющего) результата = пауза\n        S["old_clicks"] += 1\n        S["title"] = "Spotify Premium"\n    else:\n        S["title"] = S["title_on_click"]\n\n\ndef screenshot(region):\n    l, t, w, h = region\n    if S.get("behind") and not S.get("front"):    # Spotify позади окна Atlas — на экране видно Atlas\n        return Image.new("RGB", (w, h), (10, 14, 40))\n    img = Image.new("RGB", (w, h), (18, 18, 18))\n    d = ImageDraw.Draw(img)\n    d.rectangle((40, 600, 140, 700), fill=(30, 215, 96))          # зелёная обложка слева — не кнопка\n    if S.get("stale"):                                             # обложка лучшего результата: старая / новая\n        d.rectangle((int(0.30 * w), int(0.20 * h), int(0.42 * w), int(0.42 * h)),\n                    fill=(200, 40, 40) if _page_old() else (40, 60, 200))\n    if S.get("avatar"):                                            # зелёный аватар профиля «T» в шапке справа\n        d.ellipse((w - 60, 12, w - 24, 48), fill=(30, 215, 96))\n        d.ellipse((int(0.82 * w), int(0.20 * h), int(0.82 * w) + 34, int(0.20 * h) + 34), fill=(30, 215, 96))   # значок поменьше\n    mx, my = S["mouse"][0] - l, S["mouse"][1] - t\n    over_card = 0.25 * w <= mx <= 0.65 * w and 0.25 * h <= my <= 0.50 * h\n    S["hovered"] = S["hovered"] or over_card\n    if S["button"] == "genre":                                     # страница жанра, как на снимке пользователя\n        d.rectangle((int(0.07 * w), int(0.14 * h), int(0.72 * w), int(0.25 * h)), fill=(40, 40, 40))   # «Жанр» без кнопки\n        for i in range(5):\n            x0, y0 = int((0.07 + i * 0.085) * w), int(0.345 * h)\n            x1, y1 = x0 + int(0.07 * w), y0 + int(0.125 * h)\n            d.rectangle((x0, y0, x1, y1), fill=(90 + 30 * i, 60, 140))                                  # обложки\n            if x0 <= mx <= x1 and y0 <= my <= y1:                                                        # наведение\n                d.ellipse((x1 - 42, y1 - 42, x1 - 6, y1 - 6), fill=(30, 215, 96))                         # кнопка 36 px\n    if S["button"] == "visible" or (S["button"] == "hover" and over_card):\n        cx, cy = int(0.55 * w), int(0.40 * h)\n        d.ellipse((cx - 28, cy - 28, cx + 28, cy + 28), fill=(30, 215, 96))\n    return img\n\n\ndef install():\n    sys.modules["pyautogui"] = types.SimpleNamespace(\n        screenshot=screenshot,\n        position=lambda: S["mouse"],\n        moveTo=lambda x, y=None: S.update(mouse=(x, y) if y is not None else x),\n        click=_click,\n        press=lambda k: (S["keys"].append(k), S.update(title="Artist - Song") if S["space_works"] else None))\n\n\ndef load():\n    import importlib.util\n    spec = importlib.util.spec_from_file_location("system_control", os.path.join(ROOT, "system_control.py"))\n    m = importlib.util.module_from_spec(spec)\n    spec.loader.exec_module(m)\n    m._spotify_windows = lambda: [] if S.get("hidden") and not S["popen"] else [(1, S["title"], RECT)]\n    m._bring_to_front = lambda hwnd: (S.update(front=True), True)[1]\n\n    def media_key():\n        S["keys"].append("media")\n        if S["key_works"]:\n            S["title"] = "Artist - Song"\n    m._media_play_pause_key = media_key\n    m.subprocess = types.SimpleNamespace(Popen=_popen)\n    m._spotify_autoplay = functools.partial(m._spotify_autoplay, timeout=3)\n    m._spotify_resume_fast = functools.partial(m._spotify_resume, timeout=3)\n    return m\n\n\nTESTS = []\n\n\ndef test(fn):\n    TESTS.append(fn)\n    return fn\n\n\n@test\ndef play_music_resumes_when_paused():\n    reset()\n    r = M.play_on_spotify("")\n    assert S["popen"] == ["start spotify:"] and S["keys"] == ["media"] and "Artist - Song" in r, (r, S)\n\n\n@test\ndef already_playing_is_not_paused():\n    reset(title="Daft Punk - One More Time")\n    r = M.play_on_spotify("")\n    assert S["keys"] == [] and "уже играет" in r, (r, S["keys"])\n\n\n@test\ndef space_is_the_backup_when_media_key_does_nothing():\n    reset(key_works=False)\n    r = M.play_on_spotify("")\n    assert S["keys"] == ["media", "space"] and "Artist - Song" in r, (r, S["keys"])\n\n\n@test\ndef query_clicks_the_visible_button_and_restores_mouse():\n    reset(button="visible")\n    r = M.play_on_spotify("linkin park numb")\n    assert S["popen"] == ["start spotify:search:linkin%20park%20numb"] and len(S["clicks"]) == 1\n    x, y = S["clicks"][0]\n    assert abs(x - (RECT[0] + 0.55 * 1200)) < 15 and abs(y - (RECT[1] + 0.40 * 800)) < 15, S["clicks"]\n    assert "Linkin Park - Numb" in r and S["mouse"] == (5, 5)\n\n\n@test\ndef hover_reveals_the_button():\n    reset(button="hover")\n    r = M.play_on_spotify("numb")\n    assert S["hovered"] and len(S["clicks"]) == 1 and "Linkin Park - Numb" in r and S["mouse"] == (5, 5), (r, S)\n\n\n@test\ndef failure_saves_a_snapshot_for_tuning():\n    reset(button="none")\n    d = tempfile.mkdtemp()\n    cwd = os.getcwd()\n    try:\n        os.chdir(d)\n        r = M.play_on_spotify("numb")\n        shots = os.listdir(os.path.join(d, "logs"))\n    finally:\n        os.chdir(cwd)\n        shutil.rmtree(d, ignore_errors=True)\n    assert "не нашёл кнопку Play" in r and any(s.startswith("spotify_no_play_button") for s in shots), (r, shots)\n    assert not S["clicks"] and S["mouse"] == (5, 5)\n\n\n@test\ndef avatar_in_the_header_is_not_the_play_button():\n    reset(button="visible", avatar=True)\n    M.play_on_spotify("numb")\n    x, y = S["clicks"][0]\n    assert abs(x - (RECT[0] + 0.55 * 1200)) < 15 and abs(y - (RECT[1] + 0.40 * 800)) < 15, \\\n        f"нажал не туда: {S[\'clicks\']} (аватар в шапке или значок поменьше)"\n\n\n@test\ndef old_results_are_not_clicked():\n    reset(title="leadwave - unfortunately", stale=True, title_on_click="Maroon 5 - Animals")\n    r = M.play_on_spotify("animals maroon 5")\n    assert S["old_clicks"] == 0 and "Maroon 5 - Animals" in r, (r, S["old_clicks"])\n\n\n@test\ndef wrong_song_is_reported_honestly():\n    reset(title_on_click="Earth, Wind & Fire - Boogie Wonderland")\n    r = M.play_on_spotify("animals maroon 5")\n    assert "похоже, не то" in r and "Boogie Wonderland" in r, r\n\n\n@test\ndef mood_and_cyrillic_requests_are_not_second_guessed():\n    reset(title_on_click="Pharrell Williams - Happy")\n    assert M.play_on_spotify("upbeat happy music").startswith("Включил")\n    reset(title_on_click="Linkin Park - Numb")\n    assert M.play_on_spotify("линкин парк намб").startswith("Включил")\n\n\n@test\ndef genres_are_not_second_guessed():\n    for q, t in (("classic rock", "KISS - I Was Made For Lovin\' You"), ("Classic Rock Essentials", "Deep Purple - Smoke On The Water"),\n                 ("classic rock hits", "AC/DC - Highway to Hell"), ("animals", "Maroon 5 - Animals - Remix")):\n        reset(title_on_click=t)\n        r = M.play_on_spotify(q)\n        assert r.startswith("Включил") and "не то" not in r, (q, r)\n\n\n@test\ndef minimized_spotify_old_page_is_not_clicked():\n    reset(title="leadwave - unfortunately", stale=True, hidden=True, title_on_click="Maroon 5 - Animals")\n    r = M.play_on_spotify("animals maroon 5")\n    assert S["old_clicks"] == 0 and "Maroon 5 - Animals" in r, (r, S["old_clicks"])\n\n\n@test\ndef spotify_behind_atlas_old_page_is_not_clicked():\n    reset(title="Eminem - Superman", stale=True, behind=True, title_on_click="Lofi Girl - Snowman")\n    r = M.play_on_spotify("lofi beats")\n    assert S["old_clicks"] == 0 and "Lofi Girl" in r, (r, S["old_clicks"])\n\n\n@test\ndef genre_page_plays_a_playlist_card():\n    reset(button="genre", title_on_click="Lofi Girl - Snowman")\n    r = M.play_on_spotify("lofi")\n    assert len(S["clicks"]) == 1 and "Lofi Girl" in r and S["mouse"] == (5, 5), (r, S["clicks"])\n\n\n@test\ndef green_cover_on_the_left_is_not_a_button():\n    reset(button="none")\n    assert M._find_green_play(RECT) is None and M._find_green_play(RECT, x_from=0.2) is None\n\n\ndef main():\n    global M\n    install()\n    M = load()\n    ok = 0\n    for t in TESTS:\n        try:\n            t()\n            ok += 1\n            print(f"  ✓ {t.__name__}")\n        except Exception:\n            print(f"  ✗ {t.__name__}\\n" + "".join("      " + ln for ln in traceback.format_exc().splitlines(True)[-4:]))\n    print(f"\\nТестов пройдено: {ok} из {len(TESTS)}")\n    sys.stdout.flush()\n    sys.stderr.flush()\n    os._exit(0 if ok == len(TESTS) else 1)\n\n\nif __name__ == "__main__":\n    main()\n'
TOUCHED = ["system_control.py", "tests/test_spotify.py"]


def p(rel):
    return os.path.join(ROOT, *rel.split("/"))


def restore(backup):
    for rel in TOUCHED:
        b = os.path.join(backup, *rel.split("/"))
        if os.path.exists(b):
            shutil.copy2(b, p(rel))
    print(f"Вернул прежние файлы из {backup}.")


if "--rollback" in sys.argv:
    cands = sorted(d for d in os.listdir(ROOT) if d.startswith("backup_music7_"))
    if not cands:
        sys.exit("Резервной копии нет.")
    restore(p(cands[-1]))
    sys.exit(0)
src = open(p("system_control.py"), encoding="utf-8").read()
if "AttachThreadInput" not in src:
    sys.exit("Сначала поставь apply_music6.py.")

status = []
for rel, what, old, new in P7:
    if new in src:
        status.append((what, "уже было"))
    elif src.count(old) == 1:
        src = src.replace(old, new, 1)
        status.append((what, "ok"))
    else:
        status.append((what, "НЕ НАЙДЕНО"))
for what, st in status:
    print(f"  {'!' if st == 'НЕ НАЙДЕНО' else '✓'} {what}" + ("" if st == "ok" else f" ({st})"))
if any(st == "НЕ НАЙДЕНО" for _, st in status):
    sys.exit("Код выглядит иначе, чем я ожидал — ничего не изменено. Пришли вывод.")
if all(st == "уже было" for _, st in status):
    sys.exit("Уже установлено.")

backup = p(time.strftime("backup_music7_%Y%m%d_%H%M%S"))
for rel in TOUCHED:
    os.makedirs(os.path.dirname(os.path.join(backup, *rel.split("/"))), exist_ok=True)
    shutil.copy2(p(rel), os.path.join(backup, *rel.split("/")))
print(f"Резервная копия: {backup}")
for rel, s in (("system_control.py", src), ("tests/test_spotify.py", TEST_SPOTIFY)):
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
        with open(p("music7_test_output.txt"), "w", encoding="utf-8") as f:
            f.write(r.stdout + "\n" + r.stderr)
        restore(backup)
        sys.exit("✗ Проверка не прошла — всё возвращено. Пришли music7_test_output.txt.")
print("\nГотово. Проверь: «включи lo-fi», «включи джаз», «поставь что-нибудь для учёбы».")
