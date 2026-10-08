"""
voice для облака: без микрофона и колонок — только то, что нужно телефону.

    распознавание: Groq Whisper (как на компьютере, те же настройки языка)
    голос:         Fish Audio, если заданы FISH_API_KEY и FISH_VOICE_RU / FISH_VOICE_EN,
                   иначе бесплатный Edge (Дмитрий — русский, Ryan — английский)
Голоса и устройства компьютера отсюда не переключаются.
"""
import asyncio
import os
import queue
import re
import threading
import time

from cloud.stubs import UNAVAILABLE

try:
    from groq import Groq
    groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
except Exception as _e:                              # без ключа облако всё равно запустится — скажет при запросе
    print(f"[облако] Groq для распознавания недоступен: {_e}")
    groq_client = None

STT_WHISPER = os.getenv("STT_MODEL") or "whisper-large-v3"
_STT_BASE_PROMPT = ("Это обычный разговор с голосовым ассистентом Атлас: вопросы и просьбы на русском языке, "
                    "иногда с английскими словами. Бишкек.")
_WHISPER_JUNK = {"продолжение следует", "субтитры сделал dimatorzok", "спасибо за просмотр", "thanks for watching",
                 "thank you for watching", "subtitles by the amara.org community"}
_response_language = {"lang": (os.getenv("ATLAS_LANG") or "ru").lower()[:2]}
_stop_speaking = threading.Event()
EDGE_VOICES = {"ru": os.getenv("EDGE_VOICE_RU") or "ru-RU-DmitryNeural",
               "en": os.getenv("EDGE_VOICE_EN") or "en-GB-RyanNeural"}


def get_response_language() -> str:
    return _response_language["lang"]


def set_response_language(lang: str) -> str:
    lang = (lang or "").lower().strip()
    if lang not in ("en", "ru", "english", "russian"):
        return "Supported languages: English or Russian."
    _response_language["lang"] = "ru" if lang.startswith("ru") else "en"
    return f"Language switched to {'Russian' if _response_language['lang'] == 'ru' else 'English'}."


def _stt_prompt() -> str:
    extra = (os.getenv("STT_VOCAB") or "").strip()
    return (_STT_BASE_PROMPT + (" " + extra if extra else ""))[:600]


def _stt_language():
    fixed = (os.getenv("STT_LANGUAGE") or "").strip().lower()
    if fixed in ("ru", "en"):
        return fixed
    return "ru" if _response_language["lang"] == "ru" else None


def _lang_of(text: str) -> str:
    return "ru" if re.search(r"[а-яё]", text or "", re.I) else _response_language["lang"]


def _fish(text: str, filename: str, voice_id: str) -> None:
    import httpx
    from core import emotions
    fmt = "mp3" if filename.endswith(".mp3") else "wav"
    model = os.getenv("FISH_MODEL", "s2.1-pro-free")
    r = httpx.post("https://api.fish.audio/v1/tts", timeout=60,
                   headers={"Authorization": f"Bearer {os.getenv('FISH_API_KEY')}", "Content-Type": "application/json",
                            "model": model},
                   json={"text": emotions.for_fish(text, model), "reference_id": voice_id, "format": fmt})
    if r.status_code != 200:
        raise RuntimeError(f"Fish {r.status_code}: {r.text[:200]}")
    with open(filename, "wb") as f:
        f.write(r.content)


def _edge(text: str, filename: str, lang: str) -> None:
    import edge_tts
    from core import emotions

    async def gen():
        await edge_tts.Communicate(emotions.strip(text), EDGE_VOICES[lang]).save(filename)
    asyncio.run(gen())


def _default_voice(lang: str) -> str:
    voice_id = (os.getenv("FISH_VOICE_RU" if lang == "ru" else "FISH_VOICE_EN") or "").strip()
    if ":" in voice_id or "," in voice_id:            # вписали список «Имя:номер,…» — берём первый голос
        voice_id = voice_id.split(",")[0].rpartition(":")[2].strip()
    if not voice_id:                                   # или голос из списка FISH_VOICES_RU / _EN
        lst = os.getenv("FISH_VOICES_RU" if lang == "ru" else "FISH_VOICES_EN") or ""
        voice_id = lst.split(",")[0].rpartition(":")[2].strip()
    return voice_id


