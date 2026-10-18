"""
Суфлёр на созвоне: Atlas слушает собеседников (звук компьютера — Zoom, Meet, Discord, Telegram) и, когда
вопрос задали ИМЕННО ТЕБЕ, молча показывает готовый ответ карточкой справа сверху — коротко, своими словами,
чтобы прочитать и сказать. Вслух Atlas не говорит ничего. Карточку не видно, если ты показываешь экран
(Windows скрывает её от захвата экрана).

    «включи суфлёр» / «выключи суфлёр»
    «меня зовут Алим» / «на созвонах меня называют Алим, Алимчик»  — по имени он понимает, что спрашивают тебя
    «суфлёр, отвечай на все вопросы» / «только мне»                 — по умолчанию только тебе
    Ctrl+Alt+H — «подскажи»: ответ на последний вопрос, кому бы его ни задали

Как решает, что спрашивают тебя: в реплике собеседника есть вопрос И (твоё имя или обращение на «ты/вы»
без чужого имени, а ты — единственный, кто отвечает). Если в вопросе чужое имя («Дана, а ты что думаешь?») —
молчит. Сомнительное быстрая модель проверяет одним коротким запросом — она же сразу пишет ответ.

Звук собеседников распознаётся кусками по паузам (2–12 с) быстрой моделью Whisper; текст созвона —
только в памяти, на диск не пишется (для записи есть «запиши созвон»).
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time

import numpy as np

SR = 16000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "atlas_prompter.json")
STT_MODEL = os.getenv("PROMPTER_STT_MODEL") or "whisper-large-v3-turbo"
SPEECH_RMS = 0.006
END_SILENCE_S = 0.7
MAX_SEG_S = 12.0
MIN_SEG_S = 0.8
CONTEXT_S = 120
CARD_S = 35
_s = {"on": False, "stop": None, "thread": None, "worker": None, "lines": [], "card": None, "last_q": "",
      "shown": [], "mode": None, "ask_name": False, "said": None}
_lock = threading.Lock()


def _ru(text: str) -> bool:
    return bool(re.search(r"[а-яё]", text or "", re.I))


# --- настройки ---------------------------------------------------------------------------------
def cfg() -> dict:
    try:
        with open(CFG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"names": [], "mode": "me"}


def _save_cfg(c: dict) -> None:
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)


def set_names(text: str) -> list:
    raw = re.split(r"\s*(?:,|\bи\b|\bили\b|\bлибо\b|/|\band\b|\bor\b)\s*", text or "")
    names = []
    for n in raw:
        n = n.strip(" .!«»\"'")
        if 2 <= len(n) <= 25 and re.fullmatch(r"[\w\- ]+", n):
            names.append(n)
    c = cfg()
    c["names"] = names[:6]
    _save_cfg(c)
    return c["names"]


_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщыэюя",
               ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                "u", "f", "kh", "ts", "ch", "sh", "sch", "y", "e", "yu", "ya"]))
_TR_BACK = [("sch", "щ"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("yu", "ю"), ("ya", "я")] + \
    [(c, r) for r, c in zip("абвгдезиклмнопрстуф", "abvgdeziklmnoprstuf")] + [("y", "й"), ("h", "х"), ("j", "дж"),
                                                                             ("w", "у"), ("x", "кс"), ("c", "к")]


def translit(name: str) -> str:
    n = name.lower()
    if re.search(r"[а-яё]", n):
        return "".join(_TR.get(ch, ch) for ch in n)
    out, i = "", 0
    while i < len(n):
        for a, b in _TR_BACK:
            if n.startswith(a, i):
                out += b
                i += len(a)
                break
        else:
            out += n[i]
            i += 1
    return out


def _stems(names: list) -> list:
    from core import messengers
    out = []
    for n in names:
        for v in (n, translit(n)):
            st = messengers.stems(v)
            if st and st[0][:max(3, len(st[0]))] not in out:
                out.append(st[0][:max(3, len(st[0]))])
    return out


# --- кому вопрос ------------------------------------------------------------------------------------
_Q = re.compile(r"\?|(?:^|[\s,])(?:как|что|почему|зачем|когда|где|куда|откуда|кто|чей|какой|какая|какое|какие|сколько|"
                r"можешь|можете|расскажи|расскажите|объясни|объясните|скажи|скажите|согласен|согласна|согласны|"
                r"думаешь|думаете|считаешь|считаете|ли|what|why|how|when|where|who|which|could you|can you|do you|"
                r"did you|have you|would you|are you|will you|tell us|explain)(?=[\s,.?!]|$)", re.I)
_YOU = re.compile(r"(?<![\w])(?:ты|тебе|тебя|тобой|твой|твоя|твоё|твое|твои|вы|вам|вас|вами|ваш|ваша|ваше|ваши|"
                  r"you|your)(?![\w])", re.I)
_GROUP = re.compile(r"(?:кто[- ]нибудь|кто-то из вас|все|ребята|коллеги|друзья|народ|anyone|everyone|somebody|guys)", re.I)
_NAME = re.compile(r"(?:^|[.!?]\s+|,\s*)([А-ЯЁA-Z][а-яёa-z]{2,15})(?=\s*[,!?])")


def addressed(line: str, my_stems: list, mode: str = "me", others: set = None):
    """→ 'me' | 'other' | 'group' | 'maybe' | None (не вопрос)."""
    t = line.strip()
    if not _Q.search(t):
        return None
    low = t.lower().replace("ё", "е")
    words = re.findall(r"[\w-]+", low)
    if my_stems and any(w.startswith(s) for s in my_stems for w in words):
        return "me"
    named = [n for n in _NAME.findall(t) if n.lower() not in ("как", "что", "почему", "когда", "где", "кто", "ну",
                                                                 "так", "вот", "это", "окей", "хорошо", "слушай",
                                                                 "слушайте", "смотри", "смотрите", "ладно", "давай",
                                                                 "давайте", "итак", "значит", "короче", "кстати",
                                                                 "извините", "простите", "подождите", "секунду",
                                                                 "привет", "здравствуйте", "коллеги", "ребята", "да",
                                                                 "нет", "ага", "угу", "what", "why", "how", "okay",
                                                                 "so", "well", "listen", "hello", "hi", "hey",
                                                                 "yeah", "yes", "no", "guys", "sorry", "wait")]
    if named:
        return "other"
    if _GROUP.search(t):
        return "group"
    if _YOU.search(t):
        return "maybe"
    return "maybe" if mode == "all" else None


def _should(kind: str, mode: str, recent_me: bool = False) -> bool:
    """«ты/вы» без имени — подсказываем, только если разговор сейчас с тобой (твоё имя звучало недавно)."""
    if kind == "me":
        return True
    if kind == "other" or kind is None:
        return False
    if mode == "all":
        return True
    return kind == "maybe" and recent_me


# --- ответ -----------------------------------------------------------------------------------------------
def answer(question: str, context: list, names: list, kind: str, ask=None, title: str = ""):
    """→ (показывать ли, текст ответа). Один быстрый запрос: и проверка «тебе ли», и сам ответ."""
    lang = "Russian" if _ru(question + " ".join(context[-3:])) else "English"
    me = ", ".join(names) or "the user"
    system = (
        f"You are a silent prompter in a live call. The user ({me}) will READ your text aloud as their own answer. "
        "Return strict JSON: {\"to_user\": true|false, \"answer\": \"...\"}. "
        f"to_user=false if the question is clearly addressed to someone else (another person's name, or someone else "
        f"is being asked to report) — then answer is \"\". Otherwise write the answer: in {lang}, first person, "
        "spoken style, 1–2 short sentences, at most ~35 words; give the actual answer immediately — facts, numbers, "
        "a clear position. No preambles like 'Great question', no 'I think that maybe', no lists, no markdown. "
        "If you lack the facts (personal status, private data), give a short honest phrase the user can say, e.g. "
        "promise to check and reply after the call.")
    ctx = "\n".join(context[-14:])
    user = (f"Call: {title}\n" if title else "") + f"Recent conversation (others' speech, OCR-free STT):\n{ctx}\n\n" \
        f"Question just asked: {question}\nHeuristic: {kind}"
    raw = (ask or _ask_fast)(system, user)
    from core import memory
    data = memory.parse_json(raw) if hasattr(memory, "parse_json") else {}
    if not data:
        m = re.search(r"\{.*\}", raw or "", re.S)
        try:
            data = json.loads(m.group(0)) if m else {}
        except Exception:
            data = {}
    if not data and raw and not raw.strip().startswith("{"):
        return kind == "me", raw.strip()[:300]
    to_user = bool(data.get("to_user", kind == "me"))
    text = (data.get("answer") or "").strip()
    if kind == "me":
        to_user = True
    return (to_user and bool(text)), text[:400]


def _ask_fast(system: str, user: str) -> str:
    """Быстрая модель: сначала Groq fast (ответ за ~0,5 с), потом остальные."""
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        from brain import providers as P
        tries = [(P.client, P.MODEL_FAST, {"reasoning_effort": "low"})]
        if getattr(P, "cerebras_client", None) is not None:
            tries.append((P.cerebras_client, P.CEREBRAS_MODEL.split(":", 1)[1], {"reasoning_effort": "low"}))
    except Exception:
        tries = []
    for cl, model, extra in tries:
        try:
            r = cl.chat.completions.create(model=model, messages=msgs, max_tokens=260, temperature=0.2, **extra)
            out = r.choices[0].message.content or ""
            if out.strip():
                return out
        except Exception as e:
            print(f"[суфлёр] {model}: {str(e)[:90]}")
    from core import quick_llm
    return quick_llm.ask(system, user, max_tokens=260)


# --- карточка на экране (отдельный процесс — не мешает окнам Atlas) ----------------------------------------
class Card:
    def __init__(self):
        self.p = None

    def _ensure(self):
        if self.p and self.p.poll() is None:
            return True
        exe = sys.executable
        if exe.lower().endswith("python.exe") and os.path.exists(exe[:-10] + "pythonw.exe"):
            exe = exe[:-10] + "pythonw.exe"
        try:
            self.p = subprocess.Popen([exe, "-m", "core.hint_card"], cwd=ROOT, stdin=subprocess.PIPE, text=True,
                                      encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return True
        except Exception as e:
            print(f"[суфлёр] карточка не открылась: {e}")
            return False

    def show(self, title: str, text: str, seconds: int = CARD_S) -> None:
        if self._ensure():
            try:
                self.p.stdin.write(json.dumps({"title": title, "text": text, "s": seconds}, ensure_ascii=False) + "\n")
                self.p.stdin.flush()
            except Exception as e:
                print(f"[суфлёр] карточка: {e}")

    def close(self) -> None:
        if self.p and self.p.poll() is None:
            try:
                self.p.stdin.write('{"quit": true}\n')
                self.p.stdin.flush()
            except Exception:
                pass


# --- слух ---------------------------------------------------------------------------------------------------
class Segmenter:
    """Звук собеседников → куски речи по паузам."""

    def __init__(self, on_seg):
        self.on_seg, self.buf, self.silence, self.speech = on_seg, [], 0.0, 0.0

    def add(self, a: np.ndarray) -> None:
        dur = len(a) / SR
        loud = float(np.sqrt(np.mean(a * a))) > SPEECH_RMS if len(a) else False
        if loud:
            self.buf.append(a)
            self.speech += dur
            self.silence = 0.0
        elif self.buf:
            self.buf.append(a)
            self.silence += dur
        total = sum(len(b) for b in self.buf) / SR
        if self.buf and (self.silence >= END_SILENCE_S or total >= MAX_SEG_S):
            seg = np.concatenate(self.buf)
            speech = self.speech
            self.buf, self.silence, self.speech = [], 0.0, 0.0
            if speech >= MIN_SEG_S:
                self.on_seg(seg)


def _stt(seg: np.ndarray) -> str:
    import io
    import wave
    import voice
    pcm = (np.clip(seg, -1, 1) * 32767).astype(np.int16)
    bio = io.BytesIO()
    with wave.open(bio, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())
    kw = {"model": STT_MODEL, "temperature": 0.0}
    try:
        lang = voice.get_response_language()
        if lang in ("ru", "en"):
            kw["language"] = lang
    except Exception:
        pass
    r = voice.groq_client.audio.transcriptions.create(file=("seg.wav", bio.getvalue()), **kw)
    text = (getattr(r, "text", "") or "").strip()
    from core import meeting_notes
    return "" if meeting_notes._junk(text) else text


def _capture(feed, stop: threading.Event) -> None:
    from core import meeting_notes
    sc = meeting_notes._soundcard()
    spk = sc.default_speaker()
    dev = sc.get_microphone(id=str(spk.name), include_loopback=True)
    rate = 48000
    with dev.recorder(samplerate=rate, blocksize=1024) as rec:
        while not stop.is_set():
            data = rec.record(numframes=rate // 5)
            if data is not None and len(data):
                feed(meeting_notes.to_16k_mono(data, rate))


# --- работа ---------------------------------------------------------------------------------------------------
def on_line(text: str, card=None, ask=None, now: float = None) -> str:
    """Новая реплика собеседника. Возвращает показанный ответ или ''."""
    now = now or time.time()
    c = cfg()
    with _lock:
        _s["lines"].append((now, text))
        _s["lines"] = [(t, x) for t, x in _s["lines"] if now - t < CONTEXT_S]
        ctx = [x for _, x in _s["lines"]]
    my = _stems(c.get("names") or [])
    kind = addressed(text, my, c.get("mode", "me"))
    if kind is None:
        return ""
    _s["last_q"] = text
    recent_me = any(now - t < 45 and any(w.startswith(s) for s in my for w in re.findall(r"[\w-]+", x.lower()))
                    for t, x in _s["lines"][:-1]) if my else False
    if not _should(kind, c.get("mode", "me"), recent_me):
        print(f"[суфлёр] вопрос не тебе ({kind}): {text[:80]}")
        return ""
    try:
        ok, reply = answer(text, ctx, c.get("names") or [], kind, ask, title=_title())
    except Exception as e:
        print(f"[суфлёр] {e}")
        return ""
    if not ok:
        print(f"[суфлёр] модель: не тебе — {text[:80]}")
        return ""
    _show(text, reply, card)
    return reply


def hint_now(card=None, ask=None) -> str:
    """Ctrl+Alt+H — ответ на последний вопрос, кому бы его ни задали."""
    q = _s.get("last_q") or (_s["lines"][-1][1] if _s["lines"] else "")
    if not q:
        return ""
    ctx = [x for _, x in _s["lines"]]
    try:
        _ok, reply = answer(q, ctx, cfg().get("names") or [], "me", ask, title=_title())
    except Exception as e:
        print(f"[суфлёр] {e}")
        return ""
    if reply:
        _show(q, reply, card)
    return reply


def _title() -> str:
    try:
        from core import meeting_notes
        return meeting_notes._s.get("title", "") if meeting_notes.active() else ""
    except Exception:
        return ""


def _show(q: str, reply: str, card=None) -> None:
    print(f"[суфлёр] ❓ {q[:90]}\n[суфлёр] 💬 {reply}")
    _s["shown"].append((time.time(), q, reply))
    del _s["shown"][:-30]
    (card or _s.get("card") or Card()).show(q[:90], reply)
    try:
        from ui_state import shared_state
        shared_state.setdefault("chat_history", []).append(("Atlas", f"💬 Суфлёр — «{q[:80]}»: {reply}"))
    except Exception:
        pass


def start(capture=None, stt=None, card=None) -> str:
    c = cfg()
    if _s["on"]:
        return "Суфлёр уже включён."
    if not c.get("names"):
        _s["ask_name"] = True
        _s["said"] = "Как тебя называют на созвонах? Скажи, например: «меня зовут Алим»."
        return _s["said"]
    stop = threading.Event()
    segs = queue.Queue()
    _s.update(on=True, stop=stop, lines=[], card=card or Card(), last_q="")
    seg = Segmenter(lambda a: segs.put(a))

    def work():
        while not stop.is_set():
            try:
                a = segs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                text = (stt or _stt)(a)
            except Exception as e:
                print(f"[суфлёр] распознавание: {e}")
                continue
            if text:
                print(f"[суфлёр] 🗣 {text}")
                threading.Thread(target=on_line, args=(text,), daemon=True).start()

    def listen():
        try:
            (capture or _capture)(seg.add, stop)
        except Exception as e:
            print(f"[суфлёр] не слышу звук компьютера: {e}")
            _s["on"] = False
    _s["worker"] = threading.Thread(target=work, daemon=True, name="prompter-stt")
    _s["thread"] = threading.Thread(target=listen, daemon=True, name="prompter-ears")
    _s["worker"].start()
    _s["thread"].start()
    who = ", ".join(c["names"])
    mode = "на все вопросы" if c.get("mode") == "all" else f"только когда спрашивают тебя ({who})"
    return f"Суфлёр включён — молча подсказываю {mode}. Ctrl+Alt+H — подсказка на последний вопрос."


def stop() -> str:
    if not _s["on"]:
        return "Суфлёр и так выключен."
    _s["on"] = False
    if _s["stop"]:
        _s["stop"].set()
    if _s.get("card"):
        _s["card"].close()
    n = len(_s["shown"])
    return f"Суфлёр выключен. Подсказок за созвон: {n}." if n else "Суфлёр выключен."


def active() -> bool:
    return bool(_s["on"])


# --- фразы -----------------------------------------------------------------------------------------------------
_ON = re.compile(r"(?:включи|запусти|активируй|start|turn on|enable)\s+(?:мне\s+)?(?:суфл[её]р\w*|подсказ\w* на созвон\w*|"
                 r"prompter|call (?:assistant|helper))|(?:суфл[её]р|prompter)[,\s]+(?:вкл\w*|on|start)", re.I)
_OFF = re.compile(r"(?:выключи|останови|отключи|убери|stop|turn off|disable)\s+(?:суфл[её]р\w*|подсказ\w*|prompter)|"
                  r"(?:суфл[её]р|prompter)[,\s]+(?:выкл\w*|off|stop|хватит)", re.I)
_NAMES = re.compile(r"(?:меня зовут|меня называют|ко мне обращаются|на созвон\w* меня (?:зовут|называют)|my name is|"
                    r"call me|they call me)\s+(.+)", re.I)
_MODE_ALL = re.compile(r"(?:суфл[её]р\w*[,\s]+)?(?:отвечай|подсказывай)\s+(?:на\s+)?все\s+вопрос\w*|answer all questions", re.I)
_MODE_ME = re.compile(r"(?:суфл[её]р\w*[,\s]+)?(?:отвечай|подсказывай)\s+только\s+(?:мне|на мои|когда меня)|only (?:me|mine)", re.I)


def intent(text: str):
    """Без побочных эффектов: names | off | on | all | me | None."""
    t = " ".join((text or "").split()).strip(" .!")
    if not t:
        return None
    if _NAMES.search(t) and (_s["ask_name"] or re.search(r"созвон|суфл|call", t, re.I)):
        return "names"
    for kind, rx in (("off", _OFF), ("on", _ON), ("all", _MODE_ALL), ("me", _MODE_ME)):
        if rx.search(t):
            return kind
    return None


def handle(text: str, last: str = None):
    t = " ".join((text or "").split()).strip(" .!")
    if not t:
        return None
    m = _NAMES.search(t)
    if m and (_s["ask_name"] or re.search(r"созвон|суфл|call", t, re.I)):
        names = set_names(m.group(1))
        _s["ask_name"] = False
        if not names:
            return "Не расслышал имя — скажи ещё раз: «меня зовут Алим»."
        return start() if not _s["on"] else f"Запомнил: {', '.join(names)}."
    if _OFF.search(t):
        return stop()
    if _ON.search(t):
        return start()
    if _MODE_ALL.search(t):
        c = cfg()
        c["mode"] = "all"
        _save_cfg(c)
        return "Хорошо, подсказываю на все вопросы."
    if _MODE_ME.search(t):
        c = cfg()
        c["mode"] = "me"
        _save_cfg(c)
        return "Хорошо, подсказываю только когда спрашивают тебя."
    return None
