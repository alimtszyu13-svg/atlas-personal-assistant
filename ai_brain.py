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
from fun import tell_joke, random_fact, start_number_game, guess_number
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
    ""
    "TOOLS. Use them instead of saying you can't. Current facts → search_web. Local files by "
    "content, screenshots or pictures → search_file_content (never the browser for local files). "
    "Prefer native apps and deep links over browsing (music → play_on_spotify). Never open a search "
    "engine page: use search_web to find the URL, then browser_open the real page and read it; "
    "prefer trustworthy sources (Wikipedia, official sites). Browser loop: browser_open → numbered "
    "elements → browser_click/browser_type → browser_read_page; browser_scroll if needed. If the "
    "element isn't listed or DOM actions fail ('Execution context was destroyed'), switch to "
    "browser_screenshot_describe instead of repeating. Never type into password or payment fields "
    "without the user's explicit spoken confirmation. "
    ""
    "MEMORY. Quietly save_memory durable facts (projects, goals, preferences, people, routine) "
    "without announcing it; skip one-off details. When unsure, don't save. "
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
    stream = client.chat.completions.create(stream=True, **kwargs)
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
    "tell_joke": tell_joke,
    "random_fact": random_fact,
    "start_number_game": start_number_game,
    "guess_number": guess_number,
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
    {"type": "function", "function": {"name": "tell_joke", "description": "Tells a random joke", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "random_fact", "description": "Shares a random interesting fact", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "start_number_game", "description": "Starts a number guessing game between 1 and 100", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "guess_number", "description": "Submits a guess in the active number guessing game", "parameters": {"type": "object", "properties": {"guess": {"type": "integer"}}, "required": ["guess"]}}},
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
MAX_TOOL_RESULT_CHARS = 1500  # обрезаем большие результаты (поиск, чтение страницы) перед добавлением в историю


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
        "это", "this", "that", "скопир", "copied", "буфер", "clipboard"))

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
                parts.append(f"clipboard: {clip[:300]}")
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
    "word_count", "translate_text", "list_voices", "list_audio_devices",
    "search_file_content",
}


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
    first_effort = _effort_for(question, active_groups)
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
            if step_index == 2 and "browser" in active_groups:
                conversation_history.append({
                    "role": "system",
                    "content": "WARNING: You have made multiple unsuccessful attempts using DOM/HTML tools. Stop guessing. IMMEDIATELY use the `browser_screenshot_describe` tool to visually analyze the screen and click the required element."
                })

            model = MODEL_SMART if step_index == 0 else MODEL_FAST
            extra = [context_msg]
            if current_plan:
                extra.append({"role": "system", "content":
                    "Active plan: " + json.dumps(current_plan, ensure_ascii=False)})
            reasoning_effort = first_effort if step_index == 0 else "low"
            for attempt in range(3):
                try:
                    _t0 = time.time()
                    msgs = llm_gateway.fit(
                        clean_messages_for_api(conversation_history) + extra, active_schema)
                    est = llm_gateway.estimate(msgs) + llm_gateway.estimate(active_schema)
                    other = MODEL_FAST if model == MODEL_SMART else MODEL_SMART
                    model = llm_gateway.reserve_any([model, other], est, _check_cancel)
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
                        wait = 2.0
                        m_wait = re.search(r"try again in ([\d.]+)s", err)
                        if m_wait:
                            wait = float(m_wait.group(1)) + 0.5
                        print(f"[Rate limit] жду {wait:.1f}с и повторяю")
                        if _cancel_event.wait(wait):
                            raise TaskCancelled()
                        continue
                    if attempt < 2 and "tool_use_failed" in low:
                        print(f"[Retry after schema error]: {api_err}")
                        continue
                    raise

            message = _as_message(response)
            
            # Преобразуем в словарь и сразу вычищаем annotations
            msg_dump = message.model_dump()
            msg_dump.pop("annotations", None)
            conversation_history.append(msg_dump)

            if not message.tool_calls:
                reply = message.content or "Done."
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
                if g and g not in active_groups:
                    active_groups.add(g)
                    active_schema = tool_router.filter_schema(TOOLS_SCHEMA, active_groups)
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
        return "Не могу сейчас ответить, проблема со связью."

def remember_exchange(user_text: str, assistant_text: str) -> None:
    """Записывает в историю то, что выполнил быстрый путь без модели —
    иначе следующее "да, включи" не знает, о чём речь."""
    conversation_history.append({"role": "user", "content": user_text})
    conversation_history.append({"role": "assistant", "content": assistant_text})

def reset_conversation() -> None:
    global conversation_history
    conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]