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
    """Выводит окно на передний план. Windows не даёт фоновому процессу отнять фокус
    (а Atlas открыт на весь экран). Сначала — без нажатий клавиш: на миг присоединяемся
    к потоку активного окна. Не вышло — старый обход с Alt; но в Spotify Alt открывает меню
    «Файл / Правка / Вид…», которое перехватывает клики, поэтому сразу закрываем его (Esc)."""
    import ctypes
    import time as _t
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True
    cur = kernel32.GetCurrentThreadId()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_thread and fg_thread != cur and user32.AttachThreadInput(cur, fg_thread, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur, fg_thread, False)
    if user32.GetForegroundWindow() == hwnd:
        return True
    user32.keybd_event(0x12, 0, 0, 0)  # Alt down — запасной путь
    user32.keybd_event(0x12, 0, 2, 0)  # Alt up
    user32.SetForegroundWindow(hwnd)
    ok = user32.GetForegroundWindow() == hwnd
    if ok:
        _t.sleep(0.15)
        user32.keybd_event(0x1B, 0, 0, 0)  # Esc — закрыть меню, открытое Alt
        user32.keybd_event(0x1B, 0, 2, 0)
    return ok


def _find_green_play(rect, x_from: float = 0.45, min_size: int = 40, near=None):
    """Ищет круглую зелёную кнопку Play в правой верхней части окна.
    Цвет кнопки Spotify — #1ED760 (при наведении светлее). Левую часть окна
    (до x_from) не смотрим: там обложки, которые бывают зелёными. Нижнюю
    четверть тоже: там панель плеера. Маленькие зелёные галочки отсекаем по размеру."""
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
    for y in range(int(h * 0.12), int(h * 0.75), step):     # шапку (поиск, аватар профиля) пропускаем
        for x in range(int(w * x_from), w, step):
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
        if min_size <= bw <= 90 and min_size <= bh <= 90 and 0.7 <= bw / bh <= 1.4 \
                and len(c["pts"]) >= 60 \
                and (near is None or (abs(l + c["cx"] - near[0]) <= near[2] and abs(t + c["cy"] - near[1]) <= near[3])):
            good.append(c)
    if not good:
        return None

    # самая крупная и залитая — это Play у лучшего результата (аватар и значки меньше); при равенстве — верхняя
    best = max(good, key=lambda c: (len(c["pts"]), -c["cy"]))
    return l + int(best["cx"]), t + int(best["cy"])


def _results_signature(rect):
    """Уменьшенный снимок области результатов — чтобы понять, сменилась ли страница."""
    try:
        import pyautogui
        l, t, r, b = rect
        w, h = r - l, b - t
        # центральная колонка результатов: без боковой библиотеки и правой панели «Сейчас играет»
        img = pyautogui.screenshot(region=(l + int(w * 0.07), t + int(h * 0.12), int(w * 0.65), int(h * 0.43)))
        return list(img.convert("L").resize((48, 16)).getdata())
    except Exception:
        return None


def _sig_diff(a, b) -> float:
    """Сколько процентов участков картинки заметно изменилось. Страница Spotify почти вся тёмная,
    меняются только обложки и надписи — средняя яркость этого почти не замечала."""
    if not a or not b or len(a) != len(b):
        return 100.0
    return 100.0 * sum(1 for x, y in zip(a, b) if abs(x - y) > 12) / len(a)


def _wait_results_changed(before, rect, timeout: float = 6.0) -> bool:
    """Ждём, пока на месте старых результатов появятся новые и перестанут меняться.
    Иначе можно нажать Play на прошлом поиске (и, если он играет, — поставить на паузу)."""
    import time as _time
    until, prev = _time.time() + timeout, None
    while _time.time() < until:
        sig = _results_signature(rect)
        if sig and (before is None or _sig_diff(sig, before) > 0.6):        # неподвижная страница даёт ровно 0
            if prev is not None and _sig_diff(sig, prev) < 0.3:
                return True
            prev = sig
        else:
            prev = None
        _time.sleep(0.3)
    return False


_GENERIC = {"music", "songs", "song", "playlist", "mix", "calm", "happy", "upbeat", "chill", "lofi", "lo-fi", "relaxing",
            "relax", "sad", "party", "workout", "focus", "study", "jazz", "rock", "pop", "rap", "classical", "edm",
            "phonk", "instrumental", "piano", "ambient", "the", "and", "for", "with", "some", "best", "top", "hits",
            "something", "energetic", "slow", "fast", "dance", "night", "morning", "evening", "background", "quiet",
            "classic", "essentials", "oldies", "retro", "metal", "punk", "blues", "country", "folk", "indie", "soul",
            "funk", "disco", "house", "techno", "trance", "reggae", "latin", "kpop", "k-pop", "anime", "soundtrack",
            "ost", "covers", "remix", "acoustic", "hip", "hop", "rnb", "r&b", "lounge", "sleep", "summer", "winter",
            "greatest", "all", "time", "new", "old", "80s", "90s", "70s", "60s", "2000s", "radio", "vibes", "mood"}


