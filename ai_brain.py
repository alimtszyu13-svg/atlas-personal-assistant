import os
import json
from dotenv import load_dotenv
from openai import OpenAI

from system_control import open_app, close_app
from info_services import get_weather, get_news
from file_control import open_file, create_folder, delete_file, locate_file

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
    "weather and news — use them when asked, don't pretend you can't."
)

# Реальные Python-функции — по имени будем их находить и вызывать
AVAILABLE_FUNCTIONS = {
    "open_app": open_app,
    "close_app": close_app,
    "open_file": open_file,
    "create_folder": create_folder,
    "delete_file": delete_file,
    "locate_file": locate_file,
    "get_weather": get_weather,
    "get_news": get_news,
}

# JSON Schema — описание функций для модели. В отличие от Gemini,
# где SDK сам читал docstring, здесь схему нужно писать руками:
# type, описание, и какие параметры функция принимает.
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
]

# История диалога — как и в ручной версии для Gemini, ведём сами.
# Начинается с системного промпта.
conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]


def ask_ai(question: str) -> str:
    global conversation_history

    conversation_history.append({"role": "user", "content": question})

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=conversation_history,
            tools=TOOLS_SCHEMA,
        )

        message = response.choices[0].message

        # Если модель решила вызвать функцию(и) — tool_calls будет непустым
        if message.tool_calls:
            # Добавляем в историю саму "просьбу" модели вызвать функции
            conversation_history.append(message)

            for tool_call in message.tool_calls:
                func_name = tool_call.function.name
                func_args = json.loads(tool_call.function.arguments)

                print(f"[DEBUG tool_call] {func_name}({func_args})")

                                # Иногда модель присылает мусорный пустой аргумент вроде {'': ''}
                # для функций без параметров — отфильтровываем такие ключи
                func_args = {k: v for k, v in func_args.items() if k}

                func = AVAILABLE_FUNCTIONS.get(func_name)
                if func:
                    result = func(**func_args)
                else:
                    result = f"Функция {func_name} не найдена."

                # Отправляем результат выполнения обратно в историю —
                # с ролью "tool" и привязкой к id конкретного вызова
                conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(result)
                })

            # Второй запрос — теперь модель видит результат функции
            # и формулирует финальный голосовой ответ на его основе
            second_response = client.chat.completions.create(
                model=MODEL,
                messages=conversation_history,
                tools=TOOLS_SCHEMA,
            )
            final_message = second_response.choices[0].message
            conversation_history.append(final_message)
            return final_message.content

        # Модель ответила сразу текстом, без вызова функций
        conversation_history.append(message)
        return message.content

    except Exception as e:
        print(f"[Ошибка ask_ai]: {e}")
        return "Не могу сейчас ответить, проблема со связью."


def reset_conversation() -> None:
    global conversation_history
    conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]