"""
Единый маршрутизатор команд: кто из модулей Atlas «берёт» фразу.

Раньше быстрые команды проверялись длинной цепочкой: каждая функция по очереди смотрела на фразу, и от порядка
зависело, кто её перехватит. Теперь:
  • у каждой функции есть чистая проверка «моя ли это фраза?» (без побочных эффектов);
  • route(text) спрашивает всех и возвращает ВСЕ заявки по приоритету — видно, где функции спорят;
  • pc_fast выполняет заявки по очереди: если первая отказалась (например, «поставь эссе слева», а окна
    с эссе нет), берёт следующую;
  • те же проверки — основа экзамена (evals/run.py): точность, конфликты, скорость.

    route("поставь хром слева") → Route(label="window_layout", claims=["window_layout"])
    LABELS — все метки по приоритету; OWNER — где фраза выполняется (pc_fast / main / brain)
"""
import re
import time
from dataclasses import dataclass, field


@dataclass
class Route:
    label: str                       # победитель или "other" (быстрые команды и мозг)
    claims: list = field(default_factory=list)
    ms: float = 0.0
    scores: dict = field(default_factory=dict)

    @property
    def ambiguous(self) -> bool:
        return len(self.claims) > 1


def _ctx(ctx):
    c = {"last": None, "local": True}
    c.update(ctx or {})
    return c


# --- проверки (каждая — без побочных эффектов) -----------------------------------------------------------------
def _call_secretary(t, c):
    from core import call_secretary
    return call_secretary.intent(t) is not None


def _prompter(t, c):
    from core import prompter
    return c["local"] and prompter.intent(t) is not None


def _messengers(t, c):
    from core import messengers
    return messengers.intent(t, c["last"]) is not None


def _pending_disk(t, c):
    from core import disk_clean
    return bool(disk_clean.pending()) and disk_clean.intent(t, c["last"]) in (("confirm",), ("cancel",))


def _pending_proof(t, c):
    from core import proofread
    p = proofread._s.get("pending")
    if not (c["local"] and p and time.time() - p["created"] < proofread.PENDING_TTL):
        return False
    if c["last"] is not None and c["last"] != proofread._s.get("said"):
        return False
    s = " ".join(t.split()).strip()
    return len(s) < 30 and bool(proofread._YES.search(s) or proofread._NO.search(s))


def _pending_where(t, c):
    from core import where_saved
    return bool(where_saved._s.get("last")) and (where_saved.intent(t, c["last"]) or ("",))[0] in ("open", "reveal", "next")


def _snip(t, c):
    from core import screen_snip
    return c["local"] and screen_snip.intent(t) is not None


def _what_is(t, c):
    from core import what_is
    return c["local"] and what_is.intent(t)


def _scenarios(t, c):
    from core import scenarios
    return scenarios.intent(t) is not None


def _math(t, c):
    from core import quick_math
    return quick_math.answer(t, rate=lambda a, b: 1.0) is not None


def _report(t, c):
    from core import report
    return bool(report._ASK.match(" ".join(t.split()).strip()))


def _proofread(t, c):
    from core import proofread
    s = " ".join(t.split()).strip()
    return c["local"] and bool(proofread._ASK.match(s)) and not re.search(
        r"почт|погод|интернет|обновлен|диск|mail|weather", s, re.I)


def _forms(t, c):
    from core import form_fill
    s = " ".join(t.split()).strip()
    if form_fill._REMEMBER.match(s) or form_fill._SHOW.search(s) or form_fill._FORGET.search(s):
        return True
    return c["local"] and bool(form_fill._FILL.search(s))


def _screen_history(t, c):
    from core import screen_history
    return c["local"] and screen_history.intent(t, c["last"]) is not None


def _voice_notes(t, c):
    from core import voice_notes
    return voice_notes.intent(t) is not None


def _game(t, c):
    from core import game_mode
    return c["local"] and game_mode.intent(t) is not None


def _wellbeing(t, c):
    from core import wellbeing
    return c["local"] and wellbeing.intent(t) is not None


def _files(t, c):
    from core import file_ops
    return file_ops.intent(t) is not None


def _windows(t, c):
    from core import window_layout
    return window_layout.intent(t) is not None


def _watchers(t, c):
    from core import watchers
    return watchers.intent(t) is not None


def _where(t, c):
    from core import where_saved
    return where_saved.intent(t, c["last"]) is not None


