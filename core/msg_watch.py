"""
Важные сообщения вслух: «Сэр, мама пишет в Telegram: «Купи хлеб»» — и сразу можно ответить голосом.

    после объявления (без «Атлас»):  «скажи, что куплю» / «ответь: буду в 7» / «ответь ей, что…»
                                       → обычный черновик мессенджеров, «Отправить?», после «да» — отправка
    «сообщай о сообщениях от Алима» / «добавь Дану в важные»      → важные
    «не сообщай о сообщениях от группы класса» / «убери Дану из важных»
    «сообщай обо всех сообщениях» / «только о важных» / «не сообщай о сообщениях» / «включи уведомления о сообщениях»
    «о ком ты сообщаешь?»                                          → список важных
    «что я пропустил?» / «какие сообщения пришли?»                 → кто писал, пока Atlas молчал

Откуда берёт: уведомления Windows (их показывают Telegram, WhatsApp, Discord) лежат в базе Windows
%LOCALAPPDATA%\\Microsoft\\Windows\\Notifications\\wpndatabase.db. Atlas читает её только для чтения, раз в
пару секунд — без доступа к экрану, без разрешений и без интернета. Текст никуда не отправляется.

Важные по умолчанию: родные («Мама», «Папа», «Брат»…) и все, кому ты уже писал через Atlas.
Не мешает: когда ты сам смотришь в этот мессенджер, в фокусе, игре, диктовке и суфлёре — копит и
пересказывает потом одной фразой. Несколько сообщений подряд от одного чата — одним объявлением.

    python -m core.msg_watch check   → последние уведомления из базы Windows: видит ли их Atlas
"""
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(ROOT, "atlas_msg_watch.json")
DB = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Notifications", "wpndatabase.db")
APP_IDS = {"telegram": re.compile(r"telegram|ayugram|64gram|kotatogram", re.I),
           "whatsapp": re.compile(r"whatsapp", re.I),
           "discord": re.compile(r"discord", re.I)}
NAMES = {"telegram": "Telegram", "whatsapp": "WhatsApp", "discord": "Discord"}
BATCH_S = 4.0            # сообщения подряд от одного чата ждём столько и объявляем вместе
COOLDOWN_S = 90.0        # тот же чат снова — не раньше (дальше копим «и ещё N»)
MAX_BODY = 160           # длиннее — читаем начало
REPLY_WINDOW = 180       # столько секунд после объявления «скажи, что…» отвечает этому чату
_lock = threading.Lock()
_s = {"thread": None, "announce": None, "last_id": None, "queue": {}, "last_said": {}, "missed": [],
      "src": None, "env": None, "seen": {}, "calls": [], "log": []}
LOG_MAX = 120            # журнал всех сообщений (не только важных) — для «кто мне писал?»
DEDUP_S = 120            # то же уведомление (Windows иногда показывает дважды) — не повторяем
_HIDDEN = re.compile(r"^(?:новое сообщение|новые сообщения|сообщение|у вас (?:новое|\d+ новых?) сообщени\w*|"
                     r"new message|new messages|\d+ new messages?|message|you have (?:a )?new messages?)\.?$", re.I)
_GROUP = re.compile(r"^([A-ZА-ЯЁ][\w.'-]*(?:\s[A-ZА-ЯЁ][\w.'-]*){0,2}):\s+(.+)$")


def _ru() -> bool:
    try:
        from voice import get_response_language
        return get_response_language() == "ru"
    except Exception:
        return True


