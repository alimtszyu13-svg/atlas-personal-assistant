"""
Даёт Atlas реальные "руки" в браузере — не просто открыть ссылку (это уже
делает system_control.open_url), а посмотреть, что на странице, и кликать
по конкретным элементам.

Подход — DOM с нумерованными элементами ("set-of-marks"): вместо хрупких
CSS-селекторов, которые модель всё равно не видит, каждый видимый кликабельный
элемент получает номер, и модель ссылается на него как "клик по 4".
Это быстро и дёшево, и отлично работает на структурированных страницах
(Википедия, новости, формы, большинство сайтов).

Для нестандартных интерфейсов (видеоплееры с canvas/шадоу-дом, где обычный
DOM-обход ничего не находит) — browser_screenshot_describe: делает скриншот,
спрашивает vision-модель "где на картинке X" и кликает по пиксельным
координатам напрямую. Это медленнее и стоит дороже (отдельный вызов модели
с картинкой), поэтому это осознанный fallback, а не основной путь.

Защита: если элемент похож на поле пароля/оплаты (по type="password" или
ключевым словам вроде "card number", "cvv", "checkout", "place order") —
клик/ввод текста в него отклоняется без явного подтверждения пользователя.
Это эвристика по ключевым словам и типу поля, не гарантия — если что-то
выглядит подозрительно, лучше переспросить вслух, чем полагаться только
на этот фильтр.
"""

import base64
import json
import os
import concurrent.futures
import functools

from playwright.sync_api import sync_playwright

_playwright = None
_browser = None
_page = None
_last_elements = []  # последний снятый снимок элементов: [{tag, type, text, x, y}, ...]

NETFLIX_PROFILE_DIR = "browser_profile"  # cookies/сессия хранятся тут между запусками

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

