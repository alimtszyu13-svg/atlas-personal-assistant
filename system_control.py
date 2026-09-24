import subprocess
import os
import difflib
import webbrowser
import urllib.parse

# ---------------------------------------------------------------------------
# APP_MAP — явные записи для системных приложений и лаунчеров, которые лучше
# запускать через протокол, а не через ярлык. Всё остальное ищется
# автоматически в меню "Пуск" (см. _find_shortcut), так что сюда добавлять
# новые программы обычно не требуется.
#
# Формат: "имя": ("команда запуска", "имя процесса для закрытия")
# ---------------------------------------------------------------------------
APP_MAP = {
    # --- системные ---
    "блокнот":          ("notepad.exe", "notepad.exe"),
    "калькулятор":      ("calc.exe", "CalculatorApp.exe"),
    "проводник":        ("explorer.exe", "explorer.exe"),
    "paint":            ("mspaint.exe", "mspaint.exe"),
    "диспетчер задач":  ("taskmgr.exe", "Taskmgr.exe"),
    "командная строка": ("cmd.exe", "cmd.exe"),
    "powershell":       ("powershell.exe", "powershell.exe"),
    "параметры":        ("start ms-settings:", "SystemSettings.exe"),
    "панель управления": ("control.exe", "control.exe"),
    "ножницы":          ("snippingtool.exe", "SnippingTool.exe"),
    "регистратор":      ("start ms-screenclip:", "ScreenClippingHost.exe"),

    # --- браузеры ---
    "хром":     ("start chrome", "chrome.exe"),
    "браузер":  ("start chrome", "chrome.exe"),
    "edge":     ("start msedge", "msedge.exe"),
    "firefox":  ("start firefox", "firefox.exe"),
    "opera":    ("start opera", "opera.exe"),
    "яндекс браузер": ("start browser", "browser.exe"),

    # --- офис ---
    "ворд":       ("start winword", "WINWORD.EXE"),
    "word":       ("start winword", "WINWORD.EXE"),
    "эксель":     ("start excel", "EXCEL.EXE"),
    "excel":      ("start excel", "EXCEL.EXE"),
    "powerpoint": ("start powerpnt", "POWERPNT.EXE"),
    "outlook":    ("start outlook", "OUTLOOK.EXE"),
    "onenote":    ("start onenote", "ONENOTE.EXE"),

    # --- разработка ---
    "вс код":  ("code", "Code.exe"),
    "vscode":  ("code", "Code.exe"),
    "pycharm": ("start pycharm64", "pycharm64.exe"),

    # --- общение ---
    "спотифай": ("start spotify", "Spotify.exe"),
    "телеграм": ("start telegram", "Telegram.exe"),
    "whatsapp": ("start whatsapp", "WhatsApp.exe"),
    "zoom":     ("start zoom", "Zoom.exe"),
    "дискорд": ('start "" "%LocalAppData%\\Discord\\Update.exe" --processStart Discord.exe', "Discord.exe"),
    "discord": ('start "" "%LocalAppData%\\Discord\\Update.exe" --processStart Discord.exe', "Discord.exe"),

    # --- игры и лаунчеры (протоколы надёжнее ярлыков) ---
    "steam":       ("start steam://open/main", "steam.exe"),
    "epic games":  ("start com.epicgames.launcher://apps", "EpicGamesLauncher.exe"),
    "roblox":      ("start roblox://", "RobloxPlayerBeta.exe"),
    "battle net":  ("start battlenet://", "Battle.net.exe"),
    "ea app":      ("start origin://", "EADesktop.exe"),
    "xbox":        ("start xbox:", "XboxPcApp.exe"),

    # --- творчество ---
    "blender":   ("start blender", "blender.exe"),
    "obs":       ("start obs64", "obs64.exe"),
    "photoshop": ("start photoshop", "Photoshop.exe"),
    "figma":     ("start figma", "Figma.exe"),
}

# ---------------------------------------------------------------------------
# Поиск по меню "Пуск" — универсальный запасной путь.
# Там лежат ярлыки всех установленных программ, поэтому Atlas может открыть
# то, чего нет в APP_MAP: T-Launcher, игры из Steam, редкие утилиты и вообще
# всё, что пользователь поставит позже.
# ---------------------------------------------------------------------------
_START_MENU_DIRS = [
    os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                 "Microsoft", "Windows", "Start Menu", "Programs"),
    os.path.join(os.environ.get("AppData", ""),
                 "Microsoft", "Windows", "Start Menu", "Programs"),
    os.path.join(os.environ.get("UserProfile", ""), "Desktop"),
    os.path.join(os.environ.get("Public", r"C:\Users\Public"), "Desktop"),
]

