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
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cloud import stubs  # noqa: E402

CLOUD_HINT = ("(Said on the phone; Atlas is answering from the cloud: calendar, mail, web search, weather, news, "
              "translation, website checks, notes, todos, flashcards and memory all work here — use the tools. "
              "Only music, apps, files and volume need the computer; for those say briefly it works when the "
              "computer is on. Reply in 1-2 short spoken sentences, plain text, no brackets or quotes.) ")
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


GOOGLE_HINT = ("Google не подключён к облаку: добавь в Render секрет GOOGLE_TOKEN_JSON — его выводит "
               "«python -m cloud.space_secrets» на компьютере. Календарь и почта работают и на самом компьютере.")


def _google_setup() -> bool:
    """GOOGLE_TOKEN_JSON → token.json рядом с кодом; окно входа Google в облаке не открывается никогда."""
    raw = (os.getenv("GOOGLE_TOKEN_JSON") or "").strip()
    if not raw:
        stubs.stub("calendar_control")
        stubs.stub("email_reader")
        print("[облако] Google (Календарь, Gmail): нет GOOGLE_TOKEN_JSON — недоступно из облака")
        return False
    try:
        import json as _json
        _json.loads(raw)
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
              "generate_qr_code"}                                                   # сохраняет на рабочий стол


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


def _replace(name: str, module) -> None:
    sys.modules[name] = module


def boot(serve: bool = True, port: int = None):
    """Поднять облачного Atlas. serve=False — без сервера (для тестов). → модуль сервера телефона."""
    t0 = time.time()
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
    if not key:
        print("[облако] нет PHONE_KEY — телефон не сможет подключиться (секрет в настройках Space)")
    if store is not None:
        cloud_sync.start()
    print(f"[облако] Atlas готов за {time.time() - t0:.1f} с")
    if serve:
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
