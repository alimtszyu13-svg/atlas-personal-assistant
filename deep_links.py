"""
Прямые ссылки и протоколы — способ попасть точно в цель за один шаг.

Смысл модуля: вместо того чтобы открывать браузер, искать, кликать и гонять
модель по кругу (это минуты), дёрнуть готовый протокол приложения. Steam
умеет steam://rungameid/570, Spotify — spotify:search:jazz, и так далее.
Разница в скорости примерно стократная.

Игры Steam берутся из локальных файлов appmanifest_*.acf, которые Steam сам
пишет для каждой установленной игры. Это значит, что список всегда точный,
не требует интернета и работает мгновенно.
"""

import os
import re
import glob
import subprocess
import difflib
import urllib.parse

# ---------------------------------------------------------------------------
# Steam
# ---------------------------------------------------------------------------

_steam_games_cache = None


def _steam_root():
    """Путь к установленному Steam: сначала реестр, потом обычные места."""
    try:
        import winreg
        for hive, path in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                           (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, path) as key:
                    value = winreg.QueryValueEx(key, "SteamPath")[0]
                    if value and os.path.isdir(value):
                        return value
            except OSError:
                continue
    except Exception:
        pass

    for candidate in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam",
                      r"D:\Steam", r"E:\Steam"):
        if os.path.isdir(candidate):
            return candidate
    return None


def _steam_libraries(root):
    """Steam хранит игры в нескольких папках (например, на другом диске) —
    их список лежит в libraryfolders.vdf."""
    libs = [os.path.join(root, "steamapps")]
    vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, encoding="utf-8", errors="ignore") as f:
            for match in re.finditer(r'"path"\s+"([^"]+)"', f.read()):
                p = os.path.join(match.group(1).replace("\\\\", "\\"), "steamapps")
                if os.path.isdir(p) and p not in libs:
                    libs.append(p)
    except Exception:
        pass
    return libs


