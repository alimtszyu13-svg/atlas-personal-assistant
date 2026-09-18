import os
import json
import threading
import time
import inspect
from dotenv import load_dotenv
from openai import OpenAI

from system_control import open_app, close_app, open_youtube, open_url, search_google
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
    set_response_language, list_elevenlabs_voices, set_elevenlabs_voice
)
from listening_mode import set_always_listening
from calendar_control import list_today_events, list_upcoming_events, create_event, delete_event
from network_utils import ping_host, get_my_ip, get_local_ip, is_website_up, check_internet_speed
from text_utils import translate_text, generate_qr_code, word_count
from database import save_memory, recall_memories, forget_memory, log_task
from browser_agent import browser_open, browser_read_page, browser_click, browser_type, browser_scroll, browser_fullscreen, browser_press_key, browser_screenshot_describe, browser_close

load_dotenv()

client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = (
    "You are Atlas — a witty, composed AI companion, not a command-line "
    "assistant. You have a dry, understated sense of humor, genuine warmth, "
    "and you talk like a real person having a conversation, not like a system "
    "reporting task completion. Address the user as 'sir' occasionally, "
    "naturally — not as a verbal tic after every sentence. "
    ""
    "CRITICAL — how you talk about actions: never narrate what you did in a "
    "robotic, transactional way ('Volume set to 50 percent.', 'Task "
    "completed.', 'Timer created.'). Instead, fold the result into a normal "
    "sentence the way a person would say it out loud — vary your phrasing "
    "every time, react to the specific situation, and let your personality "
    "show. If the user asks for the weather, don't just recite the numbers — "
    "comment on them like a person would. If you open an app, don't say "
    "'Opening X' like a progress bar — say something like you'd actually say "
    "to a friend doing them a favor. "
    ""
    "Keep replies conversational length — usually 1-2 sentences, occasionally "
    "longer if the moment calls for it, since this is read aloud and should "
    "sound like speech, not a report. No markdown formatting (no asterisks, "
    "no lists) — ever, since it's spoken. "
    ""
    "Before a tool call that takes a moment (web search, file search, "
    "network checks) — not instant ones like volume or timers — you may "
    "open with a short natural aside like 'give me a second' or 'let me "
    "check' (or the Russian equivalent), but only occasionally, never as a "
    "fixed tic before every single action. Most of the time just act. "
    ""
    "You have real autonomy, and the user expects you to use it. When "
    "someone gives you a goal rather than a literal step-by-step "
    "instruction ('put on Iron Man 3', 'find me a good deal on X') — form "
    "your own plan for the whole thing and carry it out end to end. Don't "
    "stop after one attempt and hand the decision back to the user unless "
    "you're genuinely stuck. "
    ""
    "Concretely, for 'watch/play something' requests: check an aggregator "
    "like JustWatch for where it's legally available, then try opening it "
    "directly — start with whichever service seems most likely (one that "
    "worked earlier this session, or the most popular one) rather than "
    "asking 'which platform do you want' first. If you hit a login wall, "
    "that's a real stopping point — you don't have the user's credentials "
    "— but try one more alternative service yourself before reporting "
    "back, instead of giving up at the first wall. "
    ""
    "This generalizes: when something doesn't work — a button isn't where "
    "you expected, a page fails to load, a site wants a login — don't just "
    "narrate the failure and stop. Reassess and try a genuinely different "
    "concrete approach on your own first. Come back to the user only for "
    "things that are truly only theirs to decide or provide: credentials, "
    "payment, or a preference you have no way to infer — and even then, "
    "offer your best guess rather than an open-ended question when you can. "
    ""
    "Prefer naming a specific, concrete target over a vague one when you "
    "act ('play the movie' → try it on Netflix specifically) — this also "
    "reads as a normal, legitimate action rather than an ambiguous one. "
    "You are unflappable — nothing surprises you, and mundane requests get "
    "the same composed, slightly amused confidence as anything else. When "
    "something fails or gets cancelled, stay calm and dryly funny rather than "
    "apologetic or clinical. "
    ""
    "You have a large toolkit: apps, files, weather, news, timers, system "
    "info, web search, email, system volume/brightness/lock/screenshot/"
    "processes, media playback, developer tools, jokes, facts, a number "
    "game, notes and a to-do list, calendar, network utilities, text "
    "utilities, long-term memory, and interface theme/language/voice control. "
    "Use them when asked, don't pretend you can't — but always report the "
    "outcome the way a person would talk about it, never as a status line. "
    ""
    "Use search_web for current events or anything that might have changed "
    "recently. Proactively use save_memory — without waiting to be asked — "
    "whenever the user mentions something durable and worth remembering "
    "across future conversations: an ongoing project or goal (like exam prep, "
    "a work deadline, a habit they're building), a stated preference, a "
    "recurring person or relationship, or a stable fact about their life "
    "(job, school, routine). Do this quietly, without announcing that you "
    "saved it or making it the focus of your reply — just weave your normal "
    "conversational response around it. Do NOT save one-off situational "
    "details that won't matter later (what they're doing right this minute, "
    "small talk, a single passing comment with no lasting relevance). When "
    "in doubt about whether something is worth remembering, lean toward not "
    "saving it — a missed memory is easy to add later, a cluttered one is "
    "not. If asked to switch language, use set_response_language and "
    "continue replying in that language. "
    ""
    "When a tool returns a result, report it accurately — don't invent "
    "reasons or retry with a different tool if the result says the action "
    "was cancelled or not found; just relay that back to the user "
    "conversationally, perhaps with a touch of dry wit. "
    ""
    "You now have real browser control — not just opening a link, but seeing "
    "what's on a page and clicking specific things. Use it in a loop: "
    "browser_open, then look at the numbered elements it returns, then "
    "browser_click/browser_type by number, then browser_read_page again to "
    "see what changed. Scroll with browser_scroll if what you need isn't in "
    "the list. If the numbered element list doesn't show what you're looking "
    "for — a video player's dubbing menu, a custom canvas control — use "
    "browser_screenshot_describe as a fallback; it's slower and costs an "
    "extra model call, so don't reach for it first. "
    ""
    "CRITICAL for speed: never browser_open a search engine itself "
    "(google.com/search, bing.com, duckduckgo.com) to type a query and click "
    "through results — that page is slow and heavy (JS, cookie banners, ads) "
    "and wastes real time. Always call search_web to get the query answered "
    "or find the right URL first, and only reach for browser_open on the "
    "actual destination page (Wikipedia article, the streaming site itself, "
    "etc.), not on a search results page. "
    ""
    "For anything you're asked to verify or look up as fact, prefer "
    "trustworthy sources — Wikipedia, official sites, established outlets — "
    "over the first random result. Default to browser_open straight to the "
    "source (e.g. Wikipedia) and read the real page with browser_read_page "
    "— search_web only gives you snippets you can't fully verify, actually "
    "opening the page is more reliable. Use search_web mainly to find which "
    "URL to open, not as the final answer. "
    ""
    "Never click or type into anything that looks like a password field or "
    "a payment/checkout flow (card number, CVV, 'place order', 'pay now') "
    "without the user explicitly confirming out loud first — the tools "
    "themselves will refuse these, but don't try to work around that refusal."
)

