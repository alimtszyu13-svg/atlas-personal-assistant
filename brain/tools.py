"""
Инструменты Atlas: реестр, запуск, отбор для запроса, починка аргументов, «ход мыслей».

    AVAILABLE_FUNCTIONS / TOOLS_SCHEMA — что Atlas умеет (функции и их описания для модели)
    register(...)                      — добавить инструмент из любого модуля одной строкой
    _run_one_tool(name, args)          — запустить: защита печати → починка аргументов → вызов
    _trim_schema / _with_pairs / ...   — какие инструменты показать модели в этом запросе
    _trace_*                           — «ход мыслей»: каждый вызов виден вокруг звезды
"""
import functools
import inspect
import os
import re
import threading
import time

import tool_router
from brain import state
from system_control import open_app, close_app, open_youtube, open_url, search_google, play_on_spotify, play_on_youtube_music
from info_services import get_weather, get_news
from file_control import open_file, create_folder, delete_file, locate_file, rename_file, copy_file, move_file
from system_info import get_cpu_usage, get_memory_usage, get_battery_status, get_disk_usage
from reminders import set_timer, list_timers
from web_search_tool import search_web
from email_reader import get_recent_emails, get_unread_count
from theme_control import set_theme
from system_advanced import (
    set_volume, get_volume, volume_up, volume_down, mute_volume, unmute_volume,
    set_brightness, get_brightness, lock_screen, take_screenshot,
    list_top_processes, kill_process, empty_recycle_bin, get_uptime
)
from media_control import play_pause_media, next_track, previous_track
from dev_tools import run_git_command, open_vscode_project, calculate, convert_units
from notes import add_note, list_notes, delete_note, add_todo, list_todos, complete_todo, delete_todo
from voice import (
    list_voices, set_voice, list_audio_devices, set_microphone, set_speaker,
    set_response_language, list_elevenlabs_voices, set_elevenlabs_voice,
)
from listening_mode import set_always_listening
from calendar_control import list_today_events, list_upcoming_events, create_event, delete_event
from network_utils import ping_host, get_my_ip, get_local_ip, is_website_up, check_internet_speed
from text_utils import translate_text, generate_qr_code, word_count
from database import save_memory, recall_memories, forget_memory
from browser_agent import (browser_open, browser_read_page, browser_click, browser_type, browser_scroll,
                           browser_fullscreen, browser_press_key, browser_screenshot_describe, browser_close,
                           next_episode, play_on_netflix, play_on_rezka, media_play_pause, media_seek, media_volume,
                           media_player_fullscreen, change_rezka_quality, change_rezka_translator,
                           select_rezka_episode, skip_intro)
from deep_links import launch_steam_game, list_steam_games, open_deep_link
from file_search import search_file_content, open_found_file, open_search_result


def update_plan(goal: str, steps: list, done_when: str) -> str:
    """Записывает или пересматривает план текущей многошаговой задачи."""
    state.turn["plan"] = {"goal": goal, "steps": steps, "done_when": done_when}
    return f"План записан. Цель: {goal}. Готово когда: {done_when}"


