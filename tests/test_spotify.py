"""
Тесты музыки в Spotify (system_control.py): продолжить воспроизведение, найти кнопку Play
(в том числе видимую только при наведении), снимок окна при неудаче.
Окно Spotify, экран и клавиши — подставные: Windows и сам Spotify не нужны.

    python tests/test_spotify.py
"""
import functools
import os
import shutil
import sys
import tempfile
import traceback
import types

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from PIL import Image, ImageDraw  # noqa: E402

RECT = (100, 50, 1300, 850)                  # окно Spotify на экране
S = {}


def reset(title="Spotify Premium", button="visible", key_works=True, space_works=True, avatar=True,
          stale=False, title_on_click="Linkin Park - Numb", hidden=False, behind=False):
    S.update(hidden=hidden, behind=behind, front=not behind, title=title, button=button, key_works=key_works, space_works=space_works, mouse=(5, 5),
             clicks=[], keys=[], popen=[], hovered=False, avatar=avatar, stale=stale, switch_at=0.0,
             title_on_click=title_on_click, old_clicks=0)


def _page_old():
    import time                                      # до поиска и ещё секунду после — на экране прошлые результаты
    return S["stale"] and (not S["popen"] or time.time() < S["switch_at"])


def _popen(cmd, shell=True):
    import time
    S["popen"].append(cmd)
    if S["stale"]:
        S["switch_at"] = time.time() + 1.0          # ещё секунду на экране прошлые результаты


def _click(x, y):
    S["clicks"].append((x, y))
    if S.get("toggle"):                              # кнопка того, что уже играет, — пауза / снова играть
        S["title"] = "Spotify Premium" if not S["title"].lower().startswith("spotify") else S["title_on_click"]
        return
    if _page_old():                                  # кнопка прошлого (играющего) результата = пауза
        S["old_clicks"] += 1
        S["title"] = "Spotify Premium"
    else:
        S["title"] = S["title_on_click"]


def screenshot(region):
    l, t, w, h = region
    if S.get("behind") and not S.get("front"):    # Spotify позади окна Atlas — на экране видно Atlas
        return Image.new("RGB", (w, h), (10, 14, 40))
    img = Image.new("RGB", (w, h), (18, 18, 18))
    d = ImageDraw.Draw(img)
    d.rectangle((40, 600, 140, 700), fill=(30, 215, 96))          # зелёная обложка слева — не кнопка
    if S.get("stale") and S.get("small_change"):                  # как в жизни: меняются обложка и надпись
        d.rectangle((int(0.08 * w), int(0.15 * h), int(0.14 * w), int(0.25 * h)),
                    fill=(150, 70, 60) if _page_old() else (60, 90, 150))
        d.rectangle((int(0.16 * w), int(0.18 * h), int(0.16 * w) + (180 if _page_old() else 110), int(0.21 * h)),
                    fill=(230, 230, 230))
    elif S.get("stale"):                                           # обложка лучшего результата: старая / новая
        d.rectangle((int(0.30 * w), int(0.20 * h), int(0.42 * w), int(0.42 * h)),
                    fill=(200, 40, 40) if _page_old() else (40, 60, 200))
    if S.get("avatar"):                                            # зелёный аватар профиля «T» в шапке справа
        d.ellipse((w - 60, 12, w - 24, 48), fill=(30, 215, 96))
        d.ellipse((int(0.82 * w), int(0.20 * h), int(0.82 * w) + 34, int(0.20 * h) + 34), fill=(30, 215, 96))   # значок поменьше
    mx, my = S["mouse"][0] - l, S["mouse"][1] - t
    over_card = 0.25 * w <= mx <= 0.65 * w and 0.25 * h <= my <= 0.50 * h
    S["hovered"] = S["hovered"] or over_card
    if S["button"] == "playlist_page" and S["popen"]:            # «Любимые треки»: заголовок и кнопка слева
        d.rectangle((int(0.07 * w), int(0.10 * h), int(0.98 * w), int(0.38 * h)), fill=(80, 60, 160))
        cx, cy = int(0.13 * w), int(0.47 * h)
        d.ellipse((cx - 28, cy - 28, cx + 28, cy + 28), fill=(30, 215, 96))
    if S["button"] == "genre":                                     # страница жанра, как на снимке пользователя
        d.rectangle((int(0.07 * w), int(0.14 * h), int(0.72 * w), int(0.25 * h)), fill=(40, 40, 40))   # «Жанр» без кнопки
        for i in range(5):
            x0, y0 = int((0.07 + i * 0.085) * w), int(0.345 * h)
            x1, y1 = x0 + int(0.07 * w), y0 + int(0.125 * h)
            d.rectangle((x0, y0, x1, y1), fill=(90 + 30 * i, 60, 140))                                  # обложки
            if x0 <= mx <= x1 and y0 <= my <= y1:                                                        # наведение
                d.ellipse((x1 - 42, y1 - 42, x1 - 6, y1 - 6), fill=(30, 215, 96))                         # кнопка 36 px
    if S["button"] == "visible" or (S["button"] == "hover" and over_card):
        cx, cy = int(0.55 * w), int(0.40 * h)
        d.ellipse((cx - 28, cy - 28, cx + 28, cy + 28), fill=(30, 215, 96))
    return img


