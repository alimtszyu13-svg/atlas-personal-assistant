"""
Браузерный агент Atlas — «руки» в интернете.

Восприятие страницы
  • Все интерактивные элементы видимой части: кнопки, ссылки, поля, выпадающие
    списки, чекбоксы, вкладки, меню — в том числе внутри Shadow DOM и iframe.
  • У каждого — роль, название, значение и состояние (отмечен, раскрыт, недоступен,
    перекрыт другим элементом), для ссылок — куда ведёт.
  • Элементы помечаются атрибутом data-atlas-id, и клик идёт по нему, а не по
    координатам: сдвиг вёрстки или прокрутка ничего не ломают.
  • Отдельно — ЧТЕНИЕ страницы (browser_read_text): основной текст без меню,
    рекламы и подвалов, частями, с заголовками.

Действия
  • Клик, ввод (с очисткой поля и Enter), выбор в списке, прокрутка, поиск
    элемента по тексту, назад/вперёд, вкладки, ожидание.
  • После каждого действия — ожидание загрузки и свежий список элементов.

Надёжность
  • Новые вкладки и всплывающие окна подхватываются сами.
  • Окна alert — закрываются, confirm/prompt — отклоняются (ничего не подтверждаем
    за пользователя); об этом сообщается модели.
  • Баннеры cookie закрываются кнопкой «Принять» (выключается BROWSER_AUTO_CONSENT=0).
  • Закрыли окно браузера — поднимается заново.
  • Зрение как запасной путь: номера рисуются прямо на скриншоте (set-of-marks),
    и модель со зрением выбирает номер, а не угадывает пиксели.

Безопасность: поля паролей и оплаты — только с явного подтверждения пользователя вслух.
Макросы для кино (Rezka, Netflix) и управления плеером — без изменений.
"""

import base64
import concurrent.futures
import functools
import json
import os
import re

from playwright.sync_api import sync_playwright

_playwright = None
_browser = None
_page = None
_last_elements = []          # последний снимок: [{i, tag, role, name, ..., f: номер фрейма}]
_events = []                 # что случилось между действиями: новые вкладки, диалоги, баннеры

NETFLIX_PROFILE_DIR = "browser_profile"  # cookies/сессия хранятся тут между запусками
MAX_ELEMENTS = 60
READ_CHARS = 3500
AUTO_CONSENT = os.getenv("BROWSER_AUTO_CONSENT", "1") != "0"

SENSITIVE_KEYWORDS = (
    "password", "пароль", "card number", "номер карты", "cvv", "cvc",
    "expiry", "expiration date", "срок действия карты",
    "pay now", "buy now", "checkout", "place order", "purchase",
    "оплатить", "купить", "оформить заказ", "billing address",
)

AD_BLOCK_DOMAINS = (
    "doubleclick.net", "googlesyndication.com", "google-analytics.com",
    "googletagmanager.com", "facebook.net", "adservice.google.com",
    "amazon-adsystem.com", "scorecardresearch.com", "taboola.com",
    "outbrain.com", "criteo.com", "adnxs.com", "pubmatic.com",
    "rubiconproject.com", "moatads.com", "adform.net", "adsafeprotected.com",
)

browser_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)


