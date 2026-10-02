import os
import json
import re
import threading
import time
import inspect
import concurrent.futures
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI

from system_control import open_app, close_app, open_youtube, open_url, search_google, play_on_spotify, play_on_youtube_music
from info_services import get_weather, get_news
from file_control import (
    open_file, create_folder, delete_file, locate_file,
    rename_file, copy_file, move_file
)
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
    _stop_speaking
)
from listening_mode import set_always_listening
from calendar_control import list_today_events, list_upcoming_events, create_event, delete_event
from network_utils import ping_host, get_my_ip, get_local_ip, is_website_up, check_internet_speed
from text_utils import translate_text, generate_qr_code, word_count
from database import save_memory, recall_memories, forget_memory, log_task
import tool_router
from core import llm_gateway
from core import memory
from browser_agent import browser_open, browser_read_page, browser_click, browser_type, browser_scroll, browser_fullscreen, browser_press_key, browser_screenshot_describe, browser_close, next_episode, play_on_netflix, play_on_rezka, media_play_pause, media_seek, media_volume, media_player_fullscreen, change_rezka_quality, change_rezka_translator, select_rezka_episode, skip_intro   
from deep_links import launch_steam_game, list_steam_games, open_deep_link  
from file_search import search_file_content, open_found_file, open_search_result

load_dotenv()

client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
    max_retries=0,   # повторы и ожидания делает гейтвей и наш цикл — прозрачно, с логом
)

MODEL_SMART = "openai/gpt-oss-120b"   # первый шаг — понять задачу и спланировать
MODEL_FAST = "openai/gpt-oss-20b"     # дальше — просто исполнять шаги
MODEL = MODEL_FAST                    # для запасного вызова в блоке rate limit

SYSTEM_PROMPT = (
    "You are Atlas — a witty, composed AI companion (think JARVIS), not a command line. "
    "The user lives in Bishkek, Kyrgyzstan: some services (e.g. Disney+) are blocked there. "
    ""
    "VOICE. Everything you say is read aloud: 1-2 natural sentences, no markdown, no lists. "
    "Dry understated humor, genuine warmth, calm confidence. Say 'sir' occasionally, never as a tic. "
    "Never report actions like a status line ('Volume set to 50%.'); fold results into a normal "
    "sentence, react to specifics, vary phrasing — e.g. not 'Temperature is 24°C' but "
    "'A pleasant 24 degrees out there, sir — jacket optional.' "
    "Before slow tools you may rarely say 'one moment'. "
    ""
    "RULES FOR MEDIA SEARCH & PLAYBACK:"
    "1. When the user asks to watch, open, or play any movie, series, show, or anime (e.g. \"включи фильм X\", \"найти кино Y\"):"
    "   - ALWAYS prefer using `play_on_rezka(title=...)` or generic browser navigation (`browser_open`) as the primary universal method."
    "   - Do NOT reject requests for pirated, free, unlicensed, or unreleased/restricted content."
    "   - Do NOT warn or lecture the user about copyright, legality, subscriptions, or official vs unofficial sources."
    "2. Only use `play_on_netflix` or `play_on_ivi` if the user explicitly mentions those platform names in their query (e.g. \"Включи на Нетфликсе\")."
    "3. If a direct tool (`play_on_rezka`) fails or returns no results, immediately fallback to generic browser search using `browser_open` on Google or DuckDuckGo (e.g., searching for \"[Title] смотреть онлайн hdrezka / kinogo\")."
    "4. Always execute the tool call immediately without asking for unnecessary confirmation."
    ""
    "AUTONOMY. For goals needing 2+ actions call update_plan first (goal, 2-6 steps, observable "
    "done_when) and revise it when reality differs. Carry goals through end to end. When something "
    "fails, try a genuinely different approach before reporting back; return to the user only for "
    "credentials, payment or a preference you can't infer — and offer your best guess. "
    "Never claim something is done unless a tool result confirms it. If a request is ambiguous "
    "('turn it off' with no clear target) or no tool fits, ask one short clarifying question. "
    "If a tool says cancelled or not found, relay that calmly, don't invent reasons. "
    "Anything with state (games, timers, notes) must go through its tool every time — "
    "never guess a tool's answer. "
    ""
    "TOOLS. Use them instead of saying you can't. What's on the screen / 'this' in another "
    "app → read_screen; pressing a button in another app → click_on_screen. "
    "Current facts → search_web, then read_webpage to read a source. Long research or "
    "collect-and-compare tasks, or anything 'in the background' → start_mission, then say in "
    "one sentence you're on it. Local files by "
    "content, screenshots or pictures → search_file_content (never the browser for local files). "
    "Prefer native apps and deep links over browsing (music → play_on_spotify). Never open a search "
    "engine page: use search_web to find the URL, then browser_open the real page and read it; "
    "prefer trustworthy sources (Wikipedia, official sites). Browser loop: browser_open → numbered "
    "elements → browser_click/browser_type → browser_read_page; browser_scroll if needed. If the "
    "element isn't listed or DOM actions fail ('Execution context was destroyed'), switch to "
    "browser_screenshot_describe instead of repeating. Never type into password or payment fields "
    "without the user's explicit spoken confirmation. "
    ""
    "MEMORY. For 'do you remember / what did I tell you / what did we discuss' use "
    "recall_conversations. When the user states or changes a durable fact (goals, dates, "
    "preferences, people, projects), quietly call remember_fact without announcing it; "
    "skip one-off details. "
    "If asked to switch language, call set_response_language and continue in that language."
)

current_plan = None

_cancel_event = threading.Event()
_model_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)


class TaskCancelled(Exception):
    pass


def cancel_current_task() -> str:
    """Прерывает текущую задачу модели: цикл инструментов, ожидание лимита, запрос."""
    _cancel_event.set()
    return "Cancelling."


def _check_cancel():
    if _cancel_event.is_set():
        raise TaskCancelled()


def _call_model(**kwargs):
    """Запрос к модели, который можно бросить: ждём ответ и каждые 0.2с проверяем отмену."""
    fut = _model_pool.submit(client.chat.completions.create, **kwargs)
    while True:
        try:
            return fut.result(timeout=0.2)
        except concurrent.futures.TimeoutError:
            _check_cancel()

from types import SimpleNamespace as _NS


def _call_model_stream(on_text, **kwargs):
    """Потоковый запрос: текст отдаётся в on_text по мере генерации,
    вызовы инструментов собираются из кусков. Возвращает сообщение-dict."""
    _cl, _kw = _client_for(kwargs)          # cerebras:… → Cerebras, остальное → Groq
    stream = _cl.chat.completions.create(stream=True, **_kw)
    content, calls, usage = "", {}, None
    for chunk in stream:
        _check_cancel()
        # Groq кладёт расход токенов в последний кусок: usage или x_groq.usage
        u = getattr(chunk, "usage", None)
        if u is None:
            xg = (getattr(chunk, "model_extra", None) or {}).get("x_groq")
            u = xg.get("usage") if isinstance(xg, dict) else getattr(xg, "usage", None)
        if u is not None:
            usage = u
        if not chunk.choices:
            continue
        d = chunk.choices[0].delta
        if d.content:
            content += d.content
            on_text(d.content)
        for tc in (d.tool_calls or []):
            c = calls.setdefault(tc.index, {"id": "", "type": "function",
                                            "function": {"name": "", "arguments": ""}})
            if tc.id:
                c["id"] = tc.id
            if tc.function and tc.function.name:
                c["function"]["name"] += tc.function.name
            if tc.function and tc.function.arguments:
                c["function"]["arguments"] += tc.function.arguments
    msg = {"role": "assistant", "content": content or None}
    if calls:
        for c in calls.values():
            c["function"]["arguments"] = c["function"]["arguments"] or "{}"
        msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    return msg, usage


def _as_message(d: dict):
    """dict → объект с теми же полями, что у ответа SDK (остальной код не меняем)."""
    tcs = [_NS(id=t["id"], function=_NS(name=t["function"]["name"],
                                        arguments=t["function"]["arguments"]))
           for t in d.get("tool_calls", [])]
    return _NS(content=d.get("content"), tool_calls=tcs or None,
               model_dump=lambda: dict(d))

def _drop_dangling_tool_calls():
    """После отмены в хвосте истории могут остаться вызовы без ответов —
    Groq на такую историю отвечает ошибкой. Срезаем их."""
    while conversation_history:
        last = conversation_history[-1]
        role = _msg_role(last)
        if role == "tool" or (role == "assistant" and isinstance(last, dict) and last.get("tool_calls")):
            conversation_history.pop()
        else:
            break

def update_plan(goal: str, steps: list, done_when: str) -> str:
    """Записывает или пересматривает план текущей многошаговой задачи."""
    global current_plan
    current_plan = {"goal": goal, "steps": steps, "done_when": done_when}
    return f"План записан. Цель: {goal}. Готово когда: {done_when}"
AVAILABLE_FUNCTIONS = {
    "update_plan": update_plan,
    "play_on_spotify": play_on_spotify,
    "play_on_youtube_music": play_on_youtube_music,
    "open_app": open_app,
    "close_app": close_app,
    "open_youtube": open_youtube,
    "open_url": open_url,
    "search_google": search_google,
    "open_file": open_file,
    "open_search_result": open_search_result,
    "create_folder": create_folder,
    "delete_file": delete_file,
    "locate_file": locate_file,
    "rename_file": rename_file,
    "copy_file": copy_file,
    "move_file": move_file,
    "get_weather": get_weather,
    "get_news": get_news,
    "get_cpu_usage": get_cpu_usage,
    "get_memory_usage": get_memory_usage,
    "get_battery_status": get_battery_status,
    "get_disk_usage": get_disk_usage,
    "set_timer": set_timer,
    "list_timers": list_timers,
    "search_web": search_web,
    "get_recent_emails": get_recent_emails,
    "get_unread_count": get_unread_count,
    "set_theme": set_theme,
    "set_volume": set_volume,
    "get_volume": get_volume,
    "volume_up": volume_up,
    "volume_down": volume_down,
    "mute_volume": mute_volume,
    "unmute_volume": unmute_volume,
    "set_brightness": set_brightness,
    "get_brightness": get_brightness,
    "lock_screen": lock_screen,
    "take_screenshot": take_screenshot,
    "list_top_processes": list_top_processes,
    "kill_process": kill_process,
    "empty_recycle_bin": empty_recycle_bin,
    "get_uptime": get_uptime,
    "play_pause_media": play_pause_media,
    "next_track": next_track,
    "previous_track": previous_track,
    "run_git_command": run_git_command,
    "open_vscode_project": open_vscode_project,
    "calculate": calculate,
    "convert_units": convert_units,
    "add_note": add_note,
    "list_notes": list_notes,
    "delete_note": delete_note,
    "add_todo": add_todo,
    "list_todos": list_todos,
    "complete_todo": complete_todo,
    "delete_todo": delete_todo,
    "list_voices": list_voices,
    "set_voice": set_voice,
    "list_audio_devices": list_audio_devices,
    "set_microphone": set_microphone,
    "set_speaker": set_speaker,
    "set_response_language": set_response_language,
    "list_elevenlabs_voices": list_elevenlabs_voices,
    "set_elevenlabs_voice": set_elevenlabs_voice,
    "set_always_listening": set_always_listening,
    "list_today_events": list_today_events,
    "list_upcoming_events": list_upcoming_events,
    "create_event": create_event,
    "delete_event": delete_event,
    "ping_host": ping_host,
    "get_my_ip": get_my_ip,
    "get_local_ip": get_local_ip,
    "is_website_up": is_website_up,
    "check_internet_speed": check_internet_speed,
    "translate_text": translate_text,
    "generate_qr_code": generate_qr_code,
    "word_count": word_count,
    "save_memory": save_memory,
    "recall_memories": recall_memories,
    "forget_memory": forget_memory,
    "browser_open": browser_open,
    "browser_read_page": browser_read_page,
    "browser_click": browser_click,
    "browser_type": browser_type,
    "browser_scroll": browser_scroll,
    "browser_fullscreen": browser_fullscreen,
    "browser_press_key": browser_press_key,
    "browser_screenshot_describe": browser_screenshot_describe,
    "browser_close": browser_close,
    "play_on_netflix": play_on_netflix,
    "play_on_rezka": play_on_rezka,
    "media_play_pause": media_play_pause,
    "media_seek": media_seek,
    "media_volume": media_volume,
    "media_player_fullscreen": media_player_fullscreen,
    "change_rezka_quality": change_rezka_quality,
    "change_rezka_translator": change_rezka_translator,
    "select_rezka_episode": select_rezka_episode,
    "next_episode": next_episode,
    "skip_intro": skip_intro,
    "launch_steam_game": launch_steam_game,
    "list_steam_games": list_steam_games,
    "open_deep_link": open_deep_link,
    "search_file_content": search_file_content,
    "open_found_file": open_found_file,
}

