"""
«Врач» Atlas: проверка всех подсистем за минуту.

  python doctor.py            — всё, кроме камеры и микрофона
  python doctor.py --devices  — плюс камера и уровень микрофона (2 секунды тишины/речи)

Ничего не меняет: только смотрит, отвечает ли каждая часть и сколько это занимает.
Итог — таблица (✓ работает, ⚠ работает с оговоркой, ✗ сломано) с подсказками,
и файл doctor_results/<время>.json — его можно прислать целиком.
"""
import argparse
import importlib
import json
import os
import shutil
import sqlite3
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

RESULTS = []


def check(area, name, timeout=20):
    """Декоратор: проверка с ограничением по времени; функция возвращает (статус, подробность[, совет])."""
    def deco(fn):
        def run():
            out = {"area": area, "name": name}
            box = {}

            def target():
                try:
                    box["r"] = fn()
                except Exception as e:
                    box["r"] = ("fail", f"{type(e).__name__}: {str(e)[:160]}")
            t0 = time.time()
            th = threading.Thread(target=target, daemon=True)
            th.start()
            th.join(timeout)
            r = box.get("r") or ("fail", f"не ответило за {timeout} с")
            out.update(status=r[0], detail=str(r[1])[:200], tip=(r[2] if len(r) > 2 else ""),
                       seconds=round(time.time() - t0, 2))
            RESULTS.append(out)
            mark = {"ok": "✓", "warn": "⚠", "fail": "✗", "skip": "·"}[out["status"]]
            print(f"  {mark} {area:<11} {name:<34} {out['seconds']:>5.1f}с  {out['detail'][:70]}")
        run.__name__ = fn.__name__
        CHECKS.append(run)
        return run
    return deco


CHECKS = []
OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"


# --------------------------------------------------------------------- ключи
@check("ключи", ".env: обязательные и полезные ключи")
def _keys():
    need = ["GROQ_API_KEY"]
    good = ["CEREBRAS_API_KEY", "GEMINI_API_KEY", "FISH_API_KEY", "ELEVENLABS_API_KEY"]
    miss = [k for k in need if not os.getenv(k)]
    have = [k for k in good if os.getenv(k)]
    if miss:
        return FAIL, f"нет {', '.join(miss)}", "без GROQ_API_KEY Atlas не думает и не слышит"
    stt = os.getenv("STT_LANGUAGE") or "авто"
    return OK, f"есть: GROQ + {', '.join(k.split('_')[0] for k in have) or 'без запасных'}; язык речи: {stt}", \
        ("" if os.getenv("STT_LANGUAGE") else "STT_LANGUAGE=ru в .env — точнее распознавание, если говоришь по-русски")


# --------------------------------------------------------------------- библиотеки
@check("библиотеки", "версии ключевых пакетов")
def _packages():
    mods = {"openai": True, "groq": False, "playwright": True, "webview": True, "vosk": True, "sounddevice": True,
            "numpy": True, "psutil": True, "PIL": True, "httpx": True, "cv2": False, "fastembed": False,
            "pygame": True}
    missing, vers = [], []
    for m, required in mods.items():
        try:
            mod = importlib.import_module(m)
            vers.append(f"{m} {getattr(mod, '__version__', '')}".strip())
        except Exception:
            if required:
                missing.append(m)
    if missing:
        pip = {"webview": "pywebview", "PIL": "pillow", "cv2": "opencv-python", "pygame": "pygame-ce"}
        return FAIL, f"не установлены: {', '.join(missing)}", f"pip install {' '.join(pip.get(m, m) for m in missing)}"
    return OK, f"Python {sys.version.split()[0]}; " + ", ".join(vers[:6]) + "…"


