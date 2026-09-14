import random

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


def tell_joke() -> str:
    """Tells a random joke."""
    return random.choice(JOKES)


def random_fact() -> str:
    """Shares a random interesting fact."""
    return random.choice(FACTS)


def start_number_game() -> str:
    """Starts a number guessing game — picks a secret number between 1 and 100."""
    _game_state["target"] = random.randint(1, 100)
    return "I'm thinking of a number between 1 and 100. Try to guess it."


def guess_number(guess: int) -> str:
    """Checks a guess against the secret number from an active number guessing game."""
    target = _game_state.get("target")
    if target is None:
        return "No game is running — say 'start a number game' first."
    if guess == target:
        _game_state["target"] = None
        return f"Correct! It was {target}. Well played."
    elif guess < target:
        return "Higher."
    else:
        return "Lower."