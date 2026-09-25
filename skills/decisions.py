"""Мелкие решения: выбрать вариант, случайное число, монетка."""
import random
from core.skills import skill


@skill("fun", read_only=True, params={"options": "All options the user is choosing between"})
def pick_one(options: list[str]) -> str:
    """Randomly picks one option when the user can't decide ('pizza, sushi or shawarma?')."""
    if not options:
        return "Nothing to choose from."
    return f"Picked: {random.choice(options)}"


@skill("fun", read_only=True)
def random_number(low: int = 1, high: int = 100) -> str:
    """Generates a random whole number between low and high, inclusive."""
    if low > high:
        low, high = high, low
    return str(random.randint(low, high))


@skill("fun", read_only=True)
def flip_coin() -> str:
    """Flips a coin: heads or tails."""
    return random.choice(["Heads", "Tails"])