AVAILABLE_FUNCTIONS = {
    "update_plan": update_plan, "play_on_spotify": play_on_spotify, "play_on_youtube_music": play_on_youtube_music, "open_app": open_app,
    "close_app": close_app, "open_youtube": open_youtube, "open_url": open_url, "search_google": search_google,
    "open_file": open_file, "open_search_result": open_search_result, "create_folder": create_folder, "delete_file": delete_file,
    "locate_file": locate_file, "rename_file": rename_file, "copy_file": copy_file, "move_file": move_file,
    "get_weather": get_weather, "get_news": get_news, "get_cpu_usage": get_cpu_usage, "get_memory_usage": get_memory_usage,
    "get_battery_status": get_battery_status, "get_disk_usage": get_disk_usage, "set_timer": set_timer, "list_timers": list_timers,
    "search_web": search_web, "get_recent_emails": get_recent_emails, "get_unread_count": get_unread_count, "set_theme": set_theme,
    "set_volume": set_volume, "get_volume": get_volume, "volume_up": volume_up, "volume_down": volume_down,
    "mute_volume": mute_volume, "unmute_volume": unmute_volume, "set_brightness": set_brightness, "get_brightness": get_brightness,
    "lock_screen": lock_screen, "take_screenshot": take_screenshot, "list_top_processes": list_top_processes, "kill_process": kill_process,
    "empty_recycle_bin": empty_recycle_bin, "get_uptime": get_uptime, "play_pause_media": play_pause_media, "next_track": next_track,
    "previous_track": previous_track, "run_git_command": run_git_command, "open_vscode_project": open_vscode_project, "calculate": calculate,
    "convert_units": convert_units, "add_note": add_note, "list_notes": list_notes, "delete_note": delete_note,
    "add_todo": add_todo, "list_todos": list_todos, "complete_todo": complete_todo, "delete_todo": delete_todo,
    "list_voices": list_voices, "set_voice": set_voice, "list_audio_devices": list_audio_devices, "set_microphone": set_microphone,
    "set_speaker": set_speaker, "set_response_language": set_response_language, "list_elevenlabs_voices": list_elevenlabs_voices, "set_elevenlabs_voice": set_elevenlabs_voice,
    "set_always_listening": set_always_listening, "list_today_events": list_today_events, "list_upcoming_events": list_upcoming_events, "create_event": create_event,
    "delete_event": delete_event, "ping_host": ping_host, "get_my_ip": get_my_ip, "get_local_ip": get_local_ip,
    "is_website_up": is_website_up, "check_internet_speed": check_internet_speed, "translate_text": translate_text, "generate_qr_code": generate_qr_code,
    "word_count": word_count, "save_memory": save_memory, "recall_memories": recall_memories, "forget_memory": forget_memory,
    "browser_open": browser_open, "browser_read_page": browser_read_page, "browser_click": browser_click, "browser_type": browser_type,
    "browser_scroll": browser_scroll, "browser_fullscreen": browser_fullscreen, "browser_press_key": browser_press_key, "browser_screenshot_describe": browser_screenshot_describe,
    "browser_close": browser_close, "play_on_netflix": play_on_netflix, "play_on_rezka": play_on_rezka, "media_play_pause": media_play_pause,
    "media_seek": media_seek, "media_volume": media_volume, "media_player_fullscreen": media_player_fullscreen, "change_rezka_quality": change_rezka_quality,
    "change_rezka_translator": change_rezka_translator, "select_rezka_episode": select_rezka_episode, "next_episode": next_episode, "skip_intro": skip_intro,
    "launch_steam_game": launch_steam_game, "list_steam_games": list_steam_games, "open_deep_link": open_deep_link, "search_file_content": search_file_content,
    "open_found_file": open_found_file,
}

_F = "function"


def _s(name, desc, props=None, required=None):
    """Короткая запись описания инструмента для модели."""
    p = {"type": "object", "properties": props or {}}
    if required:
        p["required"] = required
    return {"type": _F, _F: {"name": name, "description": desc, "parameters": p}}


_S, _I, _N, _B = {"type": "string"}, {"type": "integer"}, {"type": "number"}, {"type": "boolean"}

