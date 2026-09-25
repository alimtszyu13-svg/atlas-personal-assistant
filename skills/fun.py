import random
from core.skills import skill

JOKES = [
    "I told my computer I needed a break, and it said no problem — it froze immediately.",
    "There are 10 types of people in the world: those who understand binary, and those who don't.",
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "I would tell you a UDP joke, but you might not get it.",
    "A SQL query walks into a bar, walks up to two tables and asks, 'Can I join you?'",
]

FACTS = [
    "Honey never spoils — archaeologists have found 3000-year-old honey in Egyptian tombs that's still edible.",
    "A day on Venus is longer than a year on Venus.",
    "Octopuses have three hearts and blue blood.",
    "The first computer bug was an actual moth found stuck in a relay in 1947.",
    "Bananas are berries, but strawberries aren't.",
]

_game_state = {"target": None}

@skill("fun", read_only=True, description="Tells a random joke")
def tell_joke() -> str:
    """Tells a random joke."""
    return random.choice(JOKES)

@skill("fun", read_only=True, description="Shares a random interesting fact")
def random_fact() -> str:
    """Shares a random interesting fact."""
    return random.choice(FACTS)

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