_shortcut_cache = None


def _scan_shortcuts() -> dict:
    """Собирает {имя_без_расширения: путь_к_ярлыку}. Результат кэшируется —
    обход папок занимает доли секунды, но незачем делать его на каждый запрос.
    Чтобы подхватить только что установленную программу, вызови
    refresh_app_list()."""
    global _shortcut_cache
    if _shortcut_cache is not None:
        return _shortcut_cache

    found = {}
    for directory in _START_MENU_DIRS:
        if not directory or not os.path.isdir(directory):
            continue
        try:
            for root, _dirs, files in os.walk(directory):
                for fname in files:
                    if fname.lower().endswith((".lnk", ".url")):
                        name = os.path.splitext(fname)[0].lower()
                        found.setdefault(name, os.path.join(root, fname))
        except Exception as e:
            print(f"[shortcuts] не смог просканировать {directory}: {e}")

    _shortcut_cache = found
    print(f"[shortcuts] найдено ярлыков: {len(found)}")
    return found


def refresh_app_list() -> str:
    """Пересканирует меню Пуск — пригодится после установки новой программы."""
    global _shortcut_cache
    _shortcut_cache = None
    count = len(_scan_shortcuts())
    return f"Список приложений обновлён, найдено {count}."


def _find_shortcut(query: str):
    """Ищет ярлык по названию: точное совпадение, затем вхождение,
    затем нечёткое совпадение (на случай опечаток распознавания речи)."""
    shortcuts = _scan_shortcuts()
    q = query.lower().strip()
    if not q:
        return None, None

    if q in shortcuts:
        return q, shortcuts[q]

    # начинается с запроса — "steam" найдёт "Steam", "blender" → "Blender 4.2"
    starts = [n for n in shortcuts if n.startswith(q)]
    if starts:
        best = min(starts, key=len)
        return best, shortcuts[best]

    contains = [n for n in shortcuts if q in n]
    if contains:
        best = min(contains, key=len)
        return best, shortcuts[best]

    close = difflib.get_close_matches(q, list(shortcuts), n=1, cutoff=0.75)
    if close:
        return close[0], shortcuts[close[0]]

    return None, None


def list_apps(filter_text: str = "") -> str:
    """Показывает, какие приложения Atlas умеет открывать."""
    shortcuts = _scan_shortcuts()
    names = sorted(set(list(APP_MAP.keys()) + list(shortcuts.keys())))
    if filter_text:
        f = filter_text.lower()
        names = [n for n in names if f in n]
    if not names:
        return "Ничего подходящего не нашёл."
    shown = names[:60]
    tail = f" и ещё {len(names) - len(shown)}" if len(names) > len(shown) else ""
    return f"Доступно {len(names)}: " + ", ".join(shown) + tail + "."


def open_app(app_name: str) -> str:
    """Открывает приложение: сначала по известным записям, потом поиском
    по ярлыкам в меню Пуск."""
    app_name = (app_name or "").lower().strip()
    if not app_name:
        return "Не понял, какое приложение открыть."

    if app_name in APP_MAP:
        launch_cmd, _ = APP_MAP[app_name]
        try:
            subprocess.Popen(launch_cmd, shell=True)
            return f"Открываю {app_name}."
        except Exception as e:
            print(f"[Ошибка open_app/APP_MAP]: {e}")
            # не сдаёмся — вдруг найдётся ярлык

    matched, path = _find_shortcut(app_name)
    if path:
        try:
            os.startfile(path)
            return f"Открываю {matched}."
        except Exception as e:
            print(f"[Ошибка open_app/shortcut]: {e}")
            return f"Нашёл {matched}, но не смог запустить."

    return f"Не знаю приложения {app_name}."


def _running_process_match(query: str):
    """Ищет запущенный процесс, похожий по имени на запрос."""
    try:
        import psutil
    except ImportError:
        return None
    q = query.lower().replace(" ", "")
    best = None
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "")
        stem = os.path.splitext(name)[0].lower()
        if not stem:
            continue
        if stem == q or stem.startswith(q) or q in stem:
            if best is None or len(stem) < len(best):
                best = name
    return best