TOOLS_SCHEMA = [
    _s("update_plan", "Record the plan for a multi-step task before acting. Call this FIRST for anything needing 2+ actions. done_when must be an observable condition a tool result can confirm.",
       {"goal": _S, "steps": {"type": "array", "items": _S}, "done_when": _S}, ["goal", "steps", "done_when"]),
    _s("open_app", "Opens an application on the computer by name", {"app_name": _S}, ["app_name"]),
    _s("play_on_spotify", "Opens the Spotify DESKTOP app straight at a search — instant, no browser needed. Always prefer this over browsing open.spotify.com for any music request.", {"query": _S}),
    _s("play_on_youtube_music", "Opens YouTube Music directly at a search query", {"query": _S}, ["query"]),
    _s("close_app", "Closes a running application by name", {"app_name": _S}, ["app_name"]),
    _s("open_youtube", "Opens YouTube search results for a query or video name", {"query": _S}, ["query"]),
    _s("open_url", "Opens a URL in the default browser", {"url": _S}, ["url"]),
    _s("search_google", "Opens Google search results for a query", {"query": _S}, ["query"]),
    _s("open_file", "Opens a file or folder by its exact NAME. Not for searching by content or by what is shown in a picture — use search_file_content for that.", {"name": _S}, ["name"]),
    _s("open_search_result", "Opens the N-th file from the most recent search_file_content results (1 = first). Use for 'open the second one' after a file search.", {"n": _I}, ["n"]),
    _s("create_folder", "Creates a new folder. location can be Desktop, Documents, Downloads, a drive letter, or a full path", {"name": _S, "location": _S}, ["name"]),
    _s("delete_file", "Deletes a file or folder by name (moves to Recycle Bin, asks for voice confirmation first)", {"name": _S}, ["name"]),
    _s("locate_file", "Finds a file or folder ONLY by its NAME and reports where it is. If the user describes what is inside the file or what it is about, use search_file_content instead.", {"name": _S}, ["name"]),
    _s("rename_file", "Renames a file or folder (asks for voice confirmation first)", {"old_name": _S, "new_name": _S}, ["old_name", "new_name"]),
    _s("copy_file", "Copies a file or folder to a destination, keeping the original (asks for voice confirmation first)", {"name": _S, "destination": _S}, ["name"]),
    _s("move_file", "Moves a file or folder to a destination (asks for voice confirmation first)", {"name": _S, "destination": _S}, ["name"]),
    _s("get_weather", "Gets the current weather", {"city": _S}),
    _s("get_news", "Gets the latest news headlines", {"count": _I}),
    _s("get_cpu_usage", "Gets current CPU usage percentage"),
    _s("get_memory_usage", "Gets current RAM usage"),
    _s("get_battery_status", "Gets battery charge level and charging status"),
    _s("get_disk_usage", "Gets free/used disk space for a drive", {"drive": _S}),
    _s("set_timer", "Sets a timer for a number of minutes with an optional message", {"minutes": _N, "message": _S}, ["minutes"]),
    _s("list_timers", "Lists all active timers"),
    _s("search_web", "Searches the web for current information", {"query": _S}, ["query"]),
    _s("get_recent_emails", "Reads and summarizes recent inbox emails", {"count": _I}),
    _s("get_unread_count", "Gets the number of unread emails"),
    _s("set_theme", "Switches the interface theme to dark or light", {"theme": _S}, ["theme"]),
    _s("set_volume", "Sets system volume to a specific percentage 0-100", {"level": _I}, ["level"]),
    _s("get_volume", "Gets current system volume"),
    _s("volume_up", "Increases system volume", {"step": _I}),
    _s("volume_down", "Decreases system volume", {"step": _I}),
    _s("mute_volume", "Mutes system audio"),
    _s("unmute_volume", "Unmutes system audio"),
    _s("set_brightness", "Sets screen brightness percentage 0-100", {"level": _I}, ["level"]),
    _s("get_brightness", "Gets current screen brightness"),
    _s("lock_screen", "Locks the Windows screen immediately"),
    _s("take_screenshot", "Takes a screenshot and saves it to the Desktop"),
    _s("list_top_processes", "Lists top processes by CPU usage", {"count": _I}),
    _s("kill_process", "Force-closes a process by its exact name", {"name": _S}, ["name"]),
    _s("empty_recycle_bin", "Empties the Recycle Bin"),
    _s("get_uptime", "Reports system uptime since last restart"),
    _s("play_pause_media", "Toggles play/pause on the active media player"),
    _s("next_track", "Skips to the next track"),
    _s("previous_track", "Goes to the previous track"),
    _s("run_git_command", "Runs a git command inside a project folder", {"command": _S, "project_path": _S}, ["command"]),
    _s("open_vscode_project", "Opens a project folder in VS Code", {"path": _S}, ["path"]),
    _s("calculate", "Evaluates a basic math expression", {"expression": _S}, ["expression"]),
    _s("convert_units", "Converts a value between common units (km/mi, kg/lb, celsius/fahrenheit, m/ft)", {"value": _N, "from_unit": _S, "to_unit": _S}, ["value", "from_unit", "to_unit"]),
    _s("add_note", "Saves a short note", {"text": _S}, ["text"]),
    _s("list_notes", "Lists all saved notes"),
    _s("delete_note", "Deletes a note by its number", {"index": _I}, ["index"]),
    _s("add_todo", "Adds a task to the to-do list", {"task": _S}, ["task"]),
    _s("list_todos", "Lists all to-do items"),
    _s("complete_todo", "Marks a to-do item as done by its number", {"index": _I}, ["index"]),
    _s("delete_todo", "Deletes a to-do item by its number", {"index": _I}, ["index"]),
    _s("list_voices", "Lists available English TTS voices"),
    _s("set_voice", "Switches the English TTS voice (male: troy, daniel, austin; female: autumn, diana, hannah)", {"name": _S}, ["name"]),
    _s("list_audio_devices", "Lists available speaker and microphone devices"),
    _s("set_microphone", "Switches which microphone Atlas listens through", {"name": _S}, ["name"]),
    _s("set_speaker", "Switches which speaker/headphones Atlas talks through", {"name": _S}, ["name"]),
    _s("set_response_language", "Switches Atlas's response language between English and Russian", {"lang": {"type": "string", "description": "'en' or 'ru'"}}, ["lang"]),
    _s("list_elevenlabs_voices", "Lists available Russian TTS voices"),
    _s("set_elevenlabs_voice", "Switches the Russian TTS voice", {"name": _S}, ["name"]),
    _s("set_always_listening", "Enables or disables always-listening mode (no wake word needed)", {"enabled": _B}, ["enabled"]),
    _s("list_today_events", "Lists today's calendar events"),
    _s("list_upcoming_events", "Lists upcoming calendar events", {"days": _I}),
    _s("create_event", "Creates a calendar event", {"title": _S, "date": {"type": "string", "description": "YYYY-MM-DD"}, "time": {"type": "string", "description": "HH:MM 24h"}, "duration_minutes": _I}, ["title", "date"]),
    _s("delete_event", "Deletes an upcoming calendar event by title", {"title": _S}, ["title"]),
    _s("ping_host", "Pings a host to check reachability", {"host": _S}, ["host"]),
    _s("get_my_ip", "Gets the public IP address"),
    _s("get_local_ip", "Gets the local network IP address"),
    _s("is_website_up", "Checks if a website is reachable", {"url": _S}, ["url"]),
    _s("check_internet_speed", "Runs an internet speed test"),
    _s("translate_text", "Translates text to a target language", {"text": _S, "target_language": _S}, ["text"]),
    _s("generate_qr_code", "Generates a QR code image for text or a URL, saved to Desktop", {"text": _S}, ["text"]),
    _s("word_count", "Counts words and characters in text", {"text": _S}, ["text"]),
    _s("save_memory", "Saves a fact about the user for long-term recall across sessions (e.g. preferences, personal details, ongoing projects)", {"content": _S, "category": {"type": "string", "description": "Personal, Projects, People, Preferences, Facts, or Tasks"}, "importance": {"type": "integer", "description": "1-10, how important this is to remember"}}, ["content"]),
    _s("recall_memories", "Recalls saved facts about the user. Omit the category parameter entirely to get all memories — never pass null.", {"category": {"type": "string", "description": "Optional filter: Personal, Projects, People, Preferences, Facts, or Tasks. Omit this field if not filtering."}}),
    _s("forget_memory", "Deletes a saved memory matching a text fragment", {"content_fragment": _S}, ["content_fragment"]),
    _s("browser_fullscreen", "Toggles the browser window fullscreen (F11). For a video player's own fullscreen button, use browser_click on it instead"),
    _s("browser_press_key", "Sends a key press to the page — e.g. Space to play/pause video, Escape, ArrowRight", {"key": _S}, ["key"]),
    _s("browser_close", "Closes the controlled browser window"),
    _s("play_on_netflix", "Fast dedicated macro to search and play a title on Netflix directly — use this instead of the generic browser_open/browser_click loop whenever the user wants to watch something and Netflix is a reasonable choice. Requires an already-logged-in Netflix session.", {"title": _S}, ["title"]),
    _s("play_on_rezka", "Fast dedicated macro to search and play a title on Rezka directly — use this instead of the generic browser_open/browser_click loop whenever the user wants to watch something and Rezka is a reasonable choice.", {"title": _S}, ["title"]),
    _s("media_play_pause", "Toggles play or pause for the currently playing video/movie."),
    _s("media_seek", "Seeks the video forward or backward.", {"direction": {"type": "string", "enum": ["forward", "backward"]}}, ["direction"]),
    _s("media_volume", "Adjusts the video volume. Use 'mute' to toggle sound on/off.", {"action": {"type": "string", "enum": ["up", "down", "mute"]}}, ["action"]),
    _s("media_player_fullscreen", "Toggles the web video player into fullscreen mode."),
    _s("change_rezka_quality", "Changes the video quality on HDRezka (e.g., '1080p', '720p', '480p').", {"quality": {"type": "string", "description": "The desired quality, e.g. '1080p'"}}, ["quality"]),
    _s("change_rezka_translator", "Changes the voiceover/translation (озвучка) on HDRezka (e.g., 'LostFilm', 'Кубик в кубе').", {"translator_name": _S}, ["translator_name"]),
    _s("select_rezka_episode", "Selects a specific season and episode of a TV show on HDRezka.", {"season": _I, "episode": _I}, ["season", "episode"]),
    _s("next_episode", "Plays the next episode of the currently watching TV show."),
    _s("skip_intro", "Clicks the 'Skip Intro' or 'Пропустить заставку' button on Netflix, Ivi, Rezka, etc."),
    _s("launch_steam_game", "Launches an installed Steam game instantly by appid. Always use this instead of opening the Steam library and clicking.", {"game_name": _S}, ["game_name"]),
    _s("list_steam_games", "Lists installed Steam games"),
    _s("open_deep_link", "Opens a service directly at a target in one step: youtube, twitch, maps, github, wikipedia, spotify, steam, telegram, discord, settings. Much faster than a browser session.", {"service": _S, "query": _S}, ["service"]),
    _s("search_file_content", "Finds files by meaning or words INSIDE them (not filename). Understands paraphrases: 'trip budget' finds 'travel expenses'. Also finds screenshots, images and scanned PDFs by the text visible in them (OCR). Use when the user remembers the content but not the name. Never use browser tools to look for local files.", {"query": _S}, ["query"]),
    _s("open_found_file", "Finds a file by its content and opens the best match immediately", {"query": _S}, ["query"]),
]
# Описания browser_open/… и desktop_… задаёт features.py (профессиональный режим) — здесь их нет намеренно.

