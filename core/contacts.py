"""
Телефонная книга Atlas: имя → номер. Нужна, чтобы открывать чат в WhatsApp и Telegram ПО НОМЕРУ
(whatsapp://send?phone=…, tg://resolve?phone=…), а не искать имя на экране — поиск по экрану ломается
от любой мелочи в интерфейсе (логи 23–24.10: «Назар» не находился, запрос не попадал в строку поиска).

Откуда номера (всё хранится только на этом ПК, в atlas_contacts.json — в git не попадает):
  • Google-контакты — «обнови контакты»: один раз откроется окно входа Google с разрешением
    «только читать контакты» (отдельный token_contacts.json — вход для почты и календаря не трогается);
  • файл contacts.vcf в папке Atlas — экспорт контактов с телефона (если Google-контактов нет);
  • голосом: «запомни номер Назара 0707 050 809».

Номер без кода страны дополняется кодом из ATLAS_PHONE_CC (по умолчанию 996 — Кыргызстан).
"""
import json
import os
import re
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "atlas_contacts.json")
TOKEN = os.path.join(ROOT, "token_contacts.json")
VCF = os.path.join(ROOT, "contacts.vcf")
SCOPES = ["https://www.googleapis.com/auth/contacts.readonly"]
REFRESH_S = 24 * 3600
_lock = threading.Lock()
_cache = {"data": None, "mtime": None}


# --- номера -----------------------------------------------------------------------------------------
def normalize_phone(raw: str, cc: str = None) -> str:
    """«+996 (707) 05-08-09», «0707050809», «707 050 809» → «996707050809». Не номер → ""."""
    cc = cc or os.getenv("ATLAS_PHONE_CC", "996")
    s = (raw or "").strip()
    plus = s.startswith("+")
    d = re.sub(r"\D", "", s)
    if not d:
        return ""
    if plus:
        pass
    elif d.startswith("00"):
        d = d[2:]
    elif d.startswith("8") and len(d) == 11 and cc in ("7",):  # российский формат 8XXXXXXXXXX
        d = "7" + d[1:]
    elif d.startswith("0") and 9 <= len(d) <= 11:              # местный формат: 0 707 050 809
        d = cc + d[1:]
    elif len(d) == 9 and not d.startswith(cc):                 # 707 050 809
        d = cc + d
    return d if 10 <= len(d) <= 15 else ""


# --- хранилище --------------------------------------------------------------------------------------
def _load() -> dict:
    try:
        mt = os.path.getmtime(FILE)
    except OSError:
        return {"people": [], "synced": 0}
    if _cache["data"] is not None and _cache["mtime"] == mt:
        return _cache["data"]
    try:
        with open(FILE, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            d = {"people": [], "synced": 0}
    except Exception:
        d = {"people": [], "synced": 0}
    _cache.update(data=d, mtime=mt)
    return d


def _save(d: dict) -> None:
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, FILE)
    _cache.update(data=None, mtime=None)


def people() -> list:
    return list(_load().get("people") or [])


def _merge(found: list, source: str) -> int:
    """Заменить людей из этого источника, ручные записи («запомни номер…») не трогать."""
    with _lock:
        d = _load()
        keep = [p for p in d.get("people") or [] if p.get("source") != source]
        seen = {(p["name"].lower(), tuple(p["phones"])) for p in keep}
        new = []
        for p in found:
            phones = list(dict.fromkeys(x for x in (normalize_phone(n) for n in p.get("phones") or []) if x))
            name = " ".join((p.get("name") or "").split())
            if not name or not phones or (name.lower(), tuple(phones)) in seen:
                continue
            seen.add((name.lower(), tuple(phones)))
            new.append({"name": name, "phones": phones, "source": source})
        d = {"people": keep + new, "synced": time.time() if source == "google" else d.get("synced", 0)}
        _save(d)
        return len(new)


def add(name: str, phone: str) -> str:
    num = normalize_phone(phone)
    name = " ".join((name or "").split()).strip(" .,«»\"")
    if not num:
        return f"Не понял номер «{phone}». Скажи его цифрами, например 0707 050 809."
    if not name:
        return "Не понял, чей это номер."
    with _lock:
        d = _load()
        ppl = [p for p in d.get("people") or [] if not (p.get("source") == "manual" and p["name"].lower() == name.lower())]
        ppl.append({"name": name, "phones": [num], "source": "manual"})
        d = dict(d, people=ppl)
        _save(d)
    return f"Запомнил номер: {name} — +{num}. Теперь пишу ему по номеру, без поиска на экране."


