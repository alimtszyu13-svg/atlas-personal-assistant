"""
Учебный тренер Atlas: интервальное повторение голосом.

  • Колоды карточек: слова (SAT/IELTS-лексика), формулы, факты — любые «вопрос → ответ».
  • Добавить: голосом («добавь в SAT слово ubiquitous — вездесущий») или попросить
    Atlas сгенерировать карточки по теме («сделай 10 карточек по квадратным уравнениям»).
  • Тренировка: «Атлас, давай повторим SAT» — Atlas задаёт вопрос, ты отвечаешь голосом
    без слова «Атлас», он проверяет (сначала сам, по совпадению; если неочевидно —
    быстрой моделью), объясняет ошибку и задаёт следующий.
  • Интервальное повторение (SM-2, как в Anki): знаешь хорошо — карточка вернётся через
    дни и недели; ошибся — через 10 минут в этой же сессии и завтра снова.
  • Статистика: сколько карточек на сегодня, выучено, точность за неделю, серия дней.
"""
import json
import os
import random
import re
import sqlite3
import threading
import time
from datetime import datetime

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory.db")
RELEARN_S = 600                 # ошибся — снова через 10 минут
DAY = 86400

_lock = threading.Lock()
_session = {"deck": None, "queue": [], "current": None, "asked": 0, "correct": 0, "limit": 10, "at": 0.0}
SESSION_IDLE_S = 300            # 5 минут без ответа — тренировка сама заканчивается


def _connect():
    c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS study_cards (id INTEGER PRIMARY KEY, deck TEXT, front TEXT, back TEXT, "
              "ease REAL DEFAULT 2.5, interval REAL DEFAULT 0, due REAL, reps INTEGER DEFAULT 0, "
              "lapses INTEGER DEFAULT 0, created REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS study_reviews (id INTEGER PRIMARY KEY, ts REAL, card INTEGER, grade INTEGER)")
    return c


def _lang() -> str:
    try:
        from voice import get_response_language
        return get_response_language()
    except Exception:
        return "ru"


def _norm(s: str) -> str:
    s = re.sub(r"[«»\"'`.,!?;:()\-–—]", " ", (s or "").lower().replace("ё", "е"))
    return re.sub(r"\s+", " ", s).strip()


_TR = str.maketrans({"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i",
                     "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
                     "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "",
                     "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"})
_ALIASES = {"айелтс": "ielts", "айелтс": "ielts", "иелтс": "ielts", "айлтс": "ielts", "аэлтс": "ielts",
            "эсэйти": "sat", "эсейти": "sat", "сат": "sat", "тойфл": "toefl", "тофл": "toefl"}


def _key(s: str) -> str:
    """Имя колоды для сравнения: распознавание речи пишет «сат» вместо SAT — приводим к латинице."""
    n = _norm(s)
    n = " ".join(_ALIASES.get(w, w) for w in n.split())
    return n.translate(_TR).replace(" ", "")


def _deck_name(deck: str) -> str:
    return (deck or "").strip() or "Общая"


# ---------------------------------------------------------------------------
# Колоды и карточки
# ---------------------------------------------------------------------------
def add_cards(deck: str, items: list) -> int:
    deck = _deck_name(deck)
    rows = [(deck, str(i.get("front", "")).strip()[:300], str(i.get("back", "")).strip()[:500], time.time(), time.time())
            for i in items if isinstance(i, dict) and str(i.get("front", "")).strip() and str(i.get("back", "")).strip()]
    if not rows:
        return 0
    with _lock:
        c = _connect()
        have = {(r[0], _norm(r[1])) for r in c.execute("SELECT deck, front FROM study_cards WHERE deck=?", (deck,))}
        rows = [r for r in rows if (deck, _norm(r[1])) not in have]
        c.executemany("INSERT INTO study_cards (deck, front, back, due, created) VALUES (?,?,?,?,?)", rows)
        c.commit()
        c.close()
    return len(rows)


def decks() -> list:
    now = time.time()
    with _lock:
        c = _connect()
        rows = c.execute("SELECT deck, COUNT(*), SUM(CASE WHEN due<=? THEN 1 ELSE 0 END), "
                         "SUM(CASE WHEN interval>=21 THEN 1 ELSE 0 END) FROM study_cards GROUP BY deck ORDER BY deck",
                         (now,)).fetchall()
        c.close()
    return [{"deck": r[0], "total": r[1], "due": r[2] or 0, "learned": r[3] or 0} for r in rows]


def stats() -> dict:
    now = time.time()
    with _lock:
        c = _connect()
        week = c.execute("SELECT COUNT(*), SUM(CASE WHEN grade>=3 THEN 1 ELSE 0 END) FROM study_reviews WHERE ts>?",
                         (now - 7 * DAY,)).fetchone()
        days = {datetime.fromtimestamp(r[0]).date() for r in c.execute("SELECT ts FROM study_reviews WHERE ts>?",
                                                                       (now - 60 * DAY,))}
        c.close()
    streak, d = 0, datetime.now().date()
    from datetime import timedelta
    if d not in days:
        d -= timedelta(days=1)                      # сегодня ещё не занимался — серия не прервана до полуночи
    while d in days:
        streak += 1
        d -= timedelta(days=1)
    total, ok = week[0] or 0, week[1] or 0
    return {"decks": decks(), "week_reviews": total, "week_accuracy": round(ok / total * 100) if total else None,
            "streak": streak}


