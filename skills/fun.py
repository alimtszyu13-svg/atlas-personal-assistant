import random
from core.skills import skill

import re
import requests
from collections import deque

_HTTP_TIMEOUT = 3
_recent_keys = deque(maxlen=50)     # недавно рассказанное — чтобы не повторяться
_recent_texts = deque(maxlen=8)     # последние тексты — подсказка модели «не повторяй»

_FALLBACK = {
    "joke": {
        "en": ["I told my computer I needed a break. It said: 'No problem, I'll go to sleep.'"],
        "ru": ["Программист ставит на тумбочку два стакана: с водой — если захочет пить, "
               "пустой — если не захочет."],
    },
    "fact": {
        "en": ["Octopuses have three hearts and blue blood."],
        "ru": ["У осьминога три сердца и голубая кровь."],
    },
}


def _fresh(text):
    """Текст, если его ещё не рассказывали; иначе None."""
    if not text:
        return None
    key = re.sub(r"\W+", "", text.lower())[:120]
    if key in _recent_keys:
        return None
    _recent_keys.append(key)
    _recent_texts.append(text)
    return text


def _llm(system: str, user: str):
    """Короткий запрос к быстрой модели через гейтвей лимитов."""
    try:
        from ai_brain import client, MODEL_FAST
        from core import llm_gateway
        llm_gateway.reserve(MODEL_FAST, 600)
        r = client.chat.completions.create(
            model=MODEL_FAST, reasoning_effort="low", max_tokens=500, temperature=1.0,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}])
        return (r.choices[0].message.content or "").strip() or None
    except Exception as e:
        print(f"[fun] модель недоступна: {e}")
        return None


def _joke_online():
    try:
        d = requests.get("https://v2.jokeapi.dev/joke/Any?safe-mode"
                         "&blacklistFlags=nsfw,religious,political,racist,sexist,explicit",
                         timeout=_HTTP_TIMEOUT).json()
        if not d.get("error"):
            return d["joke"] if d.get("type") == "single" else f"{d['setup']} ... {d['delivery']}"
    except Exception:
        pass
    try:
        return requests.get("https://icanhazdadjoke.com/", timeout=_HTTP_TIMEOUT,
                            headers={"Accept": "application/json",
                                     "User-Agent": "Atlas personal assistant"}).json()["joke"]
    except Exception:
        return None


def _fact_online():
    try:
        return requests.get("https://uselessfacts.jsph.pl/api/v2/facts/random?language=en",
                            timeout=_HTTP_TIMEOUT).json()["text"]
    except Exception:
        return None


def _made_up(kind: str, topic: str, ru: bool):
    lang = "Russian" if ru else "English"
    about = f" about {topic}" if topic else ""
    what = "one short original joke" if kind == "joke" else "one well-known, true fact"
    avoid = "; ".join(_recent_texts)
    return _llm(f"Reply with {what}{about} in {lang}. Family-friendly, witty, 1-3 sentences, "
                f"meant to be read aloud. No preface, no emoji, no markdown.",
                f"Don't repeat these: {avoid}" if avoid else "Go.")


def _fallback(kind: str, ru: bool) -> str:
    return random.choice(_FALLBACK[kind]["ru" if ru else "en"])


@skill("fun", read_only=True,
       description="Tells a joke — fresh from the internet or made up on the spot. "
                   "Pass a topic if the user asked for one ('about cats', 'про программистов').",
       params={"topic": "Optional joke topic; leave empty for a random joke"})
def tell_joke(topic: str = "") -> str:
    ru = get_response_language() == "ru"
    if not topic and not ru:                       # английский без темы — из интернета
        for _ in range(3):
            joke = _fresh(_joke_online())
            if joke:
                return joke
    for _ in range(2):                             # тема или русский — придумывает модель
        joke = _fresh(_made_up("joke", topic, ru))
        if joke:
            return joke
    return _fallback("joke", ru)


@skill("fun", read_only=True,
       description="Shares a random interesting fact from the internet (optionally on a topic).",
       params={"topic": "Optional fact topic; leave empty for a random fact"})
def random_fact(topic: str = "") -> str:
    ru = get_response_language() == "ru"
    if not topic:
        for _ in range(3):
            fact = _fact_online()
            if fact and ru:                        # переводим настоящий факт, а не выдумываем
                fact = _llm("Translate to natural Russian. Reply with the translation only.", fact)
            fact = _fresh(fact)
            if fact:
                return fact
    fact = _fresh(_made_up("fact", topic, ru))
    return fact or _fallback("fact", ru)

_game_state = {"target": None}

import random
from voice import get_response_language

_game = {"secret": None, "tries": 0}


def game_active() -> bool:
    return _game["secret"] is not None


@skill("fun", description="Starts a number guessing game between 1 and 100")
def start_number_game() -> str:
    _game["secret"] = random.randint(1, 100)
    _game["tries"] = 0
    if get_response_language() == "ru":
        return "Я загадал число от 1 до 100. Твой вариант?"
    return "I've picked a number between 1 and 100. Your guess?"


@skill("fun", description="Submits a guess in the active number guessing game")
def guess_number(guess: int) -> str:
    ru = get_response_language() == "ru"
    if _game["secret"] is None:
        return ("Игра не запущена — скажи «давай сыграем в угадай число»." if ru
                else "No game in progress. Say 'let's play guess the number' to start.")
    _game["tries"] += 1
    if guess < _game["secret"]:
        return f"Больше, чем {guess}." if ru else f"Higher than {guess}."
    if guess > _game["secret"]:
        return f"Меньше, чем {guess}." if ru else f"Lower than {guess}."
    tries = _game["tries"]
    _game["secret"] = None
    return (f"Угадал! Это {guess}, с {tries}-й попытки." if ru
            else f"Correct! It was {guess} — got it in {tries} tries.")