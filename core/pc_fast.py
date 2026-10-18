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


def _handlers(text: str, last, announce, local, runner, on_full, ru_default, nested) -> dict:
    """Метка маршрутизатора → как её выполнить. Ответ None = «не моё», маршрут идёт к следующей заявке."""
    from core import (call_secretary, disk_clean, file_ops, form_fill, game_mode, messengers, prompter, proofread,
                      quick_math, report, scenarios, screen_history, screen_snip, voice_notes, watchers, wellbeing,
                      what_is, where_saved, window_layout)

    def snip():
        kind = screen_snip.intent(text)
        ru = screen_snip._ru(text) if ru_default is None or screen_snip._ru(text) else ru_default
        short, full = screen_snip.snip(kind, ru=ru)
        if full and full != short and on_full:
            on_full(full)
        return short

    def what():
        hot = text.strip().lower() == what_is.HOTKEY_PHRASE and ru_default is not None
        return what_is.handle(text, ru=ru_default if hot else None)

    return {
        "call_secretary": lambda: call_secretary.handle(text),
        "prompter": lambda: prompter.handle(text),
        "messengers": lambda: messengers.handle(text, last=last),
        "pending_disk": lambda: disk_clean.handle(text, announce=announce, last=last),
        "pending_proofread": lambda: proofread.handle(text, last=last),
        "pending_where": lambda: where_saved.handle(text, last=last),
        "screen_snip": snip,
        "what_is": what,
        "scenarios": lambda: scenarios.handle(text, runner) if (runner is not None and not nested) else None,
        "quick_math": lambda: quick_math.answer(text),
        "report": lambda: report.handle(text),
        "speed_stats": lambda: __import__("core.turn_trace", fromlist=["x"]).handle(text),
        "usage_stats": lambda: __import__("core.usage", fromlist=["x"]).handle(text),
        "knowledge": lambda: __import__("core.knowledge", fromlist=["x"]).handle(text),
        "proofread": lambda: proofread.handle(text),
        "form_fill": lambda: form_fill.handle(text),
        "screen_history": lambda: screen_history.handle(text, last=last),
        "voice_notes": lambda: voice_notes.handle(text),
        "game_mode": lambda: game_mode.handle(text),
        "wellbeing": lambda: wellbeing.handle(text),
        "file_ops": lambda: file_ops.handle(text, announce=announce),
        "window_layout": lambda: window_layout.handle(text),
        "watchers": lambda: watchers.handle(text),
        "where_saved": lambda: where_saved.handle(text, last=last),
        "disk_clean": lambda: disk_clean.handle(text, announce=announce, last=last),
    }


LAST = {"text": "", "route": None, "said": None}


def handle(text: str, last: str = None, announce=None, local: bool = True, runner=default_runner,
           on_full=None, ru_default: bool = None, nested: bool = False):
    """→ ответ (строка) или None. on_full(text) — длинный текст в чат (снимок экрана, перевод).
    Кто берёт фразу — решает маршрутизатор (core/intent_router); если первая заявка отказалась,
    пробуется следующая. Фразы, которые выполняет main (записи, конспекты) или мозг, — None."""
    from core import intent_router
    try:
        r = intent_router.route(text, {"last": last, "local": local})
        if r.ambiguous:
            print(f"[маршрут] «{text[:60]}» → {r.label} (спорили: {', '.join(r.claims)})")
        if not r.claims:
            return None
        table = _handlers(text, last, announce, local, runner, on_full, ru_default, nested)
        for label in r.claims:
            if intent_router.OWNER.get(label) != "pc_fast":
                return None                               # это выполнит main или мозг — дальше по цепочке
            fn = table.get(label)
            said = fn() if fn else None
            if said:
                if not nested:
                    import time
                    LAST.update(text=text, route=label, said=said, ts=time.time())
                return said
        return None
    except Exception as e:
        print(f"[ПК] {e}")
        return None


def prepare(command: str, last: str = None, local: bool = True, ask=None) -> str:
    """До разбора: исправление («нет, я имел в виду …» — запомнить и выполнить правильное) и перефразировки
    (смысловой слой: «растяни телегу на весь экран» → «разверни телеграм»). Возвращает команду для выполнения."""
    import time
    from core import intent_router, semantic_router
    ctx = {"last": last, "local": local}
    try:
        fix = semantic_router.correction(command)
        if fix:
            r = intent_router.route(fix, ctx)
            if r.label == "other" and semantic_router.enabled():
                res = semantic_router.rescue(fix, ctx, ask)
                if res:
                    fix, r = res[0], intent_router.route(res[0], ctx)
            recent = LAST.get("text") and time.time() - LAST.get("ts", 0) < 90
            if recent and r.label not in ("other", LAST.get("route")):
                semantic_router.learn(LAST["text"], r.label)  # в следующий раз эта фраза — сюда
            return fix
        if intent_router.route(command, ctx).label == "other" and semantic_router.enabled():
            res = semantic_router.rescue(command, ctx, ask)
            if res:
                return res[0]
    except Exception as e:
        print(f"[смысл] {e}")
    return command


def is_question(said: str) -> bool:
    return bool(said) and bool(re.search(r"\?\s*(?:\([^)]*\))?\s*$", said))