# --- настройки -------------------------------------------------------------------------------------
def settings() -> dict:
    try:
        with open(CONF, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault("mode", "important")          # important | all | off
    d.setdefault("important", [])
    d.setdefault("muted", [])
    return d


def _save(d: dict) -> None:
    with _lock:
        try:
            with open(CONF, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
        except Exception as e:
            print(f"[сообщения] не сохранил настройки: {e}")


def _match(title: str, who: str) -> bool:
    from core import messengers as M
    st = M.stems(who)
    return bool(st) and M.match_score(title, st, M.is_kin(who)) >= 1


def _remembered_titles() -> list:
    """Те, кому уже писал через Atlas (atlas_messengers.json) — их сообщения важны."""
    from core import messengers as M
    out = []
    for v in M._load().values():
        out += [t for t in (M._entry(v).get("titles") or {}).values() if t]
    return out


def important(title: str, body: str = "", d: dict = None) -> bool:
    from core import messengers as M
    d = d or settings()
    if d["mode"] == "off":
        return False
    if any(_match(title, m) for m in d["muted"]):
        return False
    if d["mode"] == "all":
        return True
    if any(_match(title, w) for w in d["important"]):
        return True
    if any(M.norm(t) == M.norm(title) for t in _remembered_titles()):
        return True
    words = M.norm(title).split()
    if words and all(M._KIN.match(w) or M._NOISE_W.match(w) for w in words):   # «Мама ❤», «Папа», «Брат»
        return True
    low = M.norm(body)                                         # слова-маркеры со строчной: «срочно», «unist»
    keys = [w for w in d["important"] if " " not in w and w[:1].islower() and len(w) >= 4]
    return any(M.norm(w) in low for w in keys)


# --- источник: база уведомлений Windows ------------------------------------------------------------------
class Source:
    def __init__(self, path: str = DB):
        self.path = path

    def _conn(self):
        import sqlite3
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=1.0)

    def last_id(self) -> int:
        c = self._conn()
        try:
            return int(c.execute("SELECT IFNULL(MAX(Id), 0) FROM Notification").fetchone()[0])
        finally:
            c.close()

    def recent(self, hours: float = 6) -> list:
        """[(id, приложение, [строки], unix-время)] — уведомления за последние часы (и до запуска Atlas)."""
        import time as _t
        ft = int((_t.time() - hours * 3600 + 11644473600) * 10_000_000)      # FILETIME: 100 нс с 1601 года
        c = self._conn()
        try:
            rows = c.execute("SELECT n.Id, h.PrimaryId, n.Payload, n.ArrivalTime FROM Notification n "
                             "JOIN NotificationHandler h ON n.HandlerId = h.RecordId "
                             "WHERE n.Type = 'toast' AND n.ArrivalTime >= ? ORDER BY n.Id", (ft,)).fetchall()
        finally:
            c.close()
        out = []
        for nid, app_id, payload, at in rows:
            app = next((k for k, rx in APP_IDS.items() if rx.search(str(app_id or ""))), None)
            if app:
                out.append((int(nid), app, parse_payload(payload), at / 10_000_000 - 11644473600))
        return out

    def since(self, after: int) -> list:
        """[(id, приложение, [строки текста])] — новые уведомления мессенджеров."""
        c = self._conn()
        try:
            rows = c.execute("SELECT n.Id, h.PrimaryId, n.Payload FROM Notification n "
                             "JOIN NotificationHandler h ON n.HandlerId = h.RecordId "
                             "WHERE n.Id > ? AND n.Type = 'toast' ORDER BY n.Id", (after,)).fetchall()
        finally:
            c.close()
        out = []
        for nid, app_id, payload in rows:
            app = next((k for k, rx in APP_IDS.items() if rx.search(str(app_id or ""))), None)
            texts = parse_payload(payload)
            out.append((int(nid), app, texts))
        return out


def parse_payload(payload) -> list:
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", "replace")
    try:
        root = ET.fromstring(payload or "")
    except ET.ParseError:
        return []
    return [" ".join((t.text or "").split()) for t in root.iter() if t.tag.lower().endswith("text") and (t.text or "").strip()]


def classify(title: str, body: str) -> str:
    """call — входящий звонок, missed — пропущенный, hidden — текст скрыт настройками, msg — сообщение."""
    from core import call_secretary as C
    short = len((body or "").split()) <= 6
    if short and C._MISSED.search(body or ""):
        return "missed"
    if short and C._CALL.search(body or ""):
        return "call"
    if (title or "").strip().lower() in ("telegram", "whatsapp", "discord", "ayugram") or _HIDDEN.match((body or "").strip()):
        return "hidden"
    return "msg"


def split_message(texts: list):
    """[«Мама», «Купи хлеб»] → («Мама», «Купи хлеб», False);
    группа — три строки [«Класс», «Дана», «завтра ко второму»] → («Класс», «Дана: завтра ко второму», True)."""
    if not texts:
        return "", "", False
    if len(texts) >= 3 and len(texts[1].split()) <= 3 and not texts[1].rstrip().endswith((".", "!", "?", "…")):
        return texts[0], f"{texts[1]}: {' '.join(texts[2:]).strip()}", True
    return texts[0], " ".join(texts[1:]).strip(), False


# --- окружение (в тестах подменяется) --------------------------------------------------------------------
class Env:
    def busy(self) -> bool:
        try:
            from ui_state import shared_state
            return shared_state.get("state") in ("speaking", "thinking", "listening")
        except Exception:
            return False

    def quiet(self) -> bool:
        try:
            from core import dictation, focus, game_mode, prompter
            return bool(focus.active() or dictation.active() or prompter.active() or game_mode.active())
        except Exception:
            return False

    def in_call(self) -> bool:
        """Звонят или идёт разговор — сообщения копим и пересказываем после."""
        try:
            from core import call_secretary
            return call_secretary.busy()
        except Exception:
            return False

    def looking_at(self, app: str) -> bool:
        """Мессенджер сейчас на переднем плане — ты и так видишь сообщение."""
        try:
            from core import messengers as M
            ui = M._get_ui()
            w = M.find_window(app, ui.windows())
            return bool(w) and ui.foreground() == w["hwnd"]
        except Exception:
            return False

    def listen_now(self):
        try:
            import voice
            voice.trigger_push_to_talk()
        except Exception:
            pass

    def now(self) -> float:
        return time.time()


# --- объявление ---------------------------------------------------------------------------------------------
def _quote(body: str) -> str:
    b = body.strip()
    if len(b) > MAX_BODY:
        cut = b[:MAX_BODY].rsplit(" ", 1)[0]
        return f"«{cut}…»"
    return f"«{b}»"


def spoken_name(title: str, ru: bool = True) -> str:
    """Как назвать вслух: «Мама ❤» → «мама», «Алим Сыдыков 🏀» → «Алим Сыдыков»."""
    from core import messengers as M
    clean = " ".join(re.findall(r"[\w@.+'-]+", title or "")) or (title or "")
    if ru and M.is_kin(clean):
        return M._nominal(clean).lower()
    return clean


def line(app: str, title: str, bodies: list, ru: bool, group: bool = False) -> str:
    who = spoken_name(title, ru)
    where = NAMES.get(app, app)
    g = _GROUP.match(bodies[-1] or "") if group else None
    if g:                                                             # «Дана: завтра ко второму» в группе «Класс»
        sender = g.group(1)
        texts = [(_GROUP.match(b).group(2) if _GROUP.match(b) else b) for b in bodies]
        if ru:
            more = "" if len(texts) == 1 else f" (сообщений: {len(texts)})"
            return f"Сэр, в «{who}» {sender} пишет{more}: {_quote(texts[-1])}"
        return f"Sir, in “{who}” {sender} wrote: {_quote(texts[-1])}"
    if len(bodies) == 1:
        return (f"Сэр, {who} пишет в {where}: {_quote(bodies[0])}" if ru else
                f"Sir, {who} on {where}: {_quote(bodies[0])}")
    first, rest = bodies[0], len(bodies) - 1
    if ru:
        more = {1: "и ещё одно сообщение", 2: "и ещё два", 3: "и ещё три", 4: "и ещё четыре"}.get(rest, f"и ещё {rest}")
        return f"Сэр, {who} пишет в {where}: {_quote(first)} — {more}: {_quote(bodies[-1])}"
    return f"Sir, {who} on {where}: {_quote(first)} — and {rest} more, the last: {_quote(bodies[-1])}"


def _set_reply_target(app: str, title: str, now: float) -> None:
    from core import messengers as M
    M._s["incoming"] = {"app": app, "title": title, "ts": now}


def tick(src=None, env=None, announce=None) -> str:
    """Один проход: новые уведомления → очередь по чатам → объявить созревшие. → сказанное или ''."""
    src, env = src or _s["src"] or Source(), env or _s["env"] or Env()
    now = env.now()
    if _s["last_id"] is None:                                       # всё, что было до запуска, — не новое
        _s["last_id"] = src.last_id()
        try:                                                        # но в журнал «кто писал» — за последние часы
            for _nid, app, texts, at in src.recent(6):
                title, body, group = split_message(texts)
                if title and body and classify(title, body.split(": ", 1)[-1] if group else body) == "msg":
                    _log(app, title, body, at)
        except Exception as e:
            print(f"[сообщения] журнал: {e}")
        return ""
    try:
        top = src.last_id()
        if top < _s["last_id"]:                                     # Windows очистил базу — номера начались заново
            _s["last_id"] = top
    except Exception:
        pass
    d = settings()
    for k in [k for k, t in _s["seen"].items() if now - t > DEDUP_S]:
        del _s["seen"][k]
    for nid, app, texts in src.since(_s["last_id"]):
        _s["last_id"] = max(_s["last_id"], nid)
        if not app:
            continue
        title, body, group = split_message(texts)
        if not title:
            continue
        kind = classify(title, body.split(": ", 1)[-1] if group else body)
        if kind == "call":                                          # звонок — дело секретаря, он и объявит
            try:
                from core import call_secretary
                call_secretary.hint(NAMES.get(app, app), title)
            except Exception as e:
                print(f"[сообщения] звонок: {e}")
            continue
        if kind == "missed":
            try:
                from core import call_secretary
                told = call_secretary.recently_announced(title)
            except Exception:
                told = False
            if not told and important(title, "", d) and d["mode"] != "off":
                _s["calls"].append((app, title))
            continue
        if kind == "hidden" or not body:
            continue
        if not important(title, body, d):
            if (app, title, body) not in _s["seen"]:
                _s["seen"][(app, title, body)] = now
                _log(app, title, body)                              # не объявляем, но «кто писал?» — помним
            continue
        dk = (app, title, body)
        if dk in _s["seen"]:
            continue
        _s["seen"][dk] = now
        _log(app, title, body)
        if env.looking_at(app):
            continue
        q = _s["queue"].setdefault((app, title), {"bodies": [], "first": now, "last": now, "group": group})
        q["bodies"].append(body)
        q["last"] = now
    said = ""
    quiet = env.quiet() or env.in_call()
    if _s["calls"] and not quiet and not env.busy():               # пропущенный звонок — раньше сообщений
        app, title = _s["calls"].pop(0)
        who = spoken_name(title, _ru())
        said = (f"Сэр, пропущенный звонок от {_gen(who)} в {NAMES.get(app, app)}." if _ru() else
                f"Sir, missed call from {who} on {NAMES.get(app, app)}.")
        _set_reply_target(app, title, now)
    for key in list(_s["queue"]):
        q = _s["queue"][key]
        if now - q["last"] < BATCH_S:                               # ещё может прийти продолжение
            continue
        if now - _s["last_said"].get(key, 0) < COOLDOWN_S and len(q["bodies"]) < 3:
            continue                                                # только что говорили про этот чат
        if quiet:
            _s["missed"].append((key[0], key[1], list(q["bodies"])))
            del _s["queue"][key]
            continue
        if env.busy() or said:
            continue                                                # Atlas занят — скажем на следующем проходе
        del _s["queue"][key]
        _s["last_said"][key] = now
        said = line(key[0], key[1], q["bodies"], _ru(), q.get("group", False))
        _set_reply_target(key[0], key[1], now)
    if not said and _s["missed"] and not quiet and not env.busy():
        said = missed_line(_ru(), clear=True)
        if said and len({(a, t) for a, t, _ in _s.get("_last_missed", [])}) == 1:
            a, t, _b = _s["_last_missed"][0]
            _set_reply_target(a, t, now)
    if said:
        print(f"[сообщения] {said}")
        f = announce or _s.get("announce")
        if f:
            f(said)
        env.listen_now()                                            # ответ — без имени
    return said


def _log(app: str, title: str, body: str, at: float = None) -> None:
    import time as _t
    at = at if at is not None else _t.time()
    if any(x[1:4] == (app, title, body) for x in _s["log"][-30:]):
        return
    _s["log"].append((at, app, title, body))
    del _s["log"][:-LOG_MAX]


def recent(app: str = None, hours: float = 6) -> list:
    """[(время, приложение, чат, [тексты])] — кто писал за последние часы, свежие первыми."""
    import time as _t
    cut = _t.time() - hours * 3600
    by = {}
    for at, a, title, body in _s["log"]:
        if at < cut or (app and a != app):
            continue
        e = by.setdefault((a, title), [at, a, title, []])
        e[0] = max(e[0], at)
        e[3].append(body)
    return sorted(by.values(), key=lambda e: -e[0])


def app_of(who: str):
    """В каком мессенджере недавно писал этот человек («открой фото от папы»)."""
    for _at, a, title, _b in recent():
        if _match(title, who):
            return a
    return None


def _ago(at: float, ru: bool) -> str:
    import time as _t
    m = int((_t.time() - at) // 60)
    if m < 2:
        return "только что" if ru else "just now"
    if m < 60:
        return f"{m} мин назад" if ru else f"{m} min ago"
    return f"{m // 60} ч назад" if ru else f"{m // 60} h ago"


def recent_line(app: str = None, ru: bool = True, hours: float = 6) -> str:
    """«За последние часы писали: мама — «Купи хлеб» (5 мин назад); Алибек — «ок спасибо» (2 ч назад)». '' — никто."""
    items = recent(app, hours)
    if not items:
        return ""
    parts = []
    for at, a, title, bodies in items[:5]:
        where = "" if app else f" в {NAMES.get(a, a)}"
        who = spoken_name(title, ru)
        more = f" ({len(bodies)} сообщ.)" if len(bodies) > 1 and ru else ""
        parts.append(f"{who}{where} — {_quote(bodies[-1])}{more}, {_ago(at, ru)}")
    rest = f" И ещё {len(items) - 5}." if len(items) > 5 else ""
    return ("Писали: " if ru else "Wrote: ") + "; ".join(parts) + "." + rest


def _gen(who: str) -> str:
    """«мама» → «мамы», «Алим» → «Алима» — для «пропущенный звонок от …»."""
    try:
        from core import call_secretary
        return call_secretary.genitive(who)
    except Exception:
        return who


def missed_line(ru: bool, clear: bool = False) -> str:
    from core import messengers as M
    miss = _s["missed"]
    if not miss:
        return "Новых важных сообщений не было." if ru else "No important messages."
    by = {}
    for app, title, bodies in miss:
        by.setdefault((app, title), []).extend(bodies)
    parts = []
    for (app, title), bodies in list(by.items())[:4]:
        who = spoken_name(title, ru)
        parts.append(f"{who} — {_quote(bodies[-1])}" if len(bodies) == 1 else
                     (f"{who} — {len(bodies)} сообщ., последнее {_quote(bodies[-1])}" if ru else
                      f"{who} — {len(bodies)} messages, last {_quote(bodies[-1])}"))
    if clear:
        _s["_last_missed"] = list(miss)
        _s["missed"] = []
    return ("Пока ты был занят, писали: " if ru else "While you were busy: ") + "; ".join(parts) + "."


# --- команды голосом --------------------------------------------------------------------------------------------
_ADD = re.compile(r"^(?:сообщай|говори|предупреждай|рассказывай)\s+(?:мне\s+)?(?:о|об|про)\s+(?:сообщени\w+|переписк\w+)\s+"
                  r"(?:от|из)\s+(.+)$|^добавь\s+(.+?)\s+в\s+важн\w+$|^(.+?)\s+[—-]?\s*важн\w+(?:\s+контакт\w*)?$", re.I)
_DEL = re.compile(r"^(?:не\s+(?:сообщай|говори|предупреждай)|перестань\s+(?:сообщать|говорить))\s+(?:мне\s+)?(?:о|об|про)\s+"
                  r"(?:сообщени\w+|переписк\w+)\s+(?:от|из)\s+(.+)$|^убери\s+(.+?)\s+из\s+важн\w+$", re.I)
_ALL = re.compile(r"^(?:сообщай|говори)\s+(?:мне\s+)?(?:о|обо|про)\s+(?:всех|все)\s+сообщени\w+$", re.I)
_IMP = re.compile(r"^(?:сообщай|говори)\s+(?:мне\s+)?только\s+(?:о|об|про)\s+важн\w+(?:\s+сообщени\w+)?$|"
                  r"^(?:включи|верни)\s+(?:уведомлени\w+|оповещени\w+)\s+(?:о|об|про)\s+сообщени\w+$|^только\s+важн\w+$", re.I)
_OFF = re.compile(r"^(?:не\s+(?:сообщай|говори)|перестань\s+(?:сообщать|говорить)|выключи\s+(?:уведомлени\w+|оповещени\w+))"
                  r"\s+(?:мне\s+)?(?:о|об|про)\s+сообщени\w+$", re.I)
_LIST = re.compile(r"^(?:о|про)\s+ко(?:м|го)\s+ты\s+(?:сообщаешь|говоришь)|^кто\s+(?:у меня\s+)?важн\w+|"
                   r"^(?:чьи|какие)\s+сообщени\w+\s+(?:ты\s+)?(?:читаешь|объявляешь)", re.I)
_MISSED = re.compile(r"^(?:что|кто)\s+(?:я\s+)?пропустил\w*|^(?:какие|кто)\s+(?:мне\s+)?(?:сообщени\w+\s+)?(?:пришл\w+|писал\w*)"
                     r"(?:\s+сообщени\w+)?(?:\s+пока\s+.+)?$", re.I)


_RECENT = re.compile(r"^(?:мне\s+)?(?:кто-?(?:то|нибудь)\s+)?(?:что-?(?:то|нибудь)\s+)?(?:мне\s+)?"
                     r"(?:писал\w*|написал\w*|присылал\w*|прислал\w*)(?:\s+(?:мне|кто-?(?:то|нибудь)|что-?(?:то|нибудь)))*$|"
                     r"^(?:кто|что)\s+(?:мне\s+)?(?:писал\w*|написал\w*|пишут|пришло)(?:\s+мне)?$|"
                     r"^(?:есть|были|пришли)\s+(?:ли\s+)?(?:новые\s+)?сообщени\w*(?:\s+(?:мне|для меня))?$|"
                     r"^(?:any|got any)\s+(?:new\s+)?messages$|^did anyone (?:write|text) me$", re.I)


def _clean_who(w: str) -> str:
    return re.sub(r"\s+(?:в|во)\s+(?:телеграм\w*|ватсап\w*|дискорд\w*|whatsapp|telegram|discord)\s*$", "",
                  (w or "").strip(" .!?,«»\"")).strip()


def intent(text: str):
    t = " ".join((text or "").split()).strip(" .!?")
    t = re.sub(r"^(?:атлас|atlas)[,\s]+", "", t, flags=re.I)
    if not t:
        return None
    if _OFF.match(t):
        return ("mode", "off")
    if _ALL.match(t):
        return ("mode", "all")
    if _IMP.match(t):
        return ("mode", "important")
    m = _DEL.match(t)
    if m:
        return ("del", _clean_who(next(g for g in m.groups() if g)))
    m = _ADD.match(t)
    if m:
        who = _clean_who(next(g for g in m.groups() if g))
        if who and len(who.split()) <= 4:
            return ("add", who)
    if _LIST.match(t):
        return ("list",)
    if _MISSED.match(t):
        return ("missed",)
    if _RECENT.match(t):
        return ("recent",)
    return None


def handle(text: str):
    it = intent(text)
    if not it:
        return None
    from core import messengers as M
    ru = _ru() if not re.search(r"[а-яё]", text, re.I) else True
    d = settings()
    k = it[0]
    if k == "mode":
        d["mode"] = it[1]
        _save(d)
        return {"off": "Хорошо, о сообщениях молчу. Скажи «включи уведомления о сообщениях», когда снова нужно.",
                "all": "Буду говорить обо всех сообщениях в Telegram, WhatsApp и Discord.",
                "important": "Буду говорить только о важных: родные, те, кому ты писал через меня, и твой список."}[it[1]]
    if k == "add":
        who = M._nominal(it[1]) if M.is_kin(it[1]) else it[1]
        if not any(M.norm(x) == M.norm(who) for x in d["important"]):
            d["important"].append(who)
        d["muted"] = [x for x in d["muted"] if M.norm(x) != M.norm(who)]
        if d["mode"] == "off":
            d["mode"] = "important"
        _save(d)
        return f"Запомнил: о сообщениях от «{who}» буду говорить сразу."
    if k == "del":
        who = it[1]
        d["important"] = [x for x in d["important"] if not _match(x, who) and M.norm(x) != M.norm(who)]
        if not any(M.norm(x) == M.norm(who) for x in d["muted"]):
            d["muted"].append(who)
        _save(d)
        return f"Хорошо, о сообщениях от «{who}» молчу."
    if k == "list":
        if d["mode"] == "off":
            return "Сейчас о сообщениях молчу."
        if d["mode"] == "all":
            return "Говорю обо всех сообщениях" + (f", кроме: {', '.join(d['muted'])}." if d["muted"] else ".")
        extra = list(dict.fromkeys(d["important"] + _remembered_titles()))
        return ("Говорю о сообщениях от родных" + (f" и от: {', '.join(extra[:8])}" if extra else "") + "."
                + (f" Молчу о: {', '.join(d['muted'])}." if d["muted"] else ""))
    if k == "missed":
        if not _s["missed"]:                                          # пропущенного нет — кто вообще писал недавно
            return recent_line(None, ru) or ("Никто не писал — новых уведомлений не было." if ru else "Nobody wrote.")
        return missed_line(ru, clear=True)
    if k == "recent":
        return recent_line(None, ru) or ("Никто не писал — новых уведомлений не было." if ru else "Nobody wrote.")
    return None


# --- запуск -------------------------------------------------------------------------------------------------------
def start(announce=None, every: float = 2.0, src=None, env=None) -> threading.Thread:
    _s["announce"] = announce
    if src is not None:
        _s["src"] = src
    if env is not None:
        _s["env"] = env
    if _s["thread"] and _s["thread"].is_alive():
        return _s["thread"]
    if src is None and not os.path.exists(DB):
        print("[сообщения] база уведомлений Windows не найдена — объявления сообщений выключены")
        return None

    def loop():
        fails = 0
        while True:
            try:
                tick()
                fails = 0
            except Exception as e:
                fails += 1
                if fails in (1, 10):
                    print(f"[сообщения] {e}")
                time.sleep(min(60, 2 * fails))
            time.sleep(every)
    _s["thread"] = threading.Thread(target=loop, daemon=True, name="msg-watch")
    _s["thread"].start()
    print(f"[сообщения] слежу за уведомлениями Telegram / WhatsApp / Discord (режим: {settings()['mode']})")
    return _s["thread"]


if __name__ == "__main__":            # python -m core.msg_watch check — что видно в базе уведомлений Windows
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(f"база: {DB} — {'есть' if os.path.exists(DB) else 'НЕ НАЙДЕНА'}")
    src = Source()
    try:
        top = src.last_id()
        rows = src.since(max(0, top - 40))
    except Exception as e:
        print(f"[ошибка] не читается: {e!r}")
        sys.exit(1)
    print(f"последний номер уведомления: {top}; мессенджеры среди последних 40:")
    shown = 0
    for nid, app, texts in rows:
        if app:
            title, body, _g = split_message(texts)
            mark = "ВАЖНОЕ" if important(title, body) else "обычное"
            print(f"  #{nid} {NAMES[app]:<9} «{title}»: {body[:70]}  [{mark}]")
            shown += 1
    if not shown:
        print("  нет. Пришли себе сообщение в Telegram/WhatsApp, когда окно свёрнуто, и запусти ещё раз.\n"
              "  В Telegram: Настройки → Уведомления → «Использовать уведомления Windows» должно быть включено.")
