"""
Скорость голосов на ЭТОМ компьютере: сколько ждать готового звука для одной и той же фразы.

    python tests/voice_bench.py        (или в VS Code: Run → Run Without Debugging)

Ничего не воспроизводит и не меняет настройки; временные файлы удаляет. Тратит немного квоты голосов
(Fish, ElevenLabs — несколько коротких фраз). Итог — на экран и в logs/voice_bench.txt.
"""
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
try:
    from core import vault
    vault.load()
except Exception:
    pass

LINES = []
PHRASES = [
    ("короткая", "Готово, сэр."),
    ("средняя", "Мысль записана: дедлайн курсовой двадцатого ноября."),
    ("длинная", "Сэр, мама пишет в WhatsApp: купи, пожалуйста, хлеб и молоко по дороге домой, "
                "и не забудь позвонить бабушке."),
]
RUNS = 3


def out(s=""):
    print(s, flush=True)
    LINES.append(s)


def _rm(*paths):
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


def bench(name, fn, ext, runs=RUNS, only=None):
    out(f"\n{name}:")
    for label, text in PHRASES:
        if only and label not in only:
            continue
        times = []
        for i in range(runs):
            fn_path = os.path.join(ROOT, f"temp_bench_{time.time_ns()}.{ext}")
            t0 = time.time()
            try:
                real = fn(text, fn_path)
            except Exception as e:
                out(f"  ✗ {label}: {type(e).__name__}: {str(e)[:150]}")
                _rm(fn_path)
                break
            times.append(time.time() - t0)
            _rm(fn_path, real if isinstance(real, str) else None)
        if times:
            out(f"  {label:9s} ({len(text):3d} симв): медиана {statistics.median(times):.2f} с, "
                f"лучшее {min(times):.2f} с, все: {', '.join(f'{t:.2f}' for t in times)}")


def fish_first_byte(text):
    """Fish со стримингом: когда приходят первые байты звука (если сервер отдаёт по частям)."""
    import voice
    vid = voice._fish_choice["ru"] or next(iter(voice.FISH_VOICES["ru"].values()), None)
    headers = {"Authorization": f"Bearer {voice.FISH_API_KEY}", "Content-Type": "application/json",
               "model": voice.FISH_MODEL}
    payload = {"text": text, "reference_id": vid, "format": "mp3"}
    res = {}
    for lat in ("normal", "balanced"):
        payload["latency"] = lat
        t0 = time.time()
        first = None
        with voice._fish_session().stream("POST", "https://api.fish.audio/v1/tts", headers=headers, json=payload) as r:
            if r.status_code != 200:
                res[lat] = f"код {r.status_code}"
                continue
            for chunk in r.iter_bytes():
                if chunk and first is None:
                    first = time.time() - t0
            total = time.time() - t0
        res[lat] = f"первые байты {first:.2f} с, весь звук {total:.2f} с"
    return res


def main():
    import voice
    voice._response_language["lang"] = "ru"
    out(f"Скорость голосов · {time.strftime('%Y-%m-%d %H:%M')} · модель Fish: {voice.FISH_MODEL}")
    vid = voice._fish_choice["ru"] or next(iter(voice.FISH_VOICES["ru"].values()), None)
    out(f"Выбранный русский голос Fish: {'есть' if voice._fish_choice['ru'] else 'не выбран (беру первый из .env)'}")

    if vid and voice.FISH_API_KEY:
        voice._warm_fish()
        bench("Fish (как сейчас)", lambda t, f: voice._generate_speech_fish(t, f, vid), "mp3")
        out("\nFish — первые байты при потоковой отдаче (короткая и длинная фраза):")
        for label, text in (PHRASES[0], PHRASES[2]):
            try:
                for lat, r in fish_first_byte(text).items():
                    out(f"  {label:9s} latency={lat:8s}: {r}")
            except Exception as e:
                out(f"  ✗ {label}: {type(e).__name__}: {str(e)[:150]}")
    else:
        out("\nFish: ключа или голоса нет — пропускаю")

    if os.getenv("ELEVENLABS_API_KEY"):
        bench("ElevenLabs multilingual v2 (как сейчас, запасной)", voice._generate_speech_elevenlabs, "mp3", runs=1,
              only=("короткая", "средняя"))

        def flash(text, filename):
            audio = voice.elevenlabs_client.text_to_speech.convert(
                text=text, voice_id=voice._elevenlabs_voice["id"], model_id="eleven_flash_v2_5",
                output_format="mp3_44100_128")
            with open(filename, "wb") as f:
                for chunk in audio:
                    f.write(chunk)
        bench("ElevenLabs Flash v2.5 (быстрая модель)", flash, "mp3", runs=1, only=("короткая", "средняя"))

    def edge(text, filename):
        import asyncio
        import edge_tts

        async def go():
            await edge_tts.Communicate(text, voice.RUSSIAN_TTS_VOICE).save(filename)
        asyncio.run(go())
    bench("Edge-TTS (бесплатный, Microsoft)", edge, "mp3")

    t0 = time.time()
    try:
        voice._get_silero_model()
        out(f"\nSilero: модель загружена за {time.time() - t0:.1f} с (один раз при запуске)")
        bench("Silero (локально, без интернета)", voice._generate_speech_silero, "wav")
    except Exception as e:
        out(f"\n✗ Silero: {type(e).__name__}: {str(e)[:150]}")

    path = os.path.join(ROOT, "logs", "voice_bench.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(LINES))
    out(f"\nСохранено: {path}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