def close_app(app_name: str) -> str:
    """Закрывает приложение: по известному имени процесса, иначе — ищет
    подходящий запущенный процесс."""
    app_name = (app_name or "").lower().strip()
    if not app_name:
        return "Не понял, какое приложение закрыть."

    process_name = None
    if app_name in APP_MAP:
        _, process_name = APP_MAP[app_name]
    else:
        process_name = _running_process_match(app_name)

    if not process_name:
        return f"Не знаю приложения {app_name}."

    try:
        result = subprocess.run(
            ["taskkill", "/IM", process_name, "/F"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return f"Закрываю {app_name}."
        return f"{app_name} и так не был запущен."
    except Exception as e:
        print(f"[Ошибка close_app]: {e}")
        return f"Не получилось закрыть {app_name}."


# ---------------------------------------------------------------------------
# Spotify: надёжный автозапуск
#
# Схема: открыть поиск протоколом spotify: -> дождаться окна процесса
# Spotify.exe -> вывести его на передний план -> найти зелёную кнопку Play
# по цвету на скриншоте окна -> кликнуть -> проверить по заголовку окна,
# что трек реально заиграл (у играющего Spotify заголовок = название песни).
# ---------------------------------------------------------------------------

def _spotify_windows():
    """Окна процесса Spotify.exe: [(hwnd, заголовок, (l, t, r, b))].
    Ищем по процессу, а не по заголовку: когда играет трек, в заголовке
    название песни, а не слово Spotify."""
    try:
        import ctypes
        from ctypes import wintypes
        import psutil
    except ImportError:
        return []

    pids = {p.pid for p in psutil.process_iter(["name"])
            if (p.info.get("name") or "").lower() == "spotify.exe"}
    if not pids:
        return []

    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if rect.right - rect.left > 400 and rect.bottom - rect.top > 300:
            found.append((hwnd, buf.value,
                          (rect.left, rect.top, rect.right, rect.bottom)))
        return True

    user32.EnumWindows(_enum, 0)
    found.sort(key=lambda w: (w[2][2] - w[2][0]) * (w[2][3] - w[2][1]),
               reverse=True)
    return found


def _spotify_title():
    wins = _spotify_windows()
    return wins[0][1] if wins else ""


def _bring_to_front(hwnd) -> bool:
    """Выводит окно на передний план. Windows не даёт фоновому процессу
    отнять фокус (а Atlas открыт на весь экран) — обход: нажать и отпустить
    Alt, после этого SetForegroundWindow срабатывает."""
    import ctypes
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.keybd_event(0x12, 0, 0, 0)  # Alt down
    user32.keybd_event(0x12, 0, 2, 0)  # Alt up
    user32.SetForegroundWindow(hwnd)
    return user32.GetForegroundWindow() == hwnd


def _find_green_play(rect):
    """Ищет круглую зелёную кнопку Play в правой верхней части окна.
    Цвет кнопки Spotify — #1ED760 (при наведении светлее). Левую половину
    не смотрим: там обложки, которые бывают зелёными. Нижнюю четверть тоже:
    там панель плеера. Маленькие зелёные галочки отсекаем по размеру."""
    try:
        import pyautogui
    except ImportError:
        return None

    l, t, r, b = rect
    img = pyautogui.screenshot(region=(l, t, r - l, b - t))
    w, h = img.size
    px = img.load()
    step = 3
    hits = []
    for y in range(0, int(h * 0.75), step):
        for x in range(int(w * 0.45), w, step):
            rr, gg, bb = px[x, y][:3]
            if gg > 185 and rr < 90 and 60 < bb < 150 and gg - rr > 120:
                hits.append((x, y))
    if not hits:
        return None

    clusters = []
    for x, y in hits:
        for c in clusters:
            if abs(x - c["cx"]) < 40 and abs(y - c["cy"]) < 40:
                c["pts"].append((x, y))
                n = len(c["pts"])
                c["cx"] += (x - c["cx"]) / n
                c["cy"] += (y - c["cy"]) / n
                break
        else:
            clusters.append({"pts": [(x, y)], "cx": x, "cy": y})

    good = []
    for c in clusters:
        xs = [p[0] for p in c["pts"]]
        ys = [p[1] for p in c["pts"]]
        bw = max(xs) - min(xs) + step
        bh = max(ys) - min(ys) + step
        # кнопка круглая, 30-80 px, и достаточно "залита"
        if 28 <= bw <= 90 and 28 <= bh <= 90 and 0.7 <= bw / bh <= 1.4 \
                and len(c["pts"]) >= 60:
            good.append(c)
    if not good:
        return None

    best = min(good, key=lambda c: c["cy"])  # верхняя — у лучшего результата
    return l + int(best["cx"]), t + int(best["cy"])


def _spotify_autoplay(timeout: float = 12.0) -> str:
    """Возвращает 'playing', 'clicked' (кликнули, но не подтвердилось)
    или причину неудачи."""
    import time as _time
    try:
        import pyautogui
    except ImportError:
        return "нет pyautogui"

    deadline = _time.time() + timeout

    # 1. ждём окно (при холодном старте Spotify поднимается секунды)
    wins = []
    while _time.time() < deadline:
        wins = _spotify_windows()
        if wins:
            break
        _time.sleep(0.3)
    if not wins:
        return "окно Spotify не появилось"

    hwnd, title_before, rect = wins[0]
    _bring_to_front(hwnd)

    # 2. ждём, пока отрисуются результаты и появится кнопка
    point = None
    while _time.time() < deadline:
        wins = _spotify_windows()
        if wins:
            hwnd, _, rect = wins[0]
        point = _find_green_play(rect)
        if point:
            break
        _time.sleep(0.4)
    if not point:
        return "не нашёл кнопку Play на экране"

    # 3. клик — и возвращаем мышь туда, где она была
    old = pyautogui.position()
    pyautogui.click(*point)
    pyautogui.moveTo(*old)

    # 4. проверка: у играющего Spotify заголовок окна = название трека
    check_until = _time.time() + 3.0
    while _time.time() < check_until:
        title = _spotify_title()
        if title and title != title_before and not title.lower().startswith("spotify"):
            return "playing"
        _time.sleep(0.25)
    return "clicked"


def play_on_spotify(query: str = "", autoplay: bool = True) -> str:
    """Открывает десктопный Spotify на поиске через протокол spotify: и
    запускает лучший результат. Мгновенно, без браузера."""
    try:
        q = (query or "").strip()
        for article in ("the ", "a ", "some "):
            if q.lower().startswith(article):
                q = q[len(article):]
        uri = f"spotify:search:{urllib.parse.quote(q)}" if q else "spotify:"
        subprocess.Popen(f"start {uri}", shell=True)

        if not (autoplay and q):
            return f"Открыл Spotify{f' на поиске «{q}»' if q else ''}."

        status = _spotify_autoplay()
        print(f"[spotify] автозапуск: {status}")
        if status == "playing":
            return f"Включил «{_spotify_title()}» в Spotify."
        if status == "clicked":
            return f"Нажал Play на «{q}» в Spotify, но не уверен, что заиграло."
        return f"Открыл Spotify на поиске «{q}», но не запустил: {status}."
    except Exception as e:
        print(f"[Ошибка play_on_spotify]: {e}")
        return f"Не получилось открыть Spotify: {e}"


def play_on_youtube_music(query: str) -> str:
    """YouTube Music в браузере по прямой ссылке на поиск — без хождения
    по страницам и без лишних вызовов модели."""
    encoded = urllib.parse.quote(query)
    webbrowser.open(f"https://music.youtube.com/search?q={encoded}")
    return f"Открыл YouTube Music на поиске «{query}»."


def open_youtube(query: str) -> str:
    """Открывает YouTube в браузере с результатами поиска по запросу."""
    encoded_query = urllib.parse.quote(query)
    url = f"https://www.youtube.com/results?search_query={encoded_query}"
    try:
        webbrowser.open(url)
        return f"Opening YouTube search for '{query}'."
    except Exception as e:
        print(f"[Ошибка open_youtube]: {e}")
        return f"Couldn't open YouTube for '{query}'."


def open_url(url: str) -> str:
    """Opens any URL in the default browser."""
    if not url.startswith("http"):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opening {url}."


def search_google(query: str) -> str:
    """Opens Google search results for a query."""
    encoded = urllib.parse.quote(query)
    webbrowser.open(f"https://www.google.com/search?q={encoded}")
    return f"Searching Google for '{query}'."