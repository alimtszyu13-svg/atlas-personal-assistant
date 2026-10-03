"""
Голо-экран Atlas: серверная часть.

Карточки, которые Atlas выводит на экран:
  • image   — фото по запросу («покажи Эйфелеву башню»): Википедия → Wikimedia Commons;
  • weather — погода с историей за 7 дней и прогнозом на 7 дней (Open-Meteo, без ключа);
  • look    — снимок с веб-камеры или экрана («посмотри, что у меня в руке»).

Картинки скачиваются и уменьшаются до 2048 px, лежат в holo_cache/ и отдаются
интерфейсу с того же локального сервера — поэтому выделенный кусок можно вырезать
в браузере и отправить на разбор без ограничений чужих сайтов.

Разбор выделенной области — Gemini (умеет смотреть на картинки). Людей по лицу
он не опознаёт: описывает, что видно, но не говорит, кто это.
"""
import base64
import io
import os
import re
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "holo_cache")
MAX_SIDE = 2048
MAX_ITEMS = 4
UA = {"User-Agent": "AtlasAssistant/1.0 (https://github.com/alimtszyu13-svg/atlas-personal-assistant; student voice assistant) python-httpx"}

_lock = threading.Lock()


def _state():
    from ui_state import shared_state
    return shared_state


def _lang() -> str:
    try:
        from voice import get_response_language
        return get_response_language()
    except Exception:
        return "ru"


def _push(item: dict) -> dict:
    st = _state()
    with _lock:
        h = st.setdefault("holo", {"items": [], "focus": None})
        item.setdefault("id", uuid.uuid4().hex[:10])
        item.setdefault("ts", time.time())
        h["items"] = [i for i in h["items"] if i["id"] != item["id"]][-(MAX_ITEMS - 1):] + [item]
        h["focus"] = item["id"]
        st["holo_seq"] = st.get("holo_seq", 0) + 1
    return item


def command(action: str, item_id: str = "") -> None:
    """expand | collapse | close | close_all — для голосовых команд."""
    st = _state()
    with _lock:
        h = st.setdefault("holo", {"items": [], "focus": None})
        target = item_id or h.get("focus") or (h["items"][-1]["id"] if h["items"] else None)
        if action == "close_all":
            h["items"], h["focus"] = [], None
        elif action == "close" and target:
            h["items"] = [i for i in h["items"] if i["id"] != target]
            h["focus"] = h["items"][-1]["id"] if h["items"] else None
        elif action in ("expand", "collapse") and target:
            for i in h["items"]:
                i["expanded"] = (i["id"] == target and action == "expand")
            h["focus"] = target
        st["holo_seq"] = st.get("holo_seq", 0) + 1


def snapshot() -> dict:
    import copy
    with _lock:
        return copy.deepcopy(_state().get("holo") or {"items": [], "focus": None})


# ---------------------------------------------------------------------------
# Картинки
# ---------------------------------------------------------------------------
def _save_image(raw: bytes) -> tuple:
    """Уменьшить до MAX_SIDE, сохранить JPEG в holo_cache → (url, ширина, высота)."""
    from PIL import Image
    os.makedirs(CACHE, exist_ok=True)
    img = Image.open(io.BytesIO(raw))
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    name = uuid.uuid4().hex[:12] + ".jpg"
    img.save(os.path.join(CACHE, name), "JPEG", quality=88)
    _cleanup()
    return f"/holo_cache/{name}", img.width, img.height


def _cleanup(keep: int = 40) -> None:
    try:
        files = sorted((os.path.join(CACHE, f) for f in os.listdir(CACHE)), key=os.path.getmtime)
        for f in files[:-keep]:
            os.remove(f)
    except Exception:
        pass


def _json(r, where: str) -> dict:
    """Ответ сервиса → словарь; если пришёл не JSON — пишем в лог, что именно пришло."""
    try:
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    print(f"[голо] {where}: код {r.status_code}, ответ: {r.text[:120]!r}")
    return {}