# Только читают и ничего не меняют — их безопасно запускать одновременно
READ_ONLY_TOOLS = {
    "search_web", "get_weather", "get_news", "recall_memories", "get_cpu_usage", "get_memory_usage",
    "get_battery_status", "get_disk_usage", "get_uptime", "get_volume", "get_brightness", "list_notes",
    "list_todos", "list_timers", "list_today_events", "list_upcoming_events", "get_recent_emails",
    "get_unread_count", "get_my_ip", "get_local_ip", "is_website_up", "ping_host", "list_steam_games",
    "locate_file", "calculate", "convert_units", "word_count", "translate_text", "list_voices",
    "list_audio_devices", "search_file_content",
}


def register(name: str, fn, description: str = "", properties: dict = None, required: list = None,
             group: str = None, read_only: bool = False, schema: dict = None) -> None:
    """Добавить (или заменить) инструмент: функция, описание для модели, группа для отбора."""
    AVAILABLE_FUNCTIONS[name] = fn
    schema = schema or _s(name, description, properties, required)
    TOOLS_SCHEMA[:] = [t for t in TOOLS_SCHEMA if t["function"]["name"] != name] + [schema]
    if read_only:
        READ_ONLY_TOOLS.add(name)
    if group:
        tool_router.register_tool(name, group)


