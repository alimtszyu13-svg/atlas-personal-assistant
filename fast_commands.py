"""
Быстрый путь для простых команд.

Идея взята из того, как работают шустрые голосовые ассистенты: команда вроде
"открой Chrome" не нуждается в рассуждениях языковой модели. Круг через LLM
стоит 1-3 секунды, генерация TTS — ещё столько же. Здесь команда распознаётся
локально по шаблону и функция вызывается напрямую, так что реакция
практически мгновенная.

try_fast_command() возвращает строку, если команда распознана и выполнена,
или None — тогда main.py отправляет запрос в ask_ai() как обычно.

Правило отбора: сюда попадают только ОДНОЗНАЧНЫЕ команды. Всё, где нужно
понять намерение, выбрать цель или связать несколько шагов, идёт в модель.
Лучше пропустить команду в LLM, чем выполнить не то.
"""

import re

# Показывать ли в интерфейсе результат быстрых команд (озвучивать их не надо —
# в этом и смысл: действие видно сразу, ждать голос незачем).
# Значение — то, что уйдёт в open_app(). Это либо ключ из APP_MAP, либо
# латинское название, по которому open_app найдёт ярлык в меню Пуск.
# Русские произношения нужны потому, что распознавание речи выдаёт "фотошоп",
# а ярлык называется "Adobe Photoshop".
APP_ALIASES = {
    # системные
    "блокнот": "блокнот", "notepad": "блокнот",
    "калькулятор": "калькулятор", "calculator": "калькулятор",
    "проводник": "проводник", "explorer": "проводник",
    "file explorer": "проводник", "файловый менеджер": "проводник",
    "paint": "paint", "пейнт": "paint", "паинт": "paint",
    "диспетчер задач": "диспетчер задач", "диспетчер": "диспетчер задач",
    "task manager": "диспетчер задач",
    "командная строка": "командная строка", "cmd": "командная строка",
    "терминал": "командная строка", "terminal": "командная строка",
    "консоль": "командная строка",
    "powershell": "powershell", "пауэршелл": "powershell",
    "параметры": "параметры", "настройки виндовс": "параметры",
    "панель управления": "панель управления",
    "ножницы": "ножницы", "snipping tool": "ножницы",

    # браузеры
    "хром": "хром", "chrome": "хром", "гугл хром": "хром",
    "google chrome": "хром", "браузер": "хром", "browser": "хром",
    "edge": "edge", "эдж": "edge",
    "firefox": "firefox", "файрфокс": "firefox", "фаерфокс": "firefox",
    "opera": "opera", "опера": "opera",
    "яндекс браузер": "яндекс браузер", "яндекс": "яндекс браузер",

    # офис
    "ворд": "ворд", "word": "ворд",
    "эксель": "эксель", "excel": "эксель",
    "powerpoint": "powerpoint", "пауэрпоинт": "powerpoint",
    "презентации": "powerpoint",
    "outlook": "outlook", "аутлук": "outlook",
    "onenote": "onenote", "ванноут": "onenote",

    # разработка
    "вс код": "вс код", "вскод": "вс код", "vs code": "вс код",
    "vscode": "вс код", "visual studio code": "вс код", "код": "вс код",
    "pycharm": "pycharm", "пайчарм": "pycharm",
    "гит": "git", "github desktop": "github",

    # общение и музыка
    "спотифай": "спотифай", "spotify": "спотифай", "спотик": "спотифай",
    "телеграм": "телеграм", "telegram": "телеграм", "телега": "телеграм",
    "дискорд": "дискорд", "discord": "дискорд", "диск": "дискорд",
    "whatsapp": "whatsapp", "ватсап": "whatsapp", "вотсап": "whatsapp",
    "zoom": "zoom", "зум": "zoom",
    "skype": "skype", "скайп": "skype",
    "вайбер": "viber", "viber": "viber",

    # игры и лаунчеры
    "стим": "steam", "steam": "steam",
    "эпик": "epic games", "эпик геймс": "epic games",
    "epic": "epic games", "epic games": "epic games",
    "роблокс": "roblox", "roblox": "roblox",
    "майнкрафт": "minecraft", "minecraft": "minecraft",
    "т лаунчер": "tlauncher", "тлаунчер": "tlauncher",
    "tlauncher": "tlauncher", "т-лаунчер": "tlauncher",
    "батл нет": "battle net", "battle net": "battle net",
    "battlenet": "battle net",
    "иа апп": "ea app", "ea app": "ea app", "origin": "ea app",
    "xbox": "xbox", "иксбокс": "xbox",
    "гог": "gog galaxy", "gog": "gog galaxy",
    "юплей": "ubisoft connect", "ubisoft": "ubisoft connect",

    # творчество
    "блендер": "blender", "blender": "blender",
    "обс": "obs", "obs": "obs", "обс студио": "obs",
    "фотошоп": "photoshop", "photoshop": "photoshop",
    "премьер": "premiere", "premiere": "premiere",
    "иллюстратор": "illustrator", "illustrator": "illustrator",
    "фигма": "figma", "figma": "figma",
    "давинчи": "davinci resolve", "davinci": "davinci resolve",
    "капкат": "capcut", "capcut": "capcut",
}


