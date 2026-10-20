"""
Распознавание текста в отдельном процессе — чтобы краш winocr (бывает на новых Python) не ронял весь Atlas.

    python -m core._ocr_worker <путь к png> ru,en   →   JSON {"ru":[{text,x,y,w,h},…], "en":[…]}

Координаты строк — от левого верхнего угла картинки (смещение окна добавляет вызывающий код).
"""
import json
import sys

for _s in (sys.stdout, sys.stderr):                      # вывод всегда в UTF-8 — родитель читает его как UTF-8
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _lines(res) -> list:
    out = []
    for ln in (res or {}).get("lines") or []:
        boxes = [w.get("bounding_rect") for w in ln.get("words") or [] if w.get("bounding_rect")]
        if not boxes or not (ln.get("text") or "").strip():
            continue
        x1 = min(b["x"] for b in boxes)
        y1 = min(b["y"] for b in boxes)
        x2 = max(b["x"] + b["width"] for b in boxes)
        y2 = max(b["y"] + b["height"] for b in boxes)
        out.append({"text": ln["text"].strip(), "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1})
    return out


LIMIT = 1280     # Windows OCR падает на больших снимках — режем на куски (уменьшать нельзя: мелкий шрифт превращается в кашу)
OVERLAP = 120    # куски перекрываются, чтобы строка на стыке целиком попала хотя бы в один


def _starts(size: int) -> list:
    if size <= LIMIT:
        return [0]
    step = LIMIT - OVERLAP
    out = list(range(0, size - LIMIT, step)) + [size - LIMIT]
    return sorted(set(out))


def _dedupe(lines: list) -> list:
    """Одна и та же строка из двух перекрывающихся кусков — оставляем более длинный вариант."""
    out = []
    for ln in sorted(lines, key=lambda l: -len(l["text"])):
        cy = ln["y"] + ln["h"] / 2
        dup = next((o for o in out if abs((o["y"] + o["h"] / 2) - cy) < max(6, ln["h"] * 0.6)
                    and ln["x"] < o["x"] + o["w"] and o["x"] < ln["x"] + ln["w"]), None)
        if dup is None:
            out.append(ln)
    return sorted(out, key=lambda l: (l["y"], l["x"]))


def recognize(img, lang: str, rec) -> list:
    w, h = img.size
    found = []
    for x0 in _starts(w):
        for y0 in _starts(h):
            tile = img.crop((x0, y0, min(w, x0 + LIMIT), min(h, y0 + LIMIT)))
            for ln in _lines(rec(tile, lang)):
                ln["x"] += x0
                ln["y"] += y0
                found.append(ln)
    return _dedupe(found) if (w > LIMIT or h > LIMIT) else found


def main() -> None:
    path = sys.argv[1]
    langs = (sys.argv[2] if len(sys.argv) > 2 else "ru,en").split(",")
    from PIL import Image
    from winocr import recognize_pil_sync                 # может жёстко упасть — тогда процесс просто умрёт
    img = Image.open(path).convert("RGB")
    out = {}
    for lang in langs:
        try:
            out[lang] = recognize(img, lang, recognize_pil_sync)
        except Exception:
            out[lang] = []
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
