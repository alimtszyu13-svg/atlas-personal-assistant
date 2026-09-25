"""Фоновые миссии: запуск, статус, отмена."""
from core.skills import skill
from core import missions


@skill("missions",
       description="Starts a long task in the background (research, collecting and comparing "
                   "information, summarizing sources) so the user can keep talking. Use for "
                   "'in the background', 'research', 'find and compare several...'. Then reply "
                   "in one short sentence that you're on it.",
       params={"goal": "The full task exactly as the user said it, in the user's language "
                       "(don't translate), with every detail and constraint"})
def start_mission(goal: str) -> str:
    mid = missions.start(goal)
    return f"Mission #{mid} started in the background; the result will be saved to notes."


@skill("missions", read_only=True, description="Shows the status of background missions.")
def mission_status() -> str:
    rows = missions.recent()
    if not rows:
        return "No missions yet."
    return "\n".join(
        f"#{i} [{st}] {g[:60]}" + (f" — last steps: {p[-150:]}" if p and st == "running" else "")
        for i, g, st, p in rows)


@skill("missions",
       description="Cancels a running background mission (the latest one if no id is given).",
       params={"mission_id": "Mission number; 0 means the latest running one"})
def cancel_mission(mission_id: int = 0) -> str:
    mid = missions.cancel(mission_id)
    return f"Mission #{mid} is being cancelled." if mid else "No running missions."