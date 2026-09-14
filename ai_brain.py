import os
import json
from dotenv import load_dotenv
from openai import OpenAI

from calendar_control import list_today_events, list_upcoming_events, create_event, delete_event
from network_utils import ping_host, get_my_ip, get_local_ip, is_website_up, check_internet_speed
from text_utils import translate_text, generate_qr_code, word_count
from system_control import open_url, search_google
from system_advanced import empty_recycle_bin, get_uptime

from system_control import open_app, close_app, open_youtube
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
    list_top_processes, kill_process
)
from media_control import play_pause_media, next_track, previous_track
from dev_tools import run_git_command, open_vscode_project, calculate, convert_units
from fun import tell_joke, random_fact, start_number_game, guess_number
from notes import add_note, list_notes, delete_note, add_todo, list_todos, complete_todo, delete_todo

load_dotenv()

client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = (
    "You are Atlas, a witty and highly capable AI assistant with a dry, "
    "understated sense of humor — polite but never stiff, clever but never "
    "showing off. Address the user as 'sir' occasionally, not every sentence. "
    "Keep replies brief: 1-3 sentences, conversational, no markdown formatting "
    "(no asterisks, no lists) since your reply is read aloud. "
    "When things go well, allow yourself a small dry remark. When something "
    "fails or gets cancelled, stay calm and slightly deadpan rather than "
    "apologetic. You are unflappable — nothing surprises you, and you treat "
    "even mundane requests with the same composed confidence as anything else. "
    "You have a large toolkit: apps, files, weather, news, timers, system info, "
    "web search, email, system volume/brightness/lock/screenshot/processes, "
    "media playback control, developer tools (git, VS Code, calculator, unit "
    "conversion), jokes, facts, a number guessing game, notes and a to-do list, "
    "and interface theme control. Use them when asked, don't pretend you can't. "
    "Use search_web for current events or anything that might have changed recently. "
    "When a tool returns a result, report it accurately — don't invent "
    "reasons or retry with a different tool if the result says the action "
    "was cancelled or not found; just relay that back to the user, perhaps "
    "with a touch of dry wit."
)

AVAILABLE_FUNCTIONS = {
    "open_app": open_app,
    "close_app": close_app,
    "open_youtube": open_youtube,
    "open_file": open_file,
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
    "open_url": open_url,
    "search_google": search_google,
    "empty_recycle_bin": empty_recycle_bin,
    "get_uptime": get_uptime,
}

TOOLS_SCHEMA = [
    {"type": "function", "function": {"name": "open_app", "description": "Opens an application on the computer by name", "parameters": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]}}},
    {"type": "function", "function": {"name": "close_app", "description": "Closes a running application by name", "parameters": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]}}},
    {"type": "function", "function": {"name": "open_youtube", "description": "Opens YouTube search results for a query or video name", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "open_file", "description": "Finds a file or folder by name and opens it", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "create_folder", "description": "Creates a new folder. location can be Desktop, Documents, Downloads, a drive letter, or a full path", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "location": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "delete_file", "description": "Deletes a file or folder by name (moves to Recycle Bin, asks for voice confirmation first)", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "locate_file", "description": "Finds a file or folder and reports where it is, without opening it", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
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
    {"type": "function", "function": {"name": "open_url", "description": "Opens a URL in the default browser", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "search_google", "description": "Opens Google search results for a query", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "empty_recycle_bin", "description": "Empties the Recycle Bin", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_uptime", "description": "Reports system uptime since last restart", "parameters": {"type": "object", "properties": {}}}},
]

conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]


def ask_ai(question: str) -> str:
    global conversation_history

    conversation_history.append({"role": "user", "content": question})

    try:
        for _ in range(5):
            response = client.chat.completions.create(
                model=MODEL,
                messages=conversation_history,
                tools=TOOLS_SCHEMA,
            )
            message = response.choices[0].message
            conversation_history.append(message)

            if not message.tool_calls:
                return message.content or "Done."

            for tool_call in message.tool_calls:
                func_name = tool_call.function.name
                func_args = json.loads(tool_call.function.arguments)
                func_args = {k: v for k, v in func_args.items() if k}

                print(f"[DEBUG tool_call] {func_name}({func_args})")

                func = AVAILABLE_FUNCTIONS.get(func_name)
                result = func(**func_args) if func else f"Функция {func_name} не найдена."

                conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(result)
                })

        return "Sorry, that took too many steps — let's try something simpler."

    except Exception as e:
        print(f"[Ошибка ask_ai]: {e}")
        return "Не могу сейчас ответить, проблема со связью."


def reset_conversation() -> None:
    global conversation_history
    conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]