"""
Быстрые команды компьютера без модели — общий путь для голоса на ПК и для просьб с телефона.

Мессенджеры, окна, «скажи, когда…», уборка диска, текст с экрана, «что это?», файлы пачкой,
калькулятор, «где я это сохранил», сценарии. None — не наше (дальше — быстрые команды и мозг).

    handle(text, last=..., announce=..., local=True)
    local=False — просьба пришла с телефона: то, что требует мыши и экрана (выделить область,
    «что под курсором»), не запускаем.
"""
import re


def default_runner(cmd: str) -> str:
    """Шаг сценария: как будто сказали голосом, но молча — ответ только текстом."""
    said = handle(cmd, runner=None, nested=True)
    if said:
        return said
    try:
        from fast_commands import try_fast_command
        fast = try_fast_command(cmd)
        if fast is not None:
            return fast[1] if isinstance(fast, tuple) else str(fast)
    except Exception as e:
        print(f"[сценарий] быстрый путь: {e}")
    try:
        from ai_brain import ask_ai
        return ask_ai(cmd) or ""
    except Exception as e:
        return f"error: {e}"


def handle(text: str, last: str = None, announce=None, local: bool = True, runner=default_runner,
           on_full=None, ru_default: bool = None, nested: bool = False):
    """→ ответ (строка) или None. on_full(text) — длинный текст в чат (снимок экрана, перевод)."""
    from core import (disk_clean, file_ops, messengers, quick_math, scenarios, screen_snip, watchers,
                      what_is, where_saved, window_layout)
    try:
        from core import call_secretary, prompter
        said = call_secretary.handle(text)              # идёт звонок: «возьми трубку», «скажи, что занят»
        if said:
            return said
        if local:
            said = prompter.handle(text)                # «включи суфлёр», «меня зовут Алим»
            if said:
                return said
        if messengers.intent(text, last):               # «ответь маме в телеграме…», «да» — отправить
            said = messengers.handle(text, last=last)
            if said:
                return said
        for fn in (lambda: disk_clean.handle(text, announce=announce, last=last) if disk_clean.pending() else None,
                   lambda: where_saved.handle(text, last=last) if where_saved._s.get("last") else None):
            said = fn()
            if said:
                return said
        if local:
            snip = screen_snip.intent(text)
            if snip:
                ru = screen_snip._ru(text) if ru_default is None or screen_snip._ru(text) else ru_default
                short, full = screen_snip.snip(snip, ru=ru)
                if full and full != short and on_full:
                    on_full(full)
                return short
            if what_is.intent(text):
                return what_is.handle(text, ru=ru_default if text.strip().lower() == what_is.HOTKEY_PHRASE and
                                      ru_default is not None else None)
        if not nested and runner is not None:
            said = scenarios.handle(text, runner)
            if said:
                return said
        said = quick_math.answer(text)
        if said:
            return said
        said = file_ops.handle(text, announce=announce)
        if said:
            return said
        for fn in (window_layout.handle, watchers.handle):
            said = fn(text)
            if said:
                return said
        said = where_saved.handle(text, last=last)
        if said:
            return said
        return disk_clean.handle(text, announce=announce, last=last)
    except Exception as e:
        print(f"[ПК] {e}")
        return None


def is_question(said: str) -> bool:
    return bool(said) and bool(re.search(r"\?\s*(?:\([^)]*\))?\s*$", said))