def unregister(name: str) -> None:
    AVAILABLE_FUNCTIONS.pop(name, None)
    TOOLS_SCHEMA[:] = [t for t in TOOLS_SCHEMA if t["function"]["name"] != name]


def load_registry_skills() -> None:
    """Навыки из реестра (@skill): схема, функция, группа — из одного места."""
    from core.skills import load_skills
    skills = load_skills()
    for name, s in skills.items():
        register(name, s["fn"], schema=s["schema"], group=s["group"], read_only=s["read_only"])
    print(f"[skills] навыков из реестра: {len(skills)}")


# =============================================================================
# Ход мыслей: каждый вызов инструмента виден вокруг звезды
# =============================================================================
_TRACE_FAIL = re.compile(r"something went wrong|не найден|not found|has been closed|не смог|couldn'?t|"
                         r"failed|ошибк|error|отказываюсь|не удалось", re.I)
_trace_lock = threading.Lock()


def _trace_push(ev: dict) -> int:
    from ui_state import shared_state
    with _trace_lock:
        tr = shared_state.setdefault("trace", {"run": 0, "events": []})
        if ev.get("t") == "start":
            tr["run"] += 1
            tr["events"] = []
        ev["ts"] = time.time()
        tr["events"].append(ev)
        del tr["events"][:-40]
        shared_state["trace_seq"] = shared_state.get("trace_seq", 0) + 1
        return len(tr["events"]) - 1