# --- источники --------------------------------------------------------------------------------------
def _google_creds(interactive: bool):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = None
    if os.path.exists(TOKEN):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
        except Exception:
            creds = None
    if creds and not creds.valid and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None
    if not creds or not creds.valid:
        if not interactive:
            return None
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(os.path.join(ROOT, "credentials.json"), SCOPES)
        creds = flow.run_local_server(port=0)
    with open(TOKEN, "w") as f:
        f.write(creds.to_json())
    return creds


def fetch_google(interactive: bool = True) -> list:
    """Google People API → [{"name", "phones"}]. Только чтение."""
    creds = _google_creds(interactive)
    if creds is None:
        raise RuntimeError("нет входа Google для контактов")
    from googleapiclient.discovery import build
    svc = build("people", "v1", credentials=creds, cache_discovery=False)
    out, token = [], None
    while True:
        r = svc.people().connections().list(resourceName="people/me", pageSize=1000, pageToken=token,
                                            personFields="names,phoneNumbers").execute()
        for p in r.get("connections") or []:
            names = p.get("names") or []
            phones = [n.get("value") or "" for n in p.get("phoneNumbers") or []]
            if names and phones:
                out.append({"name": names[0].get("displayName") or "", "phones": phones})
        token = r.get("nextPageToken")
        if not token:
            return out


def parse_vcf(text: str) -> list:
    """Экспорт контактов с телефона (vCard): FN + TEL."""
    out = []
    for card in re.split(r"BEGIN:VCARD", text or "", flags=re.I)[1:]:
        card = re.sub(r"\r?\n[ \t]", "", card)                        # перенос длинных строк
        fn = re.search(r"^FN(?:;[^:\n]*)?:(.+)$", card, re.M | re.I)
        if not fn:
            n = re.search(r"^N(?:;[^:\n]*)?:(.+)$", card, re.M | re.I)
            parts = [x for x in (n.group(1).split(";") if n else []) if x]
            name = " ".join(reversed(parts[:2])) if parts else ""
        else:
            name = fn.group(1)
        name = name.strip().replace("\\,", ",")
        if "ENCODING=QUOTED-PRINTABLE" in (fn.group(0).upper() if fn else ""):
            import quopri
            try:
                name = quopri.decodestring(name.encode()).decode("utf-8", "replace")
            except Exception:
                pass
        phones = re.findall(r"^TEL(?:;[^:\n]*)?:(.+)$", card, re.M | re.I)
        if name and phones:
            out.append({"name": name, "phones": [p.strip() for p in phones]})
    return out


def sync(interactive: bool = True) -> str:
    """«Обнови контакты»: Google (если вошли или можно войти) + contacts.vcf."""
    parts, errors = [], []
    if os.path.exists(VCF):
        try:
            with open(VCF, encoding="utf-8", errors="replace") as f:
                n = _merge(parse_vcf(f.read()), "vcf")
            parts.append(f"из файла contacts.vcf — {n}")
        except Exception as e:
            errors.append(f"contacts.vcf не прочитался: {e}")
    if os.path.exists(os.path.join(ROOT, "credentials.json")):
        try:
            n = _merge(fetch_google(interactive), "google")
            parts.append(f"из Google — {n}")
        except Exception as e:
            msg = str(e)
            if "has not been used" in msg or "SERVICE_DISABLED" in msg or "accessNotConfigured" in msg:
                errors.append("в Google Cloud у проекта Atlas не включён People API — включи его "
                              "(console.cloud.google.com → APIs → People API → Enable) и скажи «обнови контакты» ещё раз")
            else:
                errors.append(f"Google-контакты не загрузились: {msg[:160]}")
    total = len(people())
    if parts:
        return f"Контакты обновлены: {', '.join(parts)}. Всего с номерами: {total}." + (
            " " + "; ".join(errors) + "." if errors else "")
    if errors:
        return "Не получилось загрузить контакты: " + "; ".join(errors) + "."
    return ("Контактов нет: скажи «обнови контакты» у компьютера (войти в Google) или положи contacts.vcf "
            "в папку Atlas — или «запомни номер Назара 0707 050 809».")