# --------------------------------------------------------------------- модели
def _ping(base_url, key, model, extra=None):
    from openai import OpenAI
    cl = OpenAI(api_key=key, base_url=base_url, max_retries=0, timeout=15)
    t0 = time.time()
    kw = dict(model=model, max_tokens=5, messages=[{"role": "user", "content": "Ответь одним словом: да"}])
    kw.update(extra or {})
    r = cl.chat.completions.create(**kw)
    return time.time() - t0, (r.choices[0].message.content or "").strip()


@check("модели", "Groq · gpt-oss-120b")
def _groq():
    if not os.getenv("GROQ_API_KEY"):
        return SKIP, "нет ключа"
    dt, txt = _ping("https://api.groq.com/openai/v1", os.getenv("GROQ_API_KEY"), "openai/gpt-oss-120b",
                    {"reasoning_effort": "low", "max_tokens": 40})
    return (OK if dt < 2 else WARN), f"ответ за {dt:.2f} с", ("" if dt < 2 else "медленно — возможно, перегрузка у Groq")


@check("модели", "Cerebras · gpt-oss-120b")
def _cerebras():
    if not os.getenv("CEREBRAS_API_KEY"):
        return SKIP, "нет ключа (необязательно)"
    dt, txt = _ping("https://api.cerebras.ai/v1", os.getenv("CEREBRAS_API_KEY"), "gpt-oss-120b",
                    {"reasoning_effort": "low", "max_tokens": 40})
    return OK, f"ответ за {dt:.2f} с"


@check("модели", "Gemini (запасная)")
def _gemini():
    if not os.getenv("GEMINI_API_KEY"):
        return SKIP, "нет ключа (необязательно)"
    model = os.getenv("GEMINI_MODEL") or "gemini-3.8-flash"
    try:
        dt, txt = _ping("https://generativelanguage.googleapis.com/v1beta/openai/", os.getenv("GEMINI_API_KEY"), model)
    except Exception as e:
        if "429" in str(e) or "quota" in str(e).lower():
            return WARN, "дневная квота исчерпана", "это нормально для бесплатного тарифа — Atlas обойдётся без него"
        raise
    return OK, f"ответ за {dt:.2f} с"


# --------------------------------------------------------------------- речь
@check("речь", "распознавание (Whisper в Groq)", timeout=30)
def _stt():
    if not os.getenv("GROQ_API_KEY"):
        return SKIP, "нет ключа"
    import io
    import wave
    import numpy as np
    from groq import Groq
    sr = 16000
    t = np.arange(sr) / sr
    tone = (0.2 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)        # 1 с тона — просто проверить связь
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(tone.tobytes())
    t0 = time.time()
    Groq(api_key=os.getenv("GROQ_API_KEY")).audio.transcriptions.create(
        file=("t.wav", buf.getvalue()), model=os.getenv("STT_MODEL") or "whisper-large-v3", language="ru")
    dt = time.time() - t0
    return (OK if dt < 1.5 else WARN), f"ответ за {dt:.2f} с", ("" if dt < 1.5 else "медленно — проверь интернет")


@check("речь", "Vosk: модель слова «Атлас»")
def _vosk():
    cands = [d for d in os.listdir(ROOT) if os.path.isdir(d) and "vosk" in d.lower()]
    cands += [os.path.join("models", d) for d in (os.listdir("models") if os.path.isdir("models") else []) if "vosk" in d.lower()]
    if not cands:
        return WARN, "папка модели Vosk не найдена рядом с проектом", "если слово «Атлас» работает — модель лежит в другом месте, всё в порядке"
    return OK, f"найдена: {cands[0]}"


@check("речь", "голоса озвучки (настройки)")
def _tts():
    parts = []
    for k, label in (("FISH_API_KEY", "Fish"), ("ELEVENLABS_API_KEY", "ElevenLabs")):
        if os.getenv(k):
            parts.append(label)
    ru = [x for x in (os.getenv("FISH_VOICES_RU") or "").split(",") if ":" in x]
    try:
        importlib.import_module("kokoro")
        parts.append("Kokoro (локально)")
    except Exception:
        pass
    if not parts:
        return WARN, "нет ни одного внешнего голоса", "Atlas будет говорить запасным голосом"
    return OK, f"{', '.join(parts)}; русских голосов Fish: {len(ru)}"