def delete_deck(deck: str) -> int:
    with _lock:
        c = _connect()
        n = c.execute("DELETE FROM study_cards WHERE deck=?", (_deck_name(deck),)).rowcount
        c.commit()
        c.close()
    return n


def _llm_json(system: str, user: str, max_tokens: int = 1500) -> dict:
    import ai_brain as _ab
    from ai_brain import client, MODEL_FAST, MODEL_SMART
    from core import llm_gateway
    est = (len(system) + len(user)) // 3 + max_tokens
    cands = _ab._candidates(MODEL_FAST, MODEL_SMART) if hasattr(_ab, "_candidates") else [MODEL_FAST, MODEL_SMART]
    model = llm_gateway.reserve_any(cands, est)
    kw = dict(model=model, max_tokens=max_tokens, response_format={"type": "json_object"},
              messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    if "gpt-oss" in model:
        kw["reasoning_effort"] = "low"
    try:
        cl, kw2 = _ab._client_for(kw) if hasattr(_ab, "_client_for") else (client, kw)
        raw = cl.chat.completions.create(**kw2).choices[0].message.content or ""
    except Exception:
        llm_gateway.reserve(MODEL_FAST, est)
        raw = client.chat.completions.create(**dict(kw, model=MODEL_FAST, reasoning_effort="low")).choices[0].message.content or ""
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0)) if m else {}


def generate_cards(deck: str, topic: str, count: int = 10) -> int:
    count = max(3, min(int(count or 10), 25))
    ru = _lang() == "ru"
    data = _llm_json(
        "You create flashcards for spaced repetition. Return JSON only: {\"cards\": [{\"front\": str, \"back\": str}]}. "
        "front = a short, unambiguous question or term; back = a short exact answer (a word, number, formula or one "
        "short sentence) that can be checked when spoken aloud. No duplicates. "
        + ("Write questions in Russian unless the topic is English vocabulary." if ru else "Write in English."),
        f"Deck: {deck}\nTopic: {topic}\nNumber of cards: {count}")
    return add_cards(deck, data.get("cards") or [])


