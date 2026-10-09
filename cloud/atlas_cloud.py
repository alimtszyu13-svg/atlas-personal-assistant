"""
Atlas в облаке — запуск.

    1. модули «только для компьютера» → заглушки «недоступно из облака»
    2. память: всё из Supabase (тот же граф, разговоры, карточки, заметки), дальше синхронизация
    3. мозг — тот же код, что на компьютере
    4. сервер для телефона на порту 7860 (Hugging Face) с тем же ключом сопряжения

Секреты Space (Settings → Variables and secrets): GROQ_API_KEY, CEREBRAS_API_KEY, GEMINI_API_KEY (по желанию),
SUPABASE_URL, SUPABASE_SERVICE_KEY, PHONE_KEY, FISH_API_KEY + FISH_VOICE_RU / FISH_VOICE_EN (по желанию).

    python -m cloud.atlas_cloud
"""
import os
import re
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cloud import stubs  # noqa: E402

# без скобок внутри: подсказка — одна пара скобок, её отрезают перед выбором инструментов
CLOUD_HINT = ("(Said on the phone; Atlas is answering from the cloud: calendar, mail, reminders and timers — they "
              "arrive as phone notifications —, web search, weather, news, translation, website checks, notes, todos, "
              "flashcards and memory all work here — use the tools. "
              "The user is holding the phone: to open a site, a search, a video, music, a map, an app, a call or "
              "a message, use open_on_phone. Only when the user mentions the computer, or it is about the "
              "computer itself — its volume, files, programs, screen, sending a file from it — use use_computer; if it says the "
              "computer is offline, tell the user briefly. "
              "Reply in 1-2 short spoken sentences, plain text, no brackets or quotes.) ")
# без этого облако бессмысленно — если не загрузились, лучше честно упасть с ошибкой в логе Space
ESSENTIAL = ("ai_brain", "brain", "brain.state", "brain.prompt", "brain.providers", "brain.tools", "brain.features",
             "brain.planner", "brain.instant", "brain.pipeline", "tool_router", "core", "core.llm_gateway",
             "core.memory", "core.cloud_sync", "phone", "phone.server", "openai")
_state = {"stubbed": [], "memory": None}

# Функции, которые мозг использует как проверку или список: у заглушки они должны отвечать
# «пусто» / «нет», а не фразой «недоступно» (иначе, например, «идёт игра» станет всегда «да»).
SAFE_DEFAULTS = {
    "core.skills": {"load_skills": lambda: {}},
    "skills.fun": {"game_active": lambda: False},
    "core.study": {"active": lambda: False},
    "core.routines": {"match_trigger": lambda q: None, "log_tools": lambda *a: None},
    "core.skill_forge": {"installed": lambda: [], "list_items": lambda n=10: [], "load_skill": lambda name: None,
                         "start": lambda *a, **k: None},
}


def _light_router() -> None:
    """Без векторов смысла выбор инструментов не может отобрать «ближайшие» внутри группы по ключевым
    словам — пусть берёт группу целиком (иначе нужные инструменты терялись бы случайно)."""
    try:
        import tool_router
        if not hasattr(tool_router, "SEMANTIC_KEYWORD_K"):
            print("[облако] tool_router другой версии: в лёгком режиме выбор инструментов может терять часть группы")
        tool_router.SEMANTIC_KEYWORD_K = 10 ** 6
    except Exception as e:
        print(f"[облако] выбор инструментов: {e}")


GOOGLE_HINT = ("Google access in the cloud has expired or was revoked. Tell the user: sign in to Google again on the "
               "computer (ask Atlas there about the calendar), then paste the new token.json into GOOGLE_TOKEN_JSON "
               "in Render. Calendar and mail keep working on the computer itself.")


def _token_text(raw: str) -> str:
    """Токен, как его вставили в Render: без «GOOGLE_TOKEN_JSON =», кавычек вокруг и потерянных скобок."""
    t = (raw or "").strip()
    if t.upper().startswith("GOOGLE_TOKEN_JSON"):
        t = t.split("=", 1)[-1].strip()
    if len(t) > 1 and t[0] == t[-1] and t[0] in "'\"" and not t.startswith('"token'):
        t = t[1:-1].strip()
    if t and not t.startswith("{"):
        t = "{" + t
    if t and not t.endswith("}"):
        t += "}"
    return t


