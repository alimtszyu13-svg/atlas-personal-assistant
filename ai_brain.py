import os
import json
from dotenv import load_dotenv
from openai import OpenAI
from system_info import get_cpu_usage, get_memory_usage, get_battery_status, get_disk_usage
from reminders import set_timer, list_timers

from system_control import open_app, close_app
from info_services import get_weather, get_news
from file_control import (
    open_file, create_folder, delete_file, locate_file,
    rename_file, copy_file, move_file
)

load_dotenv()

# Groq работает по тому же протоколу, что OpenAI — берём их библиотеку,
# просто указываем адрес сервера Groq вместо адреса OpenAI
client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = (
    "You are Atlas, a voice assistant. Reply briefly, "
    "1-3 sentences, in a conversational tone, no markdown formatting "
    "(no asterisks, no lists) — your reply will be read aloud. "
    "You have tools for controlling apps, files, "
    "weather and news — use them when asked, don't pretend you can't. "
    "When a tool returns a result, report it accurately — don't invent "
    "reasons or retry with a different tool if the result says the action "
    "was cancelled or not found; just relay that back to the user."
)

AVAILABLE_FUNCTIONS = {
    "open_app": open_app,
    "close_app": close_app,
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
    "list_timers": list_timers
}

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Открывает приложение на компьютере по названию",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "Название приложения, например 'блокнот', 'калькулятор'"}
                },
                "required": ["app_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "close_app",
            "description": "Закрывает запущенное приложение по названию",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "Название приложения для закрытия"}
                },
                "required": ["app_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_file",
            "description": "Находит файл или папку по имени на диске и открывает",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Имя файла или папки"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_folder",
            "description": "Создаёт новую папку. По умолчанию на Рабочем столе, но можно указать другое место — Documents, Downloads, букву диска (например 'D:') или полный путь",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Имя новой папки"},
                    "location": {"type": "string", "description": "Куда поместить: 'Desktop', 'Documents', 'Downloads', буква диска вроде 'D:' или полный путь. Необязательно, по умолчанию Desktop"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Удаляет файл или папку по имени (перемещает в Корзину)",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Имя файла или папки для удаления"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "locate_file",
            "description": "Находит файл или папку и сообщает, в какой папке она находится, не открывая",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Имя файла или папки для поиска"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Узнаёт текущую погоду",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_news",
            "description": "Узнаёт последние новости",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rename_file",
            "description": "Renames a file or folder found by its current name",
            "parameters": {
                "type": "object",
                "properties": {
                    "old_name": {"type": "string", "description": "Current name of the file or folder"},
                    "new_name": {"type": "string", "description": "New name to give it"}
                },
                "required": ["old_name", "new_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "copy_file",
            "description": "Copies a file or folder to a destination, leaving the original in place",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the file or folder to copy"},
                    "destination": {"type": "string", "description": "Where to copy it: 'Desktop', 'Documents', 'Downloads', a drive letter like 'D:', or a full path. Defaults to Desktop"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "Moves a file or folder to a destination, removing it from its original location",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the file or folder to move"},
                    "destination": {"type": "string", "description": "Where to move it: 'Desktop', 'Documents', 'Downloads', a drive letter like 'D:', or a full path. Defaults to Desktop"}
                },
                "required": ["name"]
            }
        }
    },
        {
        "type": "function",
        "function": {
            "name": "get_cpu_usage",
            "description": "Gets the current CPU usage percentage",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_memory_usage",
            "description": "Gets current RAM usage",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_battery_status",
            "description": "Gets battery charge level and charging status, if the device has a battery",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_disk_usage",
            "description": "Gets free and used disk space for a specific drive",
            "parameters": {
                "type": "object",
                "properties": {
                    "drive": {"type": "string", "description": "Drive letter, e.g. 'C:' or 'D:'. Defaults to C:"}
                }
            }
        }
    },
        {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": "Sets a timer for a number of minutes, optionally with a custom message to say when it goes off",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "number", "description": "How many minutes from now the timer should go off"},
                    "message": {"type": "string", "description": "What to say when the timer finishes. Defaults to \"Timer's up!\""}
                },
                "required": ["minutes"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_timers",
            "description": "Lists all currently active timers and how much time remains on each",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]

# История диалога — ведём сами, начинается с системного промпта.
conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]


def ask_ai(question: str) -> str:
    global conversation_history

    conversation_history.append({"role": "user", "content": question})

    try:
        # Цикл вместо одного фиксированного "второго запроса" — модель может
        # захотеть вызвать несколько функций подряд, прежде чем дать финальный ответ
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