# ===========================================================================
# Поток браузера и живучесть
# ===========================================================================
def run_in_browser_thread(func):
    """Все вызовы Playwright — в одном потоке. Закрыли окно во время действия —
    браузер поднимается заново, и действие повторяется один раз."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        def call():
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if not _is_closed_error(e):
                    raise
                print("[browser] окно браузера было закрыто — перезапускаю и повторяю")
                _reset_browser()
                return func(*args, **kwargs)
        return browser_executor.submit(call).result()
    return wrapper


def _is_closed_error(e) -> bool:
    msg = str(e)
    return ("has been closed" in msg or "Target closed" in msg
            or "Browser has been disconnected" in msg)


def _browser_alive() -> bool:
    try:
        return (_browser is not None and _page is not None
                and not _page.is_closed() and len(_browser.pages) > 0)
    except Exception:
        return False


def _reset_browser() -> None:
    global _playwright, _browser, _page, _last_elements
    for obj, method in ((_browser, "close"), (_playwright, "stop")):
        if obj is not None:
            try:
                getattr(obj, method)()
            except Exception:
                pass
    _browser = _playwright = _page = None
    _last_elements = []


def _route_filter(route):
    """Блокирует картинки/шрифты и рекламные домены (выключено по умолчанию — ломает защиту некоторых сайтов)."""
    request = route.request
    if request.resource_type in ("image", "font") or any(d in request.url for d in AD_BLOCK_DOMAINS):
        route.abort()
        return
    route.continue_()


def _on_dialog(dialog):
    kind, msg = dialog.type, (dialog.message or "")[:100]
    try:
        if kind in ("alert", "beforeunload"):
            dialog.accept()
            _events.append(f"страница показала сообщение «{msg}» — закрыл")
        else:
            dialog.dismiss()                          # ничего не подтверждаем за пользователя
            _events.append(f"страница спросила «{msg}» ({kind}) — отклонил; если нужно согласиться, спроси пользователя")
    except Exception:
        pass


def _watch_page(p):
    try:
        p.on("dialog", _on_dialog)
    except Exception:
        pass


def _on_new_page(p):
    """Сайт открыл новую вкладку или всплывающее окно — переключаемся на неё."""
    global _page
    _watch_page(p)
    _page = p
    try:
        p.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:
        pass
    _events.append("открылась новая вкладка — работаю в ней (browser_tabs покажет все)")


def _ensure_browser():
    global _playwright, _browser, _page
    if _browser is not None and not _browser_alive():
        try:
            _page = _browser.pages[0] if _browser.pages else _browser.new_page()
        except Exception:
            print("[browser] окно браузера было закрыто — открываю заново")
            _reset_browser()
    if _browser is None:
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch_persistent_context(
            NETFLIX_PROFILE_DIR,
            headless=os.getenv("BROWSER_HEADLESS") == "1",
            executable_path=os.getenv("BROWSER_EXECUTABLE") or None,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-infobars"],
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
        )
        _browser.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        _browser.on("page", _on_new_page)
        _page = _browser.pages[0] if _browser.pages else _browser.new_page()
        for p in _browser.pages:
            _watch_page(p)
    return _page


def _settle(quick: bool = False) -> None:
    """Дождаться, пока страница загрузится и успокоится (не дольше пары секунд)."""
    try:
        _page.wait_for_load_state("domcontentloaded", timeout=6000)
    except Exception:
        pass
    try:
        _page.wait_for_load_state("networkidle", timeout=1200 if quick else 2500)
    except Exception:
        pass


# ===========================================================================
# Восприятие страницы
# ===========================================================================
_SNAPSHOT_JS = r"""
([maxN, startId]) => {
  const INTER = 'a[href],button,input:not([type=hidden]),textarea,select,summary,[role=button],[role=link],' +
    '[role=checkbox],[role=radio],[role=tab],[role=menuitem],[role=option],[role=switch],[role=combobox],' +
    '[role=textbox],[role=searchbox],[onclick],[contenteditable="true"],[tabindex]:not([tabindex="-1"])';
  const out = []; let id = startId;
  const vh = innerHeight, vw = innerWidth;
  const seen = new Map();
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  function nameOf(el) {
    let lb = '';
    if (el.id) { try { const l = el.getRootNode().querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) lb = l.innerText; } catch (e) {} }
    if (!lb && el.closest) {
      const l = el.closest('label');
      if (l && l !== el) lb = el.tagName === 'SELECT' ? l.innerText.replace(el.innerText, '') : l.innerText;   // без текста вариантов
    }
    const img = el.querySelector ? el.querySelector('img[alt]') : null;
    return clean(el.getAttribute('aria-label') || lb || el.innerText || el.value || el.placeholder ||
                 el.title || (img && img.alt) || el.getAttribute('name') || '').slice(0, 80);
  }
  function visibility(el, r) {
    if (r.width < 2 || r.height < 2) return 0;
    if (r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) return 0;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || Number(st.opacity) === 0) return 0;
    const cx = Math.min(vw - 1, Math.max(0, r.left + r.width / 2)), cy = Math.min(vh - 1, Math.max(0, r.top + r.height / 2));
    const root = el.getRootNode();
    const top = (root.elementFromPoint ? root.elementFromPoint(cx, cy) : document.elementFromPoint(cx, cy));
    if (top && top !== el && !el.contains(top) && !(top.contains && top.contains(el))) return 2;   // перекрыт
    return 1;
  }
  function walk(root) {
    root.querySelectorAll('[data-atlas-id]').forEach(e => e.removeAttribute('data-atlas-id'));
    root.querySelectorAll(INTER).forEach(el => {
      if (out.length >= maxN) return;
      const r = el.getBoundingClientRect(); const v = visibility(el, r);
      if (!v) return;
      const tag = el.tagName.toLowerCase(); const role = el.getAttribute('role') || '';
      const nm = nameOf(el);
      if (!nm && !['input', 'textarea', 'select'].includes(tag)) return;
      for (let p = el.parentElement; p; p = p.parentElement) {          // вложенный дубль той же кнопки
        if (seen.has(p) && (seen.get(p) === nm || !nm)) return;
      }
      el.setAttribute('data-atlas-id', String(id)); seen.set(el, nm);
      const it = { i: id++, tag, role, name: nm, type: (el.type || '').toLowerCase(),
                   x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
      if (v === 2) it.covered = true;
      if (tag === 'a') it.href = (el.getAttribute('href') || '').slice(0, 70);
      if (['checkbox', 'radio'].includes(it.type) || ['checkbox', 'radio', 'switch'].includes(role))
        it.checked = !!(el.checked || el.getAttribute('aria-checked') === 'true');
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') it.disabled = true;
      if (el.getAttribute('aria-expanded')) it.expanded = el.getAttribute('aria-expanded') === 'true';
      if (tag === 'select') {
        it.value = el.options[el.selectedIndex] ? clean(el.options[el.selectedIndex].text).slice(0, 40) : '';
        it.options = [...el.options].slice(0, 8).map(o => clean(o.text).slice(0, 25));
      } else if ((tag === 'input' || tag === 'textarea') && !['password', 'checkbox', 'radio'].includes(it.type) && el.value) {
        it.value = el.value.slice(0, 40);
      }
      out.push(it);
    });
    root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) walk(el.shadowRoot); });
  }
  walk(document);
  const doc = document.scrollingElement || document.documentElement;
  return { items: out, url: location.href, title: document.title, y: Math.round(scrollY), vh, h: doc.scrollHeight };
}
"""


def _frames():
    """Главный фрейм + видимые iframe со сдвигом их координат."""
    out = [(_page.main_frame, 0, 0)]
    for fr in _page.frames[1:]:
        try:
            box = fr.frame_element().bounding_box()
        except Exception:
            continue
        if box and box["width"] > 20 and box["height"] > 20 and box["y"] < 720 and box["y"] + box["height"] > 0:
            out.append((fr, box["x"], box["y"]))
    return out


def _snapshot(max_elements: int = MAX_ELEMENTS) -> str:
    """Список интерактивных элементов видимой части с состояниями + где мы находимся."""
    global _last_elements
    items, info = [], None
    for fi, (fr, ox, oy) in enumerate(_frames()):
        if len(items) >= max_elements:
            break
        try:
            res = fr.evaluate(_SNAPSHOT_JS, [max_elements - len(items), len(items)])
        except Exception:
            continue
        if fi == 0:
            info = res
        for it in res["items"]:
            it["f"], it["x"], it["y"] = fi, it["x"] + ox, it["y"] + oy
            items.append(it)
    _last_elements = items
    head = []
    if _events:
        head.append("События: " + "; ".join(_events))
        _events.clear()
    if info:
        screens = max(1.0, info["h"] / max(1, info["vh"]))
        pct = 0 if info["h"] <= info["vh"] else min(100, round(info["y"] / (info["h"] - info["vh"]) * 100))
        head.append(f"URL: {info['url'][:150]}")
        head.append(f"Заголовок: {info['title'][:100]}")
        head.append(f"Прокрутка: {pct}% (страница ≈ {screens:.1f} экрана)"
                    + (f" · вкладок: {len(_browser.pages)}" if _browser and len(_browser.pages) > 1 else ""))
    if not items:
        head.append("Интерактивных элементов на экране нет — прокрути (browser_scroll), найди по тексту "
                    "(browser_find) или прочитай страницу (browser_read_text).")
        return "\n".join(head)
    lines = [_fmt(it) for it in items]
    tail = "" if len(items) < max_elements else "\n(показаны не все — browser_scroll или browser_find)"
    return "\n".join(head) + "\nЭлементы:\n" + "\n".join(lines) + tail


def _fmt(it: dict) -> str:
    tag, t = it["tag"], it.get("type", "")
    if it.get("role"):
        kind = it["role"]
    elif tag == "a":
        kind = "link"
    elif tag == "input":
        kind = "button" if t in ("submit", "button", "image", "reset") else ("checkbox" if t in ("checkbox", "radio") else f"input[{t or 'text'}]")
    elif tag == "textarea":
        kind = "textbox"
    else:
        kind = tag
    s = f'[{it["i"]}] {kind} "{it["name"]}"'
    if it.get("value"):
        s += f' = "{it["value"]}"'
    if it.get("options"):
        s += " options: " + " | ".join(it["options"])
    if "checked" in it:
        s += " (отмечен)" if it["checked"] else " (не отмечен)"
    if "expanded" in it:
        s += " (раскрыт)" if it["expanded"] else " (свёрнут)"
    if it.get("disabled"):
        s += " (недоступен)"
    if it.get("covered"):
        s += " (перекрыт окном)"
    if it.get("href") and not it["href"].startswith(("javascript", "#")):
        s += f" → {it['href']}"
    return s


def _el(index):
    try:
        index = int(index)
    except (TypeError, ValueError):
        return None
    return next((e for e in _last_elements if e["i"] == index), None)


def _locator(el):
    frames = _frames()
    fr = frames[el["f"]][0] if el["f"] < len(frames) else _page.main_frame
    return fr.locator(f'[data-atlas-id="{el["i"]}"]').first


def _is_sensitive(el: dict) -> bool:
    text = (el.get("name") or el.get("text") or "").lower()
    return el.get("type") == "password" or any(k in text for k in SENSITIVE_KEYWORDS)


_CONSENT_JS = r"""
() => {
  const words = ['принять все', 'принять всё', 'принять', 'согласен', 'согласна', 'разрешить все', 'понятно',
                 'accept all', 'accept', 'i agree', 'agree', 'allow all', 'got it'];
  const boxes = document.querySelectorAll('[id*=cookie i],[class*=cookie i],[id*=consent i],[class*=consent i],' +
                                          '[id*=gdpr i],[class*=gdpr i],[aria-label*=cookie i]');
  for (const b of boxes) {
    for (const el of b.querySelectorAll('button,a,[role=button]')) {
      const t = (el.innerText || '').trim().toLowerCase();
      if (t && t.length < 30 && words.some(w => t === w || t.startsWith(w))) { el.click(); return t; }
    }
  }
  return '';
}
"""


def _dismiss_consent() -> None:
    if not AUTO_CONSENT:
        return
    try:
        t = _page.evaluate(_CONSENT_JS)
        if t:
            _events.append(f"закрыл баннер cookie («{t}»)")
            _page.wait_for_timeout(400)
    except Exception:
        pass


# ===========================================================================
# Инструменты для модели
# ===========================================================================
@run_in_browser_thread
def browser_open(url: str) -> str:
    """Opens a URL in Atlas's browser and returns where it is + numbered interactive elements."""
    if url.lower().startswith("file:") or re.match(r"^[a-zA-Z]:[\\/]", url):
        import urllib.parse as _up
        path = _up.unquote(re.sub(r"^file:/*", "", url, flags=re.I)).replace("/", "\\")
        if os.path.exists(path):
            os.startfile(path)
            return f"Открыл файл программой по умолчанию: {path}"
        return f"Файл не найден: {path}. Для локальных файлов используй open_search_result или open_file."
    if not re.match(r"^[a-z]+://", url, re.I):
        url = "https://" + url
    page = _ensure_browser()
    page.goto(url, wait_until="domcontentloaded", timeout=25000)
    page.bring_to_front()
    _settle()
    _dismiss_consent()
    return "Открыл страницу.\n" + _snapshot()


