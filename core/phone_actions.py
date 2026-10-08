"""
Действия на самом телефоне: открыть сайт, поиск, приложение, музыку, видео, карту, позвонить, написать.

Облако не управляет телефоном напрямую — оно прикладывает к ответу ссылку, а приложение Atlas на
телефоне её открывает. Ссылки — обычные https (YouTube, Spotify, Google Maps, WhatsApp, Telegram):
Android сам открывает их в установленном приложении, а если его нет — в браузере.

    open_on_phone("youtube", "lofi")      → поиск lofi в YouTube
    open_on_phone("site", url="habr.com") → сайт
    take()                                → действия этого ответа (для телефона) и очистка
"""
import re
from urllib.parse import quote, quote_plus

# приложение → (название, ссылка с поиском {q}, ссылка без поиска)
APPS = {
    "google":    ("Google", "https://www.google.com/search?q={q}", "https://www.google.com"),
    "youtube":   ("YouTube", "https://www.youtube.com/results?search_query={q}", "https://www.youtube.com"),
    "spotify":   ("Spotify", "https://open.spotify.com/search/{p}", "https://open.spotify.com"),
    "ytmusic":   ("YouTube Music", "https://music.youtube.com/search?q={q}", "https://music.youtube.com"),
    "maps":      ("Карты", "https://www.google.com/maps/search/?api=1&query={q}", "https://www.google.com/maps"),
    "2gis":      ("2ГИС", "https://2gis.kg/bishkek/search/{p}", "https://2gis.kg/bishkek"),
    "translate": ("Переводчик", "https://translate.google.com/?sl=auto&tl=en&text={q}", "https://translate.google.com"),
    "wikipedia": ("Википедия", "https://ru.wikipedia.org/w/index.php?search={q}", "https://ru.wikipedia.org"),
    "whatsapp":  ("WhatsApp", "https://wa.me/?text={q}", "https://wa.me/"),
    "telegram":  ("Telegram", "https://t.me/{p}", "https://t.me/"),
    "instagram": ("Instagram", "https://www.instagram.com/{p}", "https://www.instagram.com"),
    "tiktok":    ("TikTok", "https://www.tiktok.com/search?q={q}", "https://www.tiktok.com"),
    "gmail":     ("Gmail", "https://mail.google.com/mail/u/0/#search/{p}", "https://mail.google.com"),
    "calendar":  ("Календарь", "https://calendar.google.com", "https://calendar.google.com"),
    "drive":     ("Google Диск", "https://drive.google.com/drive/search?q={q}", "https://drive.google.com"),
    "github":    ("GitHub", "https://github.com/search?q={q}", "https://github.com"),
    "chatgpt":   ("ChatGPT", "https://chatgpt.com/?q={q}", "https://chatgpt.com"),
    "netflix":   ("Netflix", "https://www.netflix.com/search?q={q}", "https://www.netflix.com"),
    "rezka":     ("Rezka", "https://rezka.ag/search/?do=search&subaction=search&q={q}", "https://rezka.ag"),
    "khan":      ("Khan Academy", "https://www.khanacademy.org/search?page_search_query={q}", "https://www.khanacademy.org"),
    "collegeboard": ("College Board", "https://satsuite.collegeboard.org/search?q={q}", "https://bluebook.collegeboard.org"),
}
_ALIASES = {
    "гугл": "google", "поиск": "google", "search": "google", "browser": "google", "браузер": "google",
    "ютуб": "youtube", "yt": "youtube", "спотифай": "spotify", "music": "spotify", "музыка": "spotify",
    "youtube music": "ytmusic", "карта": "maps", "карты": "maps", "map": "maps", "google maps": "maps",
    "2гис": "2gis", "переводчик": "translate", "википедия": "wikipedia", "wiki": "wikipedia",
    "ватсап": "whatsapp", "вотсап": "whatsapp", "телеграм": "telegram", "tg": "telegram",
    "инстаграм": "instagram", "инста": "instagram", "тикток": "tiktok", "почта": "gmail", "mail": "gmail",
    "календарь": "calendar", "диск": "drive", "нетфликс": "netflix", "резка": "rezka", "hdrezka": "rezka",
    "bluebook": "collegeboard", "sat": "collegeboard",
}
_pending = []                     # действия текущего ответа (ответы идут по одному — под замком мозга)


def _norm_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if re.match(r"^(https?|tel|sms|mailto):", u, re.I):
        return u if not u.lower().startswith("http://") else "https://" + u[7:]
    if re.match(r"^[\w.-]+\.[a-z]{2,}(/.*)?$", u, re.I):
        return "https://" + u
    return ""


def build(app: str = "", query: str = "", url: str = "", phone: str = "") -> dict:
    """→ {"label", "url"} или {} если не понять, что открыть."""
    a = (app or "").strip().lower()
    a = _ALIASES.get(a, a)
    q = (query or "").strip()
    if a in ("call", "phone", "позвонить", "звонок") or (phone and a not in ("sms", "смс")):
        num = re.sub(r"[^\d+]", "", phone or q)
        return {"label": f"Позвонить {num}", "url": f"tel:{num}"} if len(num) >= 3 else {}
    if a in ("sms", "смс"):
        num = re.sub(r"[^\d+]", "", phone)
        return {"label": "Написать SMS", "url": f"sms:{num}" + (f"?body={quote(q)}" if q else "")}
    if a in ("email", "письмо"):
        return {"label": "Написать письмо", "url": "mailto:?body=" + quote(q)}
    u = _norm_url(url)
    if u:
        return {"label": "Открыть " + re.sub(r"^https?://(www\.)?", "", u).split("/")[0], "url": u}
    if a in APPS:
        name, with_q, bare = APPS[a]
        if q:
            return {"label": f"{name}: {q}", "url": with_q.format(q=quote_plus(q), p=quote(q))}
        return {"label": f"Открыть {name}", "url": bare}
    if q:                                     # неизвестное приложение — ищем в Google
        return {"label": f"Google: {q}", "url": APPS["google"][1].format(q=quote_plus(q), p=quote(q))}
    return {}


def open_on_phone(app: str = "", query: str = "", url: str = "", phone: str = "") -> str:
    """Инструмент облачного мозга."""
    act = build(app, query, url, phone)
    if not act:
        return "Couldn't tell what to open. Ask the user which site or app."
    _pending.append(act)
    return f"Opening on the phone: {act['label']}. Tell the user briefly; do not read out the link."


def take() -> list:
    out = list(_pending)
    _pending.clear()
    return out[-3:]


SCHEMA = {"type": "function", "function": {
    "name": "open_on_phone",
    "description": "Opens something ON THE USER'S PHONE (the user is holding it): a website, a web/Google search, "
                   "a video on YouTube, music on Spotify or YouTube Music, a place on maps or 2GIS, an app "
                   "(" + ", ".join(sorted(APPS)) + "), a phone call (phone number) or an SMS/WhatsApp text. "
                   "app: one of those names, or 'call', 'sms', 'email'; query: what to search or the message text; "
                   "url: a direct link if known; phone: a number to call or text.",
    "parameters": {"type": "object", "properties": {
        "app": {"type": "string"}, "query": {"type": "string"}, "url": {"type": "string"},
        "phone": {"type": "string"}}}}}
