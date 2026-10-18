"""
Самопроверка действий: Atlas не верит слову инструмента «готово» — смотрит, что изменилось на самом деле.

    «открой фотошоп»   → инструмент: «Открываю photoshop.»  → проверка: появился процесс/окно? нет за 6 с →
                         мозгу: «[ПРОВЕРКА НЕ ПРОШЛА] окна Photoshop нет — попробуй иначе» → он пробует
                         другой способ (другое имя, ярлык, поиск) или честно говорит, что мешает.
    «громкость 30»     → читаем громкость обратно: 30? — ок; осталось 70 (не то устройство) — сбой.

Проверяются: открыть/закрыть программу, завершить процесс, громкость (уровень, ±, звук вкл/выкл), яркость,
папка создана, заметка и дело записаны, тема интерфейса. Проверка без ответа (нет датчика) — не мешает.
Каждый сбой → logs/verify.jsonl; «как часто ты ошибаешься» видно в отчёте. ATLAS_VERIFY=off — выключить.

    snap = before(name, args)         — до действия (например, громкость сейчас)
    result, ok = after(name, args, result, ok, snap)
"""
import json
import os
import re
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "verify.jsonl")
MARK = "[CHECK FAILED]"
_STATS = {"checked": 0, "failed": 0, "unknown": 0}


def enabled() -> bool:
    if os.getenv("ATLAS_TESTING") == "1" and not _PROBES_SET["on"]:
        return False                                          # в тестах — только с подставными датчиками
    return (os.getenv("ATLAS_VERIFY") or "on").strip().lower() not in ("off", "0", "false", "no")


# --- датчики (в тестах подменяются) ----------------------------------------------------------------------------
def _procs() -> set:
    import psutil
    out = set()
    for p in psutil.process_iter(["name"]):
        n = (p.info.get("name") or "").lower()
        if n:
            out.add(n[:-4] if n.endswith(".exe") else n)
    return out


def _windows() -> list:
    from core import window_layout
    return [(w["app"].lower(), w["title"].lower()) for w in window_layout.Win().windows()]


def _volume():
    from system_advanced import _get_volume_interface
    v = _get_volume_interface()
    return round(v.GetMasterVolumeLevelScalar() * 100), bool(v.GetMute())


def _brightness():
    import screen_brightness_control as sbc
    return sbc.get_brightness()[0]


def _notes() -> dict:
    import notes
    return notes._load()


def _theme():
    from ui_state import shared_state
    return shared_state.get("theme")


def _folder(name: str, location: str) -> str:
    from file_control import _resolve_location
    return os.path.join(_resolve_location(location or "Desktop"), name)


PROBES = {"procs": _procs, "windows": _windows, "volume": _volume, "brightness": _brightness, "notes": _notes,
          "theme": _theme, "folder": _folder, "sleep": time.sleep, "clock": time.time}
_PROBES_SET = {"on": False}


def set_probes(**kw) -> None:
    """Для тестов: подменить датчики. set_probes() без аргументов — вернуть настоящие."""
    global PROBES
    if not kw:
        PROBES = {"procs": _procs, "windows": _windows, "volume": _volume, "brightness": _brightness,
                  "notes": _notes, "theme": _theme, "folder": _folder, "sleep": time.sleep, "clock": time.time}
        _PROBES_SET["on"] = False
        return
    PROBES.update(kw)
    _PROBES_SET["on"] = True


def _poll(fn, timeout: float, step: float = 0.4):
    """fn() → (True|False|None, деталь). Ждём True до timeout; None — проверить нельзя."""
    end = PROBES["clock"]() + timeout
    res = (None, "")
    while True:
        try:
            res = fn()
        except Exception as e:
            return None, f"датчик: {e}"
        if res[0] is True or res[0] is None or PROBES["clock"]() >= end:
            return res
        PROBES["sleep"](step)


