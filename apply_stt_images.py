"""
Картинки на голо-экране + точнее распознавание речи.

1) «Не могу найти картинку». Википедия требует, чтобы в запросе был указан способ
   связаться с автором программы, иначе отвечает отказом обычным текстом. Теперь:
     • в запросе — ссылка на репозиторий Atlas, как просит правило Wikimedia;
     • переходы по перенаправлениям включены;
     • если сервис всё равно отказал — в лог пишется код ответа и начало текста;
     • третий запасной источник — Openverse (открытая база свободных фото).

2) Распознавание речи («Эйфелеву пашню» вместо «Эйфелеву башню»):
     • полная модель whisper-large-v3 вместо облегчённой turbo — заметно точнее
       на русском, медленнее на доли секунды;
     • подсказка-словарь: Whisper заранее «слышит» типичные слова Atlas
       (покажи, разверни, погода, Бишкек…) + твои слова из .env (STT_VOCAB);
     • STT_LANGUAGE=ru в .env — если говоришь с Atlas только по-русски, Whisper
       перестанет гадать язык (самый большой выигрыш в точности);
     • модели сказано: текст пришёл из распознавания речи и может содержать
       ослышки — угадывай задуманное («Эйфелеву пашню» → Эйфелева башня).

Запуск из корня проекта:  python apply_stt_images.py
"""
import ast
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
FILES = ["voice.py", "core/holo.py", "ai_brain.py"]
for f in FILES:
    if not os.path.exists(os.path.join(ROOT, f)):
        sys.exit(f"Не найден {f} — запусти из корня проекта Atlas (и сначала apply_holo.py).")
backup = os.path.join(ROOT, time.strftime("backup_sttimg_%Y%m%d_%H%M%S"))
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


# ---------------------------------------------------------------------------
# 1. core/holo.py — картинки
# ---------------------------------------------------------------------------
H = "core/holo.py"
rep(H, 'UA = {"User-Agent": "AtlasAssistant/1.0 (student portfolio project; local voice assistant)"}',
    'UA = {"User-Agent": "AtlasAssistant/1.0 (https://github.com/alimtszyu13-svg/atlas-personal-assistant; '
    'student voice assistant) python-httpx"}', "запросы подписаны так, как требует Wikimedia")
rep(H, '''def _wiki_lookup(query: str):''', '''def _json(r, where: str) -> dict:
    """Ответ сервиса → словарь; если пришёл не JSON — пишем в лог, что именно пришло."""
    try:
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    print(f"[голо] {where}: код {r.status_code}, ответ: {r.text[:120]!r}")
    return {}


def _wiki_lookup(query: str):''', "диагностика ответов сервисов")
rep(H, '''            r = httpx.get(f"https://{lang}.wikipedia.org/w/api.php", headers=UA, timeout=12, params={
                "action": "query", "list": "search", "srsearch": query, "srlimit": 3, "format": "json"})
            for hit in r.json().get("query", {}).get("search", []):
                s = httpx.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{hit['title'].replace(' ', '_')}",
                              headers=UA, timeout=12, follow_redirects=True).json()''',
    '''            r = httpx.get(f"https://{lang}.wikipedia.org/w/api.php", headers=UA, timeout=12, follow_redirects=True,
                          params={"action": "query", "list": "search", "srsearch": query, "srlimit": 3, "format": "json"})
            for hit in _json(r, f"Википедия ({lang})").get("query", {}).get("search", []):
                s = _json(httpx.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{hit['title'].replace(' ', '_')}",
                                    headers=UA, timeout=12, follow_redirects=True), f"Википедия ({lang}), статья")''',
    "Википедия: переходы по ссылкам и проверка ответа")
rep(H, '''        r = httpx.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=12, params={
            "action": "query", "generator": "search", "gsrnamespace": 6, "gsrsearch": query, "gsrlimit": 5,
            "prop": "imageinfo", "iiprop": "url|mime", "iiurlwidth": 2048, "format": "json"})
        pages = sorted((r.json().get("query", {}).get("pages") or {}).values(), key=lambda p: p.get("index", 99))''',
    '''        r = httpx.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=12, follow_redirects=True, params={
            "action": "query", "generator": "search", "gsrnamespace": 6, "gsrsearch": query, "gsrlimit": 5,
            "prop": "imageinfo", "iiprop": "url|mime", "iiurlwidth": 2048, "format": "json"})
        pages = sorted((_json(r, "Commons").get("query", {}).get("pages") or {}).values(), key=lambda p: p.get("index", 99))''',
    "Commons: переходы по ссылкам и проверка ответа")
rep(H, '''def show_image(query: str) -> dict:
    import httpx
    found = _wiki_lookup(query) or _commons_lookup(query)''', '''def _openverse_lookup(query: str):
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


def show_image(query: str) -> dict:
    import httpx
    found = _wiki_lookup(query) or _commons_lookup(query) or _openverse_lookup(query)''', "третий источник — Openverse")
rep(H, '''    raw = httpx.get(src, headers=UA, timeout=30, follow_redirects=True).content
    url, w, h = _save_image(raw)''', '''    resp = httpx.get(src, headers=UA, timeout=30, follow_redirects=True)
    if resp.status_code != 200 or not resp.headers.get("content-type", "").startswith("image/"):
        print(f"[голо] картинка не скачалась: код {resp.status_code}, {resp.headers.get('content-type')}")
        return {"ok": False, "text": "Нашёл, но картинка не скачалась." if _lang() == "ru" else "Found it, but the image didn't download."}
    url, w, h = _save_image(resp.content)''', "проверка, что скачалась именно картинка")

# ---------------------------------------------------------------------------
# 2. voice.py — распознавание
# ---------------------------------------------------------------------------
V = "voice.py"
rep(V, '''_WHISPER_JUNK = {''', '''STT_WHISPER = os.getenv("STT_MODEL") or "whisper-large-v3"     # полная модель: точнее turbo на русском
_STT_BASE_PROMPT = ("Атлас, покажи Эйфелеву башню. Разверни. Сверни. Закрой. Погода в Бишкеке. Найди в интернете. "
                    "Открой браузер. Включи музыку. Запомни. Напомни. Посмотри, что у меня в руке. Давай повторим SAT. "
                    "Подведи итоги дня. Стоп. Хватит.")


def _stt_prompt() -> str:
    """Подсказка-словарь для Whisper: типичные слова Atlas + твои слова из .env (STT_VOCAB)."""
    extra = (os.getenv("STT_VOCAB") or "").strip()
    return (_STT_BASE_PROMPT + (" " + extra if extra else ""))[:600]


def _stt_language():
    """STT_LANGUAGE=ru|en — язык речи зафиксирован; иначе в русском режиме — ru, в английском — угадывает сам."""
    fixed = (os.getenv("STT_LANGUAGE") or "").strip().lower()
    if fixed in ("ru", "en"):
        return fixed
    return "ru" if _response_language["lang"] == "ru" else None


_WHISPER_JUNK = {''', "настройки распознавания: модель, словарь, язык")
rep(V, '''        if _response_language["lang"] == "ru":
            result = groq_client.audio.transcriptions.create(
                file=(temp_path, data), model="whisper-large-v3-turbo", language="ru")
        else:
            result = groq_client.audio.transcriptions.create(
                file=(temp_path, data), model="whisper-large-v3-turbo",
                response_format="verbose_json")
            lang = (getattr(result, "language", "") or "").lower()
            if lang and lang not in ("en", "english", "ru", "russian"):
                # Whisper иногда принимает русскую речь за польскую — переслушиваем как русскую
                print(f"[Whisper] язык «{lang}» — перераспознаю как русский")
                result = groq_client.audio.transcriptions.create(
                    file=(temp_path, data), model="whisper-large-v3-turbo", language="ru")''',
    '''        kw = dict(file=(temp_path, data), model=STT_WHISPER, temperature=0.0, prompt=_stt_prompt())
        stt_lang = _stt_language()
        if stt_lang:
            result = groq_client.audio.transcriptions.create(language=stt_lang, **kw)
        else:
            result = groq_client.audio.transcriptions.create(response_format="verbose_json", **kw)
            lang = (getattr(result, "language", "") or "").lower()
            if lang and lang not in ("en", "english", "ru", "russian"):
                # Whisper иногда принимает русскую речь за польскую — переслушиваем как русскую
                print(f"[Whisper] язык «{lang}» — перераспознаю как русский")
                result = groq_client.audio.transcriptions.create(language="ru", **kw)''',
    "полная модель Whisper + словарь + язык")

# ---------------------------------------------------------------------------
# 3. ai_brain.py — модель угадывает ослышки
# ---------------------------------------------------------------------------
A = "ai_brain.py"
RULE = '''

# === Правило: речь распознана с ошибками ===
_STT_RULE = (" SPEECH INPUT: the user's words come from speech recognition and may contain misheard words "
             "(e.g. 'Эйфелеву пашню' or 'Эй, щелева башня' = 'Эйфелева башня'). Infer the most likely intended "
             "words from context and act on them; ask only if several readings are equally likely.")
if "SPEECH INPUT:" not in SYSTEM_PROMPT:
    SYSTEM_PROMPT = SYSTEM_PROMPT + _STT_RULE
    if conversation_history and isinstance(conversation_history[0], dict) and conversation_history[0].get("role") == "system":
        conversation_history[0]["content"] = SYSTEM_PROMPT
'''
if "# === Правило: речь распознана с ошибками ===" in src[A]:
    report.append(f"  ✓ {A}: правило про ослышки (уже было)")
else:
    src[A] = src[A].rstrip() + "\n" + RULE
    report.append(f"  ✓ {A}: модель угадывает ослышки распознавания")

ok = True
for f in FILES:
    try:
        ast.parse(src[f], filename=f)
    except SyntaxError as e:
        ok = False
        report.append(f"  ! {f}: {e} — файл НЕ изменён")
        continue
    open(os.path.join(ROOT, f), "w", encoding="utf-8", newline="\n").write(src[f])
    report.append(f"  ✓ синтаксис {f}")
print("\n".join(report))
print("\nГотово. Запускай: python main.py" if ok else f"\nЕсть ошибка — пришли вывод (копия: {backup}).")
