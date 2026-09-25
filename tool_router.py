"""
Фильтр инструментов.

Проблема: TOOLS_SCHEMA со ста инструментами уходит в модель на каждом запросе
и съедает 5-6 тысяч токенов при лимите 8000 TPM. Отсюда постоянные ошибки 413.

Решение: по ключевым словам запроса определяем, какие группы инструментов
реально нужны, и отправляем только их. Для "включи музыку" незачем слать
описания git-команд, QR-кодов и настроек микрофона.

Фильтр выбирается один раз в начале ask_ai и держится до конца задачи
(sticky), чтобы на пятом шаге не исчезли инструменты, нужные для продолжения.
Плюс группа добавляется автоматически, если модель уже вызвала инструмент
из неё.
"""

# Всегда доступны — дешёвые и нужны почти в любой задаче
CORE = {
    "update_plan", "search_web", "remember_fact", "recall_conversations",
    "forget_memory", "get_weather",
}

GROUPS = {
    "media": {
        "play_on_netflix", "play_on_rezka", "media_play_pause", "media_seek",
        "media_volume", "media_player_fullscreen", "change_rezka_quality",
        "change_rezka_translator", "select_rezka_episode", "next_episode",
        "skip_intro", "play_pause_media", "next_track", "previous_track",
        "open_youtube", "play_on_spotify", "play_on_youtube_music",
    },
    "games": {
        "launch_steam_game", "list_steam_games", "open_deep_link", "open_app",
    },
    "browser": {
        "browser_open", "browser_read_page", "browser_click", "browser_type",
        "browser_scroll", "browser_fullscreen", "browser_press_key",
        "browser_screenshot_describe", "browser_close", "open_url",
        "search_google",
    },
    "files": {
        "open_file", "create_folder", "delete_file", "locate_file",
        "rename_file", "copy_file", "move_file","open_search_result"
    },
    "system": {
        "open_app", "close_app", "set_volume", "get_volume", "volume_up",
        "volume_down", "mute_volume", "unmute_volume", "set_brightness",
        "get_brightness", "lock_screen", "take_screenshot",
        "list_top_processes", "kill_process", "empty_recycle_bin",
        "get_uptime", "get_cpu_usage", "get_memory_usage",
        "get_battery_status", "get_disk_usage", "open_deep_link",
    },
    "notes": {
        "add_note", "list_notes", "delete_note", "add_todo", "list_todos",
        "complete_todo", "delete_todo",
    },
    "calendar": {
        "list_today_events", "list_upcoming_events", "create_event",
        "delete_event", "set_timer", "list_timers",
    },
    "mail": {"get_recent_emails", "get_unread_count"},
    "dev": {
        "run_git_command", "open_vscode_project", "calculate", "convert_units",
    },
    "network": {
        "ping_host", "get_my_ip", "get_local_ip", "is_website_up",
        "check_internet_speed",
    },
    "text": {"translate_text", "generate_qr_code", "word_count"},
    "settings": {
        "set_theme", "list_voices", "set_voice", "list_audio_devices",
        "set_microphone", "set_speaker", "set_response_language",
        "list_elevenlabs_voices", "set_elevenlabs_voice",
        "set_always_listening",
    },
    "fun": {"tell_joke", "random_fact", "start_number_game", "guess_number"},
    "info": {"get_news"},
}

