"""Чтение веб-страницы без браузера: скачать и вытащить основной текст."""
import re
import requests
from html.parser import HTMLParser
from core.skills import skill

_SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside"}
_BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "br", "tr", "section", "article"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in _SKIP and self.skip:
            self.skip -= 1
        if tag in _BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


@skill("web", read_only=True,
       description="Downloads a web page and returns its main text, without opening a browser. "
                   "Use after search_web to actually read a source.",
       params={"url": "Full URL of the page"})
def read_webpage(url: str) -> str:
    try:
        r = requests.get(url, timeout=8,
                         headers={"User-Agent": "Mozilla/5.0 (Atlas personal assistant)"})
        r.raise_for_status()
    except Exception as e:
        return f"Couldn't load the page: {e}"
    p = _Text()
    p.feed(r.text)
    text = re.sub(r"[ \t]+", " ", "".join(p.out))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:3000] if len(text) > 200 else "No readable text (the page probably needs JavaScript)."