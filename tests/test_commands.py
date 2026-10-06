"""
Тесты быстрого пути (fast_commands.py) и слов управления (core/control_words.py).

    python tests/test_commands.py                       — проверки поведения
    python tests/test_commands.py --compare СТАРЫЙ.py   — старая и новая версии на одних фразах:
                                                          любое расхождение = ошибка

Без интернета и Windows: всё, что вызывает быстрый путь, заменено подставными функциями.
"""
import importlib.util
import os
import sys
import traceback
import types

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
CALLS = []
STATE = {"game": False, "browser_open": False, "fail_open_app": False, "fail_text": "Приложение не найдено"}


def _rec(name, result=None):
    def f(*a, **kw):
        CALLS.append((name, a, kw))
        if name == "open_app" and STATE["fail_open_app"]:
            return STATE["fail_text"]
        return result if result is not None else f"{name}: ok"
    return f


def _browser(name):
    def f(**kw):
        CALLS.append((name, (), kw))
        return f"{name}: ok" if STATE["browser_open"] else "Браузер не открыт."
    return f


def _mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


def install():
    _mod("core.missions", start=lambda goal: (CALLS.append(("missions.start", (goal[:40],), {})), "M1")[1])
    core = sys.modules.get("core") or _mod("core")
    if not hasattr(core, "__path__"):
        core.__path__ = [os.path.join(ROOT, "core")]
    core.missions = sys.modules["core.missions"]
    _mod("ui_state", shared_state={"chat_history": [("You", "сравни ноутбуки"), ("Atlas", "ок")]})
    _mod("voice", get_response_language=lambda: "ru")
    _mod("skills").__path__ = []
    _mod("skills.fun", game_active=lambda: STATE["game"], guess_number=_rec("guess_number", "Больше!"),
         tell_joke=_rec("tell_joke", "Шутка"), random_fact=_rec("random_fact", "Факт"))
    _mod("file_search", _ORD={"первый": 1, "второй": 2, "третий": 3, "second": 2},
         search_file_content=_rec("search_file_content"), open_found_file=_rec("open_found_file"),
         open_search_result=_rec("open_search_result"), show_search_result_in_folder=_rec("show_search_result_in_folder"))
    _mod("deep_links", launch_steam_game=_rec("launch_steam_game"), list_steam_games=_rec("list_steam_games"),
         open_deep_link=_rec("open_deep_link"))
    _mod("system_control", open_app=_rec("open_app"), close_app=_rec("close_app"), spotify_seek=_rec("spotify_seek"), spotify_play_library=_rec("spotify_play_library"),
         spotify_is_playing=lambda: STATE.get("spotify_playing", False), spotify_volume=_rec("spotify_volume"),
         play_on_spotify=_rec("play_on_spotify"), play_on_youtube_music=_rec("play_on_youtube_music"))
    _mod("system_advanced", **{n: _rec(n) for n in ("set_volume", "volume_up", "volume_down", "mute_volume",
                                                    "unmute_volume", "set_brightness", "lock_screen", "take_screenshot")})
    _mod("media_control", **{n: _rec(n) for n in ("play_pause_media", "next_track", "previous_track")})
    _mod("theme_control", set_theme=_rec("set_theme"))
    _mod("phone").__path__ = []
    _mod("phone.install", install=_rec("install", "Открыл на экране QR-код."))
    _mod("browser_agent", _browser_alive=lambda: STATE["browser_open"],
         **{n: _browser(n) for n in ("media_volume", "media_play_pause", "skip_intro", "next_episode",
                                     "media_player_fullscreen", "media_seek")})


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PHRASES = [
    "", "Открой блокнот", "открой блокнот и запиши список дел", "Открой блокнот, напиши привет", "открой гугл хром",
    "открой фотошоп", "запусти Telegram", "открой фильм интерстеллар", "открой сайт хабр", "закрой хром",
    "закрой хром и открой блокнот", "включи спокойную музыку", "включи музыку", "поставь джаз на спотифае",
    "включи linkin park на ютуб музыке", "включи музыку и сделай громче", "play some music",
    "в фоне сравни три ноутбука до 1000 долларов", "расскажи шутку", "шутка про котов", "tell me a joke about cats",
    "расскажи интересный факт", "факт о космосе", "где файл про бюджет поездки",
    "найди файл где я писал про стажировку", "покажи скриншот с ошибкой", "where is the file about the trip budget",
    "open the file about taxes", "открой второй", "open the second one", "открой 3", "покажи второй в папке",
    "открой файл про налоги", "запусти cyberpunk в стиме", "запусти игру ведьмак", "какие у меня игры",
    "найди котиков на ютубе", "открой эйфелеву башню в википедии", "найди рецепт в интернете",
    "громкость 50", "громкость на 30%", "громче", "тише", "выключи звук", "включи звук", "яркость 70",
    "заблокируй экран", "сделай скриншот", "пауза", "продолжи", "пропусти заставку", "следующая серия",
    "на весь экран", "перемотай вперёд", "назад", "следующий трек", "предыдущая песня", "тёмная тема",
    "light mode", "какая погода", "что ты обо мне знаешь", "Атлас, выключись", "«Открой проводник»",
    "open calculator", "launch steam", "открой второй файл", "volume 80", "brightness 40", "45",
]