def _trace_done(idx: int, ok: bool, result, t0: float) -> None:
    from ui_state import shared_state
    with _trace_lock:
        evs = shared_state.get("trace", {}).get("events", [])
        if 0 <= idx < len(evs):
            evs[idx].update(state="ok" if ok else "fail", ms=int((time.time() - t0) * 1000), res=str(result)[:80])
            shared_state["trace_seq"] = shared_state.get("trace_seq", 0) + 1


def _traced(name: str, fn):
    if getattr(fn, "_atlas_traced", False):
        return fn

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        short = {k: (v if isinstance(v, (int, float, bool)) else str(v)[:40]) for k, v in kwargs.items()}
        ev = {"t": "tool", "name": name, "args": short, "state": "run"}
        if name == "update_plan":
            ev["goal"] = str(kwargs.get("goal", ""))[:80]
            ev["steps"] = [str(s)[:50] for s in (kwargs.get("steps") or [])][:6]
        idx = _trace_push(ev)
        t0 = time.time()
        try:
            res = fn(*args, **kwargs)
        except Exception as e:
            _trace_done(idx, False, e, t0)
            raise
        _trace_done(idx, not _TRACE_FAIL.search(str(res)[:220]), res, t0)
        return res
    wrapper._atlas_traced = True
    return wrapper


def _trace_wrap_all() -> None:
    for name, fn in list(AVAILABLE_FUNCTIONS.items()):
        if callable(fn) and not getattr(fn, "_atlas_traced", False):
            AVAILABLE_FUNCTIONS[name] = _traced(name, fn)


# =============================================================================
# Запуск инструмента
# =============================================================================
def _heal_report(exc, where: str = "") -> None:
    try:
        from core import healer
        healer.report(exc, where)
    except Exception as e:
        print(f"[самолечение] {e}")


_ARG_ALIASES = {      # модель перепутала имя параметра (name вместо app_name и т.п.)
    "app_name": ("name", "app", "application", "program"), "query": ("q", "search", "term", "question", "text"),
    "text": ("content", "message", "note", "value"), "city": ("place", "location"), "place": ("city", "location"),
    "url": ("link", "address"), "minutes": ("duration", "mins", "time"), "expression": ("expr", "formula", "query"),
    "title": ("name",), "task": ("text", "todo", "item"),
}