def install():
    sys.modules["pyautogui"] = types.SimpleNamespace(
        screenshot=screenshot,
        position=lambda: S["mouse"],
        moveTo=lambda x, y=None: S.update(mouse=(x, y) if y is not None else x),
        click=_click,
        press=lambda k: (S["keys"].append(k), S.update(title="Artist - Song") if S["space_works"] else None),
        hotkey=lambda *k: S["keys"].append("+".join(k)))


def load():
    import importlib.util
    spec = importlib.util.spec_from_file_location("system_control", os.path.join(ROOT, "system_control.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._spotify_windows = lambda: [] if S.get("hidden") and not S["popen"] else [(1, S["title"], RECT)]
    m._bring_to_front = lambda hwnd: (S.update(front=True), True)[1]

    def media_key():
        S["keys"].append("media")
        if S["key_works"]:
            S["title"] = "Artist - Song"
    m._media_play_pause_key = media_key
    m.subprocess = types.SimpleNamespace(Popen=_popen)
    m._spotify_autoplay = functools.partial(m._spotify_autoplay, timeout=3)
    m._spotify_resume_fast = functools.partial(m._spotify_resume, timeout=3)
    return m


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def play_music_resumes_when_paused():
    reset()
    r = M.play_on_spotify("")
    assert S["popen"] == ["start spotify:"] and S["keys"] == ["media"] and "Artist - Song" in r, (r, S)


@test
def already_playing_is_not_paused():
    reset(title="Daft Punk - One More Time")
    r = M.play_on_spotify("")
    assert S["keys"] == [] and "уже играет" in r, (r, S["keys"])


@test
def space_is_the_backup_when_media_key_does_nothing():
    reset(key_works=False)
    r = M.play_on_spotify("")
    assert S["keys"] == ["media", "space"] and "Artist - Song" in r, (r, S["keys"])


@test
def query_clicks_the_visible_button_and_restores_mouse():
    reset(button="visible")
    r = M.play_on_spotify("linkin park numb")
    assert S["popen"] == ["start spotify:search:linkin%20park%20numb"] and len(S["clicks"]) == 1
    x, y = S["clicks"][0]
    assert abs(x - (RECT[0] + 0.55 * 1200)) < 15 and abs(y - (RECT[1] + 0.40 * 800)) < 15, S["clicks"]
    assert "Linkin Park - Numb" in r and S["mouse"] == (5, 5)


@test
def hover_reveals_the_button():
    reset(button="hover")
    r = M.play_on_spotify("numb")
    assert S["hovered"] and len(S["clicks"]) == 1 and "Linkin Park - Numb" in r and S["mouse"] == (5, 5), (r, S)


@test
def failure_saves_a_snapshot_for_tuning():
    reset(button="none")
    d = tempfile.mkdtemp()
    cwd = os.getcwd()
    try:
        os.chdir(d)
        r = M.play_on_spotify("numb")
        shots = os.listdir(os.path.join(d, "logs"))
    finally:
        os.chdir(cwd)
        shutil.rmtree(d, ignore_errors=True)
    assert "не нашёл кнопку Play" in r and any(s.startswith("spotify_no_play_button") for s in shots), (r, shots)
    assert not S["clicks"] and S["mouse"] == (5, 5)


@test
def avatar_in_the_header_is_not_the_play_button():
    reset(button="visible", avatar=True)
    M.play_on_spotify("numb")
    x, y = S["clicks"][0]
    assert abs(x - (RECT[0] + 0.55 * 1200)) < 15 and abs(y - (RECT[1] + 0.40 * 800)) < 15, \
        f"нажал не туда: {S['clicks']} (аватар в шапке или значок поменьше)"


@test
def old_results_are_not_clicked():
    reset(title="leadwave - unfortunately", stale=True, title_on_click="Maroon 5 - Animals")
    r = M.play_on_spotify("animals maroon 5")
    assert S["old_clicks"] == 0 and "Maroon 5 - Animals" in r, (r, S["old_clicks"])


@test
def what_is_playing_is_named_without_second_guessing():
    for q, title in (("epic cinematic music", "John Paesano - The Maze Runner"),
                     ("animals maroon 5", "Earth, Wind & Fire - Boogie Wonderland")):
        reset(title_on_click=title)
        r = M.play_on_spotify(q)
        assert r.startswith("Включил") and title in r and "не то" not in r, (q, r)


@test
def page_change_is_noticed_quickly_even_if_small():
    import time
    reset(title="Two Steps From Hell - Victory", stale=True, title_on_click="Pharrell Williams - Happy")
    S["small_change"] = True
    short = M._spotify_autoplay
    M._spotify_autoplay = functools.partial(short.func, timeout=12)     # ожидание как в жизни — до 4 с
    t0 = time.time()
    try:
        r = M.play_on_spotify("happy music")
    finally:
        M._spotify_autoplay = short
        S["small_change"] = False
    took = time.time() - t0
    assert S["old_clicks"] == 0 and "Happy" in r, (r, S["old_clicks"])
    assert took < 2.8, f"смена страницы замечена слишком поздно: {took:.1f} с (ждал до упора)"


@test
def mood_and_cyrillic_requests_are_not_second_guessed():
    reset(title_on_click="Pharrell Williams - Happy")
    assert M.play_on_spotify("upbeat happy music").startswith("Включил")
    reset(title_on_click="Linkin Park - Numb")
    assert M.play_on_spotify("линкин парк намб").startswith("Включил")


@test
def genres_are_not_second_guessed():
    for q, t in (("classic rock", "KISS - I Was Made For Lovin' You"), ("Classic Rock Essentials", "Deep Purple - Smoke On The Water"),
                 ("classic rock hits", "AC/DC - Highway to Hell"), ("animals", "Maroon 5 - Animals - Remix")):
        reset(title_on_click=t)
        r = M.play_on_spotify(q)
        assert r.startswith("Включил") and "не то" not in r, (q, r)


@test
def minimized_spotify_old_page_is_not_clicked():
    reset(title="leadwave - unfortunately", stale=True, hidden=True, title_on_click="Maroon 5 - Animals")
    r = M.play_on_spotify("animals maroon 5")
    assert S["old_clicks"] == 0 and "Maroon 5 - Animals" in r, (r, S["old_clicks"])


@test
def spotify_behind_atlas_old_page_is_not_clicked():
    reset(title="Eminem - Superman", stale=True, behind=True, title_on_click="Lofi Girl - Snowman")
    r = M.play_on_spotify("lofi beats")
    assert S["old_clicks"] == 0 and "Lofi Girl" in r, (r, S["old_clicks"])


@test
def genre_page_plays_a_playlist_card():
    reset(button="genre", title_on_click="Lofi Girl - Snowman")
    r = M.play_on_spotify("lofi")
    assert len(S["clicks"]) == 1 and "Lofi Girl" in r and S["mouse"] == (5, 5), (r, S["clicks"])


@test
def spotify_seek_forward_and_back():
    reset()
    S["front"] = False
    r = M.spotify_seek(15)
    assert S["keys"] == ["shift+right"] * 3 and S["front"] and "вперёд на 15" in r, (r, S["keys"])
    reset()
    r = M.spotify_seek(-30)
    assert S["keys"] == ["shift+left"] * 6 and "назад на 30" in r, (r, S["keys"])
    reset(hidden=True)
    assert "не открыт" in M.spotify_seek(15) and not S["keys"]


@test
def liked_songs_open_directly_and_play():
    reset(button="playlist_page", title_on_click="Imagine Dragons - Believer")
    r = M.spotify_play_library("liked")
    x, y = S["clicks"][0]
    assert S["popen"] == ["start spotify:collection:tracks"] and "Believer" in r, (r, S["popen"])
    assert abs(x - (RECT[0] + 0.13 * 1200)) < 15 and abs(y - (RECT[1] + 0.47 * 800)) < 15, S["clicks"]


@test
def liked_songs_already_playing_are_not_left_paused():
    reset(button="playlist_page", title="Imagine Dragons - Believer", title_on_click="Imagine Dragons - Believer")
    S["toggle"] = True
    r = M.spotify_play_library("любимые треки")
    S["toggle"] = False
    assert len(S["clicks"]) == 2 and "уже играли" in r and S["title"] == "Imagine Dragons - Believer", (r, S["title"])


@test
def own_playlist_by_name_is_searched():
    reset()
    M.spotify_play_library("для учёбы")
    assert S["popen"][0].startswith("start spotify:search:") and "%D1%83%D1%87" in S["popen"][0], S["popen"]


@test
def spotify_volume_keys():
    reset()
    S["front"] = False
    r = M.spotify_volume("up")
    assert S["keys"] == ["ctrl+up"] and S["front"] and "громче" in r, (r, S["keys"])
    reset()
    r = M.spotify_volume("down", steps=2)
    assert S["keys"] == ["ctrl+down"] * 2 and "тише" in r, S["keys"]
    reset()
    r = M.spotify_volume("set", 80)
    assert S["keys"] == ["ctrl+down"] * 12 + ["ctrl+up"] * 8 and "80%" in r, (r, S["keys"])
    reset(hidden=True)
    assert "не открыт" in M.spotify_volume("up") and not S["keys"]


@test
def green_cover_on_the_left_is_not_a_button():
    reset(button="none")
    assert M._find_green_play(RECT) is None and M._find_green_play(RECT, x_from=0.2) is None


def main():
    global M
    install()
    M = load()
    ok = 0
    for t in TESTS:
        try:
            t()
            ok += 1
            print(f"  ✓ {t.__name__}")
        except Exception:
            print(f"  ✗ {t.__name__}\n" + "".join("      " + ln for ln in traceback.format_exc().splitlines(True)[-4:]))
    print(f"\nТестов пройдено: {ok} из {len(TESTS)}")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if ok == len(TESTS) else 1)


if __name__ == "__main__":
    main()