def refresh_in_background() -> None:
    """При запуске Atlas: тихо обновить Google-контакты раз в сутки, если вход уже был (окно входа не открываем)."""
    d = _load()
    if not os.path.exists(TOKEN) or time.time() - (d.get("synced") or 0) < REFRESH_S:
        return

    def run():
        try:
            n = _merge(fetch_google(interactive=False), "google")
            print(f"[контакты] обновил из Google: {n}")
        except Exception as e:
            print(f"[контакты] не обновил: {e}")
    threading.Thread(target=run, daemon=True).start()


# --- поиск ------------------------------------------------------------------------------------------
def lookup(contact: str):
    """«Назару», «маме», «моему брату Алиму» → ("one", имя, номер) | ("many", [имена]) | None.
    Совпадение — те же правила, что у поиска чатов (основа слова, слова родства)."""
    if not contact:
        return None
    from core import messengers as M
    ppl = people()
    if not ppl:
        return None
    st = M.stems(contact)
    if not st:
        return None
    kin = M.is_kin(contact)
    exact = [p for p in ppl if M.norm(p["name"]) == M.norm(contact)]
    scored = exact and [(9.0, p) for p in exact] or \
        [(M.match_score(p["name"], st, kin), p) for p in ppl]
    scored = [(s, p) for s, p in scored if s >= 1]
    if not scored:
        return None
    best = max(s for s, _ in scored)
    top = [p for s, p in scored if s == best]
    if len({p["phones"][0] for p in top}) > 1:
        return ("many", list(dict.fromkeys(p["name"] for p in top))[:4])
    return ("one", top[0]["name"], top[0]["phones"][0])


# --- голосом ----------------------------------------------------------------------------------------
_ADD = re.compile(r"^(?:атлас[,\s]+|atlas[,\s]+)?(?:запомни|сохрани|добавь|запиши)\s+(?:номер|телефон)\s+"
                  r"(?:(?:у|для|of)\s+)?(?P<who>[^\d+]{2,40}?)\s*[:—–-]?\s*(?P<num>\+?[\d\s()-]{7,20}\d)\s*\.?$", re.I)
_ADD2 = re.compile(r"^(?:атлас[,\s]+|atlas[,\s]+)?(?:номер|телефон)\s+(?P<who>[^\d+]{2,40}?)\s*[:—–-]?\s*"
                   r"(?P<num>\+?[\d\s()-]{7,20}\d)\s*\.?$", re.I)
_SYNC = re.compile(r"^(?:атлас[,\s]+|atlas[,\s]+)?(?:обнови|загрузи|синхронизируй|подтяни|импортируй)\s+(?:мои\s+)?"
                   r"(?:контакты|телефонную книгу|номера)(?:\s+(?:из|с)\s+(?:гугла|google|телефона))?\s*[.!]?$|"
                   r"^(?:sync|update|import)\s+(?:my\s+)?contacts$", re.I)
def _nominative(who: str) -> str:
    """«Назара» → «Назар», «Дины» → «Дина», «мамы» → «Мама» — для красивого имени в книге (поиск всё равно по основе)."""
    from core import messengers as M
    w = " ".join(who.split())
    if M.norm(w) in M._NOMINAL:
        return M._NOMINAL[M.norm(w)]
    if len(w.split()) == 1 and re.fullmatch(r"[А-ЯЁ][а-яё]+", w):
        if w.endswith("ы"):
            return w[:-1] + "а"
        if w.endswith("а") and re.search(r"[бвгджзклмнпрстфхцчшщ]$", w[:-1]):
            return w[:-1]
    return w


def intent(text: str):
    t = " ".join((text or "").split()).strip()
    if _SYNC.match(t):
        return ("sync",)
    m = _ADD.match(t) or _ADD2.match(t)
    if m:
        return ("add", _nominative(m.group("who").strip(" ,:")), m.group("num"))
    return None


def handle(text: str):
    it = intent(text)
    if not it:
        return None
    if it[0] == "sync":
        return sync(interactive=True)
    return add(it[1], it[2])