def _wiki_lookup(query: str):
    """→ (заголовок, первое предложение, адрес картинки, ссылка) из Википедии, ru или en."""
    import httpx
    langs = ("ru", "en") if _lang() == "ru" else ("en", "ru")
    for lang in langs:
        try:
            r = httpx.get(f"https://{lang}.wikipedia.org/w/api.php", headers=UA, timeout=12, follow_redirects=True,
                          params={"action": "query", "list": "search", "srsearch": query, "srlimit": 3, "format": "json"})
            for hit in _json(r, f"Википедия ({lang})").get("query", {}).get("search", []):
                s = _json(httpx.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{hit['title'].replace(' ', '_')}",
                                    headers=UA, timeout=12, follow_redirects=True), f"Википедия ({lang}), статья")
                src = (s.get("originalimage") or s.get("thumbnail") or {}).get("source")
                if src:
                    extract = re.split(r"(?<=[.!?])\s", s.get("extract") or "", maxsplit=1)[0]
                    return s.get("title") or hit["title"], extract, src, (s.get("content_urls", {}).get("desktop", {}) or {}).get("page", "")
        except Exception as e:
            print(f"[голо] Википедия ({lang}): {e}")
    return None


def _commons_lookup(query: str):
    import httpx
    try:
        r = httpx.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=12, follow_redirects=True, params={
            "action": "query", "generator": "search", "gsrnamespace": 6, "gsrsearch": query, "gsrlimit": 5,
            "prop": "imageinfo", "iiprop": "url|mime", "iiurlwidth": 2048, "format": "json"})
        pages = sorted((_json(r, "Commons").get("query", {}).get("pages") or {}).values(), key=lambda p: p.get("index", 99))
        for p in pages:
            ii = (p.get("imageinfo") or [{}])[0]
            if str(ii.get("mime", "")).startswith("image/") and "svg" not in ii.get("mime", ""):
                title = re.sub(r"^File:|\.\w+$", "", p.get("title", query))
                return title, "", ii.get("thumburl") or ii.get("url"), ii.get("descriptionurl", "")
    except Exception as e:
        print(f"[голо] Commons: {e}")
    return None


def _openverse_lookup(query: str):
    """Запасной источник: Openverse — открытая база свободно лицензированных фото, без ключа."""
    import httpx
    try:
        r = httpx.get("https://api.openverse.org/v1/images/", headers=UA, timeout=15, follow_redirects=True,
                      params={"q": query, "page_size": 5, "mature": "false"})
        for it in _json(r, "Openverse").get("results", []):
            url = it.get("url") or it.get("thumbnail")
            if url:
                return it.get("title") or query, "", url, it.get("foreign_landing_url", "")
    except Exception as e:
        print(f"[голо] Openverse: {e}")
    return None


_BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/128.0.0.0 Safari/537.36", "Referer": "https://duckduckgo.com/"}
_JUNK = re.compile(r"logo|icon|clip.?art|vector|emblem|sticker|cartoon|silhouette|\.svg|\.gif|watermark|"
                   r"shutterstock|alamy|dreamstime|depositphotos|istockphoto|123rf", re.I)


_DDG_OFF = {"until": 0.0}


def _ddg_images(query: str, n: int = 10) -> list:
    """Поиск картинок DuckDuckGo — обычные фотографии со всего интернета, без ключа."""
    import httpx
    if time.time() < _DDG_OFF["until"]:
        return []                                  # недавно отказал — не теряем на нём секунды
    try:
        r = httpx.get("https://duckduckgo.com/", params={"q": query, "iax": "images", "ia": "images"},
                      headers=_BROWSER_UA, timeout=12, follow_redirects=True)
        m = re.search(r"vqd=[\"']?([\d-]+)", r.text)
        if not m:
            print(f"[голо] DuckDuckGo: нет токена поиска (код {r.status_code}) — 6 часов без него")
            _DDG_OFF["until"] = time.time() + 6 * 3600
            return []
        j = httpx.get("https://duckduckgo.com/i.js", headers=_BROWSER_UA, timeout=12, follow_redirects=True,
                      params={"l": "ru-ru" if _lang() == "ru" else "us-en", "o": "json", "q": query,
                              "vqd": m.group(1), "f": ",,,,,", "p": "1"})
        out = []
        for x in _json(j, "DuckDuckGo").get("results", [])[:n]:
            if x.get("image"):
                out.append({"title": x.get("title") or query, "url": x["image"], "page": x.get("url", ""),
                            "w": int(x.get("width") or 0), "h": int(x.get("height") or 0), "from": "web"})
        if not out:
            _DDG_OFF["until"] = time.time() + 6 * 3600
            print("[голо] DuckDuckGo не отдал картинки — 6 часов беру из других источников")
        return out
    except Exception as e:
        _DDG_OFF["until"] = time.time() + 6 * 3600
        print(f"[голо] DuckDuckGo: {e}")
        return []


