import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

SYSTEM_PROMPT = (
    "Ты — Атлас, голосовой ассистент. Отвечай кратко, "
    "1-3 предложения, разговорным языком, без markdown-разметки "
    "(звёздочек, списков) — твой ответ будет озвучен вслух."
)

# История диалога — список словарей вида {"role": ..., "parts": [...]}.
# Живёт, пока работает программа; обнуляется при перезапуске main.py.
conversation_history = []

MAX_HISTORY_MESSAGES = 20  # ограничиваем длину — иначе история будет расти бесконечно
                            # и каждый запрос станет всё дороже и медленнее

def ask_ai(question: str) -> str:
    global conversation_history

    try:
        # Добавляем реплику пользователя в историю ПЕРЕД отправкой
        conversation_history.append(
            types.Content(role="user", parts=[types.Part(text=question)])
        )

        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=conversation_history,  # теперь шлём ВСЮ историю, а не один вопрос
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                thinking_config=types.ThinkingConfig(thinking_level="low")
            )
        )

        answer = response.text

        # Добавляем ответ модели в историю — чтобы в следующий раз
        # модель "помнила", что сама же ответила
        conversation_history.append(
            types.Content(role="model", parts=[types.Part(text=answer)])
        )

        # Обрезаем историю, если разрослась — оставляем только последние N сообщений
        if len(conversation_history) > MAX_HISTORY_MESSAGES:
            conversation_history = conversation_history[-MAX_HISTORY_MESSAGES:]

        return answer
    except Exception as e:
        print(f"[Ошибка ask_ai]: {e}")
        return "Не могу сейчас ответить, проблема со связью."


def reset_conversation() -> None:
    """Очищает историю диалога — пригодится для команды 'забудь всё' или новой сессии."""
    global conversation_history
    conversation_history = []