def _match_app(target: str):
    """Ищет приложение по алиасу. Сначала точное совпадение, потом вхождение —
    чтобы "гугл хром" не срезалось до "хром" раньше времени."""
    t = target.strip()
    if t in APP_ALIASES:
        return APP_ALIASES[t]
    for alias in sorted(APP_ALIASES, key=len, reverse=True):
        if alias in t:
            return APP_ALIASES[alias]
    return None


def _browser_media(fn_name: str, **kwargs):
    """Пробует управлять видео в браузере Atlas. Если браузер не открыт —
    возвращает None, и вызывающий код падает обратно на системные медиа-клавиши."""
    try:
        import browser_agent
        fn = getattr(browser_agent, fn_name, None)
        if fn is None:
            return None
        result = str(fn(**kwargs))
        if "не открыт" in result.lower() or "not open" in result.lower():
            return None
        return result
    except Exception:
        return None


FAILURE_MARKERS = (
    "не знаю", "не найд", "не смог", "не удалось", "ошибка", "не открыт",
    "unknown", "not found", "couldn't", "could not", "failed", "no such",
)


def _ok(result):
    """Возвращает результат, если он похож на успех. Если функция сообщила о
    неудаче — возвращает None, и запрос уходит в модель: она разберётся лучше,
    чем шаблон (например, подберёт другое имя приложения)."""
    if result is None:
        return None
    text = str(result)
    low = text.lower()
    if any(m in low for m in FAILURE_MARKERS):
        print(f"[FAST] не сработало, передаю модели: {text}")
        return None
    return text


NOT_AN_APP = (
    "фильм", "кино", "сериал", "серия", "видео", "музык", "песн", "трек",
    "сайт", "страниц", "ссылк", "письм", "почт", "папк", "файл", "документ",
    "заметк", "календар", "погод", "новост",
    "movie", "video", "music", "song", "site", "page", "link", "mail",
    "folder", "file", "note", "weather", "news",
)