def _google_setup() -> bool:
    """GOOGLE_TOKEN_JSON → token.json рядом с кодом; окно входа Google в облаке не открывается никогда."""
    raw = _token_text(os.getenv("GOOGLE_TOKEN_JSON") or "")
    if not raw:
        stubs.stub("calendar_control")
        stubs.stub("email_reader")
        print("[облако] Google (Календарь, Gmail): нет GOOGLE_TOKEN_JSON — недоступно из облака")
        return False
    try:
        import json as _json
        if not isinstance(_json.loads(raw), dict):
            raise ValueError("это не токен Google")
        with open(os.path.join(ROOT, "token.json"), "w", encoding="utf-8") as f:
            f.write(raw)
        import google_auth

        class _NoBrowser:
            @staticmethod
            def from_client_secrets_file(*a, **k):
                raise RuntimeError(GOOGLE_HINT)
        google_auth.InstalledAppFlow = _NoBrowser
        print("[облако] Google (Календарь, Gmail): подключён")
        return True
    except Exception as e:
        stubs.stub("calendar_control")
        stubs.stub("email_reader")
        print(f"[облако] Google не подключился ({e}) — Календарь и Gmail недоступны из облака")
        return False


def _is_unavailable(fn) -> bool:
    while fn is not None:
        if getattr(fn, "__doc__", "") == "Недоступно из облака.":
            return True
        fn = getattr(fn, "__wrapped__", None)
    return False


# Обёртки мозга над компьютерными модулями (голо-экран, жесты, мастерская навыков, самолечение,
# мини-окно, ритуалы с действиями на компьютере, установка на устройства) — по метке их не распознать
CLOUD_HIDE = {"holo_show", "holo_weather", "holo_graph", "holo_control", "look", "gestures_control", "learn_skill",
              "install_skill", "reject_skill", "list_learned_skills", "remove_skill", "heal_status", "heal_apply",
              "heal_reject", "day_report", "mini_mode", "install_on_device", "create_routine", "list_routines",
              "run_routine", "delete_routine", "get_cpu_usage",   # нагрузка сервера — не твоего компьютера
              "get_my_ip", "get_local_ip", "ping_host", "check_internet_speed",   # это IP и скорость сервера
              "generate_qr_code",                                                   # сохраняет на рабочий стол
              "what_did_i_do", "find_past_activity", "reopen_from_history",         # память компьютера живёт
              "continue_last_work", "forget_activity", "save_workspace", "open_workspace",
              "list_workspaces", "accept_habit_suggestion", "decline_habit_suggestion", "list_habits",
              "plan_tidy_folder", "plan_collect_files", "confirm_file_plan", "cancel_file_plan", "undo_file_plan",
              "send_file_to_phone"}                              # на нём — через use_computer


def _hide_pc_tools() -> list:
    """Инструменты «только для компьютера» убрать из того, что видит модель: иначе она выберет их,
    получит «недоступно» и потратит шаг. Быстрый путь по-прежнему ответит «недоступно» сам."""
    from brain import tools
    hidden = sorted(n for n, f in tools.AVAILABLE_FUNCTIONS.items() if _is_unavailable(f) or n in CLOUD_HIDE)
    tools.TOOLS_SCHEMA[:] = [t for t in tools.TOOLS_SCHEMA if t["function"]["name"] not in hidden]
    try:
        import tool_router
        tool_router._tool_index["mat"] = None          # смысловой индекс пересоберётся без них
    except Exception:
        pass
    return hidden


def _prepare_optional() -> list:
    """Необязательные модули — загрузить заранее; если стали заглушкой — безопасные ответы."""
    got = []
    for name, attrs in SAFE_DEFAULTS.items():
        try:
            mod, more = stubs.import_with_autostub(name, keep=ESSENTIAL)
            got += more
        except Exception as e:
            print(f"[облако] {name}: {e} — заменяю заглушкой")
            mod = stubs.stub(name)
            got.append(name)
        if getattr(mod, "__atlas_stub__", False):
            for k, v in attrs.items():
                setattr(mod, k, v)
    return got