@run_in_browser_thread
def browser_read_page() -> str:
    """Fresh list of interactive elements in the visible part of the page."""
    if not _browser_alive():
        return "Браузер не открыт — сначала browser_open."
    return _snapshot()


_READ_JS = r"""
([maxChars, part]) => {
  let root = document.querySelector('article') || document.querySelector('main') || document.querySelector('[role=main]');
  if (!root) {
    let best = document.body, bestLen = 0; const all = (document.body.innerText || '').length;
    document.querySelectorAll('div,section').forEach(d => {
      const p = d.querySelectorAll('p').length, len = (d.innerText || '').length;
      if (p >= 3 && len > bestLen && len < all * 0.97) { best = d; bestLen = len; }
    });
    root = best;
  }
  const heads = [...root.querySelectorAll('h1,h2,h3')].slice(0, 12).map(h => h.innerText.trim()).filter(Boolean);
  const clone = root.cloneNode(true);
  clone.querySelectorAll('script,style,noscript,nav,footer,aside,form,svg,iframe,button,[aria-hidden=true],' +
                         '[class*=advert i],[class*=banner i],[id*=cookie i],[class*=share i],[class*=related i]').forEach(e => e.remove());
  clone.querySelectorAll('br').forEach(e => e.after('\n'));
  clone.querySelectorAll('p,div,h1,h2,h3,h4,h5,h6,li,tr,section,article,blockquote,pre,dd,dt,figcaption').forEach(e => e.append('\n'));
  const text = (clone.textContent || '').replace(/[ \t\u00a0]+/g, ' ').replace(/\s*\n\s*/g, '\n').replace(/\n{2,}/g, '\n').trim();
  const parts = Math.max(1, Math.ceil(text.length / maxChars));
  return { title: document.title, url: location.href, heads, text: text.slice((part - 1) * maxChars, part * maxChars), part, parts };
}
"""


