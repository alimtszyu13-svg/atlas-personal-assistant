"""
Без дежурных фраз: «Let me know if you need anything else», «Happy reading, sir», «Чем ещё могу помочь?»,
«Надеюсь, это поможет» — их не говорим вслух и не пишем в чат. Ответ заканчивается, когда сказано главное.
Если ответ целиком состоит из такой фразы — оставляем как есть (значит, она и есть ответ).
"""
import re

_FILLER = re.compile(
    r"^(?:sir,?\s+)?(?:"
    r"let me know if (?:you need|there'?s|i can)|(?:is there )?anything else (?:i can|you need)|"
    r"happy (?:reading|listening|watching|studying|coding)|enjoy(?: it| the [\w\s]+)?|hope (?:this|that|it) helps|"
    r"feel free to (?:ask|let me know)|just let me know|i'?m here if you need|have (?:fun|a great \w+)|"
    r"чем (?:ещё|еще) (?:могу )?(?:помочь|быть полезен)|обращайтесь|если (?:что-то |что )?(?:ещё |еще )?(?:понадобится|нужно)|"
    r"надеюсь,? (?:это |что это )?(?:поможет|помогло|было полезно)|приятного (?:чтения|просмотра|прослушивания)|"
    r"хорошего (?:дня|вечера|чтения)|дайте знать,? если|скажите,? если (?:что-то |ещё )"
    r")[^.!?…]*[.!?…]*$", re.I)


def is_filler(sentence: str) -> bool:
    s = re.sub(r"^[\s,—–-]*(?:сэр|sir)[,\s]+", "", (sentence or "").strip(), flags=re.I)
    s = re.sub(r"[,\s]+(?:сэр|sir)([.!?…]*)$", r"\1", s, flags=re.I)
    return bool(_FILLER.match(s))


def _sentences(text: str) -> list:
    return [p for p in re.split(r"(?<=[.!?…])\s+", (text or "").strip()) if p.strip()]


def clean(text: str) -> str:
    """Убрать дежурные фразы. Пустым ответ не делаем."""
    parts = _sentences(text)
    kept = [p for p in parts if not is_filler(p)]
    if not kept or len(kept) == len(parts):
        return text
    return " ".join(kept)


def piece(text: str) -> str:
    """Для озвучки по кусочкам: кусок, где только дежурные фразы, → ''."""
    parts = _sentences(text)
    kept = [p for p in parts if not is_filler(p)]
    return " ".join(kept) if len(kept) != len(parts) else text
