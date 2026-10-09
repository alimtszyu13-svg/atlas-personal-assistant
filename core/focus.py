"""
Режим фокуса: «фокус на час», «не дай мне отвлекаться 40 минут, но телеграм можно».

Пока идёт фокус:
  • отвлекающие программы (Discord, Steam, игры, мессенджеры…) сворачиваются, как только оказываются
    на экране, а Atlas коротко напоминает, сколько осталось;
  • отвлекающие сайты (YouTube, TikTok, Instagram, VK, Reddit…) — вкладка закрывается (Ctrl+W),
    только когда она открыта на экране;
  • Atlas сам не отвлекает: подсказки и предложения ждут конца фокуса (напоминания звенят как обычно);
  • в конце — итог: сколько реально работал, сколько раз тянуло отвлечься и куда.

Ничего не удаляется и не закрывается насовсем: программы только сворачиваются.
FOCUS_BLOCK_APPS / FOCUS_BLOCK_SITES в .env — свои списки через запятую (добавляются к стандартным).
"""
import os
import re
import threading
import time

from core import win_input

APPS = {"discord": "Discord", "steam": "Steam", "steamwebhelper": "Steam", "epicgameslauncher": "Epic Games",
        "riotclientservices": "Riot", "leagueclient": "League of Legends", "battle.net": "Battle.net",
        "telegram": "Telegram", "whatsapp": "WhatsApp", "viber": "Viber",
        "valorant": "Valorant", "cs2": "Counter-Strike", "dota2": "Dota 2", "robloxplayerbeta": "Roblox",
        "minecraft": "Minecraft"}
SITES = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram", "vk.com": "VK", "вконтакте": "VK",
         "reddit": "Reddit", "twitch": "Twitch", "netflix": "Netflix", "rezka": "Rezka", "x.com": "X",
         "twitter": "X", "facebook": "Facebook", "9gag": "9GAG", "pinterest": "Pinterest", "kinopoisk": "Кинопоиск",
         "кинопоиск": "Кинопоиск", "shorts": "YouTube"}
BROWSERS = ("chrome", "msedge", "firefox", "opera", "brave", "yandex", "browser", "vivaldi", "arc")
NAG_EVERY = 120                      # не чаще раза в 2 минуты говорить «сейчас фокус»
IDLE_AFTER = 120                     # без мыши и клавиатуры дольше — не считаем работой

_s = {"on": False, "until": 0.0, "start": 0.0, "minutes": 0, "allow": set(), "blocked": {}, "worked": 0.0,
      "last_nag": 0.0, "announce": None, "thread": None, "summary": ""}
_lock = threading.Lock()


def _extra(env: str) -> dict:
    return {w.strip().lower(): w.strip() for w in (os.getenv(env) or "").split(",") if w.strip()}


def active() -> bool:
    return _s["on"] and time.time() < _s["until"]


def left_min() -> int:
    return max(0, round((_s["until"] - time.time()) / 60))


def _allowed(name: str) -> bool:
    n = name.lower()
    return any(a in n or n in a for a in _s["allow"])


def parse_allow(text: str) -> set:
    """«телеграм и ютуб можно» → {"telegram", "youtube"}."""
    t = (text or "").lower()
    alias = {"телеграм": "telegram", "телега": "telegram", "ютуб": "youtube", "дискорд": "discord",
             "вотсап": "whatsapp", "ватсап": "whatsapp", "спотифай": "spotify", "музык": "spotify",
             "стим": "steam", "вк": "vk.com", "инстаграм": "instagram", "тикток": "tiktok"}
    out = {v for k, v in alias.items() if k in t}
    out |= {k for k in list(APPS) + list(SITES) if k in t}
    return out


def check(exe: str, title: str):
    """Окно на экране → что с ним сделать: (None|"app"|"site", имя)."""
    e = (exe or "").lower().removesuffix(".exe")
    apps = {**APPS, **_extra("FOCUS_BLOCK_APPS")}
    if e in apps and not _allowed(e) and not _allowed(apps[e]):
        return "app", apps[e]
    if any(b in e for b in BROWSERS):
        low = (title or "").lower()
        for key, name in {**SITES, **_extra("FOCUS_BLOCK_SITES")}.items():
            if re.search(r"(?<![\w])" + re.escape(key), low) and not _allowed(key) and not _allowed(name):
                return "site", name
    return None, ""


def _fg():
    """(hwnd, exe, заголовок) активного окна."""
    import ctypes
    import ctypes.wintypes as wt
    import psutil
    h = win_input.foreground()
    if not h:
        return 0, "", ""
    pid = wt.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    try:
        exe = psutil.Process(pid.value).name()
    except Exception:
        exe = ""
    return h, exe, win_input.title_of(h)


