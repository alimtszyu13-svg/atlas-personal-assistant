"""
Эмоции в голосе Atlas.

Модель может начать фразу одной меткой: [warm], [calm], [amused], [excited], [sympathetic], [serious],
или звуком: [light chuckle], [sigh], [whispering]. Метки понимает только Fish Audio:
    • S2 (s2.1-pro-free и новее) — квадратные скобки, свободный текст: «[warm] Доброе утро.»
    • S1 — круглые скобки и фиксированный набор: «(relaxed) Доброе утро.»
Всем остальным голосам (ElevenLabs, Silero, Edge, Groq Orpheus, Kokoro) и субтитрам/чату метки
не отдаются — иначе их прочитают вслух. VOICE_EMOTIONS=off в .env выключает всё.
"""
import os
import re

PALETTE = ("warm", "calm", "amused", "excited", "sympathetic", "serious", "light chuckle", "sigh", "whispering")
# метка = латинские слова в квадратных скобках ([Task …] тоже уберётся из показа — так и надо);
# [мгновенно], [1] и прочее не трогаем
_TAG = re.compile(r"\[\s*([A-Za-z][A-Za-z '\-]{0,40}?)\s*\]\s*")
_S1 = {"warm": "soft tone", "calm": "relaxed", "amused": "delighted", "excited": "excited",
       "sympathetic": "empathetic", "serious": "confident", "light chuckle": "chuckling", "sigh": "sighing",
       "whispering": "whispering"}


def enabled() -> bool:
    return (os.getenv("VOICE_EMOTIONS") or "on").strip().lower() not in ("off", "0", "false", "no", "нет")


def strip(text: str) -> str:
    """Текст без меток — для чата, субтитров, журнала и всех голосов, кроме Fish."""
    if not text or "[" not in text:
        return text
    out = _TAG.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", out).strip() if out != text else text


def for_fish(text: str, model: str = "") -> str:
    """Текст для Fish Audio: S2 — метки как есть, S1 — круглые скобки из её набора."""
    if not text or not enabled():
        return strip(text)
    if not (model or "").lower().startswith("s1"):
        return _TAG.sub(lambda m: f"[{m.group(1).strip().lower()}] ", text).strip()

    def s1(m):
        tag = _S1.get(m.group(1).strip().lower())
        return f"({tag}) " if tag else ""
    return re.sub(r"[ \t]{2,}", " ", _TAG.sub(s1, text)).strip()


RULE = ("\n\nVOICE EMOTION. Your voice can act. When a sentence clearly carries a feeling, start it with ONE tag: "
        "[warm], [calm], [amused], [excited], [sympathetic] or [serious]; for a natural laugh or sigh use "
        "[light chuckle] or [sigh]. Most sentences need no tag; never more than one per sentence; never mention "
        "or explain the tags. Example: '[sympathetic] Rough day, sir. [warm] Let me put on something gentle.'")
SLIM_RULE = (" You may start a sentence with one emotion tag when it truly fits: [warm], [calm], [amused], [excited], "
             "[sympathetic], [serious], [light chuckle], [sigh].")