def _disk(t, c):
    from core import disk_clean
    return disk_clean.intent(t, c["last"]) is not None


def _speed(t, c):
    from core import turn_trace
    return bool(turn_trace._ASK.search(t))


def _updater(t, c):
    from core import updater
    return updater.intent(t) is not None


def _background(t, c):
    from core import scheduler
    return bool(scheduler._ASK.search(t))


def _usage(t, c):
    from core import usage
    return bool(usage._ASK.search(t))


def _knowledge(t, c):
    from core import knowledge
    return knowledge.intent(t) is not None


def _meeting(t, c):
    from core import meeting_notes
    return meeting_notes.intent(t) is not None


def _link(t, c):
    from core import link_notes
    return link_notes.intent(t) is not None


def _compare(t, c):
    from core import doc_compare
    return doc_compare.intent(t) is not None


def _translate_doc(t, c):
    from core import doc_translate
    return doc_translate.intent(t) is not None


def _squeeze(t, c):
    from core import squeeze
    return squeeze.intent(t) is not None


def _media(t, c):
    from core import media_import
    return media_import.intent(t) is not None


def _dictation(t, c):
    from core import dictation
    return dictation.is_start(t)


def _instant(t, c):
    try:
        from brain import instant_rules as instant
    except Exception:
        return False
    raw = re.sub(r"^\s*\([^)]*\)\s*", "", t)
    q = re.sub(r"^(?:атлас|atlas)[,\s]+", "", raw.lower().strip()).strip(" .!?«»\"")
    try:
        return instant._instant_route(q) is not None
    except Exception:
        return False


# метка, проверка, где выполняется. Порядок = приоритет (как Atlas выполняет их на самом деле).
INTENTS = [
    ("meeting_notes", _meeting, "main"),
    ("call_secretary", _call_secretary, "pc_fast"),
    ("prompter", _prompter, "pc_fast"),
    ("messengers", _messengers, "pc_fast"),
    ("pending_disk", _pending_disk, "pc_fast"),
    ("pending_proofread", _pending_proof, "pc_fast"),
    ("pending_where", _pending_where, "pc_fast"),
    ("screen_snip", _snip, "pc_fast"),
    ("what_is", _what_is, "pc_fast"),
    ("scenarios", _scenarios, "pc_fast"),
    ("quick_math", _math, "pc_fast"),
    ("report", _report, "pc_fast"),
    ("speed_stats", _speed, "pc_fast"),
    ("usage_stats", _usage, "pc_fast"),
    ("background", _background, "pc_fast"),
    ("updater", _updater, "pc_fast"),
    ("knowledge", _knowledge, "pc_fast"),
    ("proofread", _proofread, "pc_fast"),
    ("form_fill", _forms, "pc_fast"),
    ("screen_history", _screen_history, "pc_fast"),
    ("voice_notes", _voice_notes, "pc_fast"),
    ("game_mode", _game, "pc_fast"),
    ("wellbeing", _wellbeing, "pc_fast"),
    ("file_ops", _files, "pc_fast"),
    ("window_layout", _windows, "pc_fast"),
    ("watchers", _watchers, "pc_fast"),
    ("where_saved", _where, "pc_fast"),
    ("disk_clean", _disk, "pc_fast"),
    ("link_notes", _link, "main"),
    ("doc_compare", _compare, "main"),
    ("doc_translate", _translate_doc, "main"),
    ("squeeze", _squeeze, "main"),
    ("media_import", _media, "main"),
    ("dictation", _dictation, "main"),
    ("instant", _instant, "brain"),
]
LABELS = [x[0] for x in INTENTS]
OWNER = {x[0]: x[2] for x in INTENTS}
_STATS = {"routed": 0, "ambiguous": 0}


def route(text: str, ctx: dict = None) -> Route:
    """Все заявки по приоритету. Ошибка в одной проверке не мешает остальным."""
    t0 = time.perf_counter()
    c = _ctx(ctx)
    t = " ".join((text or "").split())
    claims = []
    if t:
        for label, check, _owner in INTENTS:
            try:
                if check(t, c):
                    claims.append(label)
            except Exception as e:
                print(f"[маршрут] {label}: {e}")
    r = Route(label=claims[0] if claims else "other", claims=claims, ms=(time.perf_counter() - t0) * 1000)
    _STATS["routed"] += 1
    if r.ambiguous:
        _STATS["ambiguous"] += 1
    return r


def stats() -> dict:
    return dict(_STATS)