def _say(text: str) -> None:
    f = _s["announce"]
    if f:
        threading.Thread(target=f, args=(text,), daemon=True).start()


def _ru() -> bool:
    try:
        from voice import get_response_language
        return get_response_language() == "ru"
    except Exception:
        return True


def tick(fg=None, idle=None, act=None, now: float = None) -> str:
    """Один шаг слежения (раз в 2 с). → что сделали ('' — ничего)."""
    now = now or time.time()
    if not _s["on"]:
        return ""
    if now >= _s["until"]:
        _finish(now)
        return "finished"
    h, exe, title = (fg or _fg)()
    what, name = check(exe, title)
    if what:
        with _lock:
            _s["blocked"][name] = _s["blocked"].get(name, 0) + 1
        if act is not None:
            act(what, h)
        elif what == "app":
            win_input.minimize(h)
        else:
            win_input.keys("ctrl+w")
        if now - _s["last_nag"] > NAG_EVERY:
            _s["last_nag"] = now
            m = max(1, round((_s["until"] - now) / 60))
            _say(f"Сейчас фокус — {name} подождёт ещё {m} мин." if _ru() else f"Focus time — {name} can wait {m} more min.")
        return f"blocked {name}"
    idle_s = (idle if idle is not None else _idle())
    if idle_s < IDLE_AFTER and (fg is not None or (h and not win_input.is_atlas(h))):
        with _lock:
            _s["worked"] += 2
    return ""


def _idle() -> float:
    try:
        from core import worklog
        return worklog._idle()
    except Exception:
        return 0.0


def start(minutes: int = 50, allow: str = "", announce=None, run: bool = True) -> str:
    minutes = max(5, min(int(minutes or 50), 240))
    now = time.time()
    with _lock:
        _s.update(on=True, until=now + minutes * 60, start=now, minutes=minutes, allow=parse_allow(allow),
                  blocked={}, worked=0.0, last_nag=0.0, summary="")
        if announce:
            _s["announce"] = announce
    if run and not (_s["thread"] and _s["thread"].is_alive()):
        def loop():
            while _s["on"]:
                try:
                    tick()
                except Exception as e:
                    print(f"[фокус] {e}")
                time.sleep(2)
        _s["thread"] = threading.Thread(target=loop, daemon=True, name="focus")
        _s["thread"].start()
    allowed = ", ".join(sorted(_s["allow"]))
    print(f"[фокус] {minutes} мин" + (f", можно: {allowed}" if allowed else ""))
    return (f"Focus started for {minutes} minutes. Distracting apps get minimized and distracting sites closed while "
            f"they're on screen" + (f" (allowed: {allowed})" if allowed else "") +
            ". Atlas won't interrupt with suggestions; reminders still ring. Confirm briefly.")


def summary(now: float = None) -> str:
    now = now or time.time()
    spent = max(1, round((min(now, _s["until"]) - _s["start"]) / 60))
    worked = min(spent, round(_s["worked"] / 60))
    tries = sum(_s["blocked"].values())
    top = sorted(_s["blocked"].items(), key=lambda x: -x[1])[:3]
    if _ru():
        s = f"Фокус {spent} мин: работал примерно {worked} мин"
        s += (f", тянуло отвлечься {tries} раз — " + ", ".join(f"{n} {c}" for n, c in top) + "." if tries
              else ", ни разу не отвлёкся. Отлично.")
    else:
        s = f"Focus {spent} min: about {worked} min of real work"
        s += (f", {tries} distraction attempts — " + ", ".join(f"{n} {c}" for n, c in top) + "." if tries
              else ", zero distractions. Great job.")
    return s


def _finish(now: float = None) -> str:
    if not _s["on"]:
        return _s["summary"] or "No focus session is running."
    s = summary(now)
    _s.update(on=False, summary=s)
    print(f"[фокус] {s}")
    _say(s)
    try:
        from core import handoff
        if handoff.worklog._idle() >= 120:          # ты не у компьютера — итог на телефон
            handoff.notify_phone("Atlas · фокус закончен", s)
    except Exception:
        pass
    return s


def stop() -> str:
    if not _s["on"]:
        return "No focus session is running."
    s = summary()
    _s.update(on=False, summary=s)
    return "Focus stopped early. " + s


def status() -> str:
    if not active():
        return _s["summary"] or "Focus mode is off."
    return f"Focus is on: {left_min()} min left. " + summary()