TRIGGERS = {
    "media": (
        "фильм", "кино", "сериал", "серия", "сезон", "эпизод", "смотр",
        "плеер", "озвучк", "качеств", "субтитр", "заставк", "музык", "песн",
        "трек", "громч", "тише", "пауз", "перемот", "netflix", "нетфликс",
        "rezka", "резк", "youtube", "ютуб", "spotify", "спотиф", "movie", "series", "episode",
        "season", "watch", "play", "music", "song", "track", "pause",
        "volume", "skip", "subtitle",
    ),
    "browser": (
        "сайт", "браузер", "открой", "страниц", "найди в интернете", "гугл",
        "поиск", "вкладк", "ссылк", "кликн", "нажми на", "прокрут",
        "site", "browser", "page", "google", "tab",
        "link", "click", "scroll", "wikipedia", "википед",
    ),
    "files": (
        "файл", "папк", "документ", "переименуй", "скопируй", "перемести",
        "удали", "загрузк", "рабочий стол", "file", "folder", "document",
        "rename", "copy", "move", "delete", "desktop", "download", "search_file_content", "open_found_file", "где файл", "содержим", "внутри файла", "писал про", "скриншот", "screenshot", "картинк", "фото", "image", "picture", "photo"
    ),
    "system": (
        "приложени", "программ", "запусти", "закрой", "громкост", "яркост",
        "заблокир", "скриншот", "снимок экрана", "процесс", "корзин",
        "оператив", "диск", "батаре", "заряд", "процессор", "аптайм",
        "app", "launch", "close", "quit", "brightness", "lock",
        "screenshot", "process", "recycle", "cpu", "ram", "memory",
        "disk", "battery", "uptime",
    ),
    "notes": (
        "заметк", "запиши", "список дел", "задач", "тудушк", "напомнил",
        "note", "todo", "task", "write down", "list",
    ),
    "calendar": (
        "календар", "событи", "встреч", "расписан", "таймер", "будильник",
        "напомни", "через", "завтра", "сегодня", "повестк",
        "calendar", "event", "meeting", "schedule", "timer", "remind",
        "tomorrow", "today", "agenda",
    ),
    "mail": ("почт", "писем", "письм", "имейл", "mail", "email", "inbox",
             "unread", "непрочит"),
    "dev": (
        "git", "коммит", "репозитор", "вс код", "vs code", "vscode",
        "посчитай", "вычисли", "сколько будет", "конверт", "переведи в",
        "commit", "repo", "calculate", "convert",
    ),
    "network": (
        "пинг", "ip", "интернет", "скорость", "сеть", "доступен ли",
        "ping", "network", "speed", "online",
    ),
    "text": (
        "переведи", "перевод", "qr", "сколько слов", "количество символов",
        "translate", "translation", "word count",
    ),
    "settings": (
        "тема", "голос", "язык", "микрофон", "динамик", "наушник",
        "настройк", "прослушиван",
        "theme", "voice", "language", "microphone", "speaker", "setting",
        "listening",
    ),
    "games": (
        "игр", "game", "steam", "стим", "epic", "эпик", "запусти",
        "launch", "installed", "установлен",
    ),
    "fun": (
        "шутк", "анекдот", "факт", "игра", "угада",
        "joke", "fact", "game", "guess",
    ),
    "info": ("новост", "news", "headline"),
}

# Если ничего не совпало — скромный набор на каждый день
DEFAULT_GROUPS = ("system", "notes", "calendar", "info")

# Какой группе принадлежит инструмент (для sticky-логики)
_TOOL_TO_GROUP = {}
for _g, _names in GROUPS.items():
    for _n in _names:
        _TOOL_TO_GROUP[_n] = _g


_last_groups = set()


def _detect(q: str) -> set:
    return {g for g, words in TRIGGERS.items() if any(w in q for w in words)}


def note_topic(text: str) -> None:
    """Запоминает тему реплики, даже если её выполнил быстрый путь без модели.
    Нужно, чтобы следующее "да, включи" знало, о чём речь."""
    global _last_groups
    detected = _detect((text or "").lower())
    if detected:
        _last_groups = detected


def groups_for(question: str) -> set:
    """Определяет группы инструментов по тексту запроса. Короткие реплики
    ("да, включи", "а громче?") наследуют тему предыдущей."""
    global _last_groups
    q = question.lower()
    found = _detect(q)
    if len(q.split()) <= 6 and _last_groups:
        found |= _last_groups
    if found:
        _last_groups = set(found)
    else:
        found = set(DEFAULT_GROUPS)
    # Медиа почти всегда требует браузера — плееры живут на страницах
    if "media" in found:
        found.add("browser")
    return found


def group_of_tool(tool_name: str):
    """Группа, к которой относится инструмент (или None)."""
    return _TOOL_TO_GROUP.get(tool_name)


def filter_schema(full_schema: list, active_groups: set) -> list:
    """Оставляет в схеме только инструменты из CORE и активных групп."""
    allowed = set(CORE)
    for g in active_groups:
        allowed |= GROUPS.get(g, set())
    return [t for t in full_schema
            if t.get("function", {}).get("name") in allowed]