def _candidates(query: str) -> tuple:
    """Кандидаты из всех источников — параллельно (без логотипов и мелочи) + подпись из Википедии."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_wiki, f_web = ex.submit(_wiki_lookup, query), ex.submit(_ddg_images, query)
        f_com, f_ov = ex.submit(_commons_lookup, query), ex.submit(_openverse_lookup, query)
        wiki, web, com, ov = f_wiki.result(), f_web.result() or [], f_com.result(), f_ov.result()
    cands, caption, title = [], "", query
    if wiki:
        title, caption = wiki[0], wiki[1]
        cands.append({"title": wiki[0], "url": wiki[2], "page": wiki[3], "w": 0, "h": 0, "from": "wiki"})
    cands = web[:2] + cands + web[2:]
    for r, origin in ((com, "commons"), (ov, "openverse")):
        if r:
            cands.append({"title": r[0], "url": r[2], "page": r[3], "w": 0, "h": 0, "from": origin})
    seen, good = set(), []
    for c in cands:
        u = c["url"].split("?")[0]
        if u in seen or _JUNK.search(c["url"]) or _JUNK.search(c.get("title", "")):
            continue
        if c["w"] and c["h"] and min(c["w"], c["h"]) < 400:
            continue
        seen.add(u)
        good.append(c)
    return good, caption, title


def _fetch(c: dict):
    import httpx
    try:
        h = UA if c["from"] != "web" else _BROWSER_UA
        r = httpx.get(c["url"], headers=h, timeout=12, follow_redirects=True)
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
            return None
        from PIL import Image
        im = Image.open(io.BytesIO(r.content))
        if min(im.size) < 300:
            return None
        url, w, hh = _save_image(r.content)
        return {"src": url, "w": w, "h": hh, "source": c.get("page", ""), "title": c.get("title", "")}
    except Exception:
        return None


def show_image(query: str) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    cands, caption, title = _candidates(query)
    if not cands:
        return {"ok": False, "text": f"Не нашёл картинок по запросу «{query}»." if _lang() == "ru"
                else f"Couldn't find pictures for «{query}»."}
    with ThreadPoolExecutor(max_workers=6) as ex:     # качаем параллельно, порядок кандидатов сохраняется
        got = [g for g in ex.map(_fetch, cands[:8]) if g][:5]
    if not got:
        return {"ok": False, "text": "Нашёл картинки, но ни одна не скачалась." if _lang() == "ru"
                else "Found pictures, but none downloaded."}
    first = got[0]
    _push({"kind": "image", "title": title, "caption": caption, "src": first["src"], "w": first["w"], "h": first["h"],
           "source": first["source"], "alts": got})
    return {"ok": True, "text": (f"{title}. {caption}".strip() if caption else title)
            + (f" ({len(got)} фото на экране)" if _lang() == "ru" else f" ({len(got)} photos on screen)")}



# ---------------------------------------------------------------------------
# Погода: 7 дней назад и 7 вперёд
# ---------------------------------------------------------------------------
_WMO = {0: ("ясно", "clear"), 1: ("преимущественно ясно", "mainly clear"), 2: ("переменная облачность", "partly cloudy"),
        3: ("пасмурно", "overcast"), 45: ("туман", "fog"), 48: ("изморозь", "rime fog"), 51: ("морось", "drizzle"),
        53: ("морось", "drizzle"), 55: ("сильная морось", "heavy drizzle"), 61: ("небольшой дождь", "light rain"),
        63: ("дождь", "rain"), 65: ("ливень", "heavy rain"), 71: ("небольшой снег", "light snow"), 73: ("снег", "snow"),
        75: ("сильный снег", "heavy snow"), 77: ("снежные зёрна", "snow grains"), 80: ("ливни", "showers"),
        81: ("ливни", "showers"), 82: ("сильные ливни", "violent showers"), 85: ("снегопад", "snow showers"),
        86: ("сильный снегопад", "heavy snow showers"), 95: ("гроза", "thunderstorm"), 96: ("гроза с градом", "thunderstorm, hail"),
        99: ("сильная гроза с градом", "severe thunderstorm")}


def wmo_text(code) -> str:
    ru = _lang() == "ru"
    t = _WMO.get(int(code or 0), ("—", "—"))
    return t[0] if ru else t[1]


def show_weather(place: str) -> dict:
    import httpx
    ru = _lang() == "ru"
    g = httpx.get("https://geocoding-api.open-meteo.com/v1/search", timeout=12,
                  params={"name": place, "count": 1, "language": "ru" if ru else "en"}).json()
    if not g.get("results"):
        return {"ok": False, "text": f"Не нашёл место «{place}»." if ru else f"Couldn't find «{place}»."}
    loc = g["results"][0]
    f = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
        "latitude": loc["latitude"], "longitude": loc["longitude"], "timezone": "auto", "past_days": 7, "forecast_days": 7,
        "hourly": "temperature_2m,apparent_temperature,precipitation_probability,precipitation,weather_code,wind_speed_10m",
        "current": "temperature_2m,weather_code,wind_speed_10m"}).json()
    hourly = f.get("hourly") or {}
    cur = f.get("current") or {}
    name = ", ".join(x for x in (loc.get("name"), loc.get("country")) if x)
    _push({"kind": "weather", "title": name, "lat": loc["latitude"], "lon": loc["longitude"],
           "now": cur.get("time"), "current": {"t": cur.get("temperature_2m"), "code": cur.get("weather_code"),
                                               "text": wmo_text(cur.get("weather_code")), "wind": cur.get("wind_speed_10m")},
           "hourly": {"time": hourly.get("time", []), "t": hourly.get("temperature_2m", []),
                      "feels": hourly.get("apparent_temperature", []), "pp": hourly.get("precipitation_probability", []),
                      "pr": hourly.get("precipitation", []), "code": hourly.get("weather_code", []),
                      "wind": hourly.get("wind_speed_10m", [])},
           "codes": {str(k): (v[0] if ru else v[1]) for k, v in _WMO.items()}})
    t = cur.get("temperature_2m")
    return {"ok": True, "text": (f"{name}: сейчас {t}°, {wmo_text(cur.get('weather_code'))}. На экране — неделя назад и неделя вперёд."
                                 if ru else f"{name}: now {t}°, {wmo_text(cur.get('weather_code'))}. On screen: a week back and ahead.")}


# ---------------------------------------------------------------------------
# Зрение: камера и экран
# ---------------------------------------------------------------------------
def _camera_jpeg() -> bytes:
    try:
        import cv2
    except ImportError:
        raise RuntimeError("для камеры нужен модуль opencv-python — выполни: pip install opencv-python")
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(0)
    try:
        frame = None
        for _ in range(8):                        # первые кадры у веб-камер тёмные — даём настроиться
            ok, f = cap.read()
            if ok:
                frame = f
            time.sleep(0.05)
        if frame is None:
            raise RuntimeError("камера не отдаёт изображение (занята другой программой или отключена)")
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return buf.tobytes()
    finally:
        cap.release()


def _screen_jpeg() -> bytes:
    from PIL import ImageGrab
    img = ImageGrab.grab(all_screens=False)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=88)
    return buf.getvalue()


_VISION_RULES = ("Describe what you see accurately and briefly. Never identify real people by their face or body — "
                 "describe them (clothes, pose, setting) instead; you may name a person only if their name is written "
                 "in the image itself.")


_VISION_GROQ_PREFS = ("qwen/qwen3.8-27b", "qwen/qwen3.6-27b", "meta-llama/llama-4-maverick-17b-128e-instruct")
_vision_groq = {"model": None, "checked": False}


def _vision(img_b64: str, prompt: str, max_tokens: int = 400) -> str:
    """Gemini (с повтором при перегрузке) → модель со зрением в Groq → понятная ошибка."""
    import ai_brain as _ab
    ru = _lang() == "ru"
    msgs = [{"role": "system", "content": _VISION_RULES + (" Answer in Russian." if ru else " Answer in English.")},
            {"role": "user", "content": [{"type": "text", "text": prompt},
                                         {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + img_b64}}]}]
    cl = getattr(_ab, "gem_client", None)
    if cl is not None:
        model = getattr(_ab, "GEM_MODEL", "gem:gemini-3.8-flash").split(":", 1)[1]
        for attempt in range(2):
            try:
                r = cl.chat.completions.create(model=model, max_tokens=max_tokens, reasoning_effort="low", messages=msgs)
                text = (r.choices[0].message.content or "").replace("**", "").strip()
                if text:
                    return text
            except Exception as e:
                busy = any(k in str(e) for k in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "high demand"))
                print(f"[голо] Gemini (зрение): {str(e)[:90]}")
                if not busy:
                    break
                time.sleep(1.5)
    try:                                              # запасной путь — модель со зрением в Groq
        groq = _ab.client
        if not _vision_groq["checked"]:
            _vision_groq["checked"] = True
            have = [m.id for m in groq.models.list().data]
            _vision_groq["model"] = next((m for m in _VISION_GROQ_PREFS if m in have), None)
        if _vision_groq["model"]:
            kw = dict(model=_vision_groq["model"], max_tokens=max_tokens, temperature=0.2, messages=msgs)
            if "qwen" in _vision_groq["model"]:
                kw["reasoning_format"] = "hidden"
            r = groq.chat.completions.create(**kw)
            text = re.sub(r"<think>.*?</think>", "", r.choices[0].message.content or "", flags=re.S).replace("**", "").strip()
            if text:
                return text
    except Exception as e:
        print(f"[голо] Groq (зрение): {str(e)[:90]}")
    raise RuntimeError("Модель зрения сейчас перегружена — попробуй через минуту." if ru
                       else "The vision model is overloaded right now — try again in a minute.")



def look(question: str = "", source: str = "camera") -> dict:
    ru = _lang() == "ru"
    try:
        raw = _screen_jpeg() if source == "screen" else _camera_jpeg()
    except Exception as e:
        return {"ok": False, "text": str(e)}
    url, w, h = _save_image(raw)
    with open(os.path.join(ROOT, url.lstrip("/")), "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    q = question.strip() or ("Что здесь видно?" if ru else "What do you see?")
    try:
        answer = _vision(b64, q)
    except Exception as e:
        answer = str(e)
    title = ("Камера" if source != "screen" else "Экран") if ru else ("Camera" if source != "screen" else "Screen")
    _push({"kind": "image", "title": title, "caption": answer[:220], "src": url, "w": w, "h": h, "source": ""})
    return {"ok": True, "text": answer}


def explain_region(item_id: str, crop_b64: str, context: str = "") -> dict:
    """Выделенный кусок картинки → короткое название + объяснение для выноски."""
    ru = _lang() == "ru"
    title = ""
    for i in snapshot()["items"]:
        if i["id"] == item_id:
            title = i.get("title", "")
    prompt = (("Это выделенный фрагмент изображения" + (f" «{title}»" if title else "") + ". " if ru else
               "This is a selected region of an image" + (f" «{title}»" if title else "") + ". ")
              + ("Ответь строго в формате: первая строка — что это (2–5 слов), вторая — одно-два предложения пояснения. "
                 if ru else "Reply strictly: first line — what it is (2–5 words), second — one or two sentences of explanation. ")
              + (context or ""))
    try:
        text = _vision(crop_b64, prompt, 220)
    except Exception as e:
        return {"ok": False, "label": "Не получилось" if _lang() == "ru" else "Couldn't check", "text": str(e)[:200]}
    lines = [l.strip(" *#-") for l in text.splitlines() if l.strip()]
    return {"ok": True, "label": (lines[0] if lines else "?")[:60], "text": " ".join(lines[1:])[:260]}


# ---------------------------------------------------------------------------
# Граф знаний: что Atlas знает о тебе
# ---------------------------------------------------------------------------
GRAPH_MAX_NODES = 140


def _graph_rows():
    import sqlite3
    db = os.path.join(ROOT, "memory.db")
    c = sqlite3.connect(db, timeout=30)
    try:
        nodes = c.execute("SELECT id, name, label FROM nodes").fetchall()
        edges = c.execute("SELECT id, src, rel, dst, conf FROM edges WHERE active=1").fetchall()
    except sqlite3.Error:
        nodes, edges = [], []
    c.close()
    return nodes, edges


def graph_data(focus: str = "") -> dict:
    """Узлы и связи для голо-экрана. Если узлов много — ты и всё на расстоянии двух шагов, плюс самые связанные."""
    ru = _lang() == "ru"
    nodes, edges = _graph_rows()
    used = {e[1] for e in edges} | {e[3] for e in edges}
    nodes = [n for n in nodes if n[0] in used or n[1] == "user"]
    user = next((n[0] for n in nodes if n[1] == "user"), None)
    if len(nodes) > GRAPH_MAX_NODES:
        adj = {}
        for _id, s, _r, d, _c in edges:
            adj.setdefault(s, set()).add(d)
            adj.setdefault(d, set()).add(s)
        keep = set()
        if user is not None:
            keep = {user} | adj.get(user, set())
            for n in list(keep):
                keep |= adj.get(n, set())
        for n, _ in sorted(((n, len(v)) for n, v in adj.items()), key=lambda x: -x[1]):
            if len(keep) >= GRAPH_MAX_NODES:
                break
            keep.add(n)
        nodes = [n for n in nodes if n[0] in keep][:GRAPH_MAX_NODES]
    ids = {n[0] for n in nodes}
    out_nodes = [{"id": n[0], "label": ("Ты" if ru else "You") if n[1] == "user" else n[2], "user": n[1] == "user"}
                 for n in nodes]
    out_edges = [{"id": e[0], "s": e[1], "t": e[3], "rel": e[2], "conf": round(e[4] or 0.8, 2)}
                 for e in edges if e[1] in ids and e[3] in ids]
    return {"nodes": out_nodes, "edges": out_edges, "focus": (focus or "").strip().lower()}


def show_graph(focus: str = "") -> dict:
    ru = _lang() == "ru"
    d = graph_data(focus)
    title = "Что Atlas знает о тебе" if ru else "What Atlas knows about you"
    _push({"kind": "graph", "title": title, **d})
    n, m = len(d["nodes"]), len(d["edges"])
    if not m:
        return {"ok": True, "text": "Граф пока пустой — расскажи мне о себе, и он начнёт расти." if ru
                else "The graph is empty so far — tell me about yourself and it will grow."}
    lab = {x["id"]: x["label"] for x in d["nodes"]}
    uid = next((x["id"] for x in d["nodes"] if x["user"]), None)
    facts = [f"{e['rel'].replace('_', ' ')} → {lab.get(e['t'], '?')}" for e in d["edges"] if e["s"] == uid][:10]
    tail = ((" Главное о пользователе: " if ru else " Key facts about the user: ") + "; ".join(facts) + ".") if facts else ""
    return {"ok": True, "text": (f"На экране граф памяти: {n} понятий и {m} связей." if ru
                                 else f"The memory graph is on screen: {n} concepts, {m} links.") + tail}


def forget_edge(edge_id: int) -> bool:
    """«Забыть» связь: она становится неактивной (как при обновлении факта) и в ответы больше не попадает."""
    import sqlite3
    c = sqlite3.connect(os.path.join(ROOT, "memory.db"), timeout=30)
    try:
        n = c.execute("UPDATE edges SET active=0 WHERE id=?", (int(edge_id),)).rowcount
        c.commit()
    finally:
        c.close()
    print(f"[голо] забыта связь #{edge_id}")
    return bool(n)