# --------------------------------------------------------------------- устройства
DEVICES = False


@check("устройства", "микрофон и динамики")
def _audio():
    import sounddevice as sd
    try:
        di, do = int(sd.default.device[0]), int(sd.default.device[1])      # пара «вход, выход»
    except (TypeError, IndexError, ValueError):
        di = do = int(sd.default.device) if isinstance(sd.default.device, int) else -1
    devs = sd.query_devices()
    inp = devs[di]["name"] if di >= 0 else str(sd.query_devices(kind="input")["name"])
    out = devs[do]["name"] if do >= 0 else str(sd.query_devices(kind="output")["name"])
    if not DEVICES:
        return OK, f"вход: {inp[:30]} | выход: {out[:30]}"
    import numpy as np
    rec = sd.rec(int(2 * 16000), samplerate=16000, channels=1, dtype="float32")
    sd.wait()
    level = float(np.sqrt(np.mean(rec ** 2)))
    if level < 0.0005:
        return WARN, f"вход: {inp[:30]}; сигнал почти нулевой ({level:.4f})", "микрофон выключен, не тот или без разрешения Windows"
    return OK, f"вход: {inp[:30]}; уровень {level:.4f}"


@check("устройства", "камера (для жестов и «посмотри»)")
def _camera():
    if not DEVICES:
        return SKIP, "запусти с --devices"
    try:
        import cv2
    except ImportError:
        return WARN, "нет opencv-python", "pip install opencv-python"
    backends = [("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW), ("авто", cv2.CAP_ANY)] if os.name == "nt" else [("авто", cv2.CAP_ANY)]
    for idx in (0, 1, 2):
        for bname, be in backends:
            cap = cv2.VideoCapture(idx, be)
            ok, frame = False, None
            if cap.isOpened():
                for _ in range(8):
                    ok, frame = cap.read()
                    if ok and frame is not None:
                        break
                    time.sleep(0.05)
            cap.release()
            if ok and frame is not None:
                return OK, f"камера №{idx} через {bname}: кадр {frame.shape[1]}×{frame.shape[0]}"
    return FAIL, "ни один способ не отдал кадр", "закрой программы, которые используют камеру (в т.ч. жесты Atlas), и проверь разрешения Windows"


# --------------------------------------------------------------------- сервисы
def _get(url, **kw):
    import httpx
    t0 = time.time()
    r = httpx.get(url, timeout=12, follow_redirects=True,
                  headers={"User-Agent": "AtlasAssistant/1.0 (https://github.com/alimtszyu13-svg/atlas-personal-assistant)"}, **kw)
    return r, time.time() - t0


@check("сервисы", "погода (Open-Meteo)")
def _meteo():
    r, dt = _get("https://api.open-meteo.com/v1/forecast", params={"latitude": 42.87, "longitude": 74.59, "current": "temperature_2m"})
    return (OK if r.status_code == 200 else FAIL), f"код {r.status_code}, {dt:.2f} с"


@check("сервисы", "картинки: Википедия")
def _wiki():
    r, dt = _get("https://ru.wikipedia.org/api/rest_v1/page/summary/Эйфелева_башня")
    return (OK if r.status_code == 200 else FAIL), f"код {r.status_code}, {dt:.2f} с"


@check("сервисы", "картинки: Openverse")
def _openverse():
    r, dt = _get("https://api.openverse.org/v1/images/", params={"q": "cat", "page_size": 1})
    return (OK if r.status_code == 200 else WARN), f"код {r.status_code}, {dt:.2f} с"


@check("сервисы", "жесты: модель MediaPipe")
def _mediapipe():
    r, dt = _get("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs")
    return (OK if r.status_code == 200 else FAIL), f"код {r.status_code}, {dt:.2f} с", \
        ("" if r.status_code == 200 else "жесты не загрузятся без доступа к cdn.jsdelivr.net")


# --------------------------------------------------------------------- браузер и программы
@check("браузер", "Playwright: запуск Chromium", timeout=40)
def _browser():
    from playwright.sync_api import sync_playwright
    t0 = time.time()
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page()
        pg.set_content("<button>ok</button>")
        n = pg.locator("button").count()
        b.close()
    return (OK if n == 1 else FAIL), f"запуск и страница за {time.time() - t0:.1f} с", \
        ("" if n == 1 else "переустанови браузер: playwright install chromium")


@check("программы", "управление окнами Windows")
def _desktop():
    if os.name != "nt":
        return SKIP, "не Windows"
    from core import desktop_agent
    txt = str(desktop_agent.desktop_windows())
    n = txt.count("\n") + 1 if txt.strip() else 0
    return (OK if n else WARN), f"видно окон: {n}"


# --------------------------------------------------------------------- память и навыки
@check("память", "база memory.db")
def _memory():
    if not os.path.exists("memory.db"):
        return WARN, "базы ещё нет", "появится после первых разговоров"
    c = sqlite3.connect("memory.db", timeout=5)
    try:
        integ = c.execute("PRAGMA integrity_check").fetchone()[0]
        cnt = {}
        for t in ("turns", "episodes", "nodes", "edges", "study_cards", "activity"):
            try:
                cnt[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                pass
        active = c.execute("SELECT COUNT(*) FROM edges WHERE active=1").fetchone()[0] if "edges" in cnt else 0
    finally:
        c.close()
    size = os.path.getsize("memory.db") / 1e6
    if integ != "ok":
        return FAIL, f"целостность: {integ}", "сделай копию memory.db и пришли вывод"
    return OK, f"{size:.1f} МБ; реплик {cnt.get('turns', 0)}, эпизодов {cnt.get('episodes', 0)}, фактов {active}, " \
               f"карточек {cnt.get('study_cards', 0)}"


@check("навыки", "реестр навыков и выученные")
def _skills():
    from core.skills import load_skills
    n = len(load_skills())
    learned = [f for f in os.listdir("skills_auto") if f.endswith(".py")] if os.path.isdir("skills_auto") else []
    return OK, f"навыков в реестре: {n}; выучено самим Atlas: {len(learned)}"


@check("система", "свободное место на диске")
def _disk():
    free = shutil.disk_usage(ROOT).free / 1e9
    return (OK if free > 5 else WARN), f"свободно {free:.0f} ГБ", ("" if free > 5 else "мало места — кэш картинок и база могут не записаться")


def main():
    global DEVICES
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", action="store_true", help="проверить камеру и уровень микрофона")
    DEVICES = ap.parse_args().devices
    print("Проверяю Atlas…" + (" (с камерой и микрофоном — 2 секунды говори что-нибудь)" if DEVICES else "") + "\n")
    for c in CHECKS:
        c()
    ok = sum(r["status"] == OK for r in RESULTS)
    warn = [r for r in RESULTS if r["status"] == WARN]
    fail = [r for r in RESULTS if r["status"] == FAIL]
    print("\n" + "=" * 78)
    print(f"Работает: {ok}  ·  с оговорками: {len(warn)}  ·  сломано: {len(fail)}  ·  пропущено: "
          f"{sum(r['status'] == SKIP for r in RESULTS)}")
    for r in fail + warn:
        print(f"  {'✗' if r['status'] == FAIL else '⚠'} {r['area']} — {r['name']}: {r['detail']}"
              + (f"\n      → {r['tip']}" if r["tip"] else ""))
    os.makedirs("doctor_results", exist_ok=True)
    path = os.path.join("doctor_results", time.strftime("%Y%m%d_%H%M%S") + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=1)
    print(f"\nПодробности: {path}")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