def run_all(mod):
    out = []
    for game in (False, True):
        for browser in (False, True):
            STATE.update(game=game, browser_open=browser)
            for p in PHRASES:
                CALLS.clear()
                try:
                    r = mod.try_fast_command(p)
                except Exception as e:
                    r = f"EXC {type(e).__name__}: {e}"
                out.append(((game, browser, p), repr(r), repr(CALLS)))
    STATE.update(game=False, browser_open=False)
    return out


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def fast(p, game=False, browser=False):
    STATE.update(game=game, browser_open=browser)
    CALLS.clear()
    r = FC.try_fast_command(p)
    STATE.update(game=False, browser_open=False)
    return r, [c[0] for c in CALLS], CALLS


@test
def apps_open_by_alias_and_multi_part_goes_to_brain():
    r, names, calls = fast("Открой блокнот")
    assert names == ["open_app"] and calls[0][1] == ("блокнот",)
    assert fast("открой гугл хром")[2][0][1] == ("хром",)
    assert fast("открой блокнот и запиши список дел")[0] is None
    assert fast("Открой блокнот, напиши привет")[0] is None
    assert fast("открой фильм интерстеллар")[0] is None, "фильм — не приложение"


@test
def failed_app_goes_to_brain():
    STATE["fail_open_app"] = True
    try:
        for text in ("Приложение не найдено", "Не нашёл приложение «фотошоп»", "Не могу найти ярлык"):
            STATE["fail_text"] = text
            assert fast("открой фотошоп")[0] is None, text
    finally:
        STATE.update(fail_open_app=False, fail_text="Приложение не найдено")


@test
def wake_word_at_the_start_is_understood():
    assert fast("атлас, включи музыку")[2][0][:2] == ("play_on_spotify", ("",))
    assert fast("Атлас! открой блокнот")[2][0][1] == ("блокнот",)
    assert fast("atlas, open calculator")[2][0][1] == ("калькулятор",)


@test
def music():
    assert fast("включи спокойную музыку")[2][0][:2] == ("play_on_spotify", ("спокойную музыку",))
    assert fast("включи музыку")[2][0][:2] == ("play_on_spotify", ("",))
    assert fast("включи какую-нибудь музыку")[2][0][:2] == ("play_on_spotify", ("",)), "это «включи музыку», а не поиск"
    assert fast("включи мне любую музыку")[2][0][:2] == ("play_on_spotify", ("",))
    assert fast("поставь какую-нибудь веселую музыку")[2][0][:2] == ("play_on_spotify", ("веселую музыку",))
    assert fast("включи linkin park на ютуб музыке")[1] == ["play_on_youtube_music"]
    assert fast("включи музыку и сделай громче")[0] is None


@test
def background_mission_joke_fact_and_game():
    r, names, _ = fast("в фоне сравни три ноутбука до 1000 долларов")
    assert r[0] == "speak" and "M1" in r[1] and names == ["missions.start"]
    assert fast("шутка про котов")[2][0][:2] == ("tell_joke", ("котов",))
    assert fast("расскажи интересный факт")[1] == ["random_fact"]
    assert fast("45", game=True) == (("speak", "Больше!"), ["guess_number"], [("guess_number", (45,), {})])
    assert fast("45")[0] is None


@test
def files():
    assert fast("найди файл где я писал про стажировку")[2][0][:2] == ("search_file_content", ("стажировку",))
    assert fast("where is the file about the trip budget")[2][0][:2] == ("search_file_content", ("the trip budget",))
    assert fast("открой второй")[2][0][:2] == ("open_search_result", ("второй",))
    assert fast("покажи второй в папке")[1] == ["show_search_result_in_folder"]


@test
def steam_services_volume_media():
    assert fast("запусти cyberpunk в стиме")[2][0][:2] == ("launch_steam_game", ("cyberpunk",))
    assert fast("найди котиков на ютубе")[2][0][:2] == ("open_deep_link", ("youtube", "котиков"))
    assert fast("громкость на 30%")[2][0][:2] == ("set_volume", (30,))
    assert fast("тише")[1] == ["volume_down"], "браузер закрыт → системная громкость, браузер не запускаем"
    assert fast("тише", browser=True)[1] == ["media_volume"]
    assert fast("пауза")[1] == ["play_pause_media"]
    assert fast("на весь экран")[0] is None, "без браузера решает модель"
    assert fast("тёмная тема")[2][0][:2] == ("set_theme", ("dark",))


@test
def volume_and_seek_in_natural_phrases():
    assert fast("сделаем музыку погромче")[1] == ["volume_up"]
    assert fast("Атлас, сделай чуть-чуть тише")[1] == ["volume_down"]
    assert fast("перемотай на 15 секунд вперёд")[2][0][:2] == ("spotify_seek", (15,))
    assert fast("перемотай назад на 30 секунд")[2][0][:2] == ("spotify_seek", (-30,))
    assert fast("перемотай вперёд")[2][0][:2] == ("spotify_seek", (15,))
    assert fast("перемотай вперёд", browser=True)[1] == ["media_seek"], "видео в браузере — его плеер"
    assert fast("громче музыка у соседей")[0] is None, "не команда громкости"


@test
def my_music_phrases():
    for p in ("включи мои любимые треки", "Атлас, поставь мне любимые песни", "включи любимое",
              "play my liked songs", "включи понравившиеся"):
        assert fast(p)[2][0][:2] == ("spotify_play_library", ("liked",)), p
    assert fast("включи мой плейлист для учёбы")[2][0][:2] == ("spotify_play_library", ("для учёбы",))
    assert fast("включи мой плейлист «вечер»")[2][0][:2] == ("spotify_play_library", ("вечер",))
    assert fast("включи спокойную музыку")[2][0][0] == "play_on_spotify", "обычная музыка — как раньше"
    assert fast("включи любимые треки и сделай громче")[0] is None, "составная — мозгу"


@test
def spotify_volume_phrases():
    STATE["spotify_playing"] = True
    try:
        assert fast("Сделай звук погромче.")[2][0][:2] == ("spotify_volume", ("up",)), "играет Spotify — его ползунок"
        assert fast("сделай чуть тише")[2][0][:2] == ("spotify_volume", ("down",))
        assert fast("Сделай звук в Spotify погромче, скажем на 80.")[2][0][:2] == ("spotify_volume", ("set", 80))
        assert fast("громкость спотифая на 50 процентов")[2][0][:2] == ("spotify_volume", ("set", 50))
    finally:
        STATE["spotify_playing"] = False
    assert fast("Сделай звук погромче.")[1] == ["volume_up"], "Spotify не играет — громкость компьютера"
    assert fast("громкость 40")[2][0][:2] == ("set_volume", (40,)), "системная громкость числом — как раньше"


@test
def install_yourself_phrases():
    for p, dev in (("Атлас, установи себя на телефон", "phone"), ("установи себя на айфон", "phone"),
                   ("поставь себя на планшет", "tablet"), ("установи себя на телевизор", "tv")):
        r, names, calls = fast(p)
        assert r == ("speak", "Открыл на экране QR-код.") and calls[0][:2] == ("install", (dev,)), (p, r, calls)


@test
def unknown_goes_to_brain():
    for p in ("какая погода", "что ты обо мне знаешь", "", "найди рецепт в интернете"):
        assert fast(p)[0] is None, p


@test
def control_words():
    from core.control_words import is_stop, is_shutdown, normalize
    assert normalize("Атлас, выключись, пожалуйста!") == "выключись"
    assert is_shutdown("Атлас, выключись") and is_shutdown("go to sleep") and is_shutdown("Shut down.")
    assert not is_shutdown("выключи музыку") and not is_shutdown("выключи свет в комнате")
    assert is_stop("Стоп.") and is_stop("атлас, хватит") and is_stop("не надо")
    assert not is_stop("стоп музыку")


def main():
    global FC
    install()
    if "--compare" in sys.argv:
        old = load(sys.argv[sys.argv.index("--compare") + 1], "fast_commands_old")
        new = load(os.path.join(ROOT, "fast_commands.py"), "fast_commands_new")
        a, b = run_all(old), run_all(new)
        diffs = [(k, ra, rb, ca, cb) for (k, ra, ca), (_, rb, cb) in zip(a, b) if (ra, ca) != (rb, cb)]
        for k, ra, rb, ca, cb in diffs[:15]:
            print(f"  ✗ {k}\n      было:  {ra} {ca}\n      стало: {rb} {cb}")
        print(f"Сравнение старой и новой версии: {len(a) - len(diffs)} из {len(a)} совпали")
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0 if not diffs else 1)
    FC = load(os.path.join(ROOT, "fast_commands.py"), "fast_commands")
    ok = 0
    for t in TESTS:
        try:
            t()
            ok += 1
            print(f"  ✓ {t.__name__}")
        except Exception:
            print(f"  ✗ {t.__name__}\n" + "".join("      " + ln for ln in traceback.format_exc().splitlines(True)[-5:]))
    print(f"\nТестов пройдено: {ok} из {len(TESTS)}")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if ok == len(TESTS) else 1)


if __name__ == "__main__":
    main()