_FAST = {"on": True}       # «latency: balanced» — быстрее первый звук; если Fish его не примет — без него


def _fish_stream(text: str, voice_id: str):
    """Fish отдаёт mp3 кусками, пока ещё говорит, — телефон начинает играть с первого куска."""
    import httpx
    from core import emotions
    model = os.getenv("FISH_MODEL", "s2.1-pro-free")
    for attempt in (0, 1):
        body = {"text": emotions.for_fish(text, model), "reference_id": voice_id, "format": "mp3"}
        if _FAST["on"]:
            body["latency"] = "balanced"
        with httpx.stream("POST", "https://api.fish.audio/v1/tts", timeout=60, json=body,
                          headers={"Authorization": f"Bearer {os.getenv('FISH_API_KEY')}",
                                   "Content-Type": "application/json", "model": model}) as r:
            if r.status_code in (400, 422) and _FAST["on"] and attempt == 0:
                _FAST["on"] = False
                print("[облако] Fish не принял быстрый режим — говорю обычным")
                continue
            if r.status_code != 200:
                raise RuntimeError(f"Fish {r.status_code}: {r.read()[:200]!r}")
            for chunk in r.iter_bytes():
                if chunk:
                    yield chunk
            return


def _edge_stream(text: str, lang: str):
    import edge_tts
    from core import emotions
    q = queue.Queue()

    def run():
        async def go():
            async for ch in edge_tts.Communicate(emotions.strip(text), EDGE_VOICES[lang]).stream():
                if ch.get("type") == "audio" and ch.get("data"):
                    q.put(ch["data"])
        try:
            asyncio.run(go())
        except Exception as e:
            q.put(e)
        finally:
            q.put(None)
    threading.Thread(target=run, daemon=True).start()
    while True:
        x = q.get()
        if x is None:
            return
        if isinstance(x, Exception):
            raise x
        yield x


def stream_speech(text: str, voice_id: str = None):
    """Голос ответа потоком mp3 (для телефона). Fish — если настроен; не ответил до первого звука — Edge."""
    lang = _lang_of(text)
    vid = voice_id or _default_voice(lang)
    t0, first = time.time(), True
    if vid and os.getenv("FISH_API_KEY"):
        try:
            for chunk in _fish_stream(text, vid):
                if first:
                    print(f"[время] голос: первый звук через {time.time() - t0:.1f} с (Fish)")
                    first = False
                yield chunk
            return
        except Exception as e:
            if not first:                              # уже играет — обрываем, не начинаем заново другим голосом
                print(f"[облако] Fish оборвался ({e})")
                return
            print(f"[облако] Fish не ответил ({e}) — говорю голосом Edge")
    for chunk in _edge_stream(text, lang):
        if first:
            print(f"[время] голос: первый звук через {time.time() - t0:.1f} с (Edge)")
            first = False
        yield chunk


def _generate_any(text: str, filename: str) -> str:
    """Голос ответа в файл (mp3). Fish — если настроен, иначе Edge. → путь к файлу."""
    lang = _lang_of(text)
    voice_id = _default_voice(lang)
    if voice_id and os.getenv("FISH_API_KEY"):
        try:
            _fish(text, filename, voice_id)
            return filename
        except Exception as e:
            print(f"[облако] Fish не ответил ({e}) — говорю голосом Edge")
    _edge(text, filename, lang)
    return filename


# голоса и устройства компьютера отсюда не переключаются
def _pc_only(*a, **k):
    return UNAVAILABLE


list_voices = set_voice = list_audio_devices = set_microphone = set_speaker = _pc_only
list_elevenlabs_voices = set_elevenlabs_voice = _pc_only


def output_is_headphones() -> bool:
    return True