@run_in_browser_thread
def browser_read_text(part: int = 1) -> str:
    """Reads the MAIN TEXT of the current page (article, results, product info), without menus and ads, in parts."""
    if not _browser_alive():
        return "Браузер не открыт — сначала browser_open."
    part = max(1, int(part or 1))
    r = _page.evaluate(_READ_JS, [READ_CHARS, part])
    head = f"{r['title'][:100]}\n{r['url'][:150]}\n"
    if r["heads"] and part == 1:
        head += "Разделы: " + " | ".join(h[:60] for h in r["heads"]) + "\n"
    more = f"\n\n(часть {r['part']} из {r['parts']}; дальше — browser_read_text(part={part + 1}))" if part < r["parts"] else ""
    return head + "\n" + (r["text"] or "(текста на странице почти нет — это приложение или форма; смотри browser_read_page)") + more


@run_in_browser_thread
def browser_click(index: int) -> str:
    """Clicks element [index] from the latest element list; returns the page state after the click."""
    el = _el(index)
    if not el:
        return f"Нет элемента [{index}] — возьми номер из свежего списка (browser_read_page)."
    if _is_sensitive(el):
        return "Отказываюсь кликать — похоже на пароль или оплату. Только с явного подтверждения пользователя вслух."
    if el.get("disabled"):
        return f"Элемент [{index}] «{el['name']}» недоступен — сначала заполни то, что он требует."
    loc = _locator(el)
    n_pages = len(_browser.pages)
    try:
        loc.click(timeout=4000)
    except Exception as e:
        if _is_closed_error(e):
            raise
        try:
            loc.click(timeout=2500, force=True)                   # перекрыт прозрачным слоем
        except Exception as e2:
            if _is_closed_error(e2):
                raise
            _page.mouse.click(el["x"], el["y"])                   # последний шанс — по координатам
    for _ in range(8):                                            # ссылка могла открыть новую вкладку
        if len(_browser.pages) > n_pages:
            break
        _browser.pages[0].wait_for_timeout(100)
    _settle(quick=True)
    return f"Нажал [{index}] «{el['name']}».\n" + _snapshot()


@run_in_browser_thread
def browser_type(index: int, text: str, submit: bool = False) -> str:
    """Clears field [index] and types text; submit=true presses Enter afterwards."""
    el = _el(index)
    if not el:
        return f"Нет элемента [{index}] — возьми номер из свежего списка (browser_read_page)."
    if _is_sensitive(el):
        return "Отказываюсь вводить — похоже на пароль или оплату. Только с явного подтверждения пользователя вслух."
    loc = _locator(el)
    try:
        loc.fill(str(text), timeout=4000)
    except Exception as e:
        if _is_closed_error(e):
            raise
        _page.mouse.click(el["x"], el["y"])
        _page.keyboard.press("Control+A")
        _page.keyboard.type(str(text), delay=15)
    if submit:
        try:
            loc.press("Enter", timeout=3000)
        except Exception:
            _page.keyboard.press("Enter")
        _settle()
    return f"Ввёл «{str(text)[:60]}» в [{index}] «{el['name']}»" + (" и нажал Enter" if submit else "") + ".\n" + _snapshot()