def _local_time() -> str:
    """Сервер живёт по UTC, а «сегодня», «через час» и напоминания — по времени пользователя (ATLAS_TZ)."""
    name = os.getenv("ATLAS_TZ") or "Asia/Bishkek"
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        off = datetime.now(ZoneInfo(name)).utcoffset()
    except Exception:
        from datetime import timedelta
        off = timedelta(hours={"Asia/Bishkek": 6, "Asia/Almaty": 5, "Europe/Moscow": 3}.get(name, 0))
    mins = int(off.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    hh, mm = divmod(abs(mins), 60)
    # POSIX: «<+06>-6» — знак наоборот; файлы часовых поясов системе не нужны
    os.environ["TZ"] = f"<{sign}{hh:02d}{mm:02d}>{'-' if sign == '+' else '+'}{hh}:{mm:02d}"
    if hasattr(time, "tzset"):
        time.tzset()
    return f"{name} (UTC{sign}{hh:02d}:{mm:02d})"


def _keep_awake() -> None:
    """Бесплатный Render засыпает через 15 минут без запросов — тогда напоминание не пришло бы вовремя.
    Atlas сам заходит на свой адрес каждые 9 минут (GitHub keepalive — запасной)."""
    url = (os.getenv("RENDER_EXTERNAL_URL") or os.getenv("ATLAS_CLOUD_URL") or "").rstrip("/")
    if not url.startswith("https://"):
        return
    import urllib.request

    def loop():
        while True:
            time.sleep(9 * 60)
            try:
                urllib.request.urlopen(url + "/manifest.webmanifest", timeout=20).read()
            except Exception as e:
                print(f"[облако] не достучался до себя ({e})")
    threading.Thread(target=loop, daemon=True, name="keep-awake").start()
    print(f"[облако] не засыпаю: захожу на {url} каждые 9 минут")


def _start_reminders() -> None:
    try:
        import reminders
        reminders.start_reminder_thread(None, push=True)
        from core import briefing
        briefing.start()
        s = briefing.settings()
        print(f"[облако] утренняя сводка: " + (f"каждый день в {s['time']}" if s["on"] else "выключена"))
        from core import push
        print("[облако] напоминания: " + ("уведомления на телефон включены" if push.available()
                                          else "нет библиотеки cryptography — уведомления не уйдут"))
    except Exception as e:
        print(f"[облако] напоминания не запустились: {e}")


_MENTIONS_PC = re.compile(r"компьютер|компе?\b|компа\b|ноутбук|ноуте?\b|\bпк\b|\bpc\b|computer|laptop", re.I)


def _computer_hands(server) -> None:
    """Компьютер на связи — облако передаёт ему всё, что умеет только он (core/pc_link)."""
    from core import pc_link
    from brain import tools
    import tool_router
    server.PC_HUB = True
    tools.AVAILABLE_FUNCTIONS["use_computer"] = pc_link.use_computer
    if not any(t["function"]["name"] == "use_computer" for t in tools.TOOLS_SCHEMA):
        tools.TOOLS_SCHEMA.append(pc_link.SCHEMA)
    tool_router.CORE.add("use_computer")             # всегда под рукой: «включи музыку», «громче», «открой…»
    try:
        from brain import planner
        planner.SLIM_TOOLS.update({"use_computer", "open_on_phone", "daily_brief"})   # короткий пересказ
    except Exception:
        pass

    from core import phone_actions
    tools.AVAILABLE_FUNCTIONS["open_on_phone"] = phone_actions.open_on_phone
    if not any(t["function"]["name"] == "open_on_phone" for t in tools.TOOLS_SCHEMA):
        tools.TOOLS_SCHEMA.append(phone_actions.SCHEMA)
    tool_router.CORE.add("open_on_phone")            # телефон в руке: сайты, поиск, музыка, видео, карты
    from core import briefing                        # «что у меня сегодня» — погода, календарь, напоминания…
    tools.AVAILABLE_FUNCTIONS["daily_brief"] = briefing.daily_brief
    if not any(t["function"]["name"] == "daily_brief" for t in tools.TOOLS_SCHEMA):
        tools.TOOLS_SCHEMA.append(briefing.SCHEMA)
    tool_router.register_tool("daily_brief", "calendar")

    def forward(text):
        # быстрый путь компьютера («включи музыку») — на компьютер, только если о нём сказали;
        # иначе решает мозг: открыть на телефоне или попросить компьютер
        if not pc_link.online() or not _MENTIONS_PC.search(text or ""):
            return None
        try:
            return pc_link.submit(text)
        except pc_link.PcOffline:
            return None
        except pc_link.PcTimeout:
            return "Компьютер не ответил вовремя — попробуй ещё раз."
    server.FORWARD = forward


def _replace(name: str, module) -> None:
    sys.modules[name] = module


def boot(serve: bool = True, port: int = None):
    """Поднять облачного Atlas. serve=False — без сервера (для тестов). → модуль сервера телефона."""
    t0 = time.time()
    print(f"[облако] время: {_local_time()}")
    stubs.install_pc_stubs()
    import cloud.voice_cloud as voice_cloud
    import cloud.file_search_cloud as fs_cloud
    import cloud.ui_state_cloud as ui_cloud
    for name, mod in (("voice", voice_cloud), ("file_search", fs_cloud), ("ui_state", ui_cloud)):
        if not getattr(sys.modules.get(name), "__atlas_test__", False):   # тесты могут дать свои
            _replace(name, mod)

    print("[облако] память: " + ("полная, с моделью векторов смысла" if fs_cloud.EMBED_ON else
                                  "лёгкий режим — без модели векторов (факты, портрет, заметки, карточки — да; "
                                  "поиск похожих разговоров — на компьютере)"))
    from core import cloud_sync
    store = cloud_sync.store_from_env()
    if store is not None:
        try:
            r = cloud_sync.sync_once(store)
            _state["memory"] = r
            print(f"[облако] память из Supabase: получено {r['pulled']} записей")
        except Exception as e:
            print(f"[облако] память из Supabase не загрузилась ({e}) — начну с пустой, попробую ещё раз")
    else:
        print("[облако] нет SUPABASE_URL / SUPABASE_SERVICE_KEY — память будет только своя, без компьютера")

    # окна и буфера обмена в облаке нет — тихие пустые ответы вместо ошибок в журнале
    stubs.stub("pygetwindow", getActiveWindow=lambda: None)
    stubs.stub("pyperclip", paste=lambda: "", copy=lambda text: None)
    stubs.stub("pyautogui", hotkey=lambda *a, **k: None, position=lambda: (0, 0))
    _google_setup()
    early = _prepare_optional()
    ai_brain, stubbed = stubs.import_with_autostub("ai_brain", keep=ESSENTIAL)
    stubbed = early + stubbed
    _, more = stubs.import_with_autostub("fast_commands", keep=ESSENTIAL)
    _state["stubbed"] = sorted(set(stubbed + more))
    if _state["stubbed"]:
        print(f"[облако] дополнительно заменены заглушками: {', '.join(_state['stubbed'])}")

    if not fs_cloud.EMBED_ON:
        _light_router()
    hidden = _hide_pc_tools()
    print(f"[облако] модели не показываются {len(hidden)} инструментов, которые работают только на компьютере")

    from phone import server
    key = (os.getenv("PHONE_KEY") or "").strip()
    server.pairing_token = lambda create=False: key
    server.PHONE_HINT = os.getenv("PHONE_HINT") or CLOUD_HINT
    _computer_hands(server)
    if not key:
        print("[облако] нет PHONE_KEY — телефон не сможет подключиться (секрет в настройках Space)")
    try:
        import database
        database.init_db()                          # журнал инструментов (task_log) — как на компьютере
    except Exception as e:
        print(f"[облако] журнал инструментов: {e}")
    if store is not None:
        cloud_sync.start()
    print(f"[облако] Atlas готов за {time.time() - t0:.1f} с")
    if serve:
        _start_reminders()
        _keep_awake()
        server.start(port=port or int(os.getenv("PORT") or 7860), host="0.0.0.0")
        threading.Event().wait()                      # сервер работает в своём потоке; держим процесс
    return server


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:
            pass
    boot()