def try_fast_command(text: str):
    t = text.lower().strip().strip('«»"\'“”„').rstrip(".!?").strip()
        # Угадай число: во время игры число из фразы сразу идёт в guess_number —
    # без модели, мгновенно и без выдуманных подсказок
    from skills.fun import game_active, guess_number
    if game_active():
        m = re.search(r"\b(\d{1,3})\b", t)
        if m:
            return ("speak", guess_number(int(m.group(1))))   # ответ игры нужно услышать
    if not t:
        return None

    # ---------- поиск файлов по содержимому ----------
    # "где файл про бюджет поездки" / "найди файл где я писал про стажировку"
    m = re.match(
        r"^(?:где|найди|найти|поищи|покажи)\s+(?:мне\s+)?"
        r"(?:файл|документ|заметк\w*|запис\w*|скриншот\w*|снимок\w*|картинк\w*|фото\w*)\s*"
        r"(?:,?\s*(?:где|в котором|с текстом|про|о|об)\s+)?(.+)$", t)
    if m:
        what = m.group(1).strip()
        what = re.sub(r"^(?:я\s+)?(?:писал|написал|сохранил|говорил)\s+(?:про|о|об)\s+", "", what)
        if len(what) > 2:
            from file_search import search_file_content
            return _ok(search_file_content(what))

    # English: "where is the file about the budget for the trip"
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

        # "открой второй" / "open the second one" / "open 3"
    m = re.match(r"^(?:открой|open)\s+(?:the\s+)?(?:номер\s+|number\s+)?(\w+)"
                 r"(?:\s+(?:файл|file|one|результат))?$", t)
    if m:
        from file_search import _ORD, open_search_result
        w = m.group(1)
        if w in _ORD or w.isdigit():
            return _ok(open_search_result(w))

    # "покажи второй в папке" / "show the second one in folder"
    m = re.match(r"^(?:покажи|show)\s+(?:the\s+)?(\w+)(?:\s+(?:файл|file|one))?"
                 r"\s+(?:в папке|in (?:the\s+)?folder)$", t)
    if m:
        from file_search import _ORD, show_search_result_in_folder
        w = m.group(1)
        if w in _ORD or w.isdigit():
            return _ok(show_search_result_in_folder(w))
    
    m = re.match(r"^(?:открой)\s+файл\s+(?:где|про|о|с текстом)\s+(.+)$", t)
    if m:
        from file_search import open_found_file
        return _ok(open_found_file(m.group(1).strip()))

    # ---------- игры Steam: запуск по appid, без открытия библиотеки ----------
    m = re.match(
        r"^(?:запусти|включи|открой|play|launch|start)\s+(?:игру\s+|game\s+)?"
        r"(.+?)\s+(?:в|на|через|in|on|via)\s+(?:стим|steam)е?$", t)
    if m:
        from deep_links import launch_steam_game
        return _ok(launch_steam_game(m.group(1).strip()))

    m = re.match(r"^(?:запусти|включи|открой|play|launch)\s+игру\s+(.+)$", t)
    if m:
        from deep_links import launch_steam_game
        return _ok(launch_steam_game(m.group(1).strip()))

    if re.search(r"(какие|список|мои|покажи)\s+(у меня\s+)?игр|"
                 r"(what|which|list|show)\s+(my\s+)?games|games\s+(are\s+)?installed|"
                 r"игры\s+(в|на)\s+стим", t):
        from deep_links import list_steam_games
        return _ok(list_steam_games())

    # ---------- сервисы по прямой ссылке ----------
    SERVICE_WORDS = {
        "ютубе": "youtube", "ютуб": "youtube", "youtube": "youtube",
        "твиче": "twitch", "твич": "twitch", "twitch": "twitch",
        "картах": "maps", "картаx": "maps", "maps": "maps",
        "гитхабе": "github", "github": "github",
        "википедии": "wikipedia", "википедия": "wikipedia",
        "wikipedia": "wikipedia",
    }
    m = re.match(
        r"^(?:найди|найти|поищи|открой|покажи|search|find|open)\s+(.+?)"
        r"\s+(?:в|на|in|on)\s+(\w+)$", t)
    if m:
        service = SERVICE_WORDS.get(m.group(2).strip())
        if service:
            from deep_links import open_deep_link
            return _ok(open_deep_link(service, m.group(1).strip()))

    # ---------- музыка: сразу в приложение, без браузера и без модели ----------
    # "включи спокойную музыку" / "включи Linkin Park на спотифае" / "поставь джаз"
    m = re.match(
        r"^(?:включи|поставь|запусти|врубай|play|put on|turn on)\s+"
        r"(?:мне\s+|us\s+|some\s+)?(.+?)"
        r"(?:\s+(?:на|in|on)\s+(спотифае|спотифай|spotify|ютуб музыке|youtube music))?$", t)
    if m:
        what = m.group(1).strip()
        service = (m.group(2) or "").strip()
        is_music_word = any(w in what for w in (
            "музык", "песн", "трек", "плейлист", "альбом", "радио",
            "music", "song", "track", "playlist", "album", "radio"))
        if service or is_music_word:
            # Обрезка нужна только чтобы понять, есть ли осмысленный запрос:
            # "включи музыку" -> просто открыть приложение,
            # "включи спокойную музыку" -> искать по всей фразе, так точнее.
            stripped = what
            for filler in ("музыку", "музыка", "музыки", "песню", "песни",
                           "трек", "треки", "music", "song", "songs"):
                stripped = stripped.replace(filler, "").strip()
            query = what if stripped else ""
            if "youtube" in service or "ютуб" in service:
                from system_control import play_on_youtube_music
                return _ok(play_on_youtube_music(query or "music"))
            from system_control import play_on_spotify
            return _ok(play_on_spotify(query))

    # ---------- приложения ----------
    m = re.match(r"^(?:открой|запусти|включи приложение|open|launch|start)\s+(.+)$", t)
    if m:
        target = m.group(1).strip()
        # "открой фильм X" / "открой сайт Y" — это не про приложения, пусть
        # разбирается модель
        if not any(w in target for w in NOT_AN_APP):
            # известный алиас, иначе отдаём как есть — open_app сам поищет
            # ярлык в меню Пуск
            from system_control import open_app
            return _ok(open_app(_match_app(target) or target))

    m = re.match(r"^(?:закрой|выключи приложение|close|quit)\s+(.+)$", t)
    if m:
        target = m.group(1).strip()
        if not any(w in target for w in NOT_AN_APP):
            from system_control import close_app
            return _ok(close_app(_match_app(target) or target))

    # ---------- громкость ----------
    m = re.match(r"^(?:громкость|volume)\s+(?:на\s+)?(\d{1,3})\s*%?$", t)
    if m:
        from system_advanced import set_volume
        return _ok(set_volume(int(m.group(1))))

    if t in ("громче", "сделай громче", "погромче", "louder", "volume up"):
        result = _ok(_browser_media("media_volume", action="up"))
        if result:
            return result
        from system_advanced import volume_up
        return _ok(volume_up())

    if t in ("тише", "сделай тише", "потише", "quieter", "volume down"):
        result = _ok(_browser_media("media_volume", action="down"))
        if result:
            return result
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

    # ---------- яркость ----------
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

    # ---------- видео в браузере ----------
    if t in ("пауза", "паузу", "поставь на паузу", "pause", "продолжи",
             "продолжай", "resume", "play"):
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
            return result
        # браузера нет — пусть решает модель, что пользователь имел в виду

    if t in ("перемотай вперёд", "перемотай вперед", "вперёд", "вперед",
             "forward", "seek forward"):
        result = _ok(_browser_media("media_seek", direction="forward"))
        if result:
            return result

    if t in ("перемотай назад", "назад", "backward", "seek backward"):
        result = _ok(_browser_media("media_seek", direction="backward"))
        if result:
            return result

    # ---------- музыка (системные медиа-клавиши) ----------
    if t in ("следующий трек", "следующая песня", "next track", "next song"):
        from media_control import next_track
        return _ok(next_track())

    if t in ("предыдущий трек", "предыдущая песня", "previous track", "previous song"):
        from media_control import previous_track
        return _ok(previous_track())

    # ---------- интерфейс ----------
    if t in ("тёмная тема", "темная тема", "тёмный режим", "dark theme", "dark mode"):
        from theme_control import set_theme
        return _ok(set_theme("dark"))

    if t in ("светлая тема", "светлый режим", "light theme", "light mode"):
        from theme_control import set_theme
        return _ok(set_theme("light"))

    # не распознано — пусть решает модель
    return None