@run_in_browser_thread
def browser_select(index: int, option: str) -> str:
    """Chooses an option in dropdown [index] by its visible text (or value)."""
    el = _el(index)
    if not el:
        return f"Нет элемента [{index}]."
    loc = _locator(el)
    try:
        loc.select_option(label=option, timeout=4000)
    except Exception:
        try:
            loc.select_option(value=option, timeout=3000)
        except Exception as e:
            if _is_closed_error(e):
                raise
            return (f"Не нашёл вариант «{option}» в [{index}]. Варианты: {' | '.join(el.get('options') or [])}. "
                    "Если это не обычный список — кликни по нему и выбери пункт кликом.")
    _settle(quick=True)
    return f"Выбрал «{option}» в [{index}].\n" + _snapshot()


@run_in_browser_thread
def browser_scroll(direction: str = "down", amount: int = 600) -> str:
    """Scrolls the page up or down and returns the newly visible elements."""
    if not _browser_alive():
        return "Браузер не открыт."
    amount = int(amount or 600)
    _page.mouse.wheel(0, amount if direction != "up" else -amount)
    _page.wait_for_timeout(350)
    return f"Прокрутил {'вверх' if direction == 'up' else 'вниз'}.\n" + _snapshot()


_FIND_JS = r"""
(q) => {
  q = q.toLowerCase();
  let best = null, area = Infinity;
  for (const el of document.querySelectorAll('body *')) {
    if (['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(el.tagName)) continue;
    const t = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').toLowerCase();
    if (!t.includes(q)) continue;
    const r = el.getBoundingClientRect(); const a = r.width * r.height;
    if (a > 0 && a < area) { best = el; area = a; }
  }
  if (!best) return null;
  best.scrollIntoView({ block: 'center' });
  return (best.innerText || best.value || '').trim().slice(0, 100);
}
"""


@run_in_browser_thread
def browser_find(text: str) -> str:
    """Finds text on the page, scrolls to it and returns the elements around it."""
    if not _browser_alive():
        return "Браузер не открыт."
    found = _page.evaluate(_FIND_JS, text)
    if found is None:
        return f"На странице нет текста «{text}»."
    _page.wait_for_timeout(300)
    snap = _snapshot()
    near = [e for e in _last_elements if text.lower() in (e.get("name") or "").lower()]
    hint = f" Подходящий элемент: [{near[0]['i']}]." if near else ""
    return f"Нашёл «{found[:80]}» и прокрутил к нему.{hint}\n" + snap


@run_in_browser_thread
def browser_back() -> str:
    """Goes back to the previous page."""
    if not _browser_alive():
        return "Браузер не открыт."
    _page.go_back(wait_until="domcontentloaded", timeout=15000)
    _settle(quick=True)
    return "Вернулся назад.\n" + _snapshot()


@run_in_browser_thread
def browser_forward() -> str:
    """Goes forward to the next page."""
    if not _browser_alive():
        return "Браузер не открыт."
    _page.go_forward(wait_until="domcontentloaded", timeout=15000)
    _settle(quick=True)
    return "Перешёл вперёд.\n" + _snapshot()


@run_in_browser_thread
def browser_tabs(action: str = "list", index: int = 0, url: str = "") -> str:
    """Tabs: action=list | switch (index) | close (index) | new (url)."""
    global _page
    page = _ensure_browser()
    pages = _browser.pages
    if action == "new":
        p = _browser.new_page()
        _page = p
        if url:
            p.goto(url if re.match(r"^[a-z]+://", url, re.I) else "https://" + url,
                   wait_until="domcontentloaded", timeout=25000)
            _settle()
        _events.clear()
        return "Открыл новую вкладку.\n" + _snapshot()
    if action in ("switch", "close"):
        i = int(index or 0)
        if not 0 <= i < len(pages):
            return f"Нет вкладки {i}. Вкладок: {len(pages)}."
        if action == "close":
            pages[i].close()
            pages = _browser.pages
            if not pages:
                _page = _browser.new_page()
            elif _page not in pages:
                _page = pages[-1]
            return f"Закрыл вкладку {i}.\n" + _snapshot()
        _page = pages[i]
        _page.bring_to_front()
        return f"Переключился на вкладку {i}.\n" + _snapshot()
    rows = []
    for i, p in enumerate(pages):
        try:
            rows.append(f"{i}: {'▶ ' if p == page else ''}{p.title()[:60]} — {p.url[:80]}")
        except Exception:
            rows.append(f"{i}: ?")
    return "Вкладки:\n" + "\n".join(rows)


@run_in_browser_thread
def browser_wait(seconds: float = 2.0) -> str:
    """Waits a little (for slow pages, search results, animations) and returns the fresh element list."""
    if not _browser_alive():
        return "Браузер не открыт."
    _page.wait_for_timeout(int(min(10.0, max(0.5, float(seconds or 2))) * 1000))
    return _snapshot()