# --- программы -------------------------------------------------------------------------------------------------
def _app_keys(app: str) -> list:
    a = (app or "").lower().strip()
    keys = {a, a.replace(" ", "")}
    try:
        from system_control import APP_MAP
        if a in APP_MAP:
            proc = APP_MAP[a][1].lower()
            keys.add(proc[:-4] if proc.endswith(".exe") else proc)
    except Exception:
        pass
    return [k for k in keys if len(k) >= 3]


def _is_running(app: str) -> bool:
    keys = _app_keys(app)
    procs = PROBES["procs"]()
    if any(k == p or p.startswith(k) or (len(k) >= 4 and k in p) for k in keys for p in procs):
        return True
    try:
        wins = PROBES["windows"]()
    except Exception:
        wins = []
    return any(k in title or k == wapp or wapp.startswith(k) for k in keys for wapp, title in wins)


def _snap_app(args):
    try:
        return {"wins": len(PROBES["windows"]())}
    except Exception:
        return {}


def _check_open_app(args, result, snap):
    app = args.get("app_name", "")

    def probe():
        if _is_running(app):
            return True, ""
        try:
            if snap.get("wins") is not None and len(PROBES["windows"]()) > snap["wins"]:
                return True, ""                              # новое окно появилось (имя процесса другое — ок)
        except Exception:
            pass
        return False, f"{app} не запустился: за 6 секунд не появилось ни процесса, ни окна"
    return _poll(probe, 6.0)


def _check_close_app(args, result, snap):
    app = args.get("app_name", "")
    return _poll(lambda: (not _is_running(app), f"{app} всё ещё запущен"), 3.0)


def _check_kill(args, result, snap):
    n = (args.get("name") or "").lower().strip()
    n = n[:-4] if n.endswith(".exe") else n
    return _poll(lambda: (n not in PROBES["procs"](), f"процесс {n} всё ещё работает"), 3.0)


# --- звук, яркость ---------------------------------------------------------------------------------------------
def _snap_volume(args):
    try:
        return {"vol": PROBES["volume"]()}
    except Exception:
        return {}


def _check_set_volume(args, result, snap):
    want = max(0, min(100, int(args.get("level", -1))))
    def probe():
        vol, _ = PROBES["volume"]()
        return abs(vol - want) <= 2, f"громкость {vol}%, а нужно {want}% (возможно, звук идёт на другое устройство)"
    return _poll(probe, 1.0, 0.2)


def _check_volume_step(sign):
    def check(args, result, snap):
        if "vol" not in snap:
            return None, ""
        before = snap["vol"][0]
        if (sign > 0 and before >= 100) or (sign < 0 and before <= 0):
            return True, ""
        def probe():
            vol, _ = PROBES["volume"]()
            return (vol > before) if sign > 0 else (vol < before), f"громкость не изменилась ({vol}%)"
        return _poll(probe, 1.0, 0.2)
    return check


def _check_mute(want):
    def check(args, result, snap):
        def probe():
            _, muted = PROBES["volume"]()
            return muted == want, "звук не выключился" if want else "звук всё ещё выключен"
        return _poll(probe, 1.0, 0.2)
    return check


def _check_brightness(args, result, snap):
    if re.search(r"couldn'?t|не удалось|may not support", str(result), re.I):
        return None, ""                                       # инструмент сам сказал, что не может
    want = max(0, min(100, int(args.get("level", -1))))
    def probe():
        b = PROBES["brightness"]()
        return abs(int(b) - want) <= 5, f"яркость {b}%, а нужно {want}%"
    return _poll(probe, 1.5, 0.3)


# --- файлы, заметки, тема --------------------------------------------------------------------------------------
def _check_folder(args, result, snap):
    if re.search(r"already exists|уже есть", str(result), re.I):
        return True, ""
    p = PROBES["folder"](args.get("name", ""), args.get("location", "Desktop"))
    return os.path.isdir(p), f"папки {p} нет"


def _check_note(key, arg):
    def check(args, result, snap):
        text = (args.get(arg) or "").strip()
        items = PROBES["notes"]().get(key) or []
        flat = [x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in items]
        return any(text and text in x for x in flat), f"«{text[:40]}» не записалось в {key}"
    return check