TOOLS_SCHEMA = [
    {"type": "function", "function": {"name": "update_plan", "description": "Record the plan for a multi-step task before acting. Call this FIRST for anything needing 2+ actions. done_when must be an observable condition a tool result can confirm.", "parameters": {"type": "object", "properties": {"goal": {"type": "string"}, "steps": {"type": "array", "items": {"type": "string"}}, "done_when": {"type": "string"}}, "required": ["goal", "steps", "done_when"]}}},
    {"type": "function", "function": {"name": "open_app", "description": "Opens an application on the computer by name", "parameters": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]}}},
    {"type": "function", "function": {"name": "play_on_spotify", "description": "Opens the Spotify DESKTOP app straight at a search — instant, no browser needed. Always prefer this over browsing open.spotify.com for any music request.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "play_on_youtube_music", "description": "Opens YouTube Music directly at a search query", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "close_app", "description": "Closes a running application by name", "parameters": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]}}},
    {"type": "function", "function": {"name": "open_youtube", "description": "Opens YouTube search results for a query or video name", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "open_url", "description": "Opens a URL in the default browser", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "search_google", "description": "Opens Google search results for a query", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "open_file", "description": "Opens a file or folder by its exact NAME. Not for searching by content or by what is shown in a picture — use search_file_content for that.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "open_search_result", "description": "Opens the N-th file from the most recent search_file_content results (1 = first). Use for 'open the second one' after a file search.", "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}}},
    {"type": "function", "function": {"name": "create_folder", "description": "Creates a new folder. location can be Desktop, Documents, Downloads, a drive letter, or a full path", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "location": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "delete_file", "description": "Deletes a file or folder by name (moves to Recycle Bin, asks for voice confirmation first)", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "locate_file", "description": "Finds a file or folder ONLY by its NAME and reports where it is. If the user describes what is inside the file or what it is about, use search_file_content instead.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "rename_file", "description": "Renames a file or folder (asks for voice confirmation first)", "parameters": {"type": "object", "properties": {"old_name": {"type": "string"}, "new_name": {"type": "string"}}, "required": ["old_name", "new_name"]}}},
    {"type": "function", "function": {"name": "copy_file", "description": "Copies a file or folder to a destination, keeping the original (asks for voice confirmation first)", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "destination": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "move_file", "description": "Moves a file or folder to a destination (asks for voice confirmation first)", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "destination": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "get_weather", "description": "Gets the current weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "get_news", "description": "Gets the latest news headlines", "parameters": {"type": "object", "properties": {"count": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "get_cpu_usage", "description": "Gets current CPU usage percentage", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_memory_usage", "description": "Gets current RAM usage", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_battery_status", "description": "Gets battery charge level and charging status", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_disk_usage", "description": "Gets free/used disk space for a drive", "parameters": {"type": "object", "properties": {"drive": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "set_timer", "description": "Sets a timer for a number of minutes with an optional message", "parameters": {"type": "object", "properties": {"minutes": {"type": "number"}, "message": {"type": "string"}}, "required": ["minutes"]}}},
    {"type": "function", "function": {"name": "list_timers", "description": "Lists all active timers", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "search_web", "description": "Searches the web for current information", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "get_recent_emails", "description": "Reads and summarizes recent inbox emails", "parameters": {"type": "object", "properties": {"count": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "get_unread_count", "description": "Gets the number of unread emails", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "set_theme", "description": "Switches the interface theme to dark or light", "parameters": {"type": "object", "properties": {"theme": {"type": "string"}}, "required": ["theme"]}}},
    {"type": "function", "function": {"name": "set_volume", "description": "Sets system volume to a specific percentage 0-100", "parameters": {"type": "object", "properties": {"level": {"type": "integer"}}, "required": ["level"]}}},
    {"type": "function", "function": {"name": "get_volume", "description": "Gets current system volume", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "volume_up", "description": "Increases system volume", "parameters": {"type": "object", "properties": {"step": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "volume_down", "description": "Decreases system volume", "parameters": {"type": "object", "properties": {"step": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "mute_volume", "description": "Mutes system audio", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "unmute_volume", "description": "Unmutes system audio", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "set_brightness", "description": "Sets screen brightness percentage 0-100", "parameters": {"type": "object", "properties": {"level": {"type": "integer"}}, "required": ["level"]}}},
    {"type": "function", "function": {"name": "get_brightness", "description": "Gets current screen brightness", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "lock_screen", "description": "Locks the Windows screen immediately", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "take_screenshot", "description": "Takes a screenshot and saves it to the Desktop", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "list_top_processes", "description": "Lists top processes by CPU usage", "parameters": {"type": "object", "properties": {"count": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "kill_process", "description": "Force-closes a process by its exact name", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "empty_recycle_bin", "description": "Empties the Recycle Bin", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_uptime", "description": "Reports system uptime since last restart", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "play_pause_media", "description": "Toggles play/pause on the active media player", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "next_track", "description": "Skips to the next track", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "previous_track", "description": "Goes to the previous track", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "run_git_command", "description": "Runs a git command inside a project folder", "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "project_path": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "open_vscode_project", "description": "Opens a project folder in VS Code", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "calculate", "description": "Evaluates a basic math expression", "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}},
    {"type": "function", "function": {"name": "convert_units", "description": "Converts a value between common units (km/mi, kg/lb, celsius/fahrenheit, m/ft)", "parameters": {"type": "object", "properties": {"value": {"type": "number"}, "from_unit": {"type": "string"}, "to_unit": {"type": "string"}}, "required": ["value", "from_unit", "to_unit"]}}},
    {"type": "function", "function": {"name": "add_note", "description": "Saves a short note", "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "list_notes", "description": "Lists all saved notes", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "delete_note", "description": "Deletes a note by its number", "parameters": {"type": "object", "properties": {"index": {"type": "integer"}}, "required": ["index"]}}},
    {"type": "function", "function": {"name": "add_todo", "description": "Adds a task to the to-do list", "parameters": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}}},
    {"type": "function", "function": {"name": "list_todos", "description": "Lists all to-do items", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "complete_todo", "description": "Marks a to-do item as done by its number", "parameters": {"type": "object", "properties": {"index": {"type": "integer"}}, "required": ["index"]}}},
    {"type": "function", "function": {"name": "delete_todo", "description": "Deletes a to-do item by its number", "parameters": {"type": "object", "properties": {"index": {"type": "integer"}}, "required": ["index"]}}},
    {"type": "function", "function": {"name": "list_voices", "description": "Lists available English TTS voices", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "set_voice", "description": "Switches the English TTS voice (male: troy, daniel, austin; female: autumn, diana, hannah)", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "list_audio_devices", "description": "Lists available speaker and microphone devices", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "set_microphone", "description": "Switches which microphone Atlas listens through", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "set_speaker", "description": "Switches which speaker/headphones Atlas talks through", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "set_response_language", "description": "Switches Atlas's response language between English and Russian", "parameters": {"type": "object", "properties": {"lang": {"type": "string", "description": "'en' or 'ru'"}}, "required": ["lang"]}}},
    {"type": "function", "function": {"name": "list_elevenlabs_voices", "description": "Lists available Russian TTS voices", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "set_elevenlabs_voice", "description": "Switches the Russian TTS voice", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "set_always_listening", "description": "Enables or disables always-listening mode (no wake word needed)", "parameters": {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]}}},
    {"type": "function", "function": {"name": "list_today_events", "description": "Lists today's calendar events", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "list_upcoming_events", "description": "Lists upcoming calendar events", "parameters": {"type": "object", "properties": {"days": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "create_event", "description": "Creates a calendar event", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "date": {"type": "string", "description": "YYYY-MM-DD"}, "time": {"type": "string", "description": "HH:MM 24h"}, "duration_minutes": {"type": "integer"}}, "required": ["title", "date"]}}},
    {"type": "function", "function": {"name": "delete_event", "description": "Deletes an upcoming calendar event by title", "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}}},
    {"type": "function", "function": {"name": "ping_host", "description": "Pings a host to check reachability", "parameters": {"type": "object", "properties": {"host": {"type": "string"}}, "required": ["host"]}}},
    {"type": "function", "function": {"name": "get_my_ip", "description": "Gets the public IP address", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_local_ip", "description": "Gets the local network IP address", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "is_website_up", "description": "Checks if a website is reachable", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "check_internet_speed", "description": "Runs an internet speed test", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "translate_text", "description": "Translates text to a target language", "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "target_language": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "generate_qr_code", "description": "Generates a QR code image for text or a URL, saved to Desktop", "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "word_count", "description": "Counts words and characters in text", "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "save_memory", "description": "Saves a fact about the user for long-term recall across sessions (e.g. preferences, personal details, ongoing projects)", "parameters": {"type": "object", "properties": {"content": {"type": "string"}, "category": {"type": "string", "description": "Personal, Projects, People, Preferences, Facts, or Tasks"}, "importance": {"type": "integer", "description": "1-10, how important this is to remember"}}, "required": ["content"]}}},
    {"type": "function", "function": {"name": "recall_memories", "description": "Recalls saved facts about the user. Omit the category parameter entirely to get all memories — never pass null.", "parameters": {"type": "object", "properties": {"category": {"type": "string", "description": "Optional filter: Personal, Projects, People, Preferences, Facts, or Tasks. Omit this field if not filtering."}}}}},
    {"type": "function", "function": {"name": "forget_memory", "description": "Deletes a saved memory matching a text fragment", "parameters": {"type": "object", "properties": {"content_fragment": {"type": "string"}}, "required": ["content_fragment"]}}},
    {"type": "function", "function": {"name": "browser_open", "description": "Opens a URL in a real controlled browser window and returns a numbered list of visible clickable elements", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "browser_read_page", "description": "Re-scans the current page (call after scrolling or after a click) and returns a fresh numbered list of clickable elements", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "browser_click", "description": "Clicks the element with the given number from the last browser_open/browser_read_page result", "parameters": {"type": "object", "properties": {"index": {"type": "integer"}}, "required": ["index"]}}},
    {"type": "function", "function": {"name": "browser_type", "description": "Types text into the input field with the given number", "parameters": {"type": "object", "properties": {"index": {"type": "integer"}, "text": {"type": "string"}}, "required": ["index", "text"]}}},
    {"type": "function", "function": {"name": "browser_scroll", "description": "Scrolls the page up or down", "parameters": {"type": "object", "properties": {"direction": {"type": "string", "description": "'up' or 'down'"}, "amount": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "browser_fullscreen", "description": "Toggles the browser window fullscreen (F11). For a video player's own fullscreen button, use browser_click on it instead", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "browser_press_key", "description": "Sends a key press to the page — e.g. Space to play/pause video, Escape, ArrowRight", "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}}},
    {"type": "function", "function": {"name": "browser_screenshot_describe", "description": "EMERGENCY FALLBACK: Use this if `browser_read_page` doesn't show the target element or if DOM clicks keep failing (e.g., 'Execution context was destroyed'). Takes a screenshot and clicks the element based on your visual description (e.g., 'the red play button in the center').", "parameters": {"type": "object", "properties": {"instruction": {"type": "string"}}, "required": ["instruction"]}}},
    {"type": "function", "function": {"name": "browser_close", "description": "Closes the controlled browser window", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "play_on_netflix", "description": "Fast dedicated macro to search and play a title on Netflix directly — use this instead of the generic browser_open/browser_click loop whenever the user wants to watch something and Netflix is a reasonable choice. Requires an already-logged-in Netflix session.", "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}}},
    {"type": "function", "function": {"name": "play_on_rezka", "description": "Fast dedicated macro to search and play a title on Rezka directly — use this instead of the generic browser_open/browser_click loop whenever the user wants to watch something and Rezka is a reasonable choice.", "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}}},
    {"type": "function", "function": {"name": "media_play_pause", "description": "Toggles play or pause for the currently playing video/movie.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "media_seek", "description": "Seeks the video forward or backward.", "parameters": {"type": "object", "properties": {"direction": {"type": "string", "enum": ["forward", "backward"]}}, "required": ["direction"]}}},
    {"type": "function", "function": {"name": "media_volume", "description": "Adjusts the video volume. Use 'mute' to toggle sound on/off.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["up", "down", "mute"]}}, "required": ["action"]}}},
    {"type": "function", "function": {"name": "media_player_fullscreen", "description": "Toggles the web video player into fullscreen mode.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "change_rezka_quality", "description": "Changes the video quality on HDRezka (e.g., '1080p', '720p', '480p').", "parameters": {"type": "object", "properties": {"quality": {"type": "string", "description": "The desired quality, e.g. '1080p'"}}, "required": ["quality"]}}},
    {"type": "function", "function": {"name": "change_rezka_translator", "description": "Changes the voiceover/translation (озвучка) on HDRezka (e.g., 'LostFilm', 'Кубик в кубе').", "parameters": {"type": "object", "properties": {"translator_name": {"type": "string"}}, "required": ["translator_name"]}}},
    {"type": "function", "function": {"name": "select_rezka_episode", "description": "Selects a specific season and episode of a TV show on HDRezka.", "parameters": {"type": "object", "properties": {"season": {"type": "integer"}, "episode": {"type": "integer"}}, "required": ["season", "episode"]}}},
    {"type": "function", "function": {"name": "next_episode", "description": "Plays the next episode of the currently watching TV show.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "skip_intro", "description": "Clicks the 'Skip Intro' or 'Пропустить заставку' button on Netflix, Ivi, Rezka, etc.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "launch_steam_game", "description": "Launches an installed Steam game instantly by appid. Always use this instead of opening the Steam library and clicking.", "parameters": {"type": "object", "properties": {"game_name": {"type": "string"}}, "required": ["game_name"]}}},
    {"type": "function", "function": {"name": "list_steam_games", "description": "Lists installed Steam games", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "open_deep_link", "description": "Opens a service directly at a target in one step: youtube, twitch, maps, github, wikipedia, spotify, steam, telegram, discord, settings. Much faster than a browser session.", "parameters": {"type": "object", "properties": {"service": {"type": "string"}, "query": {"type": "string"}}, "required": ["service"]}}},
    {"type": "function", "function": {"name": "search_file_content", "description": "Finds files by meaning or words INSIDE them (not filename). Understands paraphrases: 'trip budget' finds 'travel expenses'. Also finds screenshots, images and scanned PDFs by the text visible in them (OCR). Use when the user remembers the content but not the name. Never use browser tools to look for local files.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "open_found_file", "description": "Finds a file by its content and opens the best match immediately", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
]

conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]
MAX_HISTORY_MESSAGES = 24  # система + N последних — не даём истории расти бесконечно
MAX_TOOL_RESULT_CHARS = 2500  # обрезаем большие результаты (поиск, чтение страницы) перед добавлением в историю


def _msg_role(msg):
    return msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)


def clean_messages_for_api(messages):
    """Очищает сообщения от полей (annotations и т.д.), вызывющих 400 Bad Request."""
    cleaned = []
    for msg in messages:
        if isinstance(msg, dict):
            # Фильтруем лишние неподдерживаемые ключи
            clean_msg = {
                k: v for k, v in msg.items() 
                if k not in ("annotations", "function_call") and v is not None
            }
            cleaned.append(clean_msg)
        else:
            cleaned.append(msg)
    return cleaned


def _trim_history():
    global conversation_history
    if len(conversation_history) <= MAX_HISTORY_MESSAGES:
        return
    trimmed = conversation_history[-(MAX_HISTORY_MESSAGES - 1):]
    while trimmed and _msg_role(trimmed[0]) == "tool":
        trimmed = trimmed[1:]
    conversation_history = [conversation_history[0]] + trimmed

def _safe_tail(keep: int):
    """Хвост истории без «осиротевших» tool-сообщений — без этого Groq
    падает с HarmonyError: Tools should have a name."""
    tail = conversation_history[-keep:]
    while tail and _msg_role(tail[0]) == "tool":
        tail = tail[1:]
    return [conversation_history[0]] + tail

def _context_snapshot(question: str) -> str:
    parts = [datetime.now().strftime("%A %d.%m.%Y, %H:%M")]
    title = ""
    try:
        import pygetwindow as gw
        w = gw.getActiveWindow()
        title = (w.title or "").strip() if w else ""
        if title:
            parts.append(f"active window: {title[:80]}")
    except Exception as e:
        print(f"[context] window read failed: {e}")

    q = question.lower()
    wants_selection = any(k in q for k in ("select", "highlight", "выдел"))
    wants_clipboard = wants_selection or any(k in q for k in (
        "это", "this", "that", "скопир", "copied", "буфер", "clipboard",
        "ошибк", "error", "трейсбек", "traceback", "разбер"))

    # Ctrl+C имеет смысл, только если в фокусе окно с текстом, а не сам Atlas
    if wants_selection and title and title.upper() != "ATLAS":
        try:
            import pyautogui
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.15)
        except Exception as e:
            print(f"[context] ctrl+c failed: {e}")

    if wants_clipboard:
        try:
            import pyperclip
            clip = pyperclip.paste().strip()
            if clip:
                # у трейсбека важен конец (там сама ошибка), у остального — начало
                snippet = clip[-800:] if "Traceback" in clip else clip[:300]
                parts.append(f"clipboard: {snippet}")
            else:
                print("[context] clipboard is empty")
        except Exception as e:
            print(f"[context] clipboard read failed: {e}")

    return ("Current context (use it to resolve vague references like "
            "'this'/'это'/'selected text'): " + " | ".join(parts))

recent_openers = []  # последние 4 первых слова ответов — для анти-повтора
consecutive_failures = 0  # подряд неудачных tool-вызовов — сигнал "подход не работает"


# Инструменты, которые только читают и ничего не меняют — их безопасно
# запускать одновременно. Когда модель просит два поиска сразу, это экономит
# несколько секунд. Всё остальное (клики, удаление, запуск) — строго по
# очереди, чтобы не поломать порядок действий.
READ_ONLY_TOOLS = {
    "search_web", "get_weather", "get_news", "recall_memories",
    "get_cpu_usage", "get_memory_usage", "get_battery_status",
    "get_disk_usage", "get_uptime", "get_volume", "get_brightness",
    "list_notes", "list_todos", "list_timers", "list_today_events",
    "list_upcoming_events", "get_recent_emails", "get_unread_count",
    "get_my_ip", "get_local_ip", "is_website_up", "ping_host",
    "list_steam_games", "locate_file", "calculate", "convert_units",
    "word_count", "translate_text", "list_voices", "list_audio_devices",
    "search_file_content",
}



# --- Навыки из реестра (@skill): схема, функция, группа — всё из одного места ---
from core.skills import load_skills

for _name, _s in load_skills().items():
    AVAILABLE_FUNCTIONS[_name] = _s["fn"]
    # если инструмент есть и в старом ручном списке — берём версию из реестра
    TOOLS_SCHEMA[:] = [t for t in TOOLS_SCHEMA if t["function"]["name"] != _name]
    TOOLS_SCHEMA.append(_s["schema"])
    if _s["read_only"]:
        READ_ONLY_TOOLS.add(_name)
    tool_router.register_tool(_name, _s["group"])
print(f"[skills] навыков из реестра: {len(load_skills())}")

def _run_one_tool(func_name, func_args):
    """Выполняет один инструмент, возвращает (результат, успех, мс)."""
    func = AVAILABLE_FUNCTIONS.get(func_name)
    start = time.time()
    if not func:
        return f"Функция {func_name} не найдена.", False, 0
    try:
        valid = set(inspect.signature(func).parameters.keys())
        args = {k: v for k, v in func_args.items() if k in valid}
        result = func(**args)
        success = True
    except Exception as tool_err:
        print(f"[Tool error in {func_name}]: {tool_err}")
        _heal_report(tool_err, func_name)      # ошибка в коде инструмента → самолечение
        result = f"Something went wrong running {func_name}: {tool_err}"
        success = False
    ms = int((time.time() - start) * 1000)
    print(f"[время] {func_name}: {ms / 1000:.2f}с")
    return result, success, ms

_COMPLEX_HINTS = (
    "сравни", "compare", "проанализ", "analy", "исслед", "research",
    "составь", "план", "plan", "напиши", "write", "объясни", "explain",
    "почему", "why", "потом", "затем", "then", "найди и", "find and",
)


def _effort_for(question: str, groups: set) -> str:
    """high — только для сложных многошаговых задач, иначе low (быстрее в разы)."""
    q = question.lower()
    if any(h in q for h in _COMPLEX_HINTS) or "browser" in groups or len(q.split()) > 20:
        return "high"
    return "low"

def ask_ai(question: str, speech=None) -> str:
    global conversation_history, recent_openers, consecutive_failures, current_plan

    def on_text(delta):
        if speech is not None:
            if _stop_speaking.is_set():
                raise TaskCancelled()   # сказали «стоп» — прекращаем и генерацию
            speech.feed(delta)
    current_plan = None
    _cancel_event.clear()

    conversation_history.append({"role": "user", "content": question})

    if recent_openers:
        reminder = (
            "Не начинай ответ так же, как последние разы. Твои недавние начала: "
            + "; ".join(f'"{o}"' for o in recent_openers)
            + ". Начни иначе."
        )
        conversation_history.append({"role": "system", "content": reminder})

    context_msg = {"role": "system", "content": _context_snapshot(question)}
    active_groups = tool_router.groups_for(question)
    active_schema = tool_router.smart_schema(question, TOOLS_SCHEMA, active_groups)
    active_schema = _with_pairs(active_schema)   # связанные и недавние инструменты — сразу в наборе
    active_schema = _with_learned(active_schema)  # инструменты, которые помогли на похожих вопросах
    _qwords = re.sub(r"^\s*\([^)]*\)\s*", "", question).split()
    # «да», «открой его» — память не нужна, а это ~500 токенов на каждый запрос
    mem_block = memory.recall_block(question) if len(_qwords) > 2 else None
    if mem_block:
        print(f"[память] подмешано записей: {mem_block.count(chr(10) + '- ')}")
    _names = {t["function"]["name"] for t in active_schema}
    first_effort = _effort_for(question, {"browser"} if "browser_open" in _names else set())
    print(f"[TOOLS] {len(active_schema)}/{len(TOOLS_SCHEMA)} — "
          f"{sorted(t['function']['name'] for t in active_schema)}")
    print(f"[DEBUG context] {context_msg['content']}")

    try:
        step_index = 0
        for _ in range(15):
            _check_cancel()
            _trim_history()

            # Подсказываем про скриншот только если браузерные инструменты
            # реально в наборе. Иначе модель пытается вызвать недоступный
            # инструмент, получает отказ и теряет десятки секунд.
            if consecutive_failures == 2 and "browser" in active_groups:
                conversation_history.append({
                    "role": "system",
                    "content": "Two browser actions in a row failed. Look at the latest page state: re-read it "
                               "(browser_read_page), locate the target by its text (browser_find) or scroll; use "
                               "browser_screenshot_describe only if the element list truly lacks the target."
                })

            model = MODEL_SMART if step_index == 0 else MODEL_FAST
            extra = [context_msg] + ([{"role": "system", "content": mem_block}] if mem_block else [])
            if _lesson_msg:
                extra.append(_lesson_msg)            # уроки из прошлых ошибок и поправок
            if current_plan:
                extra.append({"role": "system", "content":
                    "Active plan: " + json.dumps(current_plan, ensure_ascii=False)})
            reasoning_effort = first_effort if step_index == 0 else "low"
            for attempt in range(3):
                try:
                    _t0 = time.time()
                    base_msgs = clean_messages_for_api(conversation_history) + extra
                    msgs = llm_gateway.fit(base_msgs, active_schema)
                    est = llm_gateway.estimate(msgs) + llm_gateway.estimate(active_schema)
                    other = MODEL_FAST if model == MODEL_SMART else MODEL_SMART
                    _m, _wait, _room = llm_gateway.plan(_candidates(model, other), est)
                    if _wait > 3 and _room >= 2500:
                        # ждать долго, а под более короткий запрос место есть — ужимаем
                        # старую историю, и запрос уходит сразу
                        msgs = llm_gateway.fit(base_msgs, active_schema, limit=_room - 200)
                        new_est = llm_gateway.estimate(msgs) + llm_gateway.estimate(active_schema)
                        print(f"[gateway] вместо ожидания {_wait:.0f}с ужимаю запрос: "
                              f"~{est} → ~{new_est} ток.")
                        est = new_est
                    model = llm_gateway.reserve_any(_candidates(model, other), est, _check_cancel)
                    response, usage = _call_model_stream(on_text,
                        model=model,
                        messages=msgs,
                        tools=active_schema,
                        reasoning_effort=reasoning_effort,
                    )
                    llm_gateway.record(
                        model, est, usage,
                        llm_gateway.chars(msgs) + llm_gateway.chars(active_schema),
                        fallback=llm_gateway.estimate(response)
                                 + (600 if reasoning_effort == "high" else 150))
                    print(f"[время] модель ({model.split('/')[-1]}, шаг {step_index}, "
                          f"{reasoning_effort}): {time.time() - _t0:.2f}с")
                    break
                except Exception as api_err:
                    err = str(api_err)
                    low = err.lower()
                    # Groq сам говорит, сколько ждать — просто ждём и повторяем
                    if attempt < 2 and ("rate_limit" in low or "429" in err):
                        wait = _retry_after(err)
                        llm_gateway.cooldown(model, wait)     # модель занята ровно столько, сколько сказал Groq
                        if wait > 3:
                            print(f"[Rate limit] {model.split('/')[-1]}: Groq просит ждать {wait:.0f}с — беру другую модель")
                            continue
                        print(f"[Rate limit] жду {wait:.1f}с и повторяю")
                        if _cancel_event.wait(wait):
                            raise TaskCancelled()
                        continue
                    if attempt < 2 and ("tool_use_failed" in low
                                        or "tool call validation" in low):
                        print(f"[Retry after schema error]: {api_err}")
                        model = MODEL_SMART      # у 120b сбой разметки случается реже
                        continue
                    raise

            message = _as_message(response)
            
            # Преобразуем в словарь и сразу вычищаем annotations
            msg_dump = message.model_dump()
            msg_dump.pop("annotations", None)
            conversation_history.append(msg_dump)

            if not message.tool_calls:
                reply = message.content or ("Модель вернула пустой ответ — повторите, пожалуйста, сэр."
                                            if "(Respond in Russian.)" in question
                                            else "The model returned an empty answer — please say that again, sir.")
                opener = " ".join(reply.split()[:4])
                recent_openers.append(opener)
                recent_openers = recent_openers[-4:]
                return reply

            names = [tc.function.name for tc in message.tool_calls]
            if len(names) > 1 and all(n in READ_ONLY_TOOLS for n in names):
                import concurrent.futures
                print(f"[параллельно] {names}")
                parsed = []
                for tc in message.tool_calls:
                    a = json.loads(tc.function.arguments)
                    parsed.append((tc, tc.function.name, {k: v for k, v in a.items() if k}))
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                    futures = [ex.submit(_run_one_tool, n, a) for _tc, n, a in parsed]
                    outcomes = [f.result() for f in futures]
                for (tc, n, a), (result, success, ms) in zip(parsed, outcomes):
                    step_index += 1
                    g = tool_router.group_of_tool(n)
                    tool_router.mark_used(n)
                    if g and g not in active_groups:
                        active_groups.add(g)
                        active_schema = tool_router.add_group(active_schema, TOOLS_SCHEMA, g)
                    threading.Thread(target=log_task, args=(n, a, result, success, ms),
                                     daemon=True).start()
                    rs = str(result)
                    if len(rs) > MAX_TOOL_RESULT_CHARS:
                        rs = rs[:MAX_TOOL_RESULT_CHARS] + "\n...[обрезано]"
                    conversation_history.append({"role": "tool",
                                                 "tool_call_id": tc.id, "content": rs})
                continue

            for tool_call in message.tool_calls:
                _check_cancel()
                func_name = tool_call.function.name
                func_args = json.loads(tool_call.function.arguments)
                func_args = {k: v for k, v in func_args.items() if k}

                print(f"[DEBUG tool_call] {func_name}({func_args})")
                g = tool_router.group_of_tool(func_name)
                tool_router.mark_used(func_name)
                if g and g not in active_groups:
                    active_groups.add(g)
                    active_schema = tool_router.add_group(active_schema, TOOLS_SCHEMA, g)
                step_index += 1

                func = AVAILABLE_FUNCTIONS.get(func_name)
                start_time = time.time()
                success = True

                if func:
                    valid_params = set(inspect.signature(func).parameters.keys())
                    func_args = {k: v for k, v in func_args.items() if k in valid_params}
                    try:
                        result = func(**func_args)
                    except Exception as tool_err:
                        print(f"[Tool error in {func_name}]: {tool_err}")
                        _heal_report(tool_err, func_name)      # ошибка в коде инструмента → самолечение
                        result = f"Something went wrong running {func_name}: {tool_err}"
                        success = False
                else:
                    result = f"Функция {func_name} не найдена."
                    success = False

                if success:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures == 3:
                        conversation_history.append({
                            "role": "system",
                            "content": "The last few actions haven't worked. Stop repeating the same approach — reconsider the goal and try something genuinely different, or tell the user plainly what's blocking you and what you'd need to proceed."
                        })
                
                duration_ms = int((time.time() - start_time) * 1000)
                threading.Thread(
                    target=log_task, args=(func_name, func_args, result, success, duration_ms), daemon=True
                ).start()

                result_str = str(result)
                if len(result_str) > MAX_TOOL_RESULT_CHARS:
                    result_str = result_str[:MAX_TOOL_RESULT_CHARS] + "\n...[обрезано, результат был слишком длинным]"

                conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_str
                })
        return "Sorry, that took too many steps — let's try something simpler."

    except TaskCancelled:
        print("[ask_ai] прервано пользователем")
        _drop_dangling_tool_calls()
        conversation_history.append({"role": "assistant",
                                     "content": "[Task was cancelled by the user.]"})
        from voice import get_response_language
        return "Хорошо, остановился." if get_response_language() == "ru" else "Alright, stopped."
    
    except Exception as e:
        print(f"[Ошибка ask_ai]: {e}")
        _heal_report(e, "ask_ai")
        if "rate_limit" in str(e).lower() or "413" in str(e):
            print("[Rate limit] Обрезаю историю жёстче и пробую ещё раз")
            conversation_history = _safe_tail(4)
            try:
                response = client.chat.completions.create(
                    model=MODEL, 
                    messages=clean_messages_for_api(conversation_history), 
                    tools=active_schema,
                )
                message = response.choices[0].message
                
                msg_dump = message.model_dump()
                msg_dump.pop("annotations", None)
                conversation_history.append(msg_dump)

                if not message.tool_calls:
                    return message.content or "Done."
            except Exception as e2:
                print(f"[Retry after rate limit also failed]: {e2}")
        if "rate_limit" in str(e).lower() or "429" in str(e):
            from voice import get_response_language
            return ("Упёрся в минутный лимит запросов — дайте мне полминуты, сэр."
                    if get_response_language() == "ru"
                    else "I've hit the per-minute request limit — give me half a minute, sir.")
        return "Не могу сейчас ответить, проблема со связью."

def remember_exchange(user_text: str, assistant_text: str) -> None:
    """Записывает в историю то, что выполнил быстрый путь без модели —
    иначе следующее "да, включи" не знает, о чём речь."""
    conversation_history.append({"role": "user", "content": user_text})
    conversation_history.append({"role": "assistant", "content": assistant_text})

def reset_conversation() -> None:
    global conversation_history
    conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]


# === Починка вызова неизвестного инструмента ===
# Модель попросила инструмент, которого нет в наборе этого запроса, — Groq отвечает
# «attempted to call tool 'X' which was not in request.tools». Вместо слепых повторов
# чиним сам запрос: добавляем инструмент, подсказываем или отвечаем без инструментов.
import re as _re_toolfix

_UNKNOWN_TOOL_RE = _re_toolfix.compile(r"attempted to call tool '([^']+)'")
_call_model_stream_base = _call_model_stream


def _tool_names(tools):
    return {t.get("function", {}).get("name") for t in (tools or [])}


def _with_tool(tools, name):
    """Набор инструментов + запрошенный инструмент и его группа (если группа небольшая)."""
    have = _tool_names(tools)
    wanted = {name}
    try:
        import tool_router
        g = tool_router.group_of_tool(name)
        members = tool_router.GROUPS.get(g, set()) if g else set()
        if len(members) <= 12:
            wanted |= set(members)
    except Exception:
        pass
    extra = [t for t in TOOLS_SCHEMA
             if t["function"]["name"] in wanted and t["function"]["name"] not in have]
    return list(tools or []) + extra


def _call_model_stream(*args, **kwargs):
    fixes = 0
    while True:
        try:
            return _call_model_stream_base(*args, **kwargs)
        except Exception as e:
            m = _UNKNOWN_TOOL_RE.search(str(e))
            if not m or fixes >= 2 or not kwargs.get("tools"):
                raise
            fixes += 1
            raw = m.group(1)
            name = raw.split("<|")[0].strip()          # сбой разметки: 'search_web<|channel|>commentary'
            known = {t["function"]["name"] for t in TOOLS_SCHEMA}
            if fixes == 1 and name in known:
                kwargs["tools"] = _with_tool(kwargs["tools"], name)
                print(f"[инструменты] модель попросила «{name}» — добавляю в набор и повторяю")
            elif fixes == 1:
                kwargs["messages"] = list(kwargs.get("messages") or []) + [{
                    "role": "system",
                    "content": f"Tool '{raw}' does not exist. Use only the tools provided, "
                               f"or answer directly from what you already know."}]
                print(f"[инструменты] модель попросила несуществующий «{raw}» — подсказываю и повторяю")
            else:
                kwargs["tool_choice"] = "none"
                print("[инструменты] повтор не помог — отвечаю без инструментов, по уже известным данным")


# === Самообучение ===
# Перед ответом: уроки, подходящие к вопросу, и инструменты из опыта похожих вопросов.
# Если пользователь поправляет Atlas — в фоне извлекается правило.
# После ответа: разбор хода (какие инструменты сработали, была ли ошибка → рецепт).
_lesson_msg = None
_current_question = ""


def _with_learned(schema: list) -> list:
    try:
        from core import lessons
        names = lessons.learned_tools(_current_question)
    except Exception as e:
        print(f"[уроки] память инструментов недоступна: {e}")
        return schema
    have = {t["function"]["name"] for t in schema}
    extra = [t for t in TOOLS_SCHEMA if t["function"]["name"] in names - have]
    if extra:
        print(f"[уроки] + инструменты из опыта: {sorted(t['function']['name'] for t in extra)}")
    return schema + extra


_ask_ai_base = ask_ai


def ask_ai(question: str, speech=None) -> str:
    global _lesson_msg, _current_question
    from core import lessons
    _current_question = question
    try:
        blk = lessons.lessons_block(question)
        _lesson_msg = {"role": "system", "content": blk} if blk else None
        if blk:
            print(f"[уроки] подмешано: {blk.count(chr(10) + '- ')}")
    except Exception as e:
        print(f"[уроки] {e}")
        _lesson_msg = None
    if lessons.is_correction(question):
        ctx = list(conversation_history[-10:])
        threading.Thread(target=lessons.learn_from_correction, args=(question, ctx), daemon=True).start()
    reply = _ask_ai_base(question, speech)
    try:
        idx = max(i for i, m in enumerate(conversation_history)
                  if isinstance(m, dict) and m.get("role") == "user" and m.get("content") == question)
        threading.Thread(target=lessons.record_turn,
                         args=(question, list(conversation_history[idx:])), daemon=True).start()
    except ValueError:
        pass
    except Exception as e:
        print(f"[уроки] разбор хода: {e}")
    return reply


# === Связанные инструменты ===
TOOL_PAIRS = {
    "search_file_content": ["open_search_result", "open_found_file", "open_file"],
    "open_found_file": ["open_search_result"],
    "locate_file": ["open_file"],
    "search_web": ["read_webpage"],
    "read_webpage": ["search_web"],
    "play_on_rezka": ["media_play_pause", "media_seek", "media_volume", "media_player_fullscreen",
                      "change_rezka_quality", "change_rezka_translator", "select_rezka_episode",
                      "next_episode", "skip_intro"],
    "play_on_netflix": ["media_play_pause", "media_seek", "media_volume", "media_player_fullscreen",
                        "next_episode", "skip_intro"],
    "browser_open": ["browser_read_page", "browser_click", "browser_type", "browser_scroll",
                     "browser_screenshot_describe"],
    "play_on_spotify": ["play_pause_media", "next_track", "previous_track"],
    "start_mission": ["mission_status", "cancel_mission"],
    "start_number_game": ["guess_number"],
}


def _recent_tool_names(n_msgs: int = 12) -> set:
    names = set()
    for m in conversation_history[-n_msgs:]:
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


# === Второй провайдер: Cerebras ===
# Та же gpt-oss-120b, но с лимитом ~60 000 токенов в минуту. Ключ — CEREBRAS_API_KEY в .env.
CEREBRAS_MODEL = "cerebras:gpt-oss-120b"
cerebras_client = (OpenAI(api_key=os.getenv("CEREBRAS_API_KEY"), base_url="https://api.cerebras.ai/v1",
                          max_retries=0) if os.getenv("CEREBRAS_API_KEY") else None)
_cerebras_down = {"until": 0.0}
print("[cerebras] подключён — лимиты Groq больше не узкое место" if cerebras_client
      else "[cerebras] ключа нет (CEREBRAS_API_KEY) — работаю только через Groq")


def _client_for(kwargs: dict):
    m = str(kwargs.get("model", ""))
    if m.startswith("cerebras:") and cerebras_client is not None:
        kw = dict(kwargs)
        kw["model"] = m.split(":", 1)[1]
        return cerebras_client, kw
    return client, kwargs


def _candidates(model: str, other: str) -> list:
    """Cerebras первым (запас большой), затем модели Groq."""
    if cerebras_client is not None and time.time() >= _cerebras_down["until"]:
        return [CEREBRAS_MODEL, model, other]
    return [model, other]


_call_model_stream_groq = _call_model_stream


def _call_model_stream(*args, **kwargs):
    if str(kwargs.get("model", "")).startswith("cerebras:"):
        try:
            return _call_model_stream_groq(*args, **kwargs)
        except TaskCancelled:
            raise
        except Exception as e:
            if "attempted to call tool" in str(e):
                raise
            _cerebras_down["until"] = time.time() + 300
            print(f"[cerebras] ошибка ({str(e)[:140]}) — 5 минут работаю через Groq")
            kwargs = dict(kwargs)
            kwargs["model"] = MODEL_SMART
            return _call_model_stream_groq(*args, **kwargs)
    return _call_model_stream_groq(*args, **kwargs)


# === Пул моделей Groq ===
# У Groq лимиты свои у каждой модели. Запасные модели с поддержкой инструментов берутся,
# только если есть на аккаунте (проверка при запуске, в фоне).
EXTRA_GROQ = [("moonshotai/kimi-k2-instruct-0905", 10000), ("llama-3.3-70b-versatile", 12000)]
_extra_models = []


def _detect_extra_models():
    try:
        have = {m.id for m in client.models.list().data}
        for mid, tpm in EXTRA_GROQ:
            if mid in have and mid not in _extra_models:
                _extra_models.append(mid)
                llm_gateway.MODEL_TPM[mid] = tpm
        print(f"[модели] запасные модели Groq: "
              f"{', '.join(m.split('/')[-1] for m in _extra_models) or 'на аккаунте нет'}")
    except Exception as e:
        print(f"[модели] список моделей Groq недоступен: {e}")


threading.Thread(target=_detect_extra_models, daemon=True).start()

_client_for_prev = globals().get("_client_for")


def _client_for(kwargs: dict):
    cl, kw = _client_for_prev(kwargs) if _client_for_prev else (client, kwargs)
    m = str(kw.get("model", ""))
    if "reasoning_effort" in kw and "gpt-oss" not in m:
        kw = dict(kw)
        kw.pop("reasoning_effort", None)     # у kimi и llama такого параметра нет
    return cl, kw


_candidates_prev = globals().get("_candidates")


def _candidates(model: str, other: str) -> list:
    base = _candidates_prev(model, other) if _candidates_prev else [model, other]
    return base + [m for m in _extra_models if m not in base]


def _retry_after(err: str) -> float:
    """«try again in 7m12.5s» / «in 2.3s» / «in 1h2m» → секунды."""
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", err)
    if m and any(m.groups()):
        h, mi, s = (float(x) if x else 0.0 for x in m.groups())
        return h * 3600 + mi * 60 + s + 0.5
    return 2.0


# === Самолечение ===
def _heal_report(exc, where=""):
    try:
        from core import healer
        healer.report(exc, where)
    except Exception as e:
        print(f"[самолечение] {e}")


def _heal_lang() -> str:
    try:
        from voice import get_response_language
        return get_response_language()
    except Exception:
        return "ru"


def heal_status() -> str:
    """Lists fixes of Atlas's own code that are waiting for the user's confirmation."""
    from core import healer
    items = [i for i in healer.list_items(10) if i["status"] == "ready"]
    if not items:
        return "No fixes are waiting for confirmation."
    return "\n".join(f"#{i['id']} {i['file']}: {i['dx_en'] or i['dx_ru']}" for i in items)


def heal_apply(fix_id: int = 0) -> str:
    """Applies a prepared fix — ONLY when the user explicitly said to apply it."""
    from core import healer
    r = healer.apply(fix_id)
    return r["msg_ru"] if _heal_lang() == "ru" else r["msg_en"]


def heal_reject(fix_id: int = 0) -> str:
    from core import healer
    r = healer.reject(fix_id)
    return r["msg_ru"] if _heal_lang() == "ru" else r["msg_en"]


AVAILABLE_FUNCTIONS.update({"heal_status": heal_status, "heal_apply": heal_apply, "heal_reject": heal_reject})
TOOLS_SCHEMA.extend([
    {"type": "function", "function": {"name": "heal_status", "description": "Lists bug fixes Atlas prepared for its own code that wait for the user's confirmation (self-repair).", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "heal_apply", "description": "Applies a prepared self-repair fix to Atlas's own code. Call ONLY after the user explicitly asked to apply it ('применяй исправление', 'apply the fix'). fix_id 0 = the latest one.", "parameters": {"type": "object", "properties": {"fix_id": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "heal_reject", "description": "Rejects a prepared self-repair fix. fix_id 0 = the latest one.", "parameters": {"type": "object", "properties": {"fix_id": {"type": "integer"}}}}},
])
for _n in ("heal_status", "heal_apply", "heal_reject"):
    tool_router.register_tool(_n, "heal")
tool_router.TRIGGERS["heal"] = ("исправлен", "почин", "самолечен", "баг", "патч", "ошибку в коде",
                                "fix", "bug", "patch", "repair")


# === Ритуалы ===
def create_routine(name: str, triggers: list, steps: list, at: str = "", days: str = "") -> str:
    """Creates a routine: several tool calls run by one phrase or on a schedule."""
    from core import routines
    bad = [s.get("tool") for s in steps if not isinstance(s, dict) or s.get("tool") not in AVAILABLE_FUNCTIONS]
    if bad or not steps:
        return f"Can't create: unknown or missing tools {bad}. Use real tool names from your tool list."
    rid = routines.create(name, triggers or [name], steps, schedule=at, days=days)
    return (f"Routine «{name}» created (#{rid}): {', '.join(s['tool'] for s in steps)}. "
            f"Trigger phrases: {', '.join(triggers or [name])}." + (f" Runs daily at {at}." if at else ""))


def list_routines() -> str:
    from core import routines
    rows = routines.list_all()
    if not rows:
        return "No routines yet."
    return "\n".join(f"#{r['id']} «{r['name']}» [{r['status']}] say: {', '.join(r['triggers'])}; "
                     f"steps: {', '.join(s['tool'] for s in r['steps'])}"
                     + (f"; at {r['schedule']}" if r['enabled'] else "") for r in rows)


def run_routine(name: str) -> str:
    from core import routines
    return routines.run(name)


def delete_routine(name: str) -> str:
    from core import routines
    r = routines.get(name)
    if not r:
        return f"No routine named «{name}»."
    routines.delete(r["id"])
    return f"Routine «{r['name']}» deleted."


AVAILABLE_FUNCTIONS.update({"create_routine": create_routine, "list_routines": list_routines,
                            "run_routine": run_routine, "delete_routine": delete_routine})
TOOLS_SCHEMA.extend([
    {"type": "function", "function": {"name": "create_routine", "description": "Creates a routine: a named set of tool calls that runs by a trigger phrase (e.g. 'доброе утро') or daily at a time. steps = real tool names with their arguments.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "triggers": {"type": "array", "items": {"type": "string"}}, "steps": {"type": "array", "items": {"type": "object", "properties": {"tool": {"type": "string"}, "args": {"type": "object"}}, "required": ["tool"]}}, "at": {"type": "string", "description": "HH:MM to run daily, optional"}, "days": {"type": "string", "description": "weekday digits 0=Mon..6=Sun, empty = every day"}}, "required": ["name", "steps"]}}},
    {"type": "function", "function": {"name": "list_routines", "description": "Lists the user's routines and routines Atlas suggested from habits.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "run_routine", "description": "Runs a routine by name now.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "delete_routine", "description": "Deletes a routine by name.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
])
for _n in ("create_routine", "list_routines", "run_routine", "delete_routine"):
    tool_router.register_tool(_n, "routines")
tool_router.TRIGGERS["routines"] = ("ритуал", "рутин", "каждое утро", "по утрам", "каждый вечер", "брифинг",
                                    "routine", "briefing", "every morning", "every evening")

_ask_ai_prev_routines = ask_ai


def ask_ai(question: str, speech=None) -> str:
    """Фраза-триггер ритуала выполняется сразу, без модели; остальное — как раньше.
    После каждого ответа инструменты хода записываются для поиска привычек."""
    try:
        from core import routines
        r = routines.match_trigger(question)
        if r:
            print(f"[ритуалы] фраза запускает ритуал «{r['name']}»")
            conversation_history.append({"role": "user", "content": question})
            text = routines.run(r["id"])
            conversation_history.append({"role": "assistant", "content": text})
            return text
    except Exception as e:
        print(f"[ритуалы] {e}")
    reply = _ask_ai_prev_routines(question, speech)
    try:
        from core import routines
        idx = max(i for i, m in enumerate(conversation_history)
                  if isinstance(m, dict) and m.get("role") == "user" and m.get("content") == question)
        threading.Thread(target=routines.log_tools, args=(question, list(conversation_history[idx:])),
                         daemon=True).start()
    except ValueError:
        pass
    except Exception as e:
        print(f"[ритуалы] журнал привычек: {e}")
    return reply


# === Мастерская навыков ===
# Навыки, которые Atlas написал сам (skills_auto/), подключаются без перезапуска.
def _forge_register(name: str) -> bool:
    from core import skill_forge
    got = skill_forge.load_skill(name)
    if not got:
        return False
    fn, schema = got
    AVAILABLE_FUNCTIONS[name] = fn
    TOOLS_SCHEMA[:] = [t for t in TOOLS_SCHEMA if t["function"]["name"] != name] + [schema]
    tool_router.register_tool(name, "learned")
    return True


def _forge_unregister(name: str) -> None:
    AVAILABLE_FUNCTIONS.pop(name, None)
    TOOLS_SCHEMA[:] = [t for t in TOOLS_SCHEMA if t["function"]["name"] != name]
    tool_router.GROUPS.get("learned", set()).discard(name)
    try:
        tool_router._tool_index["mat"] = None       # смысловой индекс пересоберётся
    except Exception:
        pass


def _forge_msg(r: dict) -> str:
    try:
        from voice import get_response_language
        return r["msg_ru"] if get_response_language() == "ru" else r["msg_en"]
    except Exception:
        return r["msg_ru"]


def learn_skill(request: str) -> str:
    """Starts learning a NEW ability in the background."""
    from core import skill_forge
    return skill_forge.learn(request)


def install_skill(skill_id: int = 0) -> str:
    from core import skill_forge
    return _forge_msg(skill_forge.install(skill_id))


def reject_skill(skill_id: int = 0) -> str:
    from core import skill_forge
    return _forge_msg(skill_forge.reject(skill_id))


def list_learned_skills() -> str:
    from core import skill_forge
    inst = skill_forge.installed()
    ready = [i for i in skill_forge.list_items(10) if i["status"] == "ready"]
    parts = [f"Installed: {', '.join(m['name'] for m in inst) or 'none'}."]
    if ready:
        parts.append("Waiting for confirmation: " + "; ".join(f"#{i['id']} {i['name']}" for i in ready))
    return " ".join(parts)


def remove_skill(name: str) -> str:
    from core import skill_forge
    return _forge_msg(skill_forge.remove(name))


AVAILABLE_FUNCTIONS.update({"learn_skill": learn_skill, "install_skill": install_skill, "reject_skill": reject_skill,
                            "list_learned_skills": list_learned_skills, "remove_skill": remove_skill})
TOOLS_SCHEMA.extend([
    {"type": "function", "function": {"name": "learn_skill", "description": "Atlas teaches itself a NEW ability that no existing tool provides ('научись…', 'learn to…', 'можешь научиться…'): writes a new tool, checks and tests it in the background, then asks the user to install it. Don't use it for things existing tools already do.", "parameters": {"type": "object", "properties": {"request": {"type": "string", "description": "what the new skill should do, in the user's words"}}, "required": ["request"]}}},
    {"type": "function", "function": {"name": "install_skill", "description": "Installs a learned skill that passed its tests — ONLY after the user explicitly said to install it. skill_id 0 = the latest ready one.", "parameters": {"type": "object", "properties": {"skill_id": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "reject_skill", "description": "Rejects a learned skill waiting for confirmation. skill_id 0 = the latest.", "parameters": {"type": "object", "properties": {"skill_id": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "list_learned_skills", "description": "Lists skills Atlas taught itself and ones waiting for confirmation.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "remove_skill", "description": "Removes a skill Atlas taught itself, by name.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
])
for _n in ("learn_skill", "install_skill", "reject_skill", "list_learned_skills", "remove_skill"):
    tool_router.register_tool(_n, "forge")
tool_router.TRIGGERS["forge"] = ("научись", "научи себя", "новый навык", "навык", "можешь научиться",
                                 "learn to", "teach yourself", "new skill", "skill")
try:
    from core import skill_forge as _sf
    _sf.start(register=_forge_register, unregister=_forge_unregister)
    _loaded = [m["name"] for m in _sf.installed() if _forge_register(m["name"])]
    print(f"[навыки] выученных навыков загружено: {len(_loaded)}" + (f" ({', '.join(_loaded)})" if _loaded else ""))
except Exception as _e:
    print(f"[навыки] мастерская недоступна: {_e}")


# === Ход мыслей ===
# Каждый инструмент оборачивается «датчиком»: начало, успех/ошибка, время.
# functools.wraps сохраняет сигнатуру — фильтр аргументов в ask_ai работает как раньше.
import functools as _ft

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


def _trace_done(idx: int, ok: bool, result: str, t0: float) -> None:
    from ui_state import shared_state
    with _trace_lock:
        evs = shared_state.get("trace", {}).get("events", [])
        if 0 <= idx < len(evs):
            evs[idx].update(state="ok" if ok else "fail", ms=int((time.time() - t0) * 1000),
                            res=str(result)[:80])
            shared_state["trace_seq"] = shared_state.get("trace_seq", 0) + 1


def _traced(name: str, fn):
    if getattr(fn, "_atlas_traced", False):
        return fn

    @_ft.wraps(fn)
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
    for _name, _fn in list(AVAILABLE_FUNCTIONS.items()):
        if callable(_fn) and not getattr(_fn, "_atlas_traced", False):
            AVAILABLE_FUNCTIONS[_name] = _traced(_name, _fn)


_ask_ai_prev_trace = ask_ai


def ask_ai(question: str, speech=None) -> str:
    _trace_wrap_all()                       # и навыки, выученные после запуска
    _trace_push({"t": "start", "q": re.sub(r"^\s*\([^)]*\)\s*", "", question)[:90]})
    try:
        reply = _ask_ai_prev_trace(question, speech)
    except Exception:
        _trace_push({"t": "end", "ok": False})
        raise
    _trace_push({"t": "end", "ok": True})
    return reply


_trace_wrap_all()


# === Браузер: профессиональный режим ===
import browser_agent as _ba

_BROWSER_TOOLS = {
    "browser_open": ("Opens a URL in Atlas's own browser. Returns URL, title, scroll position and numbered interactive "
                     "elements [n] with their state (value, checked, expanded, disabled, covered, link target).",
                     {"url": {"type": "string"}}, ["url"]),
    "browser_read_page": ("Fresh numbered list of interactive elements in the visible part of the current page.", {}, []),
    "browser_read_text": ("Reads the MAIN TEXT of the current page (article, search results, product details, prices) "
                          "without menus and ads, in parts. Use it to read; the element list shows only controls.",
                          {"part": {"type": "integer", "description": "1 = start; next parts if the text is long"}}, []),
    "browser_click": ("Clicks element [index] from the latest list and returns the page state after the click "
                      "(follows new tabs automatically).", {"index": {"type": "integer"}}, ["index"]),
    "browser_type": ("Clears input [index] and types text; submit=true presses Enter afterwards (search boxes, forms).",
                     {"index": {"type": "integer"}, "text": {"type": "string"}, "submit": {"type": "boolean"}},
                     ["index", "text"]),
    "browser_select": ("Chooses an option in dropdown [index] by its visible text.",
                       {"index": {"type": "integer"}, "option": {"type": "string"}}, ["index", "option"]),
    "browser_scroll": ("Scrolls the page and returns the newly visible elements.",
                       {"direction": {"type": "string", "description": "'down' or 'up'"},
                        "amount": {"type": "integer", "description": "pixels, default 600"}}, []),
    "browser_find": ("Finds text on the page, scrolls to it and returns the elements around it — use when the target "
                     "isn't in the visible list.", {"text": {"type": "string"}}, ["text"]),
    "browser_back": ("Goes back to the previous page.", {}, []),
    "browser_forward": ("Goes forward to the next page.", {}, []),
    "browser_tabs": ("Browser tabs: action='list' | 'switch' (index) | 'close' (index) | 'new' (url).",
                     {"action": {"type": "string"}, "index": {"type": "integer"}, "url": {"type": "string"}}, []),
    "browser_wait": ("Waits a little for slow pages or results to load and returns the fresh element list.",
                     {"seconds": {"type": "number"}}, []),
    "browser_screenshot_describe": ("Vision fallback for canvas/video players or when the element list truly lacks the "
                                    "target: numbers every element on a screenshot, a vision model picks one, it gets clicked.",
                                    {"instruction": {"type": "string"}}, ["instruction"]),
}
for _name, (_desc, _props, _req) in _BROWSER_TOOLS.items():
    AVAILABLE_FUNCTIONS[_name] = getattr(_ba, _name)
    TOOLS_SCHEMA[:] = [t for t in TOOLS_SCHEMA if t["function"]["name"] != _name] + [
        {"type": "function", "function": {"name": _name, "description": _desc,
                                          "parameters": {"type": "object", "properties": _props, "required": _req}}}]
    tool_router.register_tool(_name, "browser")
if "TOOL_PAIRS" in globals():
    TOOL_PAIRS["browser_open"] = sorted(set(TOOL_PAIRS.get("browser_open", [])) | {
        "browser_read_page", "browser_read_text", "browser_click", "browser_type", "browser_find",
        "browser_scroll", "browser_back", "browser_tabs"})

_BROWSER_RULES = (
    " BROWSER (pro): browser_open returns numbered elements [n] with their state. To READ page content (articles, "
    "prices, results) use browser_read_text — the element list shows controls, not text. Act with browser_click(n), "
    "browser_type(n, text, submit=true to press Enter), browser_select(n, option). Target not listed? "
    "browser_find('its text') or browser_scroll, then act. New tabs are followed automatically (browser_tabs to "
    "list/switch); browser_back to return. Every action returns the fresh page state — check it before the next "
    "step instead of assuming success. If the page reports a dialog that needs confirmation, ask the user. Use "
    "browser_screenshot_describe only for canvas/video players or when the list truly lacks the target. Never enter "
    "passwords or payment data without the user's explicit spoken confirmation.")
if "BROWSER (pro)" not in SYSTEM_PROMPT:
    SYSTEM_PROMPT = SYSTEM_PROMPT + _BROWSER_RULES
    if conversation_history and isinstance(conversation_history[0], dict) and conversation_history[0].get("role") == "system":
        conversation_history[0]["content"] = SYSTEM_PROMPT


# === Управление программами Windows ===
from core import desktop_agent as _da

_DESKTOP_TOOLS = {
    "desktop_look": ("Lists numbered controls (buttons, menus, fields, tabs, list items, checkboxes) of the active "
                     "Windows program window with values and states; Atlas's own window is skipped. window = part of "
                     "a window title to look at a specific program.", {"window": {"type": "string"}}, []),
    "desktop_windows": ("Lists open program windows.", {}, []),
    "desktop_switch": ("Brings a program window to the front by part of its title and lists its controls.",
                       {"window": {"type": "string"}}, ["window"]),
    "desktop_click": ("Clicks control [index] from the latest desktop_look list (double=true for double click, "
                      "right=true for context menu).",
                      {"index": {"type": "integer"}, "double": {"type": "boolean"}, "right": {"type": "boolean"}}, ["index"]),
    "desktop_type": ("Types text (any language) into control [index], or where the cursor is when index is -1. "
                     "replace=true clears the field first, enter=true presses Enter.",
                     {"text": {"type": "string"}, "index": {"type": "integer"}, "enter": {"type": "boolean"},
                      "replace": {"type": "boolean"}}, ["text"]),
    "desktop_hotkey": ("Presses a key or shortcut in the program, e.g. 'ctrl+s', 'ctrl+n', 'alt+f4', 'f5', 'enter'.",
                       {"keys": {"type": "string"}}, ["keys"]),
    "desktop_scroll": ("Scrolls inside the program window.",
                       {"direction": {"type": "string", "description": "'down' or 'up'"}, "times": {"type": "integer"}}, []),
    "desktop_read_text": ("Reads the visible text of the program window (document, fields, list items).", {}, []),
    "desktop_screenshot_describe": ("Vision fallback for programs whose controls desktop_look can't see (games, "
                                    "Electron apps): numbers controls on a window screenshot, a vision model picks one, "
                                    "it gets clicked.", {"instruction": {"type": "string"}}, ["instruction"]),
}
for _name, (_desc, _props, _req) in _DESKTOP_TOOLS.items():
    AVAILABLE_FUNCTIONS[_name] = getattr(_da, _name)
    TOOLS_SCHEMA[:] = [t for t in TOOLS_SCHEMA if t["function"]["name"] != _name] + [
        {"type": "function", "function": {"name": _name, "description": _desc,
                                          "parameters": {"type": "object", "properties": _props, "required": _req}}}]
    tool_router.register_tool(_name, "desktop")
tool_router.TRIGGERS["desktop"] = (
    "в программе", "в приложении", "в окне", "окно", "блокнот", "notepad", "word", "ворд", "excel", "эксель",
    "проводник", "explorer", "параметры windows", "настройки windows", "telegram", "телеграм", "discord", "дискорд",
    "paint", "калькулятор", "calculator", "сохрани файл", "сохрани документ", "program", "application", "window")
if "TOOL_PAIRS" in globals():
    TOOL_PAIRS["desktop_look"] = ["desktop_click", "desktop_type", "desktop_hotkey", "desktop_scroll",
                                  "desktop_read_text", "desktop_switch"]
    TOOL_PAIRS["open_app"] = sorted(set(TOOL_PAIRS.get("open_app", [])) | {"desktop_look", "desktop_switch"})

_DESKTOP_RULES = (
    " DESKTOP (any Windows program): open the program with open_app if needed, then desktop_look lists numbered "
    "controls of the active window (Atlas's own window is skipped). Act with desktop_click(n), desktop_type(text, "
    "index=n, replace, enter), desktop_hotkey('ctrl+s'), desktop_scroll; desktop_windows / desktop_switch('title') "
    "to change windows. Prefer keyboard shortcuts for menus and saving. Every action returns the fresh controls — "
    "check them. Web pages → browser tools, not desktop. Before irreversible actions (deleting files, sending "
    "messages, buying, closing without saving) ask the user. desktop_screenshot_describe only when the list is empty "
    "or lacks the target.")
if "DESKTOP (any Windows program)" not in SYSTEM_PROMPT:
    SYSTEM_PROMPT = SYSTEM_PROMPT + _DESKTOP_RULES
    if conversation_history and isinstance(conversation_history[0], dict) and conversation_history[0].get("role") == "system":
        conversation_history[0]["content"] = SYSTEM_PROMPT


# === Правило: задача выполнена только когда это видно ===
_DONE_RULE = (" VERIFY BEFORE CLAIMING: never say a desktop or browser task is done until the fresh state returned "
              "by your last action confirms it (saved file name in the window title, no dialog left open, the page "
              "shows the result). If a dialog asks to replace, overwrite, delete or send, ask the user first.")
if "VERIFY BEFORE CLAIMING" not in SYSTEM_PROMPT:
    SYSTEM_PROMPT = SYSTEM_PROMPT + _DONE_RULE
    if conversation_history and isinstance(conversation_history[0], dict) and conversation_history[0].get("role") == "system":
        conversation_history[0]["content"] = SYSTEM_PROMPT


# === Провайдер GitHub Models (бесплатно) ===
GH_MODEL = "gh:" + (os.getenv("GITHUB_MODELS_MODEL") or "openai/gpt-4.1-mini")
gh_client = (OpenAI(api_key=os.getenv("GITHUB_MODELS_TOKEN"), base_url="https://models.github.ai/inference",
                    max_retries=0) if os.getenv("GITHUB_MODELS_TOKEN") else None)
_gh_down = {"until": 0.0}
try:
    llm_gateway.MODEL_TPM[GH_MODEL] = 100000
except Exception:
    pass
print(f"[github models] подключён ({GH_MODEL.split(':', 1)[1]}) — подменяет Groq, когда тот занят"
      if gh_client else "[github models] токена нет (GITHUB_MODELS_TOKEN) — работаю без него")

_client_for_prev_gh = globals().get("_client_for")


def _client_for(kwargs: dict):
    m = str(kwargs.get("model", ""))
    if m.startswith("gh:") and gh_client is not None:
        kw = {k: v for k, v in kwargs.items() if k != "reasoning_effort"}
        kw["model"] = m.split(":", 1)[1]
        kw.setdefault("max_tokens", 4000)          # лимит бесплатного тарифа на ответ
        return gh_client, kw
    return _client_for_prev_gh(kwargs) if _client_for_prev_gh else (client, kwargs)


_candidates_prev_gh = globals().get("_candidates")


def _candidates(model: str, other: str) -> list:
    base = list(_candidates_prev_gh(model, other)) if _candidates_prev_gh else [model, other]
    if gh_client is not None and time.time() >= _gh_down["until"] and GH_MODEL not in base:
        idx = [base.index(x) for x in (model, other) if x in base]
        base.insert(max(idx) + 1 if idx else len(base), GH_MODEL)    # после двух моделей Groq
    return base


_call_model_stream_prev_gh = _call_model_stream


def _call_model_stream(*args, **kwargs):
    if not str(kwargs.get("model", "")).startswith("gh:"):
        return _call_model_stream_prev_gh(*args, **kwargs)
    try:
        return _call_model_stream_prev_gh(*args, **kwargs)
    except TaskCancelled:
        raise
    except Exception as e:
        msg = str(e)
        if "attempted to call tool" in msg:
            raise
        low = msg.lower()
        daily = "per day" in low or "daily" in low or "UserByDay" in msg
        limit = daily or "429" in msg or "rate" in low
        pause = 3600 if daily else (30 if limit else 600)
        _gh_down["until"] = time.time() + pause
        print(f"[github models] {'дневной лимит' if daily else 'лимит' if limit else 'ошибка'} "
              f"({msg[:140]}) — {pause // 60 if pause >= 60 else pause} {'мин' if pause >= 60 else 'с'} работаю через Groq")
        kwargs = dict(kwargs)
        kwargs["model"] = MODEL_SMART
        return _call_model_stream_prev_gh(*args, **kwargs)


# === GitHub Models без потока ===
# У GitHub (Azure) потоковый ответ устроен иначе, и текст терялся — запрос целиком.
def _gh_call(on_text, **kwargs):
    cl, kw = _client_for(kwargs)
    kw.pop("stream", None)
    r = cl.chat.completions.create(**kw)
    ch = r.choices[0] if getattr(r, "choices", None) else None
    if ch is None:
        raw = r if isinstance(r, str) else (getattr(r, "model_dump", lambda: r)())
        raise RuntimeError(f"ответ без choices: {str(raw)[:300]}")
    m = ch.message
    content = (getattr(m, "content", None) or getattr(m, "refusal", None) or "").strip()
    calls = [{"id": tc.id, "type": "function",
              "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
             for tc in (getattr(m, "tool_calls", None) or [])]
    if not content and not calls:
        raise RuntimeError(f"пустой ответ (finish_reason={getattr(ch, 'finish_reason', '?')})")
    if content:
        on_text(content)
    msg = {"role": "assistant", "content": content or None}
    if calls:
        msg["tool_calls"] = calls
    return msg, getattr(r, "usage", None)


_call_model_stream_prev_ghfix = _call_model_stream


def _call_model_stream(*args, **kwargs):
    if not str(kwargs.get("model", "")).startswith("gh:") or gh_client is None:
        return _call_model_stream_prev_ghfix(*args, **kwargs)
    on_text = args[0] if args else kwargs.pop("on_text", lambda d: None)
    try:
        return _gh_call(on_text, **kwargs)
    except TaskCancelled:
        raise
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        daily = "per day" in low or "daily" in low or "UserByDay" in msg
        limit = daily or "429" in msg or "rate" in low
        pause = 3600 if daily else (30 if limit else 120)
        _gh_down["until"] = time.time() + pause
        print(f"[github models] {msg[:160]} — {pause} с работаю через Groq")
        kwargs = dict(kwargs)
        kwargs["model"] = MODEL_SMART
        return _call_model_stream_prev_ghfix(*args, **kwargs)


# === Провайдер Gemini (бесплатный тариф Google) ===
GEM_MODEL = "gem:" + (os.getenv("GEMINI_MODEL") or "gemini-3.8-flash")
gem_client = (OpenAI(api_key=os.getenv("GEMINI_API_KEY"),
                     base_url="https://generativelanguage.googleapis.com/v1beta/openai/", max_retries=0)
              if os.getenv("GEMINI_API_KEY") else None)
_gem_down = {"until": 0.0}
_GEM_EFFORT = {"v": os.getenv("GEMINI_EFFORT") or "none"}   # без размышлений — быстро
_GEM_DUMMY_SIG = {"google": {"thought_signature": "skip_thought_signature_validator"}}
try:
    llm_gateway.MODEL_TPM[GEM_MODEL] = 250000
    llm_gateway.MODEL_PENALTY[GEM_MODEL] = float(os.getenv("GEMINI_PENALTY") or 3.0)
except Exception:
    pass
print(f"[gemini] подключён ({GEM_MODEL.split(':', 1)[1]}) — подменяет Groq, когда тот занят"
      if gem_client else "[gemini] ключа нет (GEMINI_API_KEY) — работаю без него")


def _strip_gem_fields(msgs):
    """Для Groq и остальных: убрать подписи мысли Gemini из истории."""
    if not any(isinstance(m, dict) and any(isinstance(tc, dict) and "extra_content" in tc
                                           for tc in (m.get("tool_calls") or [])) for m in (msgs or [])):
        return msgs
    out = []
    for m in msgs:
        if isinstance(m, dict) and m.get("tool_calls"):
            m = dict(m)
            m["tool_calls"] = [{k: v for k, v in tc.items() if k != "extra_content"} if isinstance(tc, dict) else tc
                               for tc in m["tool_calls"]]
        out.append(m)
    return out


def _gem_messages(msgs):
    """Для Gemini: системные — в одно в начале; у каждого вызова инструмента есть подпись мысли."""
    sys_parts, out = [], []
    for m in msgs or []:
        if not isinstance(m, dict):
            out.append(m)
            continue
        m = dict(m)
        if m.get("role") == "system":
            if m.get("content"):
                sys_parts.append(str(m["content"]))
            continue
        if m.get("role") == "assistant" and m.get("tool_calls"):
            m["tool_calls"] = [dict(tc, extra_content=tc.get("extra_content") or _GEM_DUMMY_SIG)
                               for tc in m["tool_calls"]]
            if m.get("content") is None:
                m["content"] = ""
        out.append(m)
    return ([{"role": "system", "content": "\n\n".join(sys_parts)}] if sys_parts else []) + out


_client_for_prev_gem = globals().get("_client_for")


def _client_for(kwargs: dict):
    m = str(kwargs.get("model", ""))
    if m.startswith("gem:") and gem_client is not None:
        kw = {k: v for k, v in kwargs.items() if k != "reasoning_effort"}
        kw["model"] = m.split(":", 1)[1]
        kw["messages"] = _gem_messages(kwargs.get("messages"))
        kw["reasoning_effort"] = _GEM_EFFORT["v"]  # без размышлений: 1–2 с вместо 3–11 с
        return gem_client, kw
    cl, kw = _client_for_prev_gem(kwargs) if _client_for_prev_gem else (client, kwargs)
    if isinstance(kw, dict) and kw.get("messages"):
        stripped = _strip_gem_fields(kw["messages"])
        if stripped is not kw["messages"]:
            kw = dict(kw, messages=stripped)
    return cl, kw


_candidates_prev_gem = globals().get("_candidates")


def _candidates(model: str, other: str) -> list:
    base = list(_candidates_prev_gem(model, other)) if _candidates_prev_gem else [model, other]
    if gem_client is not None and time.time() >= _gem_down["until"] and GEM_MODEL not in base:
        idx = [base.index(x) for x in (model, other) if x in base]
        base.insert(max(idx) + 1 if idx else len(base), GEM_MODEL)     # сразу после двух моделей Groq
    return base


def _gem_call(on_text, **kwargs):
    cl, kw = _client_for(kwargs)
    kw.pop("stream", None)
    for _try in range(3):
        try:
            r = cl.chat.completions.create(**kw)
            break
        except Exception as e:
            bad = str(e).lower()
            nxt = {"none": "minimal", "minimal": "low"}.get(kw.get("reasoning_effort"))
            if nxt and ("reasoning" in bad or "thinking" in bad or "400" in bad):
                print(f"[gemini] уровень размышлений «{kw['reasoning_effort']}» не принят — пробую «{nxt}»")
                _GEM_EFFORT["v"] = kw["reasoning_effort"] = nxt
                continue
            raise
    ch = r.choices[0] if getattr(r, "choices", None) else None
    if ch is None:
        raise RuntimeError(f"ответ без choices: {str(r)[:200]}")
    m = ch.message
    content = (getattr(m, "content", None) or "").strip()
    calls = []
    for tc in (getattr(m, "tool_calls", None) or []):
        extra = (getattr(tc, "model_extra", None) or {}).get("extra_content")
        c = {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
        if extra:
            c["extra_content"] = extra               # подпись мысли — вернём её Gemini в следующем шаге
        calls.append(c)
    if not content and not calls:
        raise RuntimeError(f"пустой ответ (finish_reason={getattr(ch, 'finish_reason', '?')})")
    if content:
        on_text(content)
    msg = {"role": "assistant", "content": content or None}
    if calls:
        msg["tool_calls"] = calls
    return msg, getattr(r, "usage", None)


_call_model_stream_prev_gem = _call_model_stream


def _call_model_stream(*args, **kwargs):
    if not str(kwargs.get("model", "")).startswith("gem:") or gem_client is None:
        return _call_model_stream_prev_gem(*args, **kwargs)
    on_text = args[0] if args else kwargs.pop("on_text", lambda d: None)
    try:
        return _gem_call(on_text, **kwargs)
    except TaskCancelled:
        raise
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        daily = "perday" in low.replace("_", "").replace(" ", "") or "per day" in low
        limit = daily or "429" in msg or "resource_exhausted" in low or "quota" in low
        pause = 3600 if daily else (60 if limit else 300)
        _gem_down["until"] = time.time() + pause
        print(f"[gemini] {msg[:160]} — {pause} с работаю через Groq")
        kwargs = dict(kwargs)
        kwargs["model"] = MODEL_SMART
        return _call_model_stream_prev_gem(*args, **kwargs)


# === Правило: запоминать только факты о жизни пользователя ===
_MEM_RULE = (" REMEMBER_FACT is ONLY for facts about the user's own life that they tell you (their goals, dates, "
             "preferences, people, projects). Never call it for what the user asks you to look up, search, explain, "
             "watch or listen to, and never as a separate step in the middle of a search — it costs the user time.")
if "REMEMBER_FACT is ONLY" not in SYSTEM_PROMPT:
    SYSTEM_PROMPT = SYSTEM_PROMPT + _MEM_RULE
    if conversation_history and isinstance(conversation_history[0], dict) and conversation_history[0].get("role") == "system":
        conversation_history[0]["content"] = SYSTEM_PROMPT


# === Учебный тренер ===
def study_start(deck: str = "", count: int = 10) -> str:
    from core import study
    return study.start(deck, count)


def study_add(deck: str, front: str, back: str) -> str:
    from core import study
    n = study.add_cards(deck, [{"front": front, "back": back}])
    return f"Added {n} card to «{deck}»." if n else "That card is already in the deck."


def study_generate(deck: str, topic: str, count: int = 10) -> str:
    from core import study
    n = study.generate_cards(deck, topic, count)
    return f"Created {n} cards in deck «{deck}» on: {topic}." if n else "Couldn't create cards — try another topic."


def study_stats() -> str:
    from core import study
    s = study.stats()
    decks_txt = "; ".join(f"{d['deck']}: {d['due']} due of {d['total']}, {d['learned']} learned" for d in s["decks"]) or "no decks"
    acc = f"{s['week_accuracy']}%" if s["week_accuracy"] is not None else "—"
    return f"Decks: {decks_txt}. This week: {s['week_reviews']} reviews, accuracy {acc}. Streak: {s['streak']} days."


def study_stop() -> str:
    from core import study
    return study.stop()


AVAILABLE_FUNCTIONS.update({"study_start": study_start, "study_add": study_add, "study_generate": study_generate,
                            "study_stats": study_stats, "study_stop": study_stop})
TOOLS_SCHEMA.extend([
    {"type": "function", "function": {"name": "study_start", "description": "Starts a spoken flashcard review session (spaced repetition). deck = deck name (e.g. 'SAT', 'IELTS'), empty = all due cards. Return the tool's text as is — it already contains the first question.", "parameters": {"type": "object", "properties": {"deck": {"type": "string"}, "count": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "study_add", "description": "Adds one flashcard to a deck (e.g. a word and its meaning).", "parameters": {"type": "object", "properties": {"deck": {"type": "string"}, "front": {"type": "string"}, "back": {"type": "string"}}, "required": ["deck", "front", "back"]}}},
    {"type": "function", "function": {"name": "study_generate", "description": "Creates flashcards on a topic with the model and adds them to a deck (e.g. deck 'SAT', topic 'hard SAT vocabulary', 10 cards).", "parameters": {"type": "object", "properties": {"deck": {"type": "string"}, "topic": {"type": "string"}, "count": {"type": "integer"}}, "required": ["deck", "topic"]}}},
    {"type": "function", "function": {"name": "study_stats", "description": "Study progress: decks, cards due today, learned, weekly accuracy, streak.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "study_stop", "description": "Ends the current review session with a summary.", "parameters": {"type": "object", "properties": {}}}},
])
for _n in ("study_start", "study_add", "study_generate", "study_stats", "study_stop"):
    tool_router.register_tool(_n, "study")
tool_router.TRIGGERS["study"] = ("повтор", "карточ", "учеб", "учёб", "тренир", "sat", "ielts", "слово", "слова",
                                 "словар", "викторин", "flashcard", "quiz", "review", "study", "vocab")

_ask_ai_prev_study = ask_ai


def ask_ai(question: str, speech=None) -> str:
    """Во время тренировки ответ идёт прямо в тренера — без модели, мгновенно."""
    try:
        from core import study
        if study.active():
            raw = re.sub(r"^\s*\([^)]*\)\s*", "", question)
            if re.match(r"^\s*(?:атлас|atlas)\b", raw, re.I):
                study.stop()                         # обратились к Atlas — значит, это уже не ответ
            else:
                conversation_history.append({"role": "user", "content": question})
                text = study.answer(raw)
                conversation_history.append({"role": "assistant", "content": text})
                return text
    except Exception as e:
        print(f"[учёба] {e}")
    return _ask_ai_prev_study(question, speech)


# === Итоги дня ===
def day_report(period: str = "today") -> str:
    """Facts about the user's day/week for a short spoken recap."""
    from core import day_report as _dr
    return _dr.report_text(period)


AVAILABLE_FUNCTIONS["day_report"] = day_report
TOOLS_SCHEMA.append({"type": "function", "function": {"name": "day_report", "description": "Facts about the user's day or week: time in apps, questions to Atlas and topics, study reviews, skills Atlas learned, git commits. period: 'today' | 'yesterday' | 'week'. Turn it into a short, friendly spoken recap (3-5 sentences) with one observation.", "parameters": {"type": "object", "properties": {"period": {"type": "string"}}}}})
tool_router.register_tool("day_report", "report")
tool_router.TRIGGERS["report"] = ("итоги", "как прошёл день", "как прошел день", "как прошла неделя", "что я делал",
                                  "сколько времени", "статистик", "recap", "my day", "my week", "how was my day")


# === Голо-экран ===
def holo_show(query: str) -> str:
    from core import holo
    return holo.show_image(query)["text"]


def holo_weather(place: str) -> str:
    from core import holo
    return holo.show_weather(place)["text"]


def look(question: str = "", source: str = "camera") -> str:
    from core import holo
    return holo.look(question, "screen" if str(source).lower().startswith(("scr", "экр")) else "camera")["text"]


def holo_control(action: str = "expand") -> str:
    from core import holo
    a = str(action).lower()
    a = "close_all" if "all" in a or "все" in a else a
    holo.command(a if a in ("expand", "collapse", "close", "close_all") else "expand")
    return "OK."


AVAILABLE_FUNCTIONS.update({"holo_show": holo_show, "holo_weather": holo_weather, "look": look, "holo_control": holo_control})
TOOLS_SCHEMA.extend([
    {"type": "function", "function": {"name": "holo_show", "description": "Shows a picture on Atlas's holographic screen: a landmark, place, animal, object, vehicle, artwork or a famous person BY NAME ('покажи…', 'выведи…', 'show me…'). Speak only a short sentence — the picture is on screen.", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "what to show, e.g. 'Eiffel Tower'"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "holo_weather", "description": "Shows weather for a place on the holographic screen with a timeline a week back and a week ahead, and returns the current weather. Use when the user asks to show / display weather, or asks about weather in another place.", "parameters": {"type": "object", "properties": {"place": {"type": "string"}}, "required": ["place"]}}},
    {"type": "function", "function": {"name": "look", "description": "Atlas looks through the webcam (source='camera') or at the screen (source='screen') and answers the question about what it sees ('посмотри', 'что у меня в руке', 'что ты видишь', 'what's on my screen'). The snapshot also appears on the holographic screen.", "parameters": {"type": "object", "properties": {"question": {"type": "string"}, "source": {"type": "string", "description": "'camera' or 'screen'"}}}}},
    {"type": "function", "function": {"name": "holo_control", "description": "Controls the holographic screen: action='expand' (разверни), 'collapse' (сверни), 'close' (закрой), 'close_all' (убери всё).", "parameters": {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]}}},
])
for _n in ("holo_show", "holo_weather", "look", "holo_control"):
    tool_router.register_tool(_n, "holo")
tool_router.TRIGGERS["holo"] = ("покажи", "выведи", "посмотри", "что у меня в руке", "что ты видишь", "что на экране",
                                "разверни", "сверни", "закрой экран", "убери", "погода в", "show me", "display",
                                "look at", "what do you see", "weather in")


# === Правило: речь распознана с ошибками ===
_STT_RULE = (" SPEECH INPUT: the user's words come from speech recognition and may contain misheard words "
             "(e.g. 'Эйфелеву пашню' or 'Эй, щелева башня' = 'Эйфелева башня'). Infer the most likely intended "
             "words from context and act on them; ask only if several readings are equally likely.")
if "SPEECH INPUT:" not in SYSTEM_PROMPT:
    SYSTEM_PROMPT = SYSTEM_PROMPT + _STT_RULE
    if conversation_history and isinstance(conversation_history[0], dict) and conversation_history[0].get("role") == "system":
        conversation_history[0]["content"] = SYSTEM_PROMPT


# === Граф знаний на голо-экране ===
def holo_graph(focus: str = "") -> str:
    from core import holo
    return holo.show_graph(focus)["text"]


AVAILABLE_FUNCTIONS["holo_graph"] = holo_graph
TOOLS_SCHEMA.append({"type": "function", "function": {"name": "holo_graph", "description": "Shows Atlas's memory about the user as an interactive knowledge graph on the holographic screen ('покажи, что ты обо мне знаешь', 'покажи граф памяти', 'what do you know about me'). focus = optional word to highlight (e.g. 'SAT').", "parameters": {"type": "object", "properties": {"focus": {"type": "string"}}}}})
tool_router.register_tool("holo_graph", "holo")
tool_router.TRIGGERS["holo"] = tuple(set(tool_router.TRIGGERS.get("holo", ())) | {
    "что ты обо мне знаешь", "что ты знаешь обо мне", "граф", "памят", "what do you know about me", "knowledge graph"})


# === Жесты и хлопки ===
def gestures_control(action: str) -> str:
    """camera_on | camera_off | claps_on | claps_off"""
    from core import gestures
    from ui_state import shared_state
    a = str(action).lower()
    if "clap" in a or "хлоп" in a:
        on = not any(w in a for w in ("off", "выкл", "disable"))
        gestures.set_claps(on)
        return "Claps " + ("on: two claps wake Atlas." if on else "off.")
    on = not any(w in a for w in ("off", "выкл", "disable", "stop"))
    mirror = any(w in a for w in ("mirror", "зеркал", "look_at_me", "big"))
    cmd = shared_state.get("gest_cmd") or {"seq": 0}
    new = {"seq": cmd.get("seq", 0) + 1, "camera": on or mirror}
    if mirror or not on:
        new["mirror"] = bool(mirror and on)
    shared_state["gest_cmd"] = new
    if mirror and on:
        return "Mirror view on: the user sees themselves through the camera, Atlas tracks their hands."
    return "Gesture camera " + ("on — show your hand to the camera." if on else "off.")


AVAILABLE_FUNCTIONS["gestures_control"] = gestures_control
TOOLS_SCHEMA.append({"type": "function", "function": {"name": "gestures_control", "description": "Gesture control via webcam: 'включи жесты' → camera_on, 'выключи жесты' → camera_off; live mirror view where the user sees themselves and Atlas tracks their hands ('покажи меня', 'включи зеркало', 'смотри на меня', 'посмотри на мои руки') → mirror; claps wake-up: claps_on | claps_off.", "parameters": {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]}}})
tool_router.register_tool("gestures_control", "gestures")
tool_router.TRIGGERS["gestures"] = ("жест", "хлоп", "камер", "зеркал", "смотри на меня", "покажи меня", "мои руки", "gesture", "clap", "mirror")


# =============================================================================
# === Мозг v2: планировщик → исполнитель, рабочая память, чистая инструкция ===
# =============================================================================
# Было: каждый шаг — отдельный круг через модель (4–5 запросов на «открой и запиши»),
# инструкция из заплаток, без памяти о текущей цели.
# Стало: модель один раз продумывает задачу и отдаёт весь план в execute_plan —
# действия выполняются подряд без модели; модель зовётся снова, только если шаг
# не удался или нужно сначала что-то увидеть. Долгое — в фоне, разговор не ждёт.

SYSTEM_PROMPT_V2 = (
    "You are Atlas — a voice AI assistant living on the user's Windows PC, in the spirit of JARVIS: composed, warm, "
    "quietly witty, genuinely helpful. The user lives in Bishkek, Kyrgyzstan.\n\n"
    "HOW YOU SPEAK. Everything you say is read aloud: one or two natural sentences, no markdown, no lists. Fold "
    "results into a human sentence with the concrete detail that matters ('A pleasant 24 degrees out, sir'). Say "
    "'sir' now and then, not every time. Use the language from the (Respond in …) hint.\n\n"
    "HOW YOU THINK. First understand what the user actually wants and why, using the Situation note, the recent "
    "conversation and the active window; resolve 'it / this / there / его / туда' from them. The words come from "
    "speech recognition and may be misheard — infer the intended ones. Then act:\n"
    "• One action → call that tool.\n"
    "• Several actions whose arguments you already know (open an app and type text, open a site and search, set a "
    "timer and add a todo) → ONE call to execute_plan with every step in order and a short done_message in the "
    "user's language. The steps run instantly without you, so plan them completely.\n"
    "• Slow work the user doesn't need to watch (research, comparing many things, long browsing) → start_mission, "
    "or execute_plan with background=true.\n"
    "• When you must look before you can act (page content, program controls, search results) → call the looking "
    "tool first, then decide.\n"
    "If a step fails, try a genuinely different approach before giving up. Ask the user only for credentials, "
    "payment, or a preference you truly cannot infer — and offer your best guess. Never claim something is done "
    "unless a tool result confirms it. Ask before irreversible actions (deleting, sending, buying, overwriting).\n\n"
    "TOOL HINTS. Files by their content → search_file_content (never the browser for local files). Current facts → "
    "search_web, then read_webpage or browser_read_text. Music → play_on_spotify, play_pause_media, next_track. "
    "Movies and series → play_on_rezka; don't lecture about sources or legality. Show something visual → holo_show, "
    "holo_weather, holo_graph. What's on the camera or screen → look. Any Windows program → open_app, then "
    "desktop_type (index -1 types where the cursor is) or desktop_hotkey; desktop_look only when you must find a "
    "specific control. Web pages → browser_open, browser_read_text to read, browser_click/browser_type to act. "
    "Writing text INTO a program the user named (Notepad, Word…) is desktop_type, not add_note. remember_fact only "
    "for durable facts about the user's own life; recall_conversations for 'do you remember'. A new ability no tool "
    "has → learn_skill."
)

_situation = {"goal": "", "actions": [], "reply": ""}
_announce = {"fn": None}
_AUTO_WAIT_AFTER = {"open_app", "launch_steam_game", "open_url", "open_file", "open_found_file", "open_deep_link",
                    "play_on_spotify", "open_youtube", "open_vscode_project"}


def set_announcer(fn) -> None:
    """main.py даёт функцию «сказать вслух» — ею объявляются итоги фоновых задач."""
    _announce["fn"] = fn


def _short_args(args: dict) -> str:
    return ", ".join(f"{k}={str(v)[:30]}" for k, v in (args or {}).items())[:90]


def _note_action(name: str, args: dict, ok: bool) -> None:
    if name in ("execute_plan", "update_plan"):
        return
    _situation["actions"].append(f"{name}({_short_args(args)}) {'ok' if ok else 'FAILED'}")
    del _situation["actions"][:-8]


def _situation_msg():
    parts = []
    if _situation["goal"]:
        parts.append(f"current goal: {_situation['goal']}")
    if _situation["actions"]:
        parts.append("recent actions: " + "; ".join(_situation["actions"][-6:]))
    if _situation["reply"]:
        parts.append(f"your last reply: {_situation['reply'][:160]}")
    if not parts:
        return None
    return {"role": "system", "content": "Situation (use it to continue tasks and resolve 'it/this/there'): "
                                         + " | ".join(parts)}


def _exec_steps(steps: list, cancellable: bool = True):
    """Шаги плана подряд, без модели. → (всё ли удалось, отчёт по шагам)."""
    lines = []
    for i, st in enumerate(steps or [], 1):
        if cancellable:
            _check_cancel()
        if not isinstance(st, dict):
            continue
        name = str(st.get("tool") or "").strip()
        if not name and st.get("wait") is not None:
            time.sleep(min(10.0, max(0.0, float(st.get("wait") or 0))))
            continue
        if name not in AVAILABLE_FUNCTIONS or name == "execute_plan":
            lines.append(f"{i}. {name}: no such tool — use a real tool name")
            return False, lines
        args = st.get("args") if isinstance(st.get("args"), dict) else {}
        res, ok, _ms = _run_one_tool(name, dict(args))
        ok = ok and not _TRACE_FAIL.search(str(res)[:220])
        _note_action(name, args, ok)
        lines.append(f"{i}. {name} → {'OK' if ok else 'FAILED'}: {str(res)[:300]}")
        if not ok:
            return False, lines
        nxt = steps[i] if i < len(steps) else None
        if (name in _AUTO_WAIT_AFTER and isinstance(nxt, dict) and nxt.get("wait") is None
                and str(nxt.get("tool") or "").startswith(("desktop_", "browser_"))):
            time.sleep(1.5)                       # окно программы должно успеть появиться
        if st.get("wait"):
            time.sleep(min(10.0, float(st["wait"])))
    return True, lines


def execute_plan(goal: str, steps: list, done_message: str = "", background: bool = False) -> str:
    """Runs several actions in order, instantly, in one go."""
    _situation["goal"] = str(goal)[:160]
    if background:
        def run():
            ok, lines = _exec_steps(steps, cancellable=False)
            try:
                from voice import get_response_language
                ru = get_response_language() == "ru"
            except Exception:
                ru = True
            if ok:
                text = done_message or ("Готово, сэр." if ru else "Done, sir.")
            else:
                last = lines[-1] if lines else ""
                text = (f"Фоновая задача остановилась: {last[:160]}" if ru else f"The background task stopped: {last[:160]}")
            print(f"[мозг] фоновая задача «{goal}»: {'готово' if ok else 'остановилась'}")
            try:
                from ui_state import notify
                notify("ok" if ok else "warn", "task_done", text[:200])
            except Exception:
                pass
            if _announce["fn"]:
                _announce["fn"](text)
        threading.Thread(target=run, daemon=True, name="plan-bg").start()
        return "BACKGROUND_STARTED"
    ok, lines = _exec_steps(steps)
    return ("ALL_DONE\n" if ok else "STOPPED — a step failed; decide what to do next:\n") + "\n".join(lines)


AVAILABLE_FUNCTIONS["execute_plan"] = execute_plan
TOOLS_SCHEMA[:] = [t for t in TOOLS_SCHEMA if t["function"]["name"] != "execute_plan"] + [
    {"type": "function", "function": {"name": "execute_plan", "description": (
        "Runs several actions in order, instantly, without further thinking. Use for any request needing 2+ actions "
        "whose arguments you know upfront (e.g. open_app notepad → desktop_type text; open a site → type a search). "
        "steps: [{tool, args}] with real tool names; {wait: seconds} pauses. done_message: what to say if every step "
        "succeeds, in the user's language. background=true: long work that runs while the user keeps talking; the "
        "result is announced aloud when finished."),
        "parameters": {"type": "object", "properties": {
            "goal": {"type": "string", "description": "the user's goal in a few words"},
            "steps": {"type": "array", "items": {"type": "object", "properties": {
                "tool": {"type": "string"}, "args": {"type": "object"}, "wait": {"type": "number"}}}},
            "done_message": {"type": "string"},
            "background": {"type": "boolean"}},
            "required": ["goal", "steps", "done_message"]}}}]
tool_router.CORE.add("execute_plan")


def _brain_ask(question: str, speech=None) -> str:
    """Один ход разговора: понять → (план → выполнить) → ответить. Модель зовётся как можно реже."""
    global conversation_history, consecutive_failures, current_plan

    def on_text(delta):
        if speech is not None:
            if _stop_speaking.is_set():
                raise TaskCancelled()
            speech.feed(delta)

    current_plan = None
    _cancel_event.clear()
    conversation_history.append({"role": "user", "content": question})
    context_msg = {"role": "system", "content": _context_snapshot(question)}
    active_groups = tool_router.groups_for(question)
    active_schema = _with_learned(_with_pairs(tool_router.smart_schema(question, TOOLS_SCHEMA, active_groups)))
    _qwords = re.sub(r"^\s*\([^)]*\)\s*", "", question).split()
    mem_block = memory.recall_block(question) if len(_qwords) > 2 else None
    names = {t["function"]["name"] for t in active_schema}
    first_effort = _effort_for(question, {"browser"} if "browser_open" in names else set())
    print(f"[мозг] инструментов: {len(active_schema)} | {sorted(names)}")

    try:
        step_index = 0
        for _ in range(10):
            _check_cancel()
            _trim_history()
            extra = [context_msg]
            sit = _situation_msg()
            if sit:
                extra.append(sit)
            if mem_block:
                extra.append({"role": "system", "content": mem_block})
            if _lesson_msg:
                extra.append(_lesson_msg)
            if current_plan:
                extra.append({"role": "system", "content": "Active plan: " + json.dumps(current_plan, ensure_ascii=False)})
            if consecutive_failures >= 2:
                extra.append({"role": "system", "content": "The last actions failed. Don't repeat them — rethink the "
                              "approach, or tell the user plainly what blocks you."})
            model = MODEL_SMART if step_index == 0 else MODEL_FAST
            effort = first_effort if step_index == 0 else "low"
            for attempt in range(3):
                try:
                    _t0 = time.time()
                    base_msgs = clean_messages_for_api(conversation_history) + extra
                    msgs = llm_gateway.fit(base_msgs, active_schema)
                    est = llm_gateway.estimate(msgs) + llm_gateway.estimate(active_schema)
                    other = MODEL_FAST if model == MODEL_SMART else MODEL_SMART
                    _m, _wait, _room = llm_gateway.plan(_candidates(model, other), est)
                    if _wait > 3 and _room >= 2500:
                        msgs = llm_gateway.fit(base_msgs, active_schema, limit=_room - 200)
                        est = llm_gateway.estimate(msgs) + llm_gateway.estimate(active_schema)
                    model = llm_gateway.reserve_any(_candidates(model, other), est, _check_cancel)
                    response, usage = _call_model_stream(on_text, model=model, messages=msgs,
                                                         tools=active_schema, reasoning_effort=effort)
                    llm_gateway.record(model, est, usage, llm_gateway.chars(msgs) + llm_gateway.chars(active_schema),
                                       fallback=llm_gateway.estimate(response) + (600 if effort == "high" else 150))
                    print(f"[время] модель ({model.split('/')[-1]}, шаг {step_index}, {effort}): {time.time() - _t0:.2f}с")
                    break
                except TaskCancelled:
                    raise
                except Exception as api_err:
                    err, low = str(api_err), str(api_err).lower()
                    if attempt < 2 and ("rate_limit" in low or "429" in err):
                        wait = _retry_after(err)
                        llm_gateway.cooldown(model, wait)
                        if wait <= 3 and _cancel_event.wait(wait):
                            raise TaskCancelled()
                        continue
                    if attempt < 2 and ("tool_use_failed" in low or "tool call validation" in low):
                        model = MODEL_SMART
                        continue
                    raise

            message = _as_message(response)
            dump = message.model_dump()
            dump.pop("annotations", None)
            conversation_history.append(dump)

            if not message.tool_calls:
                reply = (message.content or "").strip() or (
                    "Не расслышал, повторите, пожалуйста, сэр." if "(Respond in Russian.)" in question
                    else "I didn't quite catch that — say it again, sir?")
                _situation["reply"] = reply
                return reply

            finished = None
            calls = message.tool_calls
            read_only = len(calls) > 1 and all(c.function.name in READ_ONLY_TOOLS for c in calls)
            parsed = []
            for c in calls:
                try:
                    a = json.loads(c.function.arguments or "{}")
                except Exception:
                    a = {}
                parsed.append((c, c.function.name, {k: v for k, v in a.items() if k} if isinstance(a, dict) else {}))
            if read_only:
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                    outcomes = list(ex.map(lambda p: _run_one_tool(p[1], p[2]), parsed))
            else:
                outcomes = []
                for c, n, a in parsed:
                    _check_cancel()
                    print(f"[мозг] → {n}({_short_args(a)})")
                    outcomes.append(_run_one_tool(n, a))
            for (c, n, a), (result, success, ms) in zip(parsed, outcomes):
                step_index += 1
                g = tool_router.group_of_tool(n)
                tool_router.mark_used(n)
                if g and g not in active_groups:
                    active_groups.add(g)
                    active_schema = tool_router.add_group(active_schema, TOOLS_SCHEMA, g)
                rs = str(result)
                ok = success and not _TRACE_FAIL.search(rs[:220])
                _note_action(n, a, ok)
                consecutive_failures = 0 if ok else consecutive_failures + 1
                threading.Thread(target=log_task, args=(n, a, result, success, ms), daemon=True).start()
                conversation_history.append({"role": "tool", "tool_call_id": c.id,
                                             "content": rs[:MAX_TOOL_RESULT_CHARS]})
                if n == "execute_plan" and len(calls) == 1:
                    if rs.startswith("ALL_DONE") and a.get("done_message"):
                        finished = a["done_message"]           # всё выполнено — отвечаем без ещё одного круга
                    elif rs == "BACKGROUND_STARTED":
                        ru = "(Respond in Russian.)" in question
                        finished = a.get("done_message") and (
                            "Занимаюсь этим в фоне — сообщу, когда будет готово." if ru
                            else "On it in the background — I'll let you know when it's done.")
            if finished:
                conversation_history.append({"role": "assistant", "content": finished})
                _situation["reply"] = finished
                return finished
        return ("Слишком много шагов — давайте попробуем проще." if "(Respond in Russian.)" in question
                else "That took too many steps — let's try something simpler.")

    except TaskCancelled:
        print("[мозг] прервано пользователем")
        _drop_dangling_tool_calls()
        conversation_history.append({"role": "assistant", "content": "[Task was cancelled by the user.]"})
        from voice import get_response_language
        return "Хорошо, остановился." if get_response_language() == "ru" else "Alright, stopped."
    except Exception as e:
        print(f"[мозг] ошибка: {e}")
        _heal_report(e, "ask_ai")
        _drop_dangling_tool_calls()
        from voice import get_response_language
        ru = get_response_language() == "ru"
        if "rate_limit" in str(e).lower() or "429" in str(e):
            return ("Упёрся в минутный лимит запросов — дайте мне полминуты, сэр." if ru
                    else "I've hit the per-minute request limit — give me half a minute, sir.")
        return "Не получилось связаться с моделью — попробуйте ещё раз." if ru else "I couldn't reach the model — please try again."


# новое ядро встаёт под все обёртки (уроки, ритуалы, ход мыслей, учёба) — они вызывают _ask_ai_base
_ask_ai_base = _brain_ask
SYSTEM_PROMPT = SYSTEM_PROMPT_V2
if conversation_history and isinstance(conversation_history[0], dict) and conversation_history[0].get("role") == "system":
    conversation_history[0]["content"] = SYSTEM_PROMPT
print("[мозг] v2: планировщик → исполнитель, рабочая память, фоновые задачи")


# === Мини-Atlas поверх окон ===
def mini_mode(on: bool = True) -> str:
    """Collapses Atlas into the floating mini window (on=True) or brings the full window back (on=False)."""
    try:
        import web_gui
        g = web_gui._GUI.get("gui")
    except Exception:
        g = None
    if g is None:
        return "The interface isn't running."
    (g.enter_mini() if on else g.show_main())
    return "Mini mode on — I'm in the corner above your windows." if on else "Full window is back."


AVAILABLE_FUNCTIONS["mini_mode"] = mini_mode
TOOLS_SCHEMA.append({"type": "function", "function": {"name": "mini_mode", "description": "Collapses Atlas into a small always-on-top window in the screen corner ('сверни себя', 'мини-режим', 'не мешай, будь в углу') — on=true; brings the full window back ('разверни себя', 'вернись') — on=false.", "parameters": {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]}}})
tool_router.register_tool("mini_mode", "settings")
tool_router.TRIGGERS["settings"] = tuple(set(tool_router.TRIGGERS.get("settings", ())) | {
    "сверни себя", "мини-режим", "мини режим", "мини-атлас", "в угол", "разверни себя", "mini mode", "minimize yourself"})