def _matches_request(query: str, title: str) -> bool:
    """Конкретная песня/исполнитель латиницей — хоть одно слово из запроса должно быть в заголовке.
    Настроения («calm music») и запросы кириллицей не проверяем — сверять не с чем."""
    import re as _re
    if _re.search(r"[а-яё]", query or "", _re.I):
        return True
    words = [w for w in _re.findall(r"[a-z0-9'&-]{3,}", (query or "").lower()) if w not in _GENERIC]
    if len(words) < 2:                  # «classic rock», «animals» — сверять не с чем, жанр/одно слово
        return True
    t = (title or "").lower()
    return any(w in t for w in words)


def _spotify_autoplay(timeout: float = 12.0, before=None, query: str = "") -> str:
    """Возвращает 'playing', 'other:<что заиграло>', 'clicked' (кликнули, но не подтвердилось)
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
    if before is None:                  # окно было свёрнуто или закрыто — старая страница может ещё висеть
        _time.sleep(2.0)
    if not _wait_results_changed(before, rect, timeout=min(4.0, max(1.0, deadline - _time.time() - 2))):
        print("[spotify] страница результатов не сменилась — ищу кнопку на том, что есть")

    # 2. ждём, пока отрисуются результаты и появится кнопка
    point = None
    hover_at = _time.time() + min(3.0, timeout / 3)        # потом — водим мышью: кнопка бывает видна только при наведении
    while _time.time() < deadline:
        wins = _spotify_windows()
        if wins:
            hwnd, _, rect = wins[0]
        point = _find_green_play(rect)
        if point:
            break
        if _time.time() >= hover_at:
            point = _hover_for_play(rect)
            if point:
                break
            hover_at = _time.time() + 2.0
        _time.sleep(0.4)
    if not point:
        _save_spotify_snapshot(rect, "no_play_button")
        return "не нашёл кнопку Play на экране"

    # 3. клик — и возвращаем мышь туда, где она была
    old = pyautogui.position()
    pyautogui.click(*point)
    pyautogui.moveTo(*old)

    # 4. проверка: у играющего Spotify заголовок окна = название трека
    check_until = _time.time() + 5.0
    while _time.time() < check_until:
        title = _spotify_title()
        if title and title != title_before and not title.lower().startswith("spotify"):
            return "playing"        # что заиграло — Atlas назовёт; перебор «не то» по словам приносил вред
        _time.sleep(0.25)
    return "clicked"


def _is_playing(title: str) -> bool:
    """У играющего Spotify заголовок окна — «Исполнитель - Трек», на паузе — «Spotify …»."""
    return bool(title) and not title.lower().startswith("spotify")


def _media_play_pause_key() -> None:
    import ctypes
    user32 = ctypes.windll.user32
    user32.keybd_event(0xB3, 0, 0, 0)          # VK_MEDIA_PLAY_PAUSE
    user32.keybd_event(0xB3, 0, 2, 0)


def _hover_for_play(rect):
    """Водим мышью по области верхнего результата — у новых версий Spotify зелёная кнопка
    появляется только при наведении. Мышь возвращается на место."""
    try:
        import pyautogui
    except ImportError:
        return None
    import time as _time
    l, t, r, b = rect
    w, h = r - l, b - t
    old = pyautogui.position()
    try:
        # верхний результат (крупная карточка) и ряд карточек плейлистов под ним: на странице жанра
        # у «Жанра» кнопки нет, а у карточек она появляется только при наведении и меньше обычной.
        # Берём только кнопку рядом с курсором — на той карточке, над которой мышь, а не значок где-то ещё.
        for fy in (0.38, 0.44, 0.30, 0.50):
            for fx in (0.11, 0.19, 0.28, 0.36, 0.45, 0.55, 0.65):
                mx, my = l + int(w * fx), t + int(h * fy)
                pyautogui.moveTo(mx, my)
                _time.sleep(0.15)
                point = _find_green_play(rect, x_from=0.06, min_size=30,           # боковую библиотеку не смотрим
                                         near=(mx, my, int(w * 0.09), int(h * 0.12)))
                if point:
                    return point
    finally:
        pyautogui.moveTo(*old)
    return None


def _save_spotify_snapshot(rect, reason: str) -> None:
    """Снимок окна Spotify при неудаче — по нему настраивается поиск кнопки на реальном экране."""
    try:
        import pyautogui
        import time as _time
        os.makedirs("logs", exist_ok=True)
        l, t, r, b = rect
        path = os.path.join("logs", f"spotify_{reason}_{_time.strftime('%Y%m%d_%H%M%S')}.png")
        pyautogui.screenshot(region=(l, t, r - l, b - t)).save(path)
        print(f"[spotify] снимок окна для настройки: {path}")
    except Exception as e:
        print(f"[spotify] снимок не сохранился: {e}")


def _spotify_resume(timeout: float = 12.0) -> str:
    """«Включи музыку» без названия: дождаться окна и продолжить воспроизведение.
    Уже играет — не трогаем (иначе клавиша Play поставит на паузу).
    → 'playing' | 'already:<трек>' | причина неудачи."""
    import time as _time
    deadline = _time.time() + timeout
    wins = []
    while _time.time() < deadline:
        wins = _spotify_windows()
        if wins:
            break
        _time.sleep(0.3)
    if not wins:
        return "окно Spotify не появилось"
    hwnd, title, rect = wins[0]
    if _is_playing(title):
        return "already:" + title
    _time.sleep(1.0)                           # приложение должно успеть загрузить последнюю очередь
    for attempt in ("media", "space"):
        if attempt == "media":
            _media_play_pause_key()
        else:                                  # запасной путь: пробел в окне Spotify
            _bring_to_front(hwnd)
            try:
                import pyautogui
                pyautogui.press("space")
            except ImportError:
                break
        until = _time.time() + 3.0
        while _time.time() < until:
            if _is_playing(_spotify_title()):
                return "playing"
            _time.sleep(0.25)
    _save_spotify_snapshot(rect, "resume")
    return "Spotify открыт, но воспроизведение не началось"


_LIKED_WORDS = ("liked", "liked songs", "favorites", "favourites", "любимые", "любимые треки", "любимые песни",
                "понравившиеся", "сохранённые", "сохраненные", "моя музыка", "")


def spotify_play_library(which: str = "liked") -> str:
    """Твоя библиотека Spotify: which='liked' — «Любимые треки» (открываются напрямую, без поиска);
    иначе — плейлист по названию (Spotify показывает твои плейлисты в поиске)."""
    w = (which or "").strip()
    if w.lower() in _LIKED_WORDS:
        return _spotify_play_page("spotify:collection:tracks", "любимые треки")
    return play_on_spotify(w)


def _spotify_play_page(uri: str, label: str, timeout: float = 12.0) -> str:
    """Открыть страницу (плейлист, «Любимые треки») и нажать её большую кнопку Play — она слева под
    заголовком. Если эта музыка уже играла, нажатие ставит на паузу — тогда жмём ещё раз."""
    import time as _time
    try:
        import pyautogui
    except ImportError:
        return "Не удалось: нет pyautogui."
    wins, before = _spotify_windows(), None
    if wins:
        _bring_to_front(wins[0][0])
        _time.sleep(0.35)
        before = _results_signature(wins[0][2])
    subprocess.Popen(f"start {uri}", shell=True)
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        wins = _spotify_windows()
        if wins:
            break
        _time.sleep(0.3)
    if not wins:
        return "Окно Spotify не появилось."
    hwnd, title_before, rect = wins[0]
    _bring_to_front(hwnd)
    if before is None:
        _time.sleep(2.0)
    _wait_results_changed(before, rect, timeout=min(4.0, max(1.0, deadline - _time.time() - 2)))
    point = None
    while _time.time() < deadline:
        point = _find_green_play(rect, x_from=0.07)
        if point:
            break
        _time.sleep(0.4)
    if not point:
        _save_spotify_snapshot(rect, "library")
        return f"Открыл {label} в Spotify, но не нашёл кнопку Play."
    old = pyautogui.position()

    def click_and_watch(seconds: float):
        pyautogui.click(*point)
        pyautogui.moveTo(*old)
        until = _time.time() + seconds
        title = ""
        while _time.time() < until:
            title = _spotify_title()
            if _is_playing(title) and title != title_before:
                return "new", title
            if not _is_playing(title) and _is_playing(title_before):
                return "paused", title
            _time.sleep(0.25)
        return ("playing" if _is_playing(title) else "unknown"), title

    status, title = click_and_watch(4.0)
    if status == "new":
        return f"Включил {label}: «{title}»."
    if status == "paused":                        # это уже играло — кнопка сработала как пауза
        status, title = click_and_watch(3.0)
        if _is_playing(_spotify_title()):
            return f"{label.capitalize()} уже играли — продолжил: «{_spotify_title()}»."
    return f"Нажал Play в «{label}», но не уверен, что заиграло."


SPOTIFY_VOLUME_STEP = float(os.getenv("SPOTIFY_VOLUME_STEP") or 10)   # на сколько % Ctrl+↑/↓ двигает ползунок Spotify


def spotify_is_playing() -> bool:
    """Играет ли сейчас Spotify (по заголовку его окна)."""
    return _is_playing(_spotify_title())


def spotify_volume(action: str = "up", percent: int = 0, steps: int = 1) -> str:
    """Громкость самого Spotify — его ползунок, а не громкость компьютера.
    action='up' / 'down' — на steps шагов (Ctrl+↑ / Ctrl+↓); action='set' — выставить percent %
    (текущий уровень Spotify узнать неоткуда: сначала до нуля, потом вверх — звук на миг проседает)."""
    import time as _time
    wins = _spotify_windows()
    if not wins:
        return "Spotify не открыт."
    try:
        import pyautogui
    except ImportError:
        return "Не удалось: нет pyautogui."
    _bring_to_front(wins[0][0])
    _time.sleep(0.2)

    def press(key, n):
        for _ in range(n):
            pyautogui.hotkey("ctrl", key)
            _time.sleep(0.04)
    a = (action or "up").strip().lower()
    if a == "set":
        p = max(0, min(100, int(percent or 0)))
        press("down", int(100 / SPOTIFY_VOLUME_STEP) + 2)
        n = round(p / SPOTIFY_VOLUME_STEP)
        press("up", n)
        return f"Громкость Spotify — {int(n * SPOTIFY_VOLUME_STEP)}%."
    down = a in ("down", "тише", "quieter")
    press("down" if down else "up", max(1, int(steps or 1)))
    return f"Сделал Spotify {'тише' if down else 'громче'}."


SPOTIFY_SEEK_STEP = float(os.getenv("SPOTIFY_SEEK_STEP") or 5)     # на сколько секунд Spotify перематывает за одно Shift+стрелка


def spotify_seek(seconds: int = 15) -> str:
    """Перематывает трек в Spotify вперёд (seconds > 0) или назад (seconds < 0) клавишами самого
    Spotify — Shift+→ / Shift+←, по SPOTIFY_SEEK_STEP секунд за нажатие."""
    import time as _time
    wins = _spotify_windows()
    if not wins:
        return "Spotify не открыт — перематывать нечего."
    try:
        import pyautogui
    except ImportError:
        return "Не удалось перемотать: нет pyautogui."
    s = int(seconds or 15)
    n = max(1, round(abs(s) / SPOTIFY_SEEK_STEP))
    _bring_to_front(wins[0][0])
    _time.sleep(0.2)
    for _ in range(n):
        pyautogui.hotkey("shift", "right" if s > 0 else "left")
        _time.sleep(0.05)
    return f"Перемотал Spotify {'вперёд' if s > 0 else 'назад'} на {int(n * SPOTIFY_SEEK_STEP)} секунд."


def play_on_spotify(query: str = "", autoplay: bool = True) -> str:
    """Открывает десктопный Spotify на поиске через протокол spotify: и
    запускает лучший результат. Мгновенно, без браузера."""
    try:
        q = (query or "").strip()
        for article in ("the ", "a ", "some "):
            if q.lower().startswith(article):
                q = q[len(article):]
        uri = f"spotify:search:{urllib.parse.quote(q)}" if q else "spotify:"
        before = None
        if autoplay and q:                            # как выглядели результаты ДО нового поиска
            wins = _spotify_windows()
            if wins:                                  # Spotify мог быть позади окна Atlas: на экране
                _bring_to_front(wins[0][0])           # тогда видно не его — сначала вперёд
                import time as _t
                _t.sleep(0.35)
            before = _results_signature(wins[0][2]) if wins else None
        subprocess.Popen(f"start {uri}", shell=True)

        if autoplay and not q:                        # «включи музыку» — продолжить то, что играло
            status = _spotify_resume()
            print(f"[spotify] продолжить: {status}")
            if status.startswith("already:"):
                return f"Spotify уже играет «{status[8:]}»."
            if status == "playing":
                return f"Включил музыку в Spotify: «{_spotify_title()}»."
            return f"Открыл Spotify, но не смог запустить музыку: {status}."
        if not autoplay:
            return f"Открыл Spotify{f' на поиске «{q}»' if q else ''}."

        status = _spotify_autoplay(before=before, query=q)
        print(f"[spotify] автозапуск: {status}")
        if status == "playing":
            return f"Включил «{_spotify_title()}» в Spotify."
        if status.startswith("other:"):
            return f"Заиграло «{status[6:]}» — похоже, не то, что просили («{q}»)."
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