def _check_theme(args, result, snap):
    want = (args.get("theme") or "").lower().strip()
    if want not in ("dark", "light"):
        return None, ""
    cur = PROBES["theme"]()
    return cur == want, f"тема осталась {cur}"


CHECKS = {
    "open_app": (_snap_app, _check_open_app),
    "close_app": (None, _check_close_app),
    "kill_process": (None, _check_kill),
    "set_volume": (None, _check_set_volume),
    "volume_up": (_snap_volume, _check_volume_step(+1)),
    "volume_down": (_snap_volume, _check_volume_step(-1)),
    "mute_volume": (None, _check_mute(True)),
    "unmute_volume": (None, _check_mute(False)),
    "set_brightness": (None, _check_brightness),
    "create_folder": (None, _check_folder),
    "add_note": (None, _check_note("notes", "text")),
    "add_todo": (None, _check_note("todos", "task")),
    "set_theme": (None, _check_theme),
}
# инструмент сам сообщил о неудаче или отказе — проверять нечего
_SELF_FAIL = re.compile(r"не знаю приложения|не понял|couldn'?t|не смог|не получилось|не найден|not found|"
                        r"cancel|отмен|must be either|и так не был запущен|wasn't running", re.I)


# --- другой способ сам, без модели (дешевле круга через мозг) --------------------------------------------------
def _alt_open_app(args) -> str:
    """APP_MAP-команда не дала окна → ярлык из меню Пуск; нет ярлыка → «start имя» через оболочку."""
    import subprocess
    from system_control import _find_shortcut
    app = (args.get("app_name") or "").strip()
    matched, path = _find_shortcut(app.lower())
    if path:
        os.startfile(path)
        return f"ярлык «{matched}»"
    subprocess.Popen(f'start "" "{app}"', shell=True)
    return f"start {app}"


ALTERNATIVES = {"open_app": _alt_open_app}


def before(name: str, args: dict):
    if not enabled() or name not in CHECKS or CHECKS[name][0] is None:
        return {}
    try:
        return CHECKS[name][0](args or {}) or {}
    except Exception:
        return {}


def after(name: str, args: dict, result, ok: bool, snap=None):
    """→ (результат, успех). Если действие на деле не сработало — к результату добавляется MARK и причина."""
    if not ok or not enabled() or name not in CHECKS or _SELF_FAIL.search(str(result)[:200]):
        return result, ok
    t0 = time.time()
    try:
        verdict, detail = CHECKS[name][1](args or {}, result, snap or {})
    except Exception as e:
        verdict, detail = None, str(e)
    _STATS["checked"] += 1
    if verdict is None:
        _STATS["unknown"] += 1
        return result, ok
    if verdict:
        return result, ok
    alt = ALTERNATIVES.get(name)
    if alt is not None:
        try:
            how = alt(args or {})
            v2, d2 = CHECKS[name][1](args or {}, result, snap or {})
            if v2:
                print(f"[проверка] {name}: первый способ не сработал, получилось через {how}")
                _STATS["retried"] = _STATS.get("retried", 0) + 1
                return f"{result} (opened on the second try via {how}; Atlas checked — it is running now)", True
            detail = f"{detail}; второй способ ({how}) тоже не помог"
        except Exception as e:
            print(f"[проверка] другой способ: {e}")
    _STATS["failed"] += 1
    _log(name, args, result, detail, time.time() - t0)
    print(f"[проверка] {name}: {detail}")
    return (f"{result}\n{MARK} Atlas checked the real state: {detail}. The action did NOT take effect. "
            f"Try a different way (another tool, app name or method), or tell the user plainly what blocks it."), False


def _log(name, args, result, detail, dt):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "tool": name, "args": args, "result": str(result)[:200],
                                "detail": detail, "s": round(dt, 2)}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def stats() -> dict:
    return dict(_STATS)
