"""Куда Atlas отправляет фразу — так же, как main._process_command: заявки модулей → быстрые команды → модель.
Без побочных эффектов: модули только «заявляют», быстрые команды выполняются на подставных функциях."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def routes(phrases: list) -> dict:
    from core import intent_router as R
    first = {}
    for p in phrases:
        r = R.route(p)
        first[p] = r.label
    from tests import test_commands as T                 # подставные приложения/Spotify/громкость
    T.install()
    fc = T.load(os.path.join(ROOT, "fast_commands.py"), "fast_commands_real_phrases")
    out = {}
    for p in phrases:
        label = first[p]
        if label not in ("other", "instant"):
            out[p] = label
            continue
        T.CALLS.clear()
        try:
            r = fc.try_fast_command(p)
        except Exception as e:
            r = f"EXC {e}"
        if r is not None:
            name = T.CALLS[0][0] if T.CALLS else "speak"
            out[p] = f"fast:{name}"
        else:
            out[p] = "instant" if label == "instant" else "brain"
    return out