@run_in_browser_thread
def browser_fullscreen() -> str:
    """Toggles browser window fullscreen (F11). For a video player's own fullscreen button, click it directly via browser_click instead."""
    if _page is None:
        return "Браузер не открыт."
    _page.keyboard.press("F11")
    return "Переключил полноэкранный режим окна."


@run_in_browser_thread
def browser_press_key(key: str) -> str:
    """Sends a key press to the page — e.g. Space to play/pause video, Escape, ArrowRight."""
    if _page is None:
        return "Браузер не открыт."
    _page.keyboard.press(key)
    _page.wait_for_timeout(200)
    return f"Нажал {key}."


# ===========================================================================
# Зрение: номера на скриншоте (set-of-marks)
# ===========================================================================
_MARKS_JS = r"""
(on) => {
  document.querySelectorAll('.__atlas_mark').forEach(m => m.remove());
  if (!on) return 0;
  let n = 0;
  const all = [];
  (function walk(root) {
    root.querySelectorAll('[data-atlas-id]').forEach(e => all.push(e));
    root.querySelectorAll('*').forEach(e => { if (e.shadowRoot) walk(e.shadowRoot); });
  })(document);
  all.forEach(el => {
    const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) return;
    const m = document.createElement('div'); m.className = '__atlas_mark'; m.textContent = el.getAttribute('data-atlas-id');
    m.style.cssText = 'position:fixed;z-index:2147483647;pointer-events:none;font:bold 12px Arial;color:#fff;' +
      'background:#e11d48;padding:0 3px;border-radius:3px;left:' + Math.max(0, r.left) + 'px;top:' + Math.max(0, r.top - 14) + 'px;' +
      'box-shadow:0 0 0 1px #fff';
    const b = document.createElement('div'); b.className = '__atlas_mark';
    b.style.cssText = 'position:fixed;z-index:2147483646;pointer-events:none;border:2px solid #e11d48;' +
      'left:' + r.left + 'px;top:' + r.top + 'px;width:' + r.width + 'px;height:' + r.height + 'px';
    document.body.appendChild(b); document.body.appendChild(m); n++;
  });
  return n;
}
"""
_VISION_PREFS = ("qwen/qwen3.8-27b", "qwen/qwen3.6-27b", "meta-llama/llama-4-maverick-17b-128e-instruct",
                 "meta-llama/llama-4-scout-17b-16e-instruct")
_vision = {"model": None, "checked": False}


def _vision_model(client):
    if not _vision["checked"]:
        _vision["checked"] = True
        try:
            have = [m.id for m in client.models.list().data]
            _vision["model"] = next((m for m in _VISION_PREFS if m in have), None) or next(
                (m for m in have if "vision" in m or ("qwen" in m and "27b" in m)), None)
        except Exception as e:
            print(f"[browser] список моделей недоступен: {e}")
        print(f"[browser] модель со зрением: {_vision['model'] or 'нет'}")
    return _vision["model"]


@run_in_browser_thread
def browser_screenshot_describe(instruction: str) -> str:
    """Vision fallback: numbers every element on a screenshot and asks a vision model which one matches the instruction, then clicks it."""
    if not _browser_alive():
        return "Браузер не открыт."
    if any(k in instruction.lower() for k in SENSITIVE_KEYWORDS):
        return "Отказываюсь — похоже на действие с оплатой/паролем, нужно подтверждение пользователя вслух."
    from groq import Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    model = _vision_model(client)
    if not model:
        return "Модели со зрением на аккаунте Groq сейчас нет — используй browser_find или browser_read_page."
    _snapshot()
    frames = [f for f, _x, _y in _frames()]
    for fr in frames:                                # номера — и в Shadow DOM, и внутри iframe
        try:
            fr.evaluate(_MARKS_JS, True)
        except Exception:
            pass
    try:
        shot = _page.screenshot(type="jpeg", quality=70)
    finally:
        for fr in frames:
            try:
                fr.evaluate(_MARKS_JS, False)
            except Exception:
                pass
    vw = _page.viewport_size or {"width": 1280, "height": 720}
    prompt = (f"Screenshot of a web page, {vw['width']}x{vw['height']} px. Interactive elements have red number labels. "
              f"Task: {instruction}. Reply ONLY with JSON: {{\"index\": <label number>}} for the element to click; "
              "if the target has no label, {\"x\": <px>, \"y\": <px>}; if it is not visible, {\"found\": false}.")
    kw = dict(model=model, temperature=0, max_tokens=300, messages=[{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(shot).decode()}}]}])
    if "qwen" in model:
        kw["reasoning_format"] = "hidden"
    try:
        raw = client.chat.completions.create(**kw).choices[0].message.content or ""
    except Exception:
        kw.pop("reasoning_format", None)
        raw = client.chat.completions.create(**kw).choices[0].message.content or ""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    m = re.search(r"\{.*?\}", raw, re.S)
    try:
        res = json.loads(m.group(0)) if m else {}
    except Exception:
        return f"Модель со зрением ответила непонятно: {raw[:150]}"
    if res.get("index") is not None:
        el = _el(res["index"])
        if el:
            if _is_sensitive(el):
                return "Отказываюсь — это поле пароля или оплаты."
            try:
                _locator(el).click(timeout=4000)
            except Exception:
                _page.mouse.click(el["x"], el["y"])
            _settle(quick=True)
            return f"По скриншоту нажал [{el['i']}] «{el['name']}».\n" + _snapshot()
    if res.get("x") is not None and res.get("y") is not None:
        _page.mouse.click(float(res["x"]), float(res["y"]))
        _settle(quick=True)
        return f"По скриншоту нажал в точку ({res['x']}, {res['y']}).\n" + _snapshot()
    return f"На скриншоте не нашёл: {instruction}"


