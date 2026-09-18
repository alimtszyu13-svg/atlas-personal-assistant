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

from playwright.sync_api import sync_playwright

_playwright = None
_browser = None
_page = None
_last_elements = []  # последний снятый снимок элементов: [{tag, type, text, x, y}, ...]

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
        # headless=False — окно браузера реально видно на экране, это
        # осознанный выбор: пользователь должен видеть, что делает Atlas,
        # а не только слышать отчёт постфактум.
        _browser = _playwright.chromium.launch(headless=False)
        _page = _browser.new_page()
        _page.route("**/*", _route_filter)
    return _page


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


def browser_open(url: str) -> str:
    """Opens a URL in a real controlled browser window and returns a numbered list of visible clickable elements."""
    if not url.startswith("http"):
        url = "https://" + url
    page = _ensure_browser()
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    page.bring_to_front()
    elements = _snapshot_elements()
    return f"Открыл: {page.title()}\n\nВидимые элементы:\n{elements}"


def browser_read_page() -> str:
    """Re-scans the current page (after scrolling or a click) and returns a fresh numbered list of elements."""
    if _page is None:
        return "Браузер ещё не открыт — сначала вызови browser_open."
    return _snapshot_elements()


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


def browser_scroll(direction: str = "down", amount: int = 600) -> str:
    """Scrolls the page up or down."""
    if _page is None:
        return "Браузер не открыт."
    delta = amount if direction == "down" else -amount
    _page.mouse.wheel(0, delta)
    _page.wait_for_timeout(300)
    return f"Проскроллил {direction}."


def browser_fullscreen() -> str:
    """Toggles browser window fullscreen (F11). For a video player's own fullscreen button, click it directly via browser_click instead."""
    if _page is None:
        return "Браузер не открыт."
    _page.keyboard.press("F11")
    return "Переключил полноэкранный режим окна."


def browser_press_key(key: str) -> str:
    """Sends a key press to the page — e.g. Space to play/pause video, Escape, ArrowRight."""
    if _page is None:
        return "Браузер не открыт."
    _page.keyboard.press(key)
    return f"Нажал {key}."


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