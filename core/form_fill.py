"""
Заполнение форм и анкет: открой анкету (сайт в браузере или программа) и скажи «заполни эту форму» —
Atlas впишет то, что знает о тебе, в нужные поля. Отправляет всегда только ты сам.

Что Atlas знает — только то, что ты сам сказал для анкет (atlas_form_profile.json, только на этом ПК):
    «для анкет: меня зовут Алим Иванов, почта alim@mail.com, телефон +996 555 123 456, город Бишкек»
    «что ты знаешь для анкет?» / «забудь мой телефон для анкет»
Пароли, данные карт, CVV, капчу — не трогает никогда. Поля, которые уже заполнены, — не перезаписывает.
Поля видит через Windows UI Automation (как программы экранного доступа) — в Chrome, Edge, Яндексе, Word…
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "atlas_form_profile.json")
_SKIP = re.compile(r"парол|password|карт\w*|card|cvv|cvc|срок действ|expir|капч|captcha|код из смс|sms code|"
                   r"one[- ]time|одноразов|pin\b|пин-код", re.I)
KEYS = {"first_name": "имя", "last_name": "фамилия", "middle_name": "отчество", "full_name": "ФИО",
        "email": "почта", "phone": "телефон", "birthday": "дата рождения", "city": "город", "country": "страна",
        "address": "адрес", "school": "школа / учебное заведение", "grade": "класс / курс", "telegram": "Telegram",
        "instagram": "Instagram", "website": "сайт / портфолио", "github": "GitHub", "gender": "пол",
        "occupation": "род занятий", "postal_code": "индекс"}


def _ru(text: str) -> bool:
    return bool(re.search(r"[а-яё]", text or "", re.I))


def load() -> dict:
    try:
        with open(FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d: dict) -> None:
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def remember(text: str, ask=None) -> str:
    """«для анкет: …» → поля профиля. Почта и телефон — без модели, остальное — одним запросом."""
    d = load()
    t = text
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", t)
    if m:
        d["email"] = m.group(0)
    m = re.search(r"\+?\d[\d\s()-]{8,}\d", t)
    if m:
        d["phone"] = re.sub(r"[\s()-]", "", m.group(0))
    rest = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d\s()-]{8,}\d", " ", t)
    if re.search(r"[а-яёa-z]{3,}", rest, re.I):
        from core import quick_llm
        system = ("Extract form-profile fields from what the user said about themselves. Return strict JSON with only "
                  "the keys they actually gave, from: " + ", ".join(KEYS) + ". Values exactly as said (keep the "
                  "language). If a full name is given, also split into first_name / last_name (and middle_name). "
                  "Do not invent anything.")
        try:
            from core import memory
            got = memory.parse_json((ask or quick_llm.ask)(system, rest, max_tokens=300)) or {}
        except Exception as e:
            print(f"[анкеты] {e}")
            got = {}
        for k, v in got.items():
            if k in KEYS and isinstance(v, str) and v.strip() and k not in ("email", "phone"):
                d[k] = v.strip()[:120]
    _save(d)
    ru = _ru(text)
    if not d:
        return "Не понял, что запомнить для анкет." if ru else "Didn't catch anything to save."
    return ("Запомнил для анкет: " if ru else "Saved for forms: ") + ", ".join(KEYS[k] if ru else k for k in d) + "."


def describe(ru: bool = True) -> str:
    d = load()
    if not d:
        return ("Для анкет пока ничего не знаю. Скажи, например: «для анкет: меня зовут …, почта …, телефон …»." if ru
                else "Nothing saved for forms yet.")
    return ("Для анкет знаю: " if ru else "For forms I know: ") + "; ".join(f"{KEYS.get(k, k)} — {v}" for k, v in d.items()) + "."


def forget(text: str) -> str:
    d = load()
    t = text.lower()
    gone = [k for k, ru_name in KEYS.items() if any(w in t for w in re.findall(r"[а-яё]{4,}", ru_name.lower())) or k in t]
    if re.search(r"\bвс[её]\b|everything|all", t):
        gone = list(d)
    for k in gone:
        d.pop(k, None)
    _save(d)
    return ("Забыл: " + ", ".join(KEYS[k] for k in gone) + "." if gone else "Не понял, что забыть.") if _ru(text) else \
        ("Forgot: " + ", ".join(gone) + "." if gone else "Not sure what to forget.")


# --- поля на экране ---------------------------------------------------------------------------------------------
class Env:
    def fields(self) -> list:
        """[{i, kind, name, value?, password?}] — поля активного окна (desktop_agent)."""
        from core import desktop_agent
        desktop_agent.desktop_look()
        return [dict((k, v) for k, v in e.items() if k != "ctrl") for e in desktop_agent._last]

    def type_into(self, index: int, text: str) -> bool:
        from core import desktop_agent
        out = desktop_agent.desktop_type(text, index=index, replace=True)
        return not re.search(r"не (?:нашёл|получилось|смог)|нет элемента|error|couldn", str(out), re.I)


_env = None


def _get_env():
    global _env
    if _env is None:
        _env = Env()
    return _env


def plan(fields: list, profile: dict, ask=None) -> dict:
    """Какие значения в какие поля: {номер_поля: значение}. Только пустые, без паролей и карт."""
    cand = [f for f in fields if f.get("kind") in ("edit", "combobox") and not f.get("password") and
            not f.get("value") and not f.get("disabled") and not _SKIP.search(f.get("name", ""))]
    if not cand or not profile:
        return {}
    from core import quick_llm
    system = ("You fill a form for the user. Given the empty form fields (index and label) and the user's saved "
              "profile, return strict JSON {\"<index>\": \"value\"} ONLY for fields you can fill confidently from the "
              "profile (name, surname, email, phone, birthday, city, school…). Format values the way the label asks "
              "(e.g. date as on the label, phone with +). Leave out anything unknown — never invent. Never fill "
              "passwords, card data, codes.")
    user = "Fields:\n" + "\n".join(f"{f['i']}: {f.get('name') or '(без названия)'}" for f in cand) + \
        "\n\nProfile:\n" + json.dumps(profile, ensure_ascii=False)
    from core import memory
    raw = (ask or quick_llm.ask)(system, user, max_tokens=500)
    data = memory.parse_json(raw) or {}
    ok = {f["i"] for f in cand}
    out = {}
    for k, v in data.items():
        try:
            i = int(k)
        except (TypeError, ValueError):
            continue
        if i in ok and isinstance(v, (str, int)) and str(v).strip():
            out[i] = str(v).strip()[:200]
    return out


def fill(env=None, ask=None, ru: bool = True) -> str:
    env = env or _get_env()
    prof = load()
    if not prof:
        return ("Я ещё ничего не знаю для анкет. Скажи: «для анкет: меня зовут …, почта …, телефон …» — и попробуем."
                if ru else "I don't know anything for forms yet.")
    try:
        fields = env.fields()
    except Exception as e:
        return (f"Не вижу полей в этом окне: {e}" if ru else f"Can't see the fields: {e}")[:200]
    p = plan(fields, prof, ask)
    if not p:
        empty = [f for f in fields if f.get("kind") == "edit" and not f.get("value")]
        return ("Не нашёл полей, которые могу заполнить из того, что знаю." if empty else
                "Не вижу пустых полей в этом окне — открой анкету и скажи ещё раз.") if ru else "Nothing I can fill here."
    done, names = 0, []
    byi = {f["i"]: f for f in fields}
    for i, v in p.items():
        if env.type_into(i, v):
            done += 1
            names.append(byi[i].get("name") or "поле")
    left = [f.get("name") for f in fields if f.get("kind") == "edit" and not f.get("value") and f["i"] not in p
            and f.get("name") and not _SKIP.search(f.get("name", ""))]
    out = (f"Заполнил {done} {'поле' if done == 1 else 'поля' if 2 <= done <= 4 else 'полей'}: {', '.join(names[:6])}."
           if ru else f"Filled {done} fields: {', '.join(names[:6])}.")
    if left:
        out += (" Не знаю: " if ru else " Didn't know: ") + ", ".join(left[:5]) + "."
    out += " Отправлять не стал — проверь и отправь сам." if ru else " I didn't submit — check it and send it yourself."
    return out


_FILL = re.compile(r"^(?:атлас[,\s]+)?(?:заполни|заполните|fill(?: in| out)?)\s+(?:за меня\s+)?(?:эту\s+|мне\s+)?"
                   r"(?:форм\w*|анкет\w*|пол[яе]|заявк\w*|регистрац\w*|this form|the form|form)", re.I)
_REMEMBER = re.compile(r"^(?:атлас[,\s]+)?(?:для анкет|для форм|запомни для анкет|for forms)\s*[:,—–-]?\s*(.+)$", re.I)
_SHOW = re.compile(r"что (?:ты )?знаешь (?:обо мне )?для анкет|мои данные для анкет|what do you know for forms", re.I)
_FORGET = re.compile(r"(?:забудь|удали)\s+(.+?)\s+для анкет|forget (.+) for forms", re.I)


def handle(text: str, env=None, ask=None):
    t = " ".join((text or "").split()).strip()
    ru = _ru(t)
    m = _REMEMBER.match(t)
    if m:
        return remember(m.group(1), ask)
    if _SHOW.search(t):
        return describe(ru)
    m = _FORGET.search(t)
    if m:
        return forget(m.group(1) or m.group(2))
    if _FILL.search(t):
        return fill(env, ask, ru)
    return None