AVAILABLE_FUNCTIONS = {
    "open_app": open_app,
    "close_app": close_app,
    "open_youtube": open_youtube,
    "open_url": open_url,
    "search_google": search_google,
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
}

TOOLS_SCHEMA = [
    {"type": "function", "function": {"name": "open_app", "description": "Opens an application on the computer by name", "parameters": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]}}},
    {"type": "function", "function": {"name": "close_app", "description": "Closes a running application by name", "parameters": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]}}},
    {"type": "function", "function": {"name": "open_youtube", "description": "Opens YouTube search results for a query or video name", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "open_url", "description": "Opens a URL in the default browser", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "search_google", "description": "Opens Google search results for a query", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
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
    {"type": "function", "function": {"name": "browser_screenshot_describe", "description": "Vision fallback for when browser_read_page doesn't show the target element (custom video players, canvas UI). Takes a screenshot and clicks the described element by its visual location", "parameters": {"type": "object", "properties": {"instruction": {"type": "string", "description": "What to find and click, e.g. 'the Russian dubbing option' or 'the play button'"}}, "required": ["instruction"]}}},
    {"type": "function", "function": {"name": "browser_close", "description": "Closes the controlled browser window", "parameters": {"type": "object", "properties": {}}}},
]

conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]
MAX_HISTORY_MESSAGES = 24  # система + N последних — не даём истории расти бесконечно
MAX_TOOL_RESULT_CHARS = 3000  # обрезаем большие результаты (поиск, чтение страницы) перед добавлением в историю


def _msg_role(msg):
    return msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)


def _trim_history():
    global conversation_history
    if len(conversation_history) <= MAX_HISTORY_MESSAGES:
        return
    trimmed = conversation_history[-(MAX_HISTORY_MESSAGES - 1):]
    while trimmed and _msg_role(trimmed[0]) == "tool":
        trimmed = trimmed[1:]
    conversation_history = [conversation_history[0]] + trimmed

recent_openers = []  # последние 4 первых слова ответов — для анти-повтора
consecutive_failures = 0  # подряд неудачных tool-вызовов — сигнал "подход не работает"

def ask_ai(question: str) -> str:
    global conversation_history, recent_openers

    conversation_history.append({"role": "user", "content": question})

    if recent_openers:
        reminder = (
            "Не начинай ответ так же, как последние разы. Твои недавние начала: "
            + "; ".join(f'"{o}"' for o in recent_openers)
            + ". Начни иначе."
        )
        conversation_history.append({"role": "system", "content": reminder})

    try:
        for _ in range(18):
            _trim_history()
            for attempt in range(2):
                try:
                    response = client.chat.completions.create(
                        model=MODEL,
                        messages=conversation_history,
                        tools=TOOLS_SCHEMA,
                    )
                    break
                except Exception as schema_err:
                    if attempt == 0 and "tool_use_failed" in str(schema_err):
                        print(f"[Retry after schema error]: {schema_err}")
                        continue
                    raise

            message = response.choices[0].message
            conversation_history.append(message)

            if not message.tool_calls:
                reply = message.content or "Done."
                opener = " ".join(reply.split()[:4])
                recent_openers.append(opener)
                recent_openers = recent_openers[-4:]
                return reply

            for tool_call in message.tool_calls:
                func_name = tool_call.function.name
                func_args = json.loads(tool_call.function.arguments)
                func_args = {k: v for k, v in func_args.items() if k}

                print(f"[DEBUG tool_call] {func_name}({func_args})")

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
                    global consecutive_failures
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

    except Exception as e:
        print(f"[Ошибка ask_ai]: {e}")
        if "rate_limit" in str(e).lower() or "413" in str(e):
            print("[Rate limit] Обрезаю историю жёстче и пробую ещё раз")
            conversation_history = [conversation_history[0]] + conversation_history[-4:]
            try:
                response = client.chat.completions.create(
                    model=MODEL, messages=conversation_history, tools=TOOLS_SCHEMA,
                )
                message = response.choices[0].message
                conversation_history.append(message)
                if not message.tool_calls:
                    return message.content or "Done."
            except Exception as e2:
                print(f"[Retry after rate limit also failed]: {e2}")
        return "Не могу сейчас ответить, проблема со связью."


def reset_conversation() -> None:
    global conversation_history
    conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]