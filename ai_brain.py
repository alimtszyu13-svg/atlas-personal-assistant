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

def ask_ai(question: str) -> str:
    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=question,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                # low — минимальная глубина рассуждений у Gemini 3.x,
                # полностью отключить thinking на этой линейке моделей нельзя,
                # но low вместо дефолтного "high" должен ощутимо ускорить ответ
                thinking_config=types.ThinkingConfig(thinking_level="low")
            )
        )
        return response.text
    except Exception as e:
        print(f"[Ошибка ask_ai]: {e}")
        return "Не могу сейчас ответить, проблема со связью."