"""
Картинки как в поиске + надёжное «зрение» + голос Володарского.

1) Картинки не только из Википедии. Atlas собирает кандидатов из нескольких мест:
   поиск картинок DuckDuckGo (обычные фото со всего интернета), Википедия, Commons,
   Openverse. Отсеивает логотипы, иконки, векторные рисунки и мелочь, скачивает
   до 5 лучших параллельно. На карточке — главное фото и полоска миниатюр,
   в полноэкранном режиме — лента внизу: щёлкаешь и переключаешь.
2) «Error code: 503 … high demand» в выноске. Gemini иногда перегружен. Теперь
   Atlas повторяет попытку, затем пробует модель со зрением в Groq, а если и она
   недоступна — пишет понятное «модель зрения перегружена, попробуй через минуту»,
   без технического текста.
3) Пропал голос Володарского. Язык ответа не запоминался между запусками: Atlas
   стартовал на английском, а русские голоса Fish в английском режиме не предлагались.
   Теперь язык запоминается, а любой голос Fish (они многоязычные) можно выбрать
   для обоих языков — Володарский может говорить и по-английски.

Запуск из корня проекта:  python apply_images_v2.py
"""
import ast
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
FILES = ["core/holo.py", "voice.py", "atlas_ui.html"]
for f in FILES:
    if not os.path.exists(os.path.join(ROOT, f)):
        sys.exit(f"Не найден {f} — запусти из корня проекта Atlas.")
backup = os.path.join(ROOT, time.strftime("backup_img2_%Y%m%d_%H%M%S"))
for f in FILES:
    os.makedirs(os.path.dirname(os.path.join(backup, f)), exist_ok=True)
    shutil.copy2(os.path.join(ROOT, f), os.path.join(backup, f))
print(f"Резервная копия: {backup}")
src = {f: open(os.path.join(ROOT, f), encoding="utf-8").read() for f in FILES}
report = []


def rep(f, old, new, what):
    s = src[f]
    if new in s:
        report.append(f"  ✓ {f}: {what} (уже было)")
    elif s.count(old) == 1:
        src[f] = s.replace(old, new, 1)
        report.append(f"  ✓ {f}: {what}")
    else:
        report.append(f"  ! {f}: {what} — фрагмент не найден, пропущено")


def replace_func(f, start, end, new, what):
    """Заменить функцию целиком: от строки start до начала end."""
    s = src[f]
    if new.strip() in s:
        report.append(f"  ✓ {f}: {what} (уже было)")
        return
    i = s.find(start)
    j = s.find(end, i + 1) if i >= 0 else -1
    if i < 0 or j < 0:
        report.append(f"  ! {f}: {what} — функция не найдена, пропущено")
        return
    src[f] = s[:i] + new.rstrip() + "\n\n\n" + s[j:]
    report.append(f"  ✓ {f}: {what}")


# ---------------------------------------------------------------------------
# 1. core/holo.py — картинки из нескольких источников
# ---------------------------------------------------------------------------
H = "core/holo.py"
NEW_SHOW = '''_BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/128.0.0.0 Safari/537.36", "Referer": "https://duckduckgo.com/"}
_JUNK = re.compile(r"logo|icon|clip.?art|vector|emblem|sticker|cartoon|silhouette|\\.svg|\\.gif|watermark|"
                   r"shutterstock|alamy|dreamstime|depositphotos|istockphoto|123rf", re.I)


def _ddg_images(query: str, n: int = 10) -> list:
    """Поиск картинок DuckDuckGo — обычные фотографии со всего интернета, без ключа."""
    import httpx
    try:
        r = httpx.get("https://duckduckgo.com/", params={"q": query, "iax": "images", "ia": "images"},
                      headers=_BROWSER_UA, timeout=12, follow_redirects=True)
        m = re.search(r"vqd=[\\"']?([\\d-]+)", r.text)
        if not m:
            print(f"[голо] DuckDuckGo: нет токена поиска (код {r.status_code})")
            return []
        j = httpx.get("https://duckduckgo.com/i.js", headers=_BROWSER_UA, timeout=12, follow_redirects=True,
                      params={"l": "ru-ru" if _lang() == "ru" else "us-en", "o": "json", "q": query,
                              "vqd": m.group(1), "f": ",,,,,", "p": "1"})
        out = []
        for x in _json(j, "DuckDuckGo").get("results", [])[:n]:
            if x.get("image"):
                out.append({"title": x.get("title") or query, "url": x["image"], "page": x.get("url", ""),
                            "w": int(x.get("width") or 0), "h": int(x.get("height") or 0), "from": "web"})
        return out
    except Exception as e:
        print(f"[голо] DuckDuckGo: {e}")
        return []


def _candidates(query: str) -> tuple:
    """Кандидаты из всех источников (без логотипов и мелочи) + подпись из Википедии."""
    cands, caption, title = [], "", query
    wiki = _wiki_lookup(query)
    if wiki:
        title, caption = wiki[0], wiki[1]
        cands.append({"title": wiki[0], "url": wiki[2], "page": wiki[3], "w": 0, "h": 0, "from": "wiki"})
    web = _ddg_images(query)
    cands = web[:2] + cands + web[2:]                 # обычные фото — первыми, Википедия — рядом
    for fn in (_commons_lookup, _openverse_lookup):
        if len(cands) < 6:
            r = fn(query)
            if r:
                cands.append({"title": r[0], "url": r[2], "page": r[3], "w": 0, "h": 0, "from": fn.__name__})
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
            + (f" ({len(got)} фото на экране)" if _lang() == "ru" else f" ({len(got)} photos on screen)")}'''
replace_func(H, "def show_image(query: str) -> dict:", "\n# ----", NEW_SHOW, "картинки из нескольких источников + галерея")

NEW_VISION = '''_VISION_GROQ_PREFS = ("qwen/qwen3.8-27b", "qwen/qwen3.6-27b", "meta-llama/llama-4-maverick-17b-128e-instruct")
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
                       else "The vision model is overloaded right now — try again in a minute.")'''
replace_func(H, "def _vision(img_b64: str, prompt: str, max_tokens: int = 400) -> str:", "\ndef look(",
             NEW_VISION, "зрение: повтор, запасная модель, понятная ошибка")
rep(H, '''    except Exception as e:
        return {"ok": False, "label": "—", "text": str(e)[:200]}''', '''    except Exception as e:
        return {"ok": False, "label": "Не получилось" if _lang() == "ru" else "Couldn't check", "text": str(e)[:200]}''',
    "выноска при ошибке — понятный заголовок")

# ---------------------------------------------------------------------------
# 2. atlas_ui.html — галерея на карточке и в полноэкранном режиме
# ---------------------------------------------------------------------------
U = "atlas_ui.html"
rep(U, """      el.insertAdjacentHTML('beforeend', '<div class="hc-img"><img alt="' + holoHTML(it.title) + '" decoding="async" src="' + holoHTML(it.src) + '"></div>' +
        (it.caption ? '<p class="hc-cap">' + holoHTML(it.caption) + '</p>' : ''));""",
    """      el.insertAdjacentHTML('beforeend', '<div class="hc-img"><img alt="' + holoHTML(it.title) + '" decoding="async" src="' + holoHTML(it.src) + '"></div>' +
        (it.caption ? '<p class="hc-cap">' + holoHTML(it.caption) + '</p>' : ''));
      if (it.alts && it.alts.length > 1) {                       // полоска миниатюр: другие фото
        const strip = document.createElement('div'); strip.className = 'hc-strip';
        it.alts.forEach(function (a, i) {
          const b = document.createElement('button'); b.className = 'hc-th' + (i ? '' : ' on'); b.setAttribute('aria-label', String(i + 1));
          b.innerHTML = '<img alt="" decoding="async" src="' + holoHTML(a.src) + '">';
          b.addEventListener('click', function (e) { e.stopPropagation(); holoSetAlt(it.id, i); });
          strip.appendChild(b);
        });
        el.appendChild(strip);
      }""", "миниатюры других фото на карточке")
rep(U, """  // --- погода: точки, масштабы ---""", """  function holoSetAlt(id, i) {
    const c = holoCards.get(id); if (!c || !c.item.alts || !c.item.alts[i]) return;
    c.cur = i;
    const im = c.el.querySelector('.hc-img img'); if (im) im.src = c.item.alts[i].src;
    c.el.querySelectorAll('.hc-th').forEach(function (b, k) { b.classList.toggle('on', k === i); });
  }

  // --- погода: точки, масштабы ---""", "переключение фото")
rep(U, """    if (it.kind === 'weather') buildWeatherView(panel, it); else buildImageView(panel, it);""",
    """    if (it.kind === 'weather') buildWeatherView(panel, it);
    else {
      const c = holoCards.get(it.id), i = (c && c.cur) || 0;
      buildImageView(panel, it.alts && it.alts[i] ? Object.assign({}, it, it.alts[i], { title: it.title, caption: it.caption, cur: i }) : it);
    }""", "полноэкранный режим открывает выбранное фото")
rep(U, """    img.src = it.src;
    const ro = new ResizeObserver(fit); ro.observe(stage);""", """    img.src = it.src;
    if (it.alts && it.alts.length > 1) {                         // лента фото внизу
      const strip = document.createElement('div'); strip.className = 'hf-strip';
      it.alts.forEach(function (a, i) {
        const b = document.createElement('button'); b.className = 'hc-th' + (i === (it.cur || 0) ? ' on' : '');
        b.innerHTML = '<img alt="" decoding="async" src="' + holoHTML(a.src) + '">';
        b.addEventListener('click', function () {
          notes.length = 0; labels.innerHTML = ''; svg.innerHTML = '';
          img.src = a.src; holoSetAlt(it.id, i);
          strip.querySelectorAll('.hc-th').forEach(function (x, k) { x.classList.toggle('on', k === i); });
        });
        strip.appendChild(b);
      });
      panel.insertBefore(strip, panel.querySelector('.hf-foot'));
    }
    const ro = new ResizeObserver(fit); ro.observe(stage);""", "лента фото в полноэкранном режиме")
CSS = """  .hc-strip { display: flex; gap: 6px; margin-top: 8px; position: relative; z-index: 1; }
  .hc-th { width: 46px; height: 34px; padding: 0; border-radius: 5px; overflow: hidden; cursor: pointer; opacity: .6;
    border: 1px solid rgba(134,214,255,.2); background: rgba(0,0,0,.3); transition: opacity .15s, border-color .15s; flex: none; }
  .hc-th.on, .hc-th:hover { opacity: 1; border-color: #86D6FF; box-shadow: 0 0 10px rgba(134,214,255,.3); }
  .hc-th img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .hf-strip { display: flex; gap: 8px; padding: 8px 14px; border-top: 1px solid rgba(134,214,255,.12); overflow-x: auto; }
  .hf-strip .hc-th { width: 76px; height: 50px; }
</style>"""
if ".hc-strip" in src[U]:
    report.append(f"  ✓ {U}: оформление галереи (уже было)")
elif src[U].count("</style>") == 1:
    src[U] = src[U].replace("</style>", CSS, 1)
    report.append(f"  ✓ {U}: оформление галереи")

# ---------------------------------------------------------------------------
# 3. voice.py — язык запоминается, голоса Fish доступны в обоих языках
# ---------------------------------------------------------------------------
V = "voice.py"
rep(V, '''    _response_language["lang"] = "ru" if lang.startswith("ru") else "en"
    name = "Russian" if _response_language["lang"] == "ru" else "English"''',
    '''    _response_language["lang"] = "ru" if lang.startswith("ru") else "en"
    try:
        _save_voice_prefs()                              # язык запоминается между запусками
    except Exception:
        pass
    name = "Russian" if _response_language["lang"] == "ru" else "English"''', "язык ответа запоминается")
rep(V, '''            json.dump({"kokoro": KOKORO_VOICE, "fish": _fish_choice,''',
    '''            json.dump({"lang": _response_language["lang"], "kokoro": KOKORO_VOICE, "fish": _fish_choice,''',
    "язык сохраняется вместе с голосом")
rep(V, '''    KOKORO_VOICE = p.get("kokoro", KOKORO_VOICE)
    _en_engine["name"] = p.get("en_engine", "kokoro")''', '''    KOKORO_VOICE = p.get("kokoro", KOKORO_VOICE)
    if p.get("lang") in ("ru", "en"):
        _response_language["lang"] = p["lang"]             # язык с прошлого запуска
    _en_engine["name"] = p.get("en_engine", "kokoro")''', "язык восстанавливается при запуске")
rep(V, '''        vid = (p.get("fish") or {}).get(lang)
        if vid in FISH_VOICES[lang].values():''', '''        vid = (p.get("fish") or {}).get(lang)
        if vid in _all_fish().values():''', "выбранный голос Fish восстанавливается в любом языке")
rep(V, '''def list_voice_choices(lang: str) -> list:''', '''def _all_fish() -> dict:
    """Голоса Fish многоязычные: русский голос (Володарский) может говорить и по-английски."""
    return {**FISH_VOICES["en"], **FISH_VOICES["ru"]}


def list_voice_choices(lang: str) -> list:''', "общий список голосов Fish")
rep(V, '''    return base + list(FISH_VOICES[lang].keys())''', '''    return base + list(_all_fish().keys())''', "голоса Fish видны в обоих языках")
rep(V, '''        for name, v in FISH_VOICES[lang].items():
            if v == vid:
                return name''', '''        for name, v in _all_fish().items():
            if v == vid:
                return name''', "текущий голос Fish узнаётся в любом языке")
rep(V, '''    if name in FISH_VOICES[lang]:
        _fish_choice[lang] = FISH_VOICES[lang][name]''', '''    if name in _all_fish():
        _fish_choice[lang] = _all_fish()[name]''', "любой голос Fish можно выбрать для текущего языка")

ok = True
for f in ("core/holo.py", "voice.py"):
    try:
        ast.parse(src[f], filename=f)
    except SyntaxError as e:
        ok = False
        report.append(f"  ! {f}: {e} — файл НЕ изменён")
        continue
    open(os.path.join(ROOT, f), "w", encoding="utf-8", newline="\n").write(src[f])
    report.append(f"  ✓ синтаксис {f}")
open(os.path.join(ROOT, U), "w", encoding="utf-8", newline="\n").write(src[U])
print("\n".join(report))
print("\nГотово. Запускай: python main.py" if ok else f"\nЕсть ошибка — пришли вывод (копия: {backup}).")