# ---------------------------------------------------------------------------
# Семантический роутер: инструменты по смыслу запроса, а не только по словам
# ---------------------------------------------------------------------------
import re as _re
import numpy as _np

SEMANTIC_TOP_K = 8          # сколько инструментов брать по смыслу
SEMANTIC_MIN_SIM = 0.2      # ниже — инструмент к запросу не относится
_tool_index = {"names": None, "mat": None}


def _build_tool_index(full_schema: list) -> None:
    from file_search import _embed          # та же MiniLM, что в поиске файлов
    names, texts = [], []
    for t in full_schema:
        f = t["function"]
        names.append(f["name"])
        texts.append(f"{f['name'].replace('_', ' ')}: {f.get('description', '')}")
    _tool_index["names"] = names
    _tool_index["mat"] = _embed(texts)


def semantic_tools(question: str, full_schema: list, k: int = SEMANTIC_TOP_K) -> set:
    from file_search import _embed
    if _tool_index["mat"] is None:
        _build_tool_index(full_schema)
    q = _re.sub(r"^\s*\([^)]*\)\s*", "", question)      # убираем подсказку языка в начале
    sims = _tool_index["mat"] @ _embed([q])[0]
    names = _tool_index["names"]
    return {names[j] for j in _np.argsort(-sims)[:k] if sims[j] >= SEMANTIC_MIN_SIM}


SEMANTIC_KEYWORD_K = 25     # из групп по ключевым словам — только инструменты из топ-25 по смыслу
_used_groups = set()        # группы инструментов, реально вызванных в прошлом ходе


def mark_used(tool_name: str) -> None:
    """Модель вызвала инструмент — его группа пригодится следующей реплике («50» после игры)."""
    g = _TOOL_TO_GROUP.get(tool_name)
    if g:
        _used_groups.add(g)


def _semantic_ranking(question: str, full_schema: list) -> list:
    from file_search import _embed
    if _tool_index["mat"] is None:
        _build_tool_index(full_schema)
    q = _re.sub(r"^\s*\([^)]*\)\s*", "", question)
    sims = _tool_index["mat"] @ _embed([q])[0]
    return [(_tool_index["names"][j], float(sims[j])) for j in _np.argsort(-sims)]


def smart_schema(question: str, full_schema: list, groups: set) -> list:
    """CORE
     + топ-8 инструментов по смыслу
     + из групп по ключевым словам — только близкие по смыслу
       («play the game» не тянет весь браузер и плееры)
     + группы, реально использованные в прошлом ходе."""
    global _used_groups
    allowed = set(CORE)
    for g in _used_groups:
        allowed |= GROUPS.get(g, set())
    _used_groups = set()
    try:
        ranking = _semantic_ranking(question, full_schema)
        allowed |= {n for n, s in ranking[:SEMANTIC_TOP_K] if s >= SEMANTIC_MIN_SIM}
        if set(groups) != set(DEFAULT_GROUPS):
            near = {n for n, _ in ranking[:SEMANTIC_KEYWORD_K]}
            for g in groups:
                grp = GROUPS.get(g, set())
                # маленькие группы (зрение, игры) — целиком: дёшево и надёжно
                allowed |= grp if len(grp) <= 4 else grp & near
    except Exception as e:
        print(f"[router] семантика недоступна ({e}) — беру группы по ключевым словам")
        for g in groups:
            allowed |= GROUPS.get(g, set())
    return [t for t in full_schema if t["function"]["name"] in allowed]

def add_group(active_schema: list, full_schema: list, group) -> list:
    """Модель вызвала инструмент из новой группы — добавляем её инструменты."""
    have = {t["function"]["name"] for t in active_schema}
    extra = [t for t in full_schema
             if t["function"]["name"] in GROUPS.get(group, set())
             and t["function"]["name"] not in have]
    return active_schema + extra

def register_tool(name: str, group: str) -> None:
    """Навык из реестра → в свою группу; смысловой индекс пересоберётся."""
    GROUPS.setdefault(group, set()).add(name)
    _TOOL_TO_GROUP[name] = group
    _tool_index["mat"] = None

# Зрение: явные слова про экран и клики
TRIGGERS["vision"] = ("жми", "нажми", "кликни", "ткни", "click", "press", "экран", "screen",
                      "в окне", "window", "что написано", "переведи то")