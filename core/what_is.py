"""
«Что это?» — наведи мышь на что угодно и нажми Ctrl+Alt+W (или скажи «что это под курсором?»):
кнопка, значок, слово, ошибка, пункт меню, график. Atlas смотрит, что под курсором (Windows UI Automation —
название и тип элемента; плюс OCR небольшого участка вокруг мыши), и коротко объясняет:
слово — значение и перевод, кнопка или значок — что делает, ошибка — почему и как исправить.

Снимок участка экрана не сохраняется; модели уходит только распознанный текст рядом с курсором.
"""
import re

BOX_W, BOX_H = 760, 180


def _ru(text: str) -> bool:
    return bool(re.search(r"[а-яё]", text or "", re.I))


class Env:
    def __init__(self):
        import ctypes
        self.c = ctypes
        self.u = ctypes.windll.user32
        try:
            self.u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass

    def cursor(self):
        from ctypes import wintypes as wt
        pt = wt.POINT()
        self.u.GetCursorPos(self.c.byref(pt))
        return pt.x, pt.y

    def screen(self):
        gm = self.u.GetSystemMetrics
        return gm(76), gm(77), gm(76) + gm(78), gm(77) + gm(79)

    def element(self, x: int, y: int) -> dict:
        try:
            import uiautomation as auto
            c = auto.ControlFromPoint(x, y)
            if not c:
                return {}
            top = c.GetTopLevelControl()
            out = {"name": (c.Name or "")[:200], "type": c.LocalizedControlType or c.ControlTypeName or "",
                   "help": (getattr(c, "HelpText", "") or "")[:200], "window": (top.Name if top else "")[:120]}
            try:
                vp = c.GetValuePattern()
                out["value"] = (vp.Value or "")[:300]
            except Exception:
                pass
            p = c.GetParentControl()
            if p and p.Name and p.Name != out["name"]:
                out["parent"] = p.Name[:120]
            return out
        except Exception as e:
            print(f"[что это] UIA: {e}")
            return {}

    def ocr(self, box) -> list:
        from PIL import ImageGrab
        img = ImageGrab.grab(bbox=tuple(box), all_screens=True)
        img = img.resize((img.width * 2, img.height * 2))
        try:
            from winocr import recognize_pil_sync
        except ImportError:
            return []
        from file_search import _ocr_lock
        from core import messengers
        lines = []
        for lang in ("ru", "en"):
            try:
                with _ocr_lock:
                    res = recognize_pil_sync(img, lang)
            except Exception:
                continue
            got = messengers._lines(res, 0, 0)
            for ln in got:                                   # назад в координаты экрана
                ln.update(x=box[0] + ln["x"] / 2, y=box[1] + ln["y"] / 2, w=ln["w"] / 2, h=ln["h"] / 2)
            if got:
                lines.append((lang, got))
        return lines


_env = None


def _get_env():
    global _env
    if _env is None:
        _env = Env()
    return _env


def nearest(lines: list, x: int, y: int):
    """Строка под курсором (или ближайшая) и слово под курсором."""
    best, dist = None, 1e9
    for ln in lines:
        dx = 0 if ln["x"] <= x <= ln["x"] + ln["w"] else min(abs(x - ln["x"]), abs(x - ln["x"] - ln["w"]))
        dy = 0 if ln["y"] <= y <= ln["y"] + ln["h"] else min(abs(y - ln["y"]), abs(y - ln["y"] - ln["h"]))
        d = dx + dy * 3
        if d < dist:
            best, dist = ln, d
    if not best:
        return None, ""
    words = best["text"].split()
    word = ""
    if words and best["w"]:
        k = (x - best["x"]) / best["w"]
        word = words[max(0, min(len(words) - 1, int(k * len(words))))]
    return best, word.strip(".,:;!?()«»\"'")


def look(env=None) -> dict:
    env = env or _get_env()
    x, y = env.cursor()
    l, t, r, b = env.screen()
    box = (max(l, x - BOX_W // 2), max(t, y - BOX_H // 2), min(r, x + BOX_W // 2), min(b, y + BOX_H // 2))
    el = env.element(x, y)
    ocr = env.ocr(box)
    pick = []
    if ocr:
        best = max(ocr, key=lambda kv: sum(len(ln["text"]) for ln in kv[1]))
        letters = "".join(ln["text"] for ln in best[1])
        cyr = sum(1 for c in letters if "а" <= c.lower() <= "я")
        lang = "ru" if cyr > len(letters) * 0.3 else "en"
        pick = dict(ocr).get(lang) or best[1]
    line, word = nearest(pick, x, y)
    around = [ln["text"] for ln in sorted(pick, key=lambda z: (z["y"], z["x"]))][:12]
    return {"element": el, "line": line["text"] if line else "", "word": word, "around": around}


def explain(seen: dict, ru: bool = True, ask=None) -> str:
    el = seen.get("element") or {}
    if not el.get("name") and not seen.get("line"):
        return ("Не вижу, что под курсором — наведи мышь точнее и нажми ещё раз." if ru else
                "I can't tell what's under the cursor — point more precisely and try again.")
    from core import quick_llm
    q = ask or quick_llm.ask
    facts = []
    if el:
        facts.append(f"UI element under the cursor: type «{el.get('type', '')}», name «{el.get('name', '')}»"
                     + (f", value «{el['value']}»" if el.get("value") else "")
                     + (f", tooltip «{el['help']}»" if el.get("help") else "")
                     + (f", inside «{el['parent']}»" if el.get("parent") else "")
                     + f", window «{el.get('window', '')}».")
    if seen.get("line"):
        facts.append(f"Text line under the cursor (OCR): «{seen['line']}»; word under the cursor: «{seen['word']}».")
    if seen.get("around"):
        facts.append("Text nearby: " + " | ".join(seen["around"]))
    system = ("The user points the mouse at something on their screen and asks «what is this?». Using the facts, "
              "say what it is and what it means or does for them — in 1–3 short sentences, in "
              f"{'Russian' if ru else 'English'}. A word or term — meaning (and translation if it's in another "
              "language). A button, icon or menu item — what it does. An error — cause and how to fix. A number or "
              "chart — what it shows. Don't describe the facts themselves, don't guess wildly; no filler.")
    try:
        return q(system, "\n".join(facts), max_tokens=260).strip()
    except Exception as e:
        name = el.get("name") or seen.get("line")
        return (f"Под курсором: «{name}». Объяснить не получилось: {e}" if ru else f"Under the cursor: “{name}”.")[:220]


_ASK = re.compile(r"^(?:атлас[,\s]+)?(?:что это(?: такое)?\s+(?:под\s+(?:курсором|мышк\w*|указател\w*)|за\s+(?:кнопк\w*|значок|"
                  r"иконк\w*|штук\w*|слово|ошибк\w*|пункт)(?:\s+под\s+(?:курсором|мышк\w*))?)|что\s+под\s+(?:курсором|мышк\w*)|"
                  r"объясни,?\s+(?:что\s+)?под\s+(?:курсором|мышк\w*)|what(?:'s| is)\s+(?:this|that)?\s*under\s+(?:the\s+)?"
                  r"(?:cursor|mouse)|what does this button do)\s*\??$", re.I)
HOTKEY_PHRASE = "что это под курсором"


def intent(text: str) -> bool:
    return bool(_ASK.search(" ".join((text or "").replace("ё", "е").split()).strip(" .!")))


def handle(text: str, env=None, ask=None, ru: bool = None):
    if not intent(text):
        return None
    ru = _ru(text) if ru is None else ru
    try:
        seen = look(env)
    except Exception as e:
        return (f"Не получилось посмотреть на экран: {e}" if ru else f"Couldn't look at the screen: {e}")[:200]
    return explain(seen, ru, ask)
