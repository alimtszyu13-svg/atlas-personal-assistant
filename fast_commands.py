"""
Быстрый путь для простых команд — без модели, практически мгновенно.

Команда вроде «открой Chrome» не нуждается в рассуждениях модели: она распознаётся
локально по шаблону, и функция вызывается напрямую.

try_fast_command(text) возвращает:
    строку            — выполнено, показать в чате (озвучивать не нужно — результат и так виден);
    ("speak", текст)  — выполнено, ответ нужно произнести;
    None              — не распознано или не получилось → main.py отдаёт команду мозгу.

Правило отбора: сюда попадают только ОДНОЗНАЧНЫЕ команды. Всё, где нужно понять намерение,
выбрать цель или связать несколько шагов, идёт в модель. Лучше пропустить команду в модель,
чем выполнить не то. Составные просьбы («открой блокнот и запиши…») — тоже в модель.

Разделы: фоновые миссии · игра · шутки и факты · поиск файлов · Steam · сервисы по ссылке ·
музыка · приложения · громкость и яркость · система · видео в браузере · треки · тема.
"""
import re

# Произношение → имя для open_app (ключ APP_MAP или название ярлыка в меню Пуск).
# Русские варианты нужны потому, что распознавание речи выдаёт «фотошоп», а ярлык — «Adobe Photoshop».
APP_ALIASES = {
    # системные
    "блокнот": "блокнот", "notepad": "блокнот",
    "калькулятор": "калькулятор", "calculator": "калькулятор",
    "проводник": "проводник", "explorer": "проводник", "file explorer": "проводник", "файловый менеджер": "проводник",
    "paint": "paint", "пейнт": "paint", "паинт": "paint",
    "диспетчер задач": "диспетчер задач", "диспетчер": "диспетчер задач", "task manager": "диспетчер задач",
    "командная строка": "командная строка", "cmd": "командная строка", "терминал": "командная строка",
    "terminal": "командная строка", "консоль": "командная строка",
    "powershell": "powershell", "пауэршелл": "powershell",
    "параметры": "параметры", "настройки виндовс": "параметры",
    "панель управления": "панель управления",
    "ножницы": "ножницы", "snipping tool": "ножницы",
    # браузеры
    "хром": "хром", "chrome": "хром", "гугл хром": "хром", "google chrome": "хром", "браузер": "хром", "browser": "хром",
    "edge": "edge", "эдж": "edge",
    "firefox": "firefox", "файрфокс": "firefox", "фаерфокс": "firefox",
    "opera": "opera", "опера": "opera",
    "яндекс браузер": "яндекс браузер", "яндекс": "яндекс браузер",
    # офис
    "ворд": "ворд", "word": "ворд", "эксель": "эксель", "excel": "эксель",
    "powerpoint": "powerpoint", "пауэрпоинт": "powerpoint", "презентации": "powerpoint",
    "outlook": "outlook", "аутлук": "outlook", "onenote": "onenote", "ванноут": "onenote",
    # разработка
    "вс код": "вс код", "вскод": "вс код", "vs code": "вс код", "vscode": "вс код", "visual studio code": "вс код",
    "код": "вс код", "pycharm": "pycharm", "пайчарм": "pycharm", "гит": "git", "github desktop": "github",
    # общение и музыка
    "спотифай": "спотифай", "spotify": "спотифай", "спотик": "спотифай",
    "телеграм": "телеграм", "telegram": "телеграм", "телега": "телеграм",
    "дискорд": "дискорд", "discord": "дискорд", "диск": "дискорд",
    "whatsapp": "whatsapp", "ватсап": "whatsapp", "вотсап": "whatsapp",
    "zoom": "zoom", "зум": "zoom", "skype": "skype", "скайп": "skype", "вайбер": "viber", "viber": "viber",
    # игры и лаунчеры
    "стим": "steam", "steam": "steam",
    "эпик": "epic games", "эпик геймс": "epic games", "epic": "epic games", "epic games": "epic games",
    "роблокс": "roblox", "roblox": "roblox", "майнкрафт": "minecraft", "minecraft": "minecraft",
    "т лаунчер": "tlauncher", "тлаунчер": "tlauncher", "tlauncher": "tlauncher", "т-лаунчер": "tlauncher",
    "батл нет": "battle net", "battle net": "battle net", "battlenet": "battle net",
    "иа апп": "ea app", "ea app": "ea app", "origin": "ea app",
    "xbox": "xbox", "иксбокс": "xbox", "гог": "gog galaxy", "gog": "gog galaxy",
    "юплей": "ubisoft connect", "ubisoft": "ubisoft connect",
    # творчество
    "блендер": "blender", "blender": "blender", "обс": "obs", "obs": "obs", "обс студио": "obs",
    "фотошоп": "photoshop", "photoshop": "photoshop", "премьер": "premiere", "premiere": "premiere",
    "иллюстратор": "illustrator", "illustrator": "illustrator", "фигма": "figma", "figma": "figma",
    "давинчи": "davinci resolve", "davinci": "davinci resolve", "капкат": "capcut", "capcut": "capcut",
}

# «найди X в/на ютубе» → прямая ссылка на сервис
SERVICE_WORDS = {
    "ютубе": "youtube", "ютуб": "youtube", "youtube": "youtube",
    "твиче": "twitch", "твич": "twitch", "twitch": "twitch",
    "картах": "maps", "картаx": "maps", "maps": "maps",
    "гитхабе": "github", "github": "github",
    "википедии": "wikipedia", "википедия": "wikipedia", "wikipedia": "wikipedia",
}

# «открой фильм X» / «открой сайт Y» — это не приложения, пусть разбирается модель
NOT_AN_APP = (
    "фильм", "кино", "сериал", "серия", "видео", "музык", "песн", "трек",
    "сайт", "страниц", "ссылк", "письм", "почт", "папк", "файл", "документ",
    "заметк", "календар", "погод", "новост",
    "movie", "video", "music", "song", "site", "page", "link", "mail",
    "folder", "file", "note", "weather", "news",
)

FAILURE_MARKERS = (
    "не знаю", "не найд", "не нашёл", "не нашел", "не могу найти", "не смог", "не удалось", "ошибка", "не открыт",
    "unknown", "not found", "couldn't", "could not", "failed", "no such",
)

_MULTI = re.compile(r"\s(?:и|а потом|потом|затем|после этого|and|then)\s|,\s*(?:и\s+)?"
                    r"(?:запиши|напиши|открой|включи|найди|сделай|поставь|write|open|play|find)")


_SPOTIFY_WORD = r"(?:(?:в|на)\s+(?:spotify|спотифа[йе]|спотике)\s+)?"
_LOUDER = re.compile(r"(?:(?:сделай(?:те)?|сделаем|давай)\s+)?(?:(?:музыку|звук|громкость)\s+)?" + _SPOTIFY_WORD +
                     r"(?:(?:немного|чуть|чуть-чуть|ещё|еще)\s+)?(?:по)?громче(?:\s+(?:музыку|звук|пожалуйста))?")
_QUIETER = re.compile(r"(?:(?:сделай(?:те)?|сделаем|давай)\s+)?(?:(?:музыку|звук|громкость)\s+)?" + _SPOTIFY_WORD +
                      r"(?:(?:немного|чуть|чуть-чуть|ещё|еще)\s+)?(?:по)?тише(?:\s+(?:музыку|звук|пожалуйста))?")
_SEEK = re.compile(r"(?:перемотай|промотай|отмотай)(?:\s+(?:на\s+)?(?P<n1>\d+)\s*(?:секунд\w*|сек))?\s+"
                   r"(?P<dir>вперёд|вперед|назад)(?:\s+(?:на\s+)?(?P<n2>\d+)\s*(?:секунд\w*|сек))?")


def _match_app(target: str):
    """Сначала точное совпадение, потом вхождение (длинные первыми — «гугл хром» не срежется до «хром»)."""
    t = target.strip()
    if t in APP_ALIASES:
        return APP_ALIASES[t]
    for alias in sorted(APP_ALIASES, key=len, reverse=True):
        if alias in t:
            return APP_ALIASES[alias]
    return None


def _browser_media(fn_name: str, **kwargs):
    """Управление видео в браузере Atlas; браузер не открыт → None (дальше — системные медиа-клавиши)."""
    try:
        import browser_agent
        if not getattr(browser_agent, "_browser_alive", lambda: True)():
            return None                        # браузер закрыт — не запускаем его ради громкости/паузы
        fn = getattr(browser_agent, fn_name, None)
        if fn is None:
            return None
        result = str(fn(**kwargs))
        if "не открыт" in result.lower() or "not open" in result.lower():
            return None
        return result
    except Exception:
        return None


def _ok(result):
    """Результат, если похож на успех; иначе None — пусть разберётся модель (подберёт другое имя и т.п.)."""
    if result is None:
        return None
    text = str(result)
    if any(m in text.lower() for m in FAILURE_MARKERS):
        print(f"[FAST] не сработало, передаю модели: {text}")
        return None
    return text


def try_fast_command(text: str):
    t = text.lower().strip().strip('«»"\'“”„').rstrip(".!?").strip()
    t = re.sub(r"^(?:атлас|atlas)[,!\s]+", "", t).strip()     # «Атлас, включи музыку» — та же команда
    if not t:
        return None
    multi = bool(_MULTI.search(t))

    # ---------- «в фоне …» — сразу фоновая миссия ----------
    if re.search(r"(?:^|\s)(?:в|на) фоне\b|in the background", t):
        goal = re.sub(r"\s*(?:атлас,?\s*)?(?:(?:в|на) фоне|in the background)\s*", " ", text, flags=re.I).strip(" ,.")
        if len(goal) > 5:
            from core import missions
            from ui_state import shared_state
            from voice import get_response_language
            # последние реплики — чтобы «сравни их» знало, кого «их»
            ctx = [f"{who}: {str(msg)[:200]}" for who, msg in shared_state.get("chat_history", [])[-5:-1]]
            mid = missions.start(goal + ("\n\nRecent conversation for context:\n" + "\n".join(ctx) if ctx else ""))
            if get_response_language() == "ru":
                return ("speak", f"Принял, миссия {mid} в работе. Скажу, когда будет готово.")
            return ("speak", f"On it — mission {mid} is running. I'll tell you when it's done.")

    # ---------- игра «угадай число»: число из фразы — сразу в игру ----------
    from skills.fun import game_active, guess_number
    if game_active():
        m = re.search(r"\b(\d{1,3})\b", t)
        if m:
            return ("speak", guess_number(int(m.group(1))))

    # ---------- шутка / факт ----------
    m = re.match(r"^(?:расскажи\s+)?(?:мне\s+)?(?:шутк\w*|анекдот\w*)(?:\s+(?:про|о|об)\s+(.+))?$", t) \
        or re.match(r"^(?:tell\s+(?:me\s+)?)?(?:a\s+)?joke(?:\s+about\s+(.+))?$", t)
    if m:
        from skills.fun import tell_joke
        return ("speak", tell_joke(m.group(1) or ""))
    m = re.match(r"^(?:расскажи\s+)?(?:мне\s+)?(?:интересный\s+)?факт(?:\s+(?:про|о|об)\s+(.+))?$", t) \
        or re.match(r"^(?:tell\s+(?:me\s+)?)?(?:a\s+|an\s+)?(?:random\s+|interesting\s+)?fact(?:\s+about\s+(.+))?$", t)
    if m:
        from skills.fun import random_fact
        return ("speak", random_fact(m.group(1) or ""))

    # ---------- поиск файлов по содержимому ----------
    # «где файл про бюджет поездки» / «найди файл где я писал про стажировку»
    m = re.match(
        r"^(?:где|найди|найти|поищи|покажи)\s+(?:мне\s+)?"
        r"(?:файл|документ|заметк\w*|запис\w*|скриншот\w*|снимок\w*|картинк\w*|фото\w*)\s*"
        r"(?:,?\s*(?:где|в котором|с текстом|про|о|об)\s+)?(.+)$", t)
    if m:
        what = re.sub(r"^(?:я\s+)?(?:писал|написал|сохранил|говорил)\s+(?:про|о|об)\s+", "", m.group(1).strip())
        if len(what) > 2:
            from file_search import search_file_content
            return _ok(search_file_content(what))
    m = re.match(
        r"^(?:where(?:'s| is)|find|search for|show me)\s+(?:the\s+|my\s+|a\s+)?"
        r"(?:file|document|doc|note|screenshot|image|picture|photo)s?\s+"
        r"(?:about|with|on|where|that|of|showing)\s+(.+)$", t)
    if m and len(m.group(1).strip()) > 2:
        from file_search import search_file_content
        return _ok(search_file_content(m.group(1).strip()))
    m = re.match(r"^open\s+(?:the\s+)?(?:file|document)\s+(?:about|with|where)\s+(.+)$", t)
    if m:
        from file_search import open_found_file
        return _ok(open_found_file(m.group(1).strip()))
    # «открой второй» / «open the second one» / «open 3»
    m = re.match(r"^(?:открой|open)\s+(?:the\s+)?(?:номер\s+|number\s+)?(\w+)(?:\s+(?:файл|file|one|результат))?$", t)
    if m:
        from file_search import _ORD, open_search_result
        w = m.group(1)
        if w in _ORD or w.isdigit():
            return _ok(open_search_result(w))
    # «покажи второй в папке» / «show the second one in folder»
    m = re.match(r"^(?:покажи|show)\s+(?:the\s+)?(\w+)(?:\s+(?:файл|file|one))?\s+(?:в папке|in (?:the\s+)?folder)$", t)
    if m:
        from file_search import _ORD, show_search_result_in_folder
        w = m.group(1)
        if w in _ORD or w.isdigit():
            return _ok(show_search_result_in_folder(w))
    m = re.match(r"^(?:открой)\s+файл\s+(?:где|про|о|с текстом)\s+(.+)$", t)
    if m:
        from file_search import open_found_file
        return _ok(open_found_file(m.group(1).strip()))

    # ---------- игры Steam: запуск по appid, без библиотеки ----------
    m = re.match(r"^(?:запусти|включи|открой|play|launch|start)\s+(?:игру\s+|game\s+)?"
                 r"(.+?)\s+(?:в|на|через|in|on|via)\s+(?:стим|steam)е?$", t)
    if m:
        from deep_links import launch_steam_game
        return _ok(launch_steam_game(m.group(1).strip()))
    m = re.match(r"^(?:запусти|включи|открой|play|launch)\s+игру\s+(.+)$", t)
    if m:
        from deep_links import launch_steam_game
        return _ok(launch_steam_game(m.group(1).strip()))
    if re.search(r"(какие|список|мои|покажи)\s+(у меня\s+)?игр|(what|which|list|show)\s+(my\s+)?games|"
                 r"games\s+(are\s+)?installed|игры\s+(в|на)\s+стим", t):
        from deep_links import list_steam_games
        return _ok(list_steam_games())

    # ---------- сервисы по прямой ссылке ----------
    m = re.match(r"^(?:найди|найти|поищи|открой|покажи|search|find|open)\s+(.+?)\s+(?:в|на|in|on)\s+(\w+)$", t)
    if m:
        service = SERVICE_WORDS.get(m.group(2).strip())
        if service:
            from deep_links import open_deep_link
            return _ok(open_deep_link(service, m.group(1).strip()))

    # ---------- Atlas на других устройствах ----------
    m = re.fullmatch(r"(?:установи|поставь|загрузи|перенеси)\s+себя\s+(?:на|в)\s+(?:мой\s+)?"
                     r"(телефон|смартфон|мобильник|айфон|андроид|планшет|айпад|телевизор|телек|тв|другое устройство)", t)
    if m:
        dev = {"планшет": "tablet", "айпад": "tablet", "телевизор": "tv", "телек": "tv", "тв": "tv"}.get(m.group(1), "phone")
        from phone.install import install
        return ("speak", install(dev))

    # ---------- моя библиотека Spotify: любимые треки и свои плейлисты ----------
    if not multi and re.fullmatch(r"(?:включи|поставь|запусти|врубай|play)\s+(?:мне\s+)?(?:мои\s+|мою\s+)?"
                                  r"(?:любимые|понравившиеся|сохран[её]нные)\s*(?:треки|песни|музыку)?|"
                                  r"(?:включи|поставь|запусти|play)\s+(?:мне\s+)?(?:любимое|мою музыку|my liked songs|"
                                  r"liked songs|my favorites|my favourites)", t):
        from system_control import spotify_play_library
        return _ok(spotify_play_library("liked"))
    m = re.fullmatch(r"(?:включи|поставь|запусти|play)\s+(?:мне\s+)?(?:мой|my)\s+(?:плейлист|playlist)\s+(.+)", t)
    if m and not multi:
        from system_control import spotify_play_library
        return _ok(spotify_play_library(m.group(1).strip(" «»\"'")))

    # ---------- музыка: сразу в приложение ----------
    # «включи спокойную музыку» / «включи Linkin Park на спотифае» / «поставь джаз»
    m = re.match(r"^(?:включи|поставь|запусти|врубай|play|put on|turn on)\s+(?:мне\s+|us\s+|some\s+)?(.+?)"
                 r"(?:\s+(?:на|in|on)\s+(спотифае|спотифай|spotify|ютуб музыке|youtube music))?$", t)
    if m and not multi:
        what, service = m.group(1).strip(), (m.group(2) or "").strip()
        is_music_word = any(w in what for w in ("музык", "песн", "трек", "плейлист", "альбом", "радио",
                                                "music", "song", "track", "playlist", "album", "radio"))
        if service or is_music_word:
            # «включи музыку» → просто открыть приложение; «включи спокойную музыку» → искать по всей фразе
            stripped = what
            for filler in ("какую-нибудь", "какую нибудь", "какую-то", "что-нибудь", "что нибудь", "любую", "немного",
                           "мне", "музыку", "музыка", "музыки", "песню", "песни", "трек", "треки",
                           "any", "some", "music", "song", "songs"):
                stripped = stripped.replace(filler, "").strip()
            query = " ".join(re.sub(r"(?<![\w-])(?:какую-нибудь|какую нибудь|какую-то|что-нибудь|что нибудь|"
                                    r"любую|немного|мне)(?![\w-])", " ", what).split()) if stripped else ""
            if "youtube" in service or "ютуб" in service:
                from system_control import play_on_youtube_music
                return _ok(play_on_youtube_music(query or "music"))
            from system_control import play_on_spotify
            return _ok(play_on_spotify(query))

    # ---------- приложения ----------
    m = re.match(r"^(?:открой|запусти|включи приложение|open|launch|start)\s+(.+)$", t)
    if m and not multi:
        target = m.group(1).strip()
        if not any(w in target for w in NOT_AN_APP):
            from system_control import open_app
            return _ok(open_app(_match_app(target) or target))     # неизвестное — open_app поищет ярлык сам
    m = re.match(r"^(?:закрой|выключи приложение|close|quit)\s+(.+)$", t)
    if m and not multi:
        target = m.group(1).strip()
        if not any(w in target for w in NOT_AN_APP):
            from system_control import close_app
            return _ok(close_app(_match_app(target) or target))

    # ---------- громкость и яркость ----------
    m = re.fullmatch(r"(?:(?:сделай|поставь|выставь)\s+)?(?:громкость|звук)\s+(?:в\s+|у\s+)?(?:spotify|спотифа[йея]|музыки)"
                     r"[\s,]+(?:(?:погромче|потише|скажем|где-то|примерно)[\s,]+)*(?:на\s+)?(\d{1,3})"
                     r"(?:\s*(?:%|процент\w*))?", t)
    if m:
        from system_control import spotify_volume
        return _ok(spotify_volume("set", int(m.group(1))))
    m = re.match(r"^(?:громкость|volume)\s+(?:на\s+)?(\d{1,3})\s*%?$", t)
    if m:
        from system_advanced import set_volume
        return _ok(set_volume(int(m.group(1))))
    if t in ("громче", "сделай громче", "погромче", "louder", "volume up") or _LOUDER.fullmatch(t):
        result = _ok(_browser_media("media_volume", action="up"))
        if result:
            return result
        from system_control import spotify_is_playing, spotify_volume
        if spotify_is_playing():                   # играет Spotify — двигаем его ползунок
            return _ok(spotify_volume("up"))
        from system_advanced import volume_up
        return _ok(volume_up())
    if t in ("тише", "сделай тише", "потише", "quieter", "volume down") or _QUIETER.fullmatch(t):
        result = _ok(_browser_media("media_volume", action="down"))
        if result:
            return result
        from system_control import spotify_is_playing, spotify_volume
        if spotify_is_playing():
            return _ok(spotify_volume("down"))
        from system_advanced import volume_down
        return _ok(volume_down())
    if t in ("выключи звук", "без звука", "мьют", "mute", "приглуши"):
        result = _ok(_browser_media("media_volume", action="mute"))
        if result:
            return result
        from system_advanced import mute_volume
        return _ok(mute_volume())
    if t in ("включи звук", "верни звук", "unmute"):
        from system_advanced import unmute_volume
        return _ok(unmute_volume())
    m = re.match(r"^(?:яркость|brightness)\s+(?:на\s+)?(\d{1,3})\s*%?$", t)
    if m:
        from system_advanced import set_brightness
        return _ok(set_brightness(int(m.group(1))))

    # ---------- система ----------
    if t in ("заблокируй экран", "заблокируй", "блокировка", "lock", "lock screen"):
        from system_advanced import lock_screen
        return _ok(lock_screen())
    if t in ("скриншот", "сделай скриншот", "снимок экрана", "screenshot", "take a screenshot"):
        from system_advanced import take_screenshot
        return _ok(take_screenshot())

    # ---------- видео в браузере (иначе — системные медиа-клавиши) ----------
    if t in ("пауза", "паузу", "поставь на паузу", "pause", "продолжи", "продолжай", "resume", "play"):
        result = _ok(_browser_media("media_play_pause"))
        if result:
            return result
        from media_control import play_pause_media
        return _ok(play_pause_media())
    if t in ("пропусти заставку", "пропусти интро", "скип интро", "skip intro"):
        result = _ok(_browser_media("skip_intro"))
        if result:
            return result
    if t in ("следующая серия", "следующий эпизод", "next episode"):
        result = _ok(_browser_media("next_episode"))
        if result:
            return result
    if t in ("на весь экран", "полный экран", "разверни", "fullscreen", "full screen"):
        result = _ok(_browser_media("media_player_fullscreen"))
        if result:
            return result                          # браузера нет — пусть модель решит, что имелось в виду
    m = _SEEK.fullmatch(t)
    if m:
        back = m.group("dir") == "назад"
        result = _ok(_browser_media("media_seek", direction="backward" if back else "forward"))
        if result:
            return result                          # видео в браузере Atlas
        n = int(m.group("n1") or m.group("n2") or 15)
        from system_control import spotify_seek
        return _ok(spotify_seek(-n if back else n))

    if t in ("перемотай вперёд", "перемотай вперед", "вперёд", "вперед", "forward", "seek forward"):
        result = _ok(_browser_media("media_seek", direction="forward"))
        if result:
            return result
    if t in ("перемотай назад", "назад", "backward", "seek backward"):
        result = _ok(_browser_media("media_seek", direction="backward"))
        if result:
            return result

    # ---------- треки (системные медиа-клавиши) ----------
    if t in ("следующий трек", "следующая песня", "next track", "next song"):
        from media_control import next_track
        return _ok(next_track())
    if t in ("предыдущий трек", "предыдущая песня", "previous track", "previous song"):
        from media_control import previous_track
        return _ok(previous_track())

    # ---------- тема интерфейса ----------
    if t in ("тёмная тема", "темная тема", "тёмный режим", "dark theme", "dark mode"):
        from theme_control import set_theme
        return _ok(set_theme("dark"))
    if t in ("светлая тема", "светлый режим", "light theme", "light mode"):
        from theme_control import set_theme
        return _ok(set_theme("light"))

    return None                                    # не распознано — пусть решает модель