def _repair_args(name: str, args: dict) -> dict:
    f = AVAILABLE_FUNCTIONS.get(name)
    if not f or not isinstance(args, dict):
        return args or {}
    try:
        params = inspect.signature(f).parameters
    except (TypeError, ValueError):
        return args
    out = dict(args)
    for p in params:
        if p in out:
            continue
        for alias in _ARG_ALIASES.get(p, ()):
            if alias in out and alias not in params:
                out[p] = out.pop(alias)
                break
    required = [p for p, v in params.items() if v.default is inspect.Parameter.empty
                and v.kind in (v.POSITIONAL_OR_KEYWORD, v.KEYWORD_ONLY)]
    missing = [p for p in required if p not in out]
    extra = [k for k in out if k not in params]
    if len(missing) == 1 and len(extra) == 1:
        out[missing[0]] = out.pop(extra[0])
    if out != args:
        print(f"[мозг] поправил аргументы {name}: {args} → {out}")
    return out


_TYPE_INTENT = re.compile(r"напиш|запиш|допиш|впиш|введи|напечат|вставь|заполни|набери|пиши|type|write|enter|fill|paste|jot",
                          re.I)


def _run_one_tool(name: str, args: dict):
    """Запустить инструмент → (результат, успех, мс).
    Печать в программы — только по просьбе; перепутанные параметры чинятся; ошибка в коде → самолечение."""
    if name == "desktop_type":
        q = re.sub(r"^\s*\([^)]*\)\s*", "", str(state.turn.get("question") or ""))
        if not (_TYPE_INTENT.search(q) or str(state.situation.get("reply", "")).rstrip().endswith("?")):
            print("[мозг] не печатаю: пользователь не просил ничего писать")
            return ("Blocked: the user didn't ask to type or change anything. Never type into opened files or "
                    "programs unless the user asked you to write something.", False, 0)
    args = _repair_args(name, args or {})
    func = AVAILABLE_FUNCTIONS.get(name)
    start = time.time()
    if not func:
        return f"Функция {name} не найдена.", False, 0
    try:
        valid = set(inspect.signature(func).parameters.keys())
        result = func(**{k: v for k, v in args.items() if k in valid})
        success = True
    except Exception as tool_err:
        print(f"[Tool error in {name}]: {tool_err}")
        _heal_report(tool_err, name)
        result, success = f"Something went wrong running {name}: {tool_err}", False
    ms = int((time.time() - start) * 1000)
    print(f"[время] {name}: {ms / 1000:.2f}с")
    return result, success, ms


# =============================================================================
# Какие инструменты показать модели в этом запросе
# =============================================================================
TOOL_PAIRS = {        # связанные: открыл одно — понадобится и другое
    "search_file_content": ["open_search_result", "open_found_file", "open_file"],
    "open_found_file": ["open_search_result"],
    "locate_file": ["open_file"],
    "search_web": ["read_webpage"],
    "read_webpage": ["search_web"],
    "play_on_rezka": ["media_play_pause", "media_seek", "media_volume", "media_player_fullscreen",
                      "change_rezka_quality", "change_rezka_translator", "select_rezka_episode", "next_episode", "skip_intro"],
    "play_on_netflix": ["media_play_pause", "media_seek", "media_volume", "media_player_fullscreen", "next_episode", "skip_intro"],
    "browser_open": ["browser_read_page", "browser_click", "browser_type", "browser_scroll", "browser_screenshot_describe"],
    "play_on_spotify": ["play_pause_media", "next_track", "previous_track"],
    "start_mission": ["mission_status", "cancel_mission"],
    "start_number_game": ["guess_number"],
}


def _recent_tool_names(n_msgs: int = 12) -> set:
    names = set()
    for m in state.conversation_history[-n_msgs:]:
        if isinstance(m, dict):
            for tc in (m.get("tool_calls") or []):
                fn = tc.get("function") if isinstance(tc, dict) else None
                if isinstance(fn, dict) and fn.get("name"):
                    names.add(fn["name"])
    return names


def _with_pairs(schema: list) -> list:
    have = {t["function"]["name"] for t in schema}
    recent = _recent_tool_names()
    want = set(recent)
    for n in have | recent:
        want |= set(TOOL_PAIRS.get(n, []))
    extra = [t for t in TOOLS_SCHEMA if t["function"]["name"] in want - have]
    if extra:
        print(f"[инструменты] + связанные: {sorted(t['function']['name'] for t in extra)}")
    return schema + extra