def run_in_browser_thread(func):
    """Декоратор: перенаправляет выполнение функции в вечный поток"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Отправляем задачу в поток браузера и ждем результат
        future = browser_executor.submit(func, *args, **kwargs)
        return future.result()
    return wrapper

def _route_filter(route):
    """Блокирует картинки/шрифты и известные рекламные/трекинговые домены —
    НЕ блокирует video/audio (media), чтобы не сломать воспроизведение фильмов.
    Это основная причина, почему обычные страницы грузились медленно: браузер
    по умолчанию тянет вообще всё, включая рекламные баннеры на сайтах с кино."""
    request = route.request
    if request.resource_type in ("image", "font"):
        route.abort()
        return
    if any(d in request.url for d in AD_BLOCK_DOMAINS):
        route.abort()
        return
    route.continue_()


def _ensure_browser():
    global _playwright, _browser, _page
    if _browser is None:
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch_persistent_context(
            NETFLIX_PROFILE_DIR,
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars"
            ],
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        _page = _browser.pages[0] if _browser.pages else _browser.new_page()
        
        # Полностью скрываем следы бота
        _page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        
        # ВАЖНО: Обязательно отключаем фильтр рекламы для этого окна!
        # Anubis не пропустит тебя, если блокировать его ресурсы.
        # _page.route("**/*", _route_filter) 
        
    return _page

@run_in_browser_thread
def play_on_rezka(title: str) -> str:
    """Ищет прямую ссылку на фильм через DuckDuckGo, обходя защиту поиска на самом сайте."""
    import urllib.parse
    page = _ensure_browser()
    
    # 1. Ищем через облегченную HTML-версию DuckDuckGo (идеально для ботов, нет блокировок)
    query = urllib.parse.quote(f"смотреть {title} hdrezka")
    search_url = f"https://html.duckduckgo.com/html/?q={query}"
    
    print(f"[Rezka] Ищу фильм в обход внутреннего поиска: {title}")
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
    except Exception:
        pass

    # 2. Переходим по первой найденной ссылке
    try:
        # Ищем ссылку на результат в выдаче DDG
        first_link = page.query_selector("a.result__snippet") or page.query_selector("a.result__url")
        if not first_link:
            return f"Не смог найти прямую ссылку на «{title}»."
        
        first_link.click()
        page.wait_for_timeout(4000) # Ждем, пока пройдет редирект и загрузится сам онлайн-кинотеатр
    except Exception as e:
        print(f"[Rezka] Ошибка клика по результату поиска: {e}")
        return "Ошибка при переходе на сайт."

    # 3. Обход Anubis/Cloudflare уже на самой странице фильма (если вылезет)
    print("[Rezka] Ожидание загрузки плеера...")
    for _ in range(10):
        try:
            content = page.content()
            if "Проверяем, что вы не бот" in content or "Anubis" in content or "Cloudflare" in content:
                cb = page.query_selector("input[type='checkbox'], .cb-i, #btn")
                if cb: cb.click()
                page.wait_for_timeout(1000)
            else:
                break
        except Exception:
            pass
            
    page.wait_for_timeout(3000)

    # 4. Запускаем плеер
    try:
        play_btn = page.query_selector(".b-player__play, pjsip-play-button, #player, #cdnplayer")
        if play_btn:
            play_btn.click()
        else:
            # Если точной кнопки нет, кликаем примерно по центру плеера
            page.mouse.click(page.viewport_size['width'] / 2, 400)
    except Exception:
        pass

    # 5. Включаем полный экран
    try:
        page.keyboard.press("F11")
    except:
        pass
        
    return f"Запустил «{title}» по прямой ссылке."


def _snapshot_elements(max_elements: int = 60) -> str:
    """Собирает видимые интерактивные элементы текущего вьюпорта, нумерует
    их — модель обращается к элементу по номеру, а не по селектору."""
    global _last_elements
    page = _page

    js = """
    () => {
        const nodes = Array.from(document.querySelectorAll(
            'a, button, input, textarea, select, [role="button"], [onclick], video'
        ));
        const results = [];
        for (const el of nodes) {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            if (rect.bottom < 0 || rect.top > window.innerHeight) continue;
            const style = window.getComputedStyle(el);
            if (style.visibility === 'hidden' || style.display === 'none') continue;
            let text = (el.innerText || el.value || el.placeholder ||
                        el.getAttribute('aria-label') || '').trim().slice(0, 80);
            if (!text && el.tagName !== 'INPUT' && el.tagName !== 'VIDEO') continue;
            results.push({
                tag: el.tagName.toLowerCase(),
                type: el.type || '',
                text: text,
                x: Math.round(rect.x + rect.width / 2),
                y: Math.round(rect.y + rect.height / 2),
            });
        }
        return results;
    }
    """
    raw = page.evaluate(js)
    _last_elements = raw[:max_elements]

    if not _last_elements:
        return "На видимой части страницы не найдено интерактивных элементов — возможно, нужно прокрутить (browser_scroll) или это нестандартный интерфейс (тогда попробуй browser_screenshot_describe)."

    lines = []
    for i, el in enumerate(_last_elements):
        label = el["text"] or f"({el['tag']})"
        type_hint = f" [{el['type']}]" if el["type"] else ""
        lines.append(f"{i}: <{el['tag']}{type_hint}> {label}")
    return "\n".join(lines)


def _is_sensitive(el: dict) -> bool:
    text = (el.get("text") or "").lower()
    return el.get("type") == "password" or any(k in text for k in SENSITIVE_KEYWORDS)

@run_in_browser_thread
def browser_open(url: str) -> str:
    """Opens a URL in a real controlled browser window and returns a numbered list of visible clickable elements."""
    if not url.startswith("http"):
        url = "https://" + url
    page = _ensure_browser()
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    page.bring_to_front()
    elements = _snapshot_elements()
    return f"Открыл: {page.title()}\n\nВидимые элементы:\n{elements}"

@run_in_browser_thread
def browser_read_page() -> str:
    """Re-scans the current page (after scrolling or a click) and returns a fresh numbered list of elements."""
    if _page is None:
        return "Браузер ещё не открыт — сначала вызови browser_open."
    return _snapshot_elements()

@run_in_browser_thread
def browser_click(index: int) -> str:
    """Clicks the element with the given number from the last browser_open/browser_read_page snapshot, and returns a fresh element list right away — no separate browser_read_page call needed."""
    if not _last_elements or index < 0 or index >= len(_last_elements):
        return f"Нет элемента с номером {index}. Сначала вызови browser_read_page, чтобы получить актуальный список."
    el = _last_elements[index]
    if _is_sensitive(el):
        return "Отказываюсь кликать — похоже на поле пароля или оплаты. Такое действие только с явного подтверждения пользователя вслух."
    _page.mouse.click(el["x"], el["y"])
    _page.wait_for_timeout(250)
    clicked_text = el["text"] or el["tag"]
    fresh = _snapshot_elements()
    return f"Кликнул: {clicked_text}\n\nОбновлённые элементы:\n{fresh}"

@run_in_browser_thread
def browser_type(index: int, text: str) -> str:
    """Types text into the input field with the given number, and returns a fresh element list right away."""
    if not _last_elements or index < 0 or index >= len(_last_elements):
        return f"Нет элемента с номером {index}."
    el = _last_elements[index]
    if _is_sensitive(el):
        return "Отказываюсь вводить текст — похоже на поле пароля или оплаты. Нужно явное подтверждение пользователя вслух."
    _page.mouse.click(el["x"], el["y"])
    _page.keyboard.type(text, delay=15)
    typed_into = el["text"] or el["tag"]
    fresh = _snapshot_elements()
    return f"Ввёл текст в: {typed_into}\n\nОбновлённые элементы:\n{fresh}"

@run_in_browser_thread
def browser_scroll(direction: str = "down", amount: int = 600) -> str:
    """Scrolls the page up or down."""
    if _page is None:
        return "Браузер не открыт."
    delta = amount if direction == "down" else -amount
    _page.mouse.wheel(0, delta)
    _page.wait_for_timeout(300)
    return f"Проскроллил {direction}."

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
    return f"Нажал {key}."

@run_in_browser_thread
def browser_screenshot_describe(instruction: str) -> str:
    """Vision fallback for when the DOM element list doesn't show the target (custom video players, canvas UI). Takes a screenshot, asks a vision model where the described element is, clicks those pixel coordinates directly."""
    if _page is None:
        return "Браузер не открыт."

    screenshot_bytes = _page.screenshot()
    b64 = base64.b64encode(screenshot_bytes).decode()
    viewport = _page.viewport_size

    from groq import Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    prompt = (
        f"Screenshot is {viewport['width']}x{viewport['height']} pixels. "
        f'Find this element: "{instruction}". '
        'Reply with ONLY raw JSON, no markdown fences: '
        '{"found": true, "x": <int>, "y": <int>} with pixel coordinates of its '
        'center, or {"found": false} if it is not visible in the screenshot.'
    )

    response = client.chat.completions.create(
        # ВАЖНО: проверь в своей Groq-консоли, какая vision-модель у тебя
        # реально доступна и как она называется сейчас — список моделей
        # на Groq меняется чаще, чем текстовые.
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        result = json.loads(raw)
    except Exception:
        return f"Vision-модель вернула нечитаемый ответ: {raw[:200]}"

    if not result.get("found"):
        return f"Vision-модель не нашла на экране: {instruction}"

    if any(k in instruction.lower() for k in SENSITIVE_KEYWORDS):
        return "Отказываюсь — похоже на действие с оплатой/паролем, нужно подтверждение пользователя вслух."

    x, y = result["x"], result["y"]
    _page.mouse.click(x, y)
    _page.wait_for_timeout(500)
    return f"Кликнул (через vision): {instruction}"

@run_in_browser_thread
def play_on_netflix(title: str) -> str:
    """Fast dedicated Netflix macro — no LLM read/decide/click loop, just a
    fixed sequence in Python: search → click first result → click play →
    fullscreen. Needs the user to have logged into Netflix once in Atlas's
    browser window; the session persists after that (see NETFLIX_PROFILE_DIR).
    Netflix changes its page structure occasionally, so this may need
    re-tuning if Netflix ships a redesign — it is not immune to that."""
    import urllib.parse
    page = _ensure_browser()
    query = urllib.parse.quote(title)
    page.goto(f"https://www.netflix.com/search?q={query}", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(1500)  # даём JS дорендерить карточки результатов

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

@run_in_browser_thread
def browser_close() -> str:
    """Closes the controlled browser window."""
    global _browser, _playwright, _page
    if _browser:
        _browser.close()
        _playwright.stop()
        _browser = None
        _playwright = None
        _page = None
    return "Браузер закрыт."