def _scan_steam_games():
    """{имя игры в нижнем регистре: appid} для всех установленных игр."""
    global _steam_games_cache
    if _steam_games_cache is not None:
        return _steam_games_cache

    games = {}
    root = _steam_root()
    if root:
        for lib in _steam_libraries(root):
            for manifest in glob.glob(os.path.join(lib, "appmanifest_*.acf")):
                try:
                    with open(manifest, encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                    appid = re.search(r'"appid"\s+"(\d+)"', text)
                    name = re.search(r'"name"\s+"([^"]+)"', text)
                    if appid and name:
                        games[name.group(1).lower()] = appid.group(1)
                except Exception:
                    continue

    _steam_games_cache = games
    print(f"[steam] установленных игр найдено: {len(games)}")
    return games


def refresh_steam_games() -> str:
    """Пересканировать библиотеку — после установки новой игры."""
    global _steam_games_cache
    _steam_games_cache = None
    return f"Библиотека Steam обновлена: {len(_scan_steam_games())} игр."


def list_steam_games() -> str:
    """Показывает установленные игры Steam."""
    games = _scan_steam_games()
    if not games:
        return "Не нашёл установленных игр Steam — возможно, Steam не установлен."
    names = sorted(g.title() for g in games)
    return f"Установлено {len(names)}: " + ", ".join(names[:40]) + "."


_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _translit(text: str) -> str:
    """Кириллица в латиницу. Нужно потому, что распознавание речи выдаёт
    "дота 2" и "киберпанк", а в библиотеке игры названы латиницей."""
    return "".join(_TRANSLIT.get(ch, ch) for ch in text.lower())


# Народные названия, которые транслитерацией не берутся: "ведьмак" — это
# перевод, а не транслит, "кс" — аббревиатура, "киберпанк" пишется через c.
GAME_ALIASES = {
    "ведьмак": "witcher", "ведьмак 3": "witcher 3",
    "киберпанк": "cyberpunk", "кибербанк": "cyberpunk",
    "кс": "counter-strike", "кс 2": "counter-strike 2",
    "ксго": "counter-strike", "контра": "counter-strike",
    "гта": "grand theft auto", "гта 5": "grand theft auto v",
    "пубг": "pubg", "рдр": "red dead", "рдр 2": "red dead redemption 2",
    "сталкер": "stalker", "атомное сердце": "atomic heart",
    "майнкрафт": "minecraft", "террария": "terraria",
    "раст": "rust", "варфрейм": "warframe", "фортнайт": "fortnite",
    "танки": "world of tanks", "доту": "dota", "дотка": "dota",
    "скайрим": "skyrim", "фоллаут": "fallout", "халва": "half-life",
    "халф лайф": "half-life", "портал": "portal",
    "геншин": "genshin", "варзон": "warzone", "батла": "battlefield",
}


def launch_steam_game(game_name: str) -> str:
    """Запускает игру Steam напрямую по appid — мгновенно, без открытия
    библиотеки и поиска. Если игра не установлена, честно об этом говорит."""
    games = _scan_steam_games()
    if not games:
        return "Не нашёл библиотеку Steam на этом компьютере."

    q = (game_name or "").lower().strip()
    if not q:
        subprocess.Popen("start steam://open/games", shell=True)
        return "Открыл библиотеку Steam."

    appid = games.get(q)
    matched = q
    if not appid:
        # по очереди: как есть, народное название, транслит
        variants = [q]
        if q in GAME_ALIASES:
            variants.append(GAME_ALIASES[q])
        for alias, real in GAME_ALIASES.items():
            if alias in q and real not in variants:
                variants.append(real)
        variants.append(_translit(q))

        for variant in variants:
            candidates = [n for n in games if variant in n or n in variant]
            if not candidates:
                candidates = difflib.get_close_matches(
                    variant, list(games), n=1, cutoff=0.55)
            if candidates:
                matched = min(candidates, key=len)
                appid = games[matched]
                break

    if not appid:
        return (f"«{game_name}» не установлена. Установленные: "
                + ", ".join(sorted(g.title() for g in games)[:15]) + ".")

    subprocess.Popen(f"start steam://rungameid/{appid}", shell=True)
    return f"Запускаю {matched.title()}."


# ---------------------------------------------------------------------------
# Прямые ссылки на сервисы — один шаг вместо браузерной сессии
# ---------------------------------------------------------------------------

def _run(cmd: str):
    subprocess.Popen(cmd, shell=True)


def open_deep_link(service: str, query: str = "") -> str:
    """Открывает сервис сразу на нужном месте. Заметно быстрее, чем искать
    через браузер: одна команда вместо загрузки страниц и кликов."""
    s = (service or "").lower().strip()
    q = urllib.parse.quote(query or "")

    if s in ("steam",):
        if query:
            return launch_steam_game(query)
        _run("start steam://open/main")
        return "Открыл Steam."

    if s in ("epic", "epic games"):
        _run("start com.epicgames.launcher://apps")
        return "Открыл Epic Games."

    if s in ("spotify",):
        _run(f"start spotify:search:{q}" if query else "start spotify:")
        return f"Открыл Spotify{f' на «{query}»' if query else ''}."

    if s in ("youtube", "ютуб"):
        _run(f'start "" "https://www.youtube.com/results?search_query={q}"')
        return f"Открыл YouTube по запросу «{query}»."

    if s in ("youtube music", "ютуб музыка"):
        _run(f'start "" "https://music.youtube.com/search?q={q}"')
        return f"Открыл YouTube Music по запросу «{query}»."

    if s in ("twitch",):
        url = f"https://www.twitch.tv/search?term={q}" if query else "https://www.twitch.tv/"
        _run(f'start "" "{url}"')
        return "Открыл Twitch."

    if s in ("maps", "карты", "google maps"):
        _run(f'start "" "https://www.google.com/maps/search/{q}"')
        return f"Открыл карты: «{query}»."

    if s in ("translate", "переводчик"):
        _run(f'start "" "https://translate.google.com/?text={q}"')
        return "Открыл переводчик."

    if s in ("github",):
        url = f"https://github.com/search?q={q}" if query else "https://github.com/"
        _run(f'start "" "{url}"')
        return "Открыл GitHub."

    if s in ("wikipedia", "википедия"):
        _run(f'start "" "https://ru.wikipedia.org/w/index.php?search={q}"')
        return f"Открыл Википедию: «{query}»."

    if s in ("telegram",):
        _run("start tg://")
        return "Открыл Telegram."

    if s in ("discord",):
        _run("start discord://")
        return "Открыл Discord."

    if s in ("whatsapp",):
        _run("start whatsapp://")
        return "Открыл WhatsApp."

    if s in ("settings", "параметры", "настройки"):
        pages = {
            "звук": "sound", "sound": "sound",
            "дисплей": "display", "экран": "display", "display": "display",
            "wifi": "network-wifi", "вайфай": "network-wifi",
            "bluetooth": "bluetooth",
            "приложения": "appsfeatures", "apps": "appsfeatures",
            "обновления": "windowsupdate", "update": "windowsupdate",
            "питание": "powersleep", "батарея": "batterysaver",
        }
        page = pages.get((query or "").lower(), "")
        _run(f"start ms-settings:{page}")
        return f"Открыл параметры{f' — {query}' if page else ''}."

    return f"Не знаю сервиса «{service}»."