def _with_learned(schema: list) -> list:
    try:
        from core import lessons
        names = lessons.learned_tools(state.turn.get("question", ""))
    except Exception as e:
        print(f"[уроки] память инструментов недоступна: {e}")
        return schema
    have = {t["function"]["name"] for t in schema}
    extra = [t for t in TOOLS_SCHEMA if t["function"]["name"] in set(names) - have]
    if extra:
        print(f"[уроки] + инструменты из опыта: {sorted(t['function']['name'] for t in extra)}")
    return schema + extra


def _with_tool(tools: list, name: str) -> list:
    """Набор + запрошенный инструмент и его группа (если группа небольшая)."""
    have = {t.get("function", {}).get("name") for t in (tools or [])}
    wanted = {name}
    g = tool_router.group_of_tool(name)
    members = tool_router.GROUPS.get(g, set()) if g else set()
    if len(members) <= 12:
        wanted |= set(members)
    return list(tools or []) + [t for t in TOOLS_SCHEMA if t["function"]["name"] in wanted - have]


MAX_TOOLS = int(os.getenv("ATLAS_MAX_TOOLS") or 22)
_PIN_TOOLS = {"play_on_rezka", "play_on_netflix", "play_on_spotify", "play_on_youtube_music", "launch_steam_game",
              "open_deep_link", "holo_show", "holo_weather", "holo_graph", "look", "open_app", "desktop_type",
              "search_file_content", "open_found_file", "start_mission", "learn_skill", "memory_review"}


def _trim_schema(question: str, schema: list, k: int = MAX_TOOLS) -> list:
    """Не больше k инструментов по смыслу запроса; быстрые «макросы» и инструменты из опыта не выкидываются."""
    if len(schema) <= k:
        return schema
    have = [t["function"]["name"] for t in schema]
    learned = set()
    try:
        from core import lessons
        learned = set(lessons.learned_tools(question))
    except Exception:
        pass
    keep = set(tool_router.CORE) | {"execute_plan"} | ((_PIN_TOOLS | learned) & set(have))
    try:
        rank = [n for n, _ in tool_router._semantic_ranking(question, TOOLS_SCHEMA)]
    except Exception:
        return schema
    ordered = [n for n in rank if n in have and n not in keep]
    allowed = (keep & set(have)) | set(ordered[:max(0, k - len(keep & set(have)))])
    print(f"[мозг] инструментов {len(schema)} → {len(allowed)} (по смыслу запроса; важные сохранены)")
    return [t for t in schema if t["function"]["name"] in allowed]


# =============================================================================
# Ускорители: погода из памяти на 10 минут, процессор — фоновым замером
# =============================================================================
_weather_cache = {}


def _cached_weather(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        key = (tuple(a), tuple(sorted(kw.items())))
        hit = _weather_cache.get(key)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        res = fn(*a, **kw)
        if res and not _TRACE_FAIL.search(str(res)[:200]):
            _weather_cache[key] = (time.time(), res)
        return res
    return wrapper


_cpu_now = {"v": None}
_cpu_orig = get_cpu_usage


def _cpu_sampler() -> None:
    try:
        import psutil
    except ImportError:
        return
    while True:
        try:
            _cpu_now["v"] = psutil.cpu_percent(interval=2)
        except Exception:
            time.sleep(5)


def get_cpu_usage_fast() -> str:
    """Current CPU usage percentage."""
    if _cpu_now["v"] is None:
        return _cpu_orig()
    return f"CPU usage: {_cpu_now['v']:.0f}%"


get_cpu_usage_fast.__name__ = "get_cpu_usage"


def start() -> None:
    """Однократная настройка при загрузке мозга."""
    global _cpu_orig
    load_registry_skills()
    _cpu_orig = AVAILABLE_FUNCTIONS.get("get_cpu_usage") or get_cpu_usage   # навык из реестра мог её заменить
    AVAILABLE_FUNCTIONS["get_weather"] = _cached_weather(AVAILABLE_FUNCTIONS["get_weather"])
    AVAILABLE_FUNCTIONS["get_cpu_usage"] = get_cpu_usage_fast
    threading.Thread(target=_cpu_sampler, daemon=True, name="cpu-sampler").start()
