"""
Компьютер ↔ телефон: «что на экране компьютера?», «продолжи на телефоне» (core/handoff.py).
"""
from core import handoff
from core.skills import skill

try:
    import tool_router
    tool_router.TRIGGERS["handoff"] = (
        "на экране компьютера", "на экране компа", "что на компе", "что на компьютере", "покажи экран",
        "скриншот на телефон", "продолжи на телефоне", "открой на телефоне то", "перекинь на телефон",
        "что сейчас открыто", "screen of my computer", "continue on my phone", "what's on my computer",
    )
except Exception:
    pass


@skill("handoff",
       description="Shows the computer's screen on the user's phone: takes a screenshot, sends it to the phone, and "
                   "returns the text on the screen so you can say briefly what is there. Use for 'что на экране компа?', "
                   "'покажи экран компьютера'.")
def show_screen_on_phone() -> str:
    return handoff.screen_to_phone()


@skill("handoff",
       description="Moves what is open on the computer to the phone: the web page open in the browser opens on the "
                   "phone, an open document is sent to the phone. Use for 'продолжи на телефоне', 'перекинь это на "
                   "телефон'.")
def continue_on_phone() -> str:
    return handoff.continue_on_phone()
