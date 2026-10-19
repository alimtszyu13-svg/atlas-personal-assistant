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


LIMIT = 1280     # Windows OCR падает на больших снимках — уменьшаем, координаты возвращаем в исходный масштаб


def main() -> None:
    path = sys.argv[1]
    langs = (sys.argv[2] if len(sys.argv) > 2 else "ru,en").split(",")
    from PIL import Image
    from winocr import recognize_pil_sync                 # может жёстко упасть — тогда процесс просто умрёт
    img = Image.open(path).convert("RGB")
    w, h = img.size
    scale = 1.0
    if max(w, h) > LIMIT:
        scale = LIMIT / max(w, h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    out = {}
    for lang in langs:
        try:
            lines = _lines(recognize_pil_sync(img, lang))
            if scale != 1.0:
                for ln in lines:
                    ln["x"], ln["y"] = int(ln["x"] / scale), int(ln["y"] / scale)
                    ln["w"], ln["h"] = int(ln["w"] / scale), int(ln["h"] / scale)
            out[lang] = lines
        except Exception:
            out[lang] = []
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