# ===========================================================================
# Макросы: кино и плеер (без изменений)
# ===========================================================================
@run_in_browser_thread
def play_on_rezka(title: str) -> str:
    """Ищет прямую ссылку на фильм через DuckDuckGo, обходя защиту поиска на самом сайте."""
    import urllib.parse
    page = _ensure_browser()

    query = urllib.parse.quote(f"смотреть {title} hdrezka")
    search_url = f"https://html.duckduckgo.com/html/?q={query}"

    print(f"[Rezka] Ищу фильм в обход внутреннего поиска: {title}")
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
    except Exception:
        pass

    try:
        first_link = page.query_selector("a.result__snippet, a.result__url, a.result__a")
        if not first_link:
            return f"Не смог найти прямую ссылку на «{title}»."

        first_link.click()
        page.wait_for_timeout(4000)
    except Exception as e:
        print(f"[Rezka] Ошибка перехода: {e}")
        return f"Ошибка перехода на сайт Резки: {e}"

    print("[Rezka] Ожидание загрузки плеера...")
    for _ in range(10):
        try:
            content = page.content()
            if "Проверяем, что вы не бот" in content or "Anubis" in content or "Cloudflare" in content:
                cb = page.query_selector("input[type='checkbox'], .cb-i, #btn")
                if cb:
                    cb.click()
                page.wait_for_timeout(1000)
            else:
                break
        except Exception:
            pass

    page.wait_for_timeout(3000)

    try:
        play_btn = page.query_selector(".b-player__play, pjsip-play-button, #player, #cdnplayer")
        if play_btn:
            play_btn.click()
        else:
            page.mouse.click(page.viewport_size['width'] / 2, 400)
    except Exception:
        pass

    try:
        page.keyboard.press("F11")
    except Exception:
        pass

    return f"Запустил «{title}» по прямой ссылке."