# ---------------------------------------------------------------------------
# Интервальное повторение (SM-2)
# ---------------------------------------------------------------------------
def _schedule(card_id: int, grade: int) -> None:
    now = time.time()
    with _lock:
        c = _connect()
        ease, interval, reps, lapses = c.execute("SELECT ease, interval, reps, lapses FROM study_cards WHERE id=?",
                                                 (card_id,)).fetchone()
        if grade < 3:
            reps, lapses, interval, due = 0, lapses + 1, 0, now + RELEARN_S
        else:
            reps += 1
            interval = 1 if reps == 1 else (3 if reps == 2 else round(interval * ease, 1))
            due = now + interval * DAY
        ease = max(1.3, ease + 0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
        c.execute("UPDATE study_cards SET ease=?, interval=?, reps=?, lapses=?, due=? WHERE id=?",
                  (ease, interval, reps, lapses, due, card_id))
        c.execute("INSERT INTO study_reviews (ts, card, grade) VALUES (?,?,?)", (now, card_id, grade))
        c.commit()
        c.close()


def _card(card_id: int):
    with _lock:
        c = _connect()
        r = c.execute("SELECT id, deck, front, back FROM study_cards WHERE id=?", (card_id,)).fetchone()
        c.close()
    return {"id": r[0], "deck": r[1], "front": r[2], "back": r[3]} if r else None


def _question(card: dict) -> str:
    _session["at"] = time.time()
    n = _session["asked"] + 1
    ru = _lang() == "ru"
    front = card["front"].strip()
    if len(front.split()) == 1 and not front.endswith("?"):            # одно слово — спрашиваем значение
        front = f"что значит «{front}»" if ru else f"what does «{front}» mean"
    q = (f"Вопрос {n}: {front}" if ru else f"Question {n}: {front}").strip()
    return q if q.endswith("?") else q + "?"                         # «?» в конце — Atlas слушает ответ без имени


# ---------------------------------------------------------------------------
# Сессия
# ---------------------------------------------------------------------------
def active() -> bool:
    if _session["current"] is not None and time.time() - _session["at"] > SESSION_IDLE_S:
        _session.update(deck=None, queue=[], current=None, asked=0, correct=0)     # ушёл — не держим тренировку
    return _session["current"] is not None


def start(deck: str = "", count: int = 10) -> str:
    ru = _lang() == "ru"
    now = time.time()
    with _lock:
        c = _connect()
        if deck.strip():
            # колоду можно назвать неточно: «сат» → «SAT»
            names = [r[0] for r in c.execute("SELECT DISTINCT deck FROM study_cards")]
            match = next((n for n in names if _key(n) == _key(deck)), None) or \
                next((n for n in names if _key(deck) in _key(n) or _key(n) in _key(deck)), None)
            if not match:
                c.close()
                return (f"Колоды «{deck}» нет. Есть: {', '.join(names) or 'пока ни одной'}. "
                        "Могу создать её — скажи тему, и я сделаю карточки."
                        if ru else f"No deck «{deck}». Decks: {', '.join(names) or 'none yet'}.")
            rows = c.execute("SELECT id FROM study_cards WHERE deck=? ORDER BY due", (match,)).fetchall()
            due = [r[0] for r in c.execute("SELECT id FROM study_cards WHERE deck=? AND due<=? ORDER BY due",
                                           (match, now))]
        else:
            match = None
            rows = c.execute("SELECT id FROM study_cards ORDER BY due").fetchall()
            due = [r[0] for r in c.execute("SELECT id FROM study_cards WHERE due<=? ORDER BY due", (now,))]
        c.close()
    if not rows:
        return ("Карточек пока нет. Скажи, например: «сделай 10 карточек по SAT-словам» или «добавь в IELTS слово…»."
                if ru else "No cards yet. Say, e.g., 'make 10 cards on SAT vocabulary'.")
    limit = max(3, min(int(count or 10), 30))
    queue = due[:limit] or [r[0] for r in rows][:limit]           # нечего повторять — повторим ближайшие
    random.shuffle(queue)
    _session.update(deck=match, queue=queue[1:], current=queue[0], asked=0, correct=0, limit=len(queue))
    intro = (f"Поехали: {len(queue)} {'карточек' if len(queue) >= 5 else 'карточки'}"
             + (f" из «{match}»" if match else "") + ". Отвечай голосом, «хватит» — закончить. "
             if ru else f"Let's go: {len(queue)} cards" + (f" from «{match}»" if match else "") + ". Say 'stop' to finish. ")
    return intro + _question(_card(queue[0]))


def _grade(card: dict, answer: str):
    """→ (оценка 0–5, верно ли, короткий комментарий). Сначала сами, потом — быстрая модель."""
    a, b = _norm(answer), _norm(card["back"])
    if not a:
        return 1, False, ""
    if a == b or (len(b) > 3 and (b in a or a in b) and len(a) <= len(b) * 2.5):
        return 5, True, ""
    try:
        d = _llm_json("You grade a spoken flashcard answer. Be lenient with wording, synonyms, speech-recognition "
                      "typos and word order; strict with meaning, numbers and formulas. Return JSON only: "
                      "{\"grade\": 0-5, \"correct\": bool, \"feedback\": \"<one short sentence in the user's "
                      "language explaining the right answer if wrong, empty if right>\"}.",
                      f"Question: {card['front']}\nCorrect answer: {card['back']}\nUser's answer: {answer}", 300)
        g = int(d.get("grade", 0))
        return max(0, min(5, g)), bool(d.get("correct", g >= 3)), str(d.get("feedback") or "")
    except Exception as e:
        print(f"[учёба] проверка моделью недоступна: {e}")
        return 1, False, ""


def answer(text: str) -> str:
    ru = _lang() == "ru"
    if not active():
        return "Сейчас нет тренировки. Скажи «давай повторим» — начнём." if ru else "No session. Say 'let's review'."
    if _norm(text) in ("хватит", "стоп", "закончим", "все", "stop", "enough", "finish", "quit"):
        return stop()
    card = _card(_session["current"])
    grade, ok, fb = _grade(card, text)
    _schedule(card["id"], grade)
    _session["asked"] += 1
    if ok:
        _session["correct"] += 1
        reply = random.choice(["Верно.", "Точно.", "Так и есть.", "Правильно."] if ru
                              else ["Correct.", "Right.", "Exactly.", "Spot on."])
    else:
        reply = (f"Не совсем. Правильно: {card['back']}." if ru else f"Not quite. It's: {card['back']}.") + \
                (f" {fb}" if fb and _norm(card['back']) not in _norm(fb) else "")
        if len(_session["queue"]) < 30:
            _session["queue"].insert(min(3, len(_session["queue"])), card["id"])   # ещё раз в этой же сессии
    if not _session["queue"]:
        return reply + " " + stop()
    _session["current"] = _session["queue"].pop(0)
    return reply + " " + _question(_card(_session["current"]))


def stop() -> str:
    ru = _lang() == "ru"
    asked, correct = _session["asked"], _session["correct"]
    _session.update(deck=None, queue=[], current=None, asked=0, correct=0)
    if not asked:
        return "Хорошо, закончили." if ru else "Alright, stopped."
    pct = round(correct / asked * 100)
    st = stats()
    tail = (f" Серия занятий: {st['streak']} дн." if ru else f" Streak: {st['streak']} days.") if st["streak"] > 1 else ""
    return (f"Итог: {correct} из {asked}, {pct}%.{tail}" if ru else f"Done: {correct} of {asked}, {pct}%.{tail}")