@run_in_browser_thread
def play_on_netflix(title: str) -> str:
    """Fast dedicated Netflix macro: search → first result → play → fullscreen.
    Needs the user to have logged into Netflix once in Atlas's browser window."""
    import urllib.parse
    page = _ensure_browser()
    query = urllib.parse.quote(title)
    page.goto(f"https://www.netflix.com/search?q={query}", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(1500)

    if "login" in page.url or page.query_selector("input[name='userLoginId']"):
        return (f"Netflix просит войти — сохранённая сессия истекла или ещё не создана. "
                f"Залогинься вручную один раз в открывшемся окне браузера, потом попробуй снова.")

    first_card = page.query_selector("a[href*='/watch/'], a[href*='/title/']")
    if not first_card:
        return f"Не нашёл «{title}» в результатах поиска Netflix."
    first_card.click()
    page.wait_for_timeout(2000)

    play_btn = page.query_selector("button[data-uia*='play-button'], button[aria-label*='Play'], button[aria-label*='Воспроизвести']")
    if play_btn:
        play_btn.click()
        page.wait_for_timeout(1500)

    page.keyboard.press("F11")
    return f"Запустил «{title}» на Netflix, на весь экран."


def _focus_player_fallback(page, key_to_press):
    """Страховочный захват фокуса перед нажатием горячей клавиши"""
    try:
        page.mouse.click(page.viewport_size['width'] / 2, page.viewport_size['height'] / 2)
        page.wait_for_timeout(100)
        page.keyboard.press(key_to_press)
    except Exception:
        pass


@run_in_browser_thread
def media_play_pause() -> str:
    page = _ensure_browser()
    try:
        js = """() => {
            const v = document.querySelector('video');
            if (v) { v.paused ? v.play() : v.pause(); return true; }
            return false;
        }"""
        if not page.evaluate(js):
            _focus_player_fallback(page, "Space")
    except Exception:
        _focus_player_fallback(page, "Space")
    return "Нажал Play/Pause."


@run_in_browser_thread
def media_seek(direction: str) -> str:
    page = _ensure_browser()
    offset = 15 if direction == "forward" else -15
    try:
        js = f"""() => {{
            const v = document.querySelector('video');
            if (v) {{ v.currentTime += {offset}; return true; }}
            return false;
        }}"""
        if not page.evaluate(js):
            key = "ArrowRight" if direction == "forward" else "ArrowLeft"
            _focus_player_fallback(page, key)
    except Exception:
        pass
    return f"Перемотал {'вперед' if direction == 'forward' else 'назад'}."


@run_in_browser_thread
def media_volume(action: str) -> str:
    page = _ensure_browser()
    try:
        js = f"""() => {{
            const v = document.querySelector('video');
            if (v) {{
                if ('{action}' === 'mute') {{ v.muted = !v.muted; }}
                else if ('{action}' === 'up') {{ v.volume = Math.min(1, v.volume + 0.1); }}
                else {{ v.volume = Math.max(0, v.volume - 0.1); }}
                return true;
            }}
            return false;
        }}"""
        if not page.evaluate(js):
            key = "m" if action == "mute" else ("ArrowUp" if action == "up" else "ArrowDown")
            _focus_player_fallback(page, key)
    except Exception:
        pass
    return f"Изменил громкость ({action})."


@run_in_browser_thread
def media_player_fullscreen() -> str:
    page = _ensure_browser()
    try:
        js = """() => {
            const v = document.querySelector('video');
            if (v) {
                if (document.fullscreenElement) { document.exitFullscreen(); }
                else { const c = v.closest('.pjsip-container, .player, #player'); (c || v).requestFullscreen(); }
                return true;
            }
            return false;
        }"""
        if not page.evaluate(js):
            _focus_player_fallback(page, "f")
    except Exception:
        _focus_player_fallback(page, "f")
    return "Переключил полноэкранный режим плеера."


@run_in_browser_thread
def change_rezka_quality(quality: str) -> str:
    page = _ensure_browser()
    try:
        gear = page.query_selector(".pjsip-settings-icon, pjsip-settings-icon, #pjs-settings-icon")
        if not gear:
            return "Не нашел кнопку настроек на плеере."

        gear.click()
        page.wait_for_timeout(500)

        quality_element = page.get_by_text(quality, exact=False).first
        if quality_element:
            quality_element.click(timeout=3000)
            return f"Качество изменено на {quality}."

        return f"Качество '{quality}' не найдено в списке доступных."
    except Exception as e:
        return f"Ошибка изменения качества: {e}"


@run_in_browser_thread
def change_rezka_translator(translator_name: str) -> str:
    page = _ensure_browser()
    try:
        query = translator_name.lower()
        if any(w in query for w in ["original", "оригинал", "англ", "english", "субтитры", "sub"]):
            keywords = ["оригинал", "original", "eng", "english", "субтитры", "sub"]
        else:
            keywords = [query]

        translators = page.query_selector_all(".b-translator__item")
        for t in translators:
            text = (t.inner_text() or "").lower()
            if any(kw in text for kw in keywords):
                t.click()
                return f"Включил озвучку: {t.inner_text().strip()}."

        return f"Не нашел озвучку, подходящую под '{translator_name}'."
    except Exception as e:
        return f"Ошибка при смене озвучки: {e}"


@run_in_browser_thread
def select_rezka_episode(season: int, episode: int) -> str:
    """Включает конкретный сезон и серию на HDRezka."""
    page = _ensure_browser()
    try:
        season_elem = page.query_selector(f".b-simple_seasons__list_item[data-tab_id='{season}']")
        if season_elem:
            season_elem.click()
            page.wait_for_timeout(800)
        else:
            return f"Не смог найти {season} сезон. Возможно, это фильм или сезон еще не вышел."

        episode_elem = page.query_selector(f".b-simple_episodes__list_item[data-season_id='{season}'][data-episode_id='{episode}']")
        if episode_elem:
            episode_elem.click()
            return f"Успешно включил {season} сезон, {episode} серию."

        return f"Не нашел {episode} серию в {season} сезоне."
    except Exception as e:
        return f"Ошибка переключения серии: {e}"


@run_in_browser_thread
def next_episode() -> str:
    """Универсальная кнопка 'Следующая серия' для Netflix, Ivi, Rezka и других."""
    page = _ensure_browser()
    try:
        url = page.url

        if "netflix.com" in url:
            nxt = page.query_selector("button[data-uia*='next-episode']")
            if nxt:
                nxt.click()
                return "Включил следующую серию на Netflix."

        if "ivi.ru" in url:
            nxt = page.query_selector("button[aria-label*='Следующая'], [class*='next-episode']")
            if nxt:
                nxt.click()
                return "Включил следующую серию на Ivi."

        next_btn = page.query_selector(".pjsip-next, #pjs-next-button")
        if next_btn:
            next_btn.click()
            return "Включил следующую серию в плеере."

        js = """() => {
            const active = document.querySelector('.b-simple_episodes__list_item.active');
            if (active && active.nextElementSibling) {
                active.nextElementSibling.click();
                return true;
            }
            return false;
        }"""
        if page.evaluate(js):
            return "Переключил на следующую серию через список."

        return "Не смог определить, как включить следующую серию на этом сайте."
    except Exception as e:
        return f"Ошибка переключения эпизода: {e}"


@run_in_browser_thread
def skip_intro() -> str:
    """Нажимает 'Пропустить заставку/интро' на Netflix, Ivi или Резке."""
    page = _ensure_browser()
    try:
        url = page.url

        if "netflix.com" in url:
            skip = page.query_selector("button[data-uia*='skip-intro']")
            if skip:
                skip.click()
                return "Пропустил заставку на Netflix."

        js = """() => {
            const elements = Array.from(document.querySelectorAll('button, div, span, a'));
            const skipBtn = elements.find(el => {
                const text = (el.innerText || '').toLowerCase();
                return text.includes('пропустить') || text.includes('skip');
            });
            if (skipBtn) {
                skipBtn.click();
                return true;
            }
            return false;
        }"""
        if page.evaluate(js):
            return "Пропустил заставку."

        return "Кнопка 'Пропустить заставку' не найдена на экране."
    except Exception as e:
        return f"Ошибка пропуска заставки: {e}"


@run_in_browser_thread
def browser_close() -> str:
    """Closes the controlled browser window."""
    _reset_browser()
    return "Браузер закрыт."
