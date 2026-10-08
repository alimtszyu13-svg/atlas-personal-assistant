"""
Тесты Atlas на телефоне: сервер (как его видит телефон), туннель и команда «установи себя».

    python tests/test_phone.py

Сервер запускается по-настоящему на свободном порту; мозг, распознавание, голос и туннели —
подставные (интернет, ключи и Windows не нужны).
"""
import base64
import json
import os
import sys
import tempfile
import traceback
import types
import urllib.error
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LOG = {"ask": [], "tts": [], "stt": [], "opened": []}
FAST = {"открой блокнот": "Открываю блокнот."}


def _mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


def install():
    _mod("fast_commands", try_fast_command=lambda t: FAST.get(t.lower().strip(" .!?")))
    _mod("ai_brain", ask_ai=lambda q, speech=None: (LOG["ask"].append(q), REPLY[0])[1],
         remember_exchange=lambda u, a: None)

    class _Tr:
        @staticmethod
        def create(**kw):
            LOG["stt"].append(kw)
            return types.SimpleNamespace(text=HEARD[0])

    def gen(text, filename):
        LOG["tts"].append(text)
        if TTS_FAIL[0]:
            raise RuntimeError("нет голоса")
        with open(filename, "wb") as f:
            f.write(b"MP3DATA")
        return filename
    _mod("voice", groq_client=types.SimpleNamespace(audio=types.SimpleNamespace(transcriptions=_Tr)),
         STT_WHISPER="whisper-large-v3", _stt_language=lambda: "ru", _stt_prompt=lambda: "подсказка",
         _WHISPER_JUNK={"продолжение следует"}, _generate_any=gen, get_response_language=lambda: "ru")
    _mod("ui_state", shared_state={"chat_history": []})
    core = sys.modules.get("core") or _mod("core")
    core.__path__ = [os.path.join(ROOT, "core")]                 # настоящие core/emotions.py, если есть
    _mod("core.memory", log_turn=lambda who, t: None)
    core.memory = sys.modules["core.memory"]


REPLY, HEARD, TTS_FAIL = ["Готово."], ["какая погода"], [False]
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def call(path, method="GET", body=None, headers=None):
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def ask(text, key=None):
    return call("/api/ask", "POST", json.dumps({"text": text}).encode(),
                {"Content-Type": "application/json", "X-Atlas-Key": key if key is not None else TOKEN})


@test
def app_files_for_installing():
    st, h, b = call("/")
    assert st == 200 and 'id="star"' in b.decode() and h["Content-Type"].startswith("text/html")
    st, h, b = call("/manifest.webmanifest")
    man = json.loads(b)
    assert st == 200 and man["display"] == "standalone" and man["start_url"] == "/" and len(man["icons"]) == 2
    st, h, b = call("/sw.js")
    assert st == 200 and h.get("Service-Worker-Allowed") == "/" and b"caches" in b
    st, h, b = call("/icon-192.png")
    assert st == 200 and b[:8] == b"\x89PNG\r\n\x1a\n", "иконка-звезда"


@test
def nothing_without_the_pairing_key():
    for key in ("", "wrong-key"):
        st, _, b = ask("какая погода", key=key)
        assert st == 401 and "ключ" in json.loads(b)["error"], (key, st)
    st, _, _ = call("/api/talk", "POST", b"audio", {"Content-Type": "audio/webm", "X-Atlas-Key": "nope"})
    assert st == 401
    assert call("/pair")[0] == 404 and call("/phone_pairing.json")[0] == 404, "ключ по сети не отдаётся"
    assert not LOG["ask"], "без ключа мозг не вызывался"


@test
def pairing_check():
    st, _, b = call("/api/pair", "POST", b"", {"X-Atlas-Key": TOKEN})
    assert st == 200 and json.loads(b)["ok"]


@test
def text_question_fast_path_answers_with_voice():
    LOG["ask"].clear()
    st, _, b = ask("Открой блокнот.")
    r = json.loads(b)
    assert st == 200 and r["text"] == "Открываю блокнот." and not LOG["ask"], "быстрый путь, без модели"
    assert base64.b64decode(r["audio"]) == b"MP3DATA" and r["mime"] == "audio/mpeg"


@test
def text_question_goes_to_the_brain_and_back():
    LOG["ask"].clear()
    LOG["tts"].clear()
    REPLY[0] = "[warm] Тепло, сэр."
    st, _, b = ask("какая погода")
    r = json.loads(b)
    assert len(LOG["ask"]) == 1 and LOG["ask"][0].startswith("(Respond in Russian.) (Said on the phone:") \
        and LOG["ask"][0].endswith("какая погода"), LOG["ask"]
    emotions = os.path.exists(os.path.join(ROOT, "core", "emotions.py"))
    assert r["text"] == ("Тепло, сэр." if emotions else "[warm] Тепло, сэр."), r["text"]
    assert LOG["tts"][-1] == "[warm] Тепло, сэр.", "голосу метки нужны"
    chat = sys.modules["ui_state"].shared_state["chat_history"]
    assert ("You 📱", "какая погода") in chat, "разговор виден и в окне на компьютере"


@test
def voice_question_from_android_and_iphone():
    for ctype, ext in (("audio/webm;codecs=opus", "webm"), ("audio/mp4", "m4a")):
        LOG["stt"].clear()
        HEARD[0], REPLY[0] = "какая погода", "Солнечно."
        st, _, b = call("/api/talk", "POST", b"\x1a\x45\xdf\xa3audio", {"Content-Type": ctype, "X-Atlas-Key": TOKEN})
        r = json.loads(b)
        assert st == 200 and r["heard"] == "какая погода" and r["text"] == "Солнечно.", r
        kw = LOG["stt"][0]
        assert kw["file"][0] == f"phone.{ext}" and kw["language"] == "ru" and kw["file"][1].startswith(b"\x1a"), kw


@test
def noise_is_not_sent_to_the_brain():
    LOG["ask"].clear()
    HEARD[0] = "Продолжение следует..."
    st, _, b = call("/api/talk", "POST", b"noise", {"Content-Type": "audio/webm", "X-Atlas-Key": TOKEN})
    assert st == 200 and json.loads(b)["heard"] == "" and not LOG["ask"]
    HEARD[0] = "какая погода"


@test
def too_long_recording_is_refused_and_server_keeps_working():
    big = b"0" * (S.MAX_AUDIO + 10)
    try:                                   # сервер отказывает, не дочитывая: 413 или обрыв соединения
        st, _, _ = call("/api/talk", "POST", big, {"Content-Type": "audio/webm", "X-Atlas-Key": TOKEN})
        assert st == 413, st
    except OSError:                        # Linux: ошибка отправки; Windows: ConnectionResetError при чтении ответа
        pass
    LOG["ask"].clear()
    st, _, b = ask("какая погода")
    assert st == 200 and LOG["ask"], "после отказа сервер отвечает как обычно"


@test
def phone_speaks_by_itself_when_atlas_voice_fails():
    TTS_FAIL[0] = True
    try:
        st, _, b = ask("какая погода")
        r = json.loads(b)
        assert st == 200 and r["audio"] is None and r["text"], r
    finally:
        TTS_FAIL[0] = False


@test
def phone_answers_short_in_the_language_spoken_and_pc_stays_quiet():
    import time as _t
    v = sys.modules["voice"]
    saved = v.get_response_language
    v.get_response_language = lambda: "en"                 # в настройках компьютера — английский
    try:
        LOG["ask"].clear()
        REPLY[0] = "Привет, сэр."
        ask("Атлас, привет!")
        q = LOG["ask"][-1]
        assert q.startswith("(Respond in Russian.) (Said on the phone:") and q.endswith("Атлас, привет!"), q
        ask("what time is it in Tokyo")
        assert LOG["ask"][-1].startswith("(Respond in English.) (Said on the phone:"), LOG["ask"][-1]
        until = sys.modules["ui_state"].shared_state.get("phone_active_until", 0)
        assert until > _t.time() + 20, "компьютер молчит, пока идёт разговор с телефона"
    finally:
        v.get_response_language = saved


def _panel(path, payload=None, key=None):
    st, _, b = call(path, "POST", json.dumps(payload or {}).encode(),
                    {"Content-Type": "application/json", "X-Atlas-Key": key if key is not None else TOKEN})
    return st, json.loads(b)


def _real_data():
    """Настоящие notes.py и core/study.py — но на временных файлах, не на твоих данных."""
    import importlib
    import notes
    d = tempfile.mkdtemp()
    notes.DATA_FILE = os.path.join(d, "atlas_data.json")
    study = importlib.import_module("core.study")
    study.DB = os.path.join(d, "memory.db")
    return notes, study


@test
def todos_and_notes_screens():
    notes, _ = _real_data()
    assert _panel("/api/home", key="wrong")[0] == 401, "без ключа экраны закрыты"
    st, h = _panel("/api/home")
    assert st == 200 and h["todos"] == [] and h["notes"] == []
    _panel("/api/todo/add", {"task": "Сдать SAT practice test"})
    st, h = _panel("/api/todo/add", {"task": "Купить тетрадь"})
    assert [t["task"] for t in h["todos"]] == ["Сдать SAT practice test", "Купить тетрадь"]
    st, h = _panel("/api/todo/done", {"i": 1})
    assert h["todos"][0]["done"] and not h["todos"][1]["done"]
    st, h = _panel("/api/todo/delete", {"i": 2})
    assert len(h["todos"]) == 1
    st, h = _panel("/api/note/add", {"text": "Идея: голосовые карточки по физике"})
    assert h["notes"] == ["Идея: голосовые карточки по физике"]
    assert "Идея" in notes.list_notes(), "голосовая команда видит заметку с экрана"
    st, h = _panel("/api/note/delete", {"i": 1})
    assert h["notes"] == []
    assert _panel("/api/todo/add", {"task": "   "})[1]["todos"] == h["todos"], "пустое дело не добавляется"


@test
def study_screen_full_session():
    _, study = _real_data()
    study.add_cards("SAT", [{"front": "ubiquitous", "back": "вездесущий"}, {"front": "arcane", "back": "тайный"}])
    st, h = _panel("/api/home")
    assert h["decks"][0]["deck"] == "SAT" and h["decks"][0]["due"] == 2
    st, r = _panel("/api/study/start", {"deck": "сат"})
    assert st == 200 and r["card"]["front"] in ("ubiquitous", "arcane") and r["card"]["n"] == 1 and r["card"]["total"] == 2
    first = r["card"]["front"]
    right = {"ubiquitous": "вездесущий", "arcane": "тайный"}[first]
    st, r = _panel("/api/study/answer", {"text": right})
    assert r["feedback"].rstrip(".") in ("Верно", "Точно", "Так и есть", "Правильно"), r
    assert r["card"]["n"] == 2 and "Вопрос" not in r["feedback"], "отзыв без следующего вопроса"
    assert _panel("/api/home")[1]["study"]["n"] == 2, "открыл экран заново — тренировка продолжается"
    st, r = _panel("/api/study/answer", {"text": ""})          # «показать ответ»
    assert "Правильно:" in r["feedback"] and r["card"], "не знал — карточка вернётся в этой же тренировке"
    st, r = _panel("/api/study/stop")
    assert r["done"] and "Итог" in r["feedback"] and _panel("/api/home")[1]["study"] is None


@test
def answer_by_voice_is_only_transcribed():
    LOG["ask"].clear()
    HEARD[0] = "вездесущий"
    st, _, b = call("/api/transcribe", "POST", b"audio", {"Content-Type": "audio/webm", "X-Atlas-Key": TOKEN})
    assert st == 200 and json.loads(b) == {"text": "вездесущий"} and not LOG["ask"], "мозг не вызывался"
    assert call("/api/transcribe", "POST", b"audio", {"Content-Type": "audio/webm", "X-Atlas-Key": "nope"})[0] == 401
    HEARD[0] = "какая погода"


@test
def app_has_the_screens():
    page = call("/")[2].decode()
    for must in ('id="v-todos"', 'id="v-notes"', 'id="v-study"', "/api/home", "/api/study/answer", "/api/transcribe"):
        assert must in page, must


@test
def voice_list_and_choice_from_the_phone():
    v = sys.modules["voice"]
    said = []
    v._generate_speech_fish = lambda text, fn, vid: (said.append((vid, text)), open(fn, "wb").write(b"FISH"))
    env = {"FISH_API_KEY": "k", "FISH_VOICES_RU": "Джарвис:680d74fbef69419f87cfc70f092a1451,Леонид:17e5fd9aed774aedb98141de6e5c4447",
           "FISH_VOICE_EN": "Jarvis:14129c3e320149449d6bada6862f7338"}          # список в одиночном поле — как на скриншоте
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        st, lst = _panel("/api/voices")
        assert st == 200 and lst["fish"] and [x["name"] for x in lst["ru"]] == ["Джарвис", "Леонид"], lst
        assert lst["en"] == [{"name": "Jarvis", "id": "14129c3e320149449d6bada6862f7338"}], lst["en"]
        REPLY[0] = "Добрый вечер, сэр."
        hdr = {"Content-Type": "application/json", "X-Atlas-Key": TOKEN, "X-Atlas-Voice-Ru": "17e5fd9aed774aedb98141de6e5c4447"}
        st, _, b = call("/api/ask", "POST", json.dumps({"text": "привет"}).encode(), hdr)
        assert said[-1] == ("17e5fd9aed774aedb98141de6e5c4447", "Добрый вечер, сэр.") and base64.b64decode(json.loads(b)["audio"]) == b"FISH"
        n = len(said)
        hdr["X-Atlas-Voice-Ru"] = "deadbeefdeadbeefdeadbeefdeadbeef"                  # не из списка — не принимаем
        call("/api/ask", "POST", json.dumps({"text": "привет"}).encode(), hdr)
        assert len(said) == n, "чужой номер голоса не используется"
        st, _, b = call("/api/voice/test", "POST", b'{"lang":"en"}', {"Content-Type": "application/json", "X-Atlas-Key": TOKEN,
                                                                         "X-Atlas-Voice-En": "14129c3e320149449d6bada6862f7338"})
        assert said[-1][0] == "14129c3e320149449d6bada6862f7338" and "sir" in json.loads(b)["text"]
        assert call("/api/voices", "POST", b"{}", {"X-Atlas-Key": "nope"})[0] == 401
    finally:
        for k, val in saved.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val
        del v._generate_speech_fish


@test
def app_has_voice_settings():
    page = call("/")[2].decode()
    for must in ('id="v-settings"', 'id="gear"', "/api/voices", "/api/voice/test", "X-Atlas-Voice-", "atlasMute"):
        assert must in page, must


@test
def tunnel_prefers_tailscale_and_explains_what_is_missing():
    from phone import tunnel
    saved = (tunnel._tailscale_exe, tunnel._cloudflared_exe, tunnel._run, tunnel.subprocess.Popen)
    try:
        tunnel._tailscale_exe = lambda: "tailscale"
        runs = []

        def run_ok(args, timeout=20):
            runs.append(args)
            out = json.dumps({"BackendState": "Running", "Self": {"DNSName": "atlas-pc.tail1234.ts.net."}}) \
                if "status" in args else "Available within your tailnet"
            return types.SimpleNamespace(returncode=0, stdout=out, stderr="")
        tunnel._run = run_ok
        assert tunnel.start(8765) == ("https://atlas-pc.tail1234.ts.net", "tailscale", "")
        assert ["tailscale", "serve", "--bg", "8765"] in runs

        def run_no_https(args, timeout=20):
            if "status" in args:
                return run_ok(args)
            return types.SimpleNamespace(returncode=1, stdout="", stderr="HTTPS is not enabled for this tailnet")
        tunnel._run = run_no_https
        tunnel._cloudflared_exe = lambda: "cloudflared"

        class FakeCF:
            def __init__(self, args, **kw):
                self.stdout = iter(["INF Requesting new quick Tunnel\n",
                                    "INF |  https://quiet-star-1234.trycloudflare.com  |\n"])

            def poll(self):
                return None

            def kill(self):
                pass
        tunnel.subprocess.Popen = FakeCF
        url, kind, hint = tunnel.start(8765)
        assert (url, kind) == ("https://quiet-star-1234.trycloudflare.com", "cloudflare"), (url, kind, hint)

        tunnel._tailscale_exe = tunnel._cloudflared_exe = lambda: ""
        url, kind, hint = tunnel.start(8765)
        assert url is None and "Tailscale не установлен" in hint and "cloudflared не установлен" in hint
    finally:
        tunnel._tailscale_exe, tunnel._cloudflared_exe, tunnel._run, tunnel.subprocess.Popen = saved


@test
def install_opens_a_local_qr_page_with_the_key():
    from phone import install as I
    import webbrowser
    saved_open, saved_state = webbrowser.open, dict(S._state)
    webbrowser.open = lambda url: LOG["opened"].append(url)
    try:
        S._state.update(url="https://atlas-pc.tail1234.ts.net", kind="tailscale")
        msg = I.install("телефон")
        page = LOG["opened"][-1]
        html = open(page.replace("file:///", "/" if os.name != "nt" else ""), encoding="utf-8").read()
        assert "QR" in msg and page.startswith("file:///"), msg
        assert ("<img alt=\"QR-код" in html) or (f"https://atlas-pc.tail1234.ts.net/?pair={TOKEN}" in html)
        assert "Tailscale" in html and "iPhone" in html and "Android" in html
        msg = I.install("телевизор")
        assert "телевизор" in msg and f"/?pair={TOKEN}" in open(LOG["opened"][-1].replace("file:///", "/" if os.name != "nt" else ""),
                                                               encoding="utf-8").read()
        S._state.update(url=None, hint="Tailscale не установлен; cloudflared не установлен")
        S.connect = lambda force=False: ""
        msg = I.install("phone")
        assert "Tailscale" in msg and "winget" in msg, msg
    finally:
        webbrowser.open = saved_open
        S._state.clear()
        S._state.update(saved_state)


@test
def computer_link_is_closed_on_the_computer_itself():
    st, _, _ = call("/api/pc/poll", "POST", b"{}", {"Content-Type": "application/json", "X-Atlas-Key": TOKEN})
    assert st == 404, "на компьютере облачной «очереди дел» нет"


@test
def phone_controls_the_computer_through_the_cloud():
    import threading
    from core import pc_link
    S.PC_HUB = True
    stop = threading.Event()
    done = []

    def handler(text):
        done.append(text)
        return "Включил лоу-фай в Spotify."
    t = threading.Thread(target=pc_link.run_agent, args=(BASE, TOKEN, handler, "test-pc", stop), daemon=True)
    try:
        st, _, _ = call("/api/pc/poll", "POST", b"{}", {"Content-Type": "application/json", "X-Atlas-Key": "чужой".encode().hex()})
        assert st == 401, "чужой компьютер не подключится"
        assert pc_link.use_computer("включи музыку") == pc_link.OFFLINE, "компьютер не на связи — честно"
        t.start()
        for _ in range(50):
            if pc_link.online():
                break
            import time as _t
            _t.sleep(0.1)
        assert pc_link.online()
        st, _, b = call("/api/pc/status", "POST", b"{}", {"Content-Type": "application/json", "X-Atlas-Key": TOKEN})
        assert json.loads(b)["online"] is True
        r = pc_link.use_computer("включи лоу-фай")
        assert done == ["включи лоу-фай"] and "Включил лоу-фай в Spotify." in r, r
    finally:
        stop.set()
        S.PC_HUB = False


@test
def fast_command_the_cloud_cannot_do_goes_to_the_computer():
    FAST["включи музыку"] = "Это недоступно из облака: компьютер сейчас не на связи."
    asked = len(LOG["ask"])
    try:
        S.FORWARD = lambda text: "Включил музыку на компьютере."
        st, _, b = ask("Включи музыку")
        assert st == 200 and json.loads(b)["text"] == "Включил музыку на компьютере.", b
        S.FORWARD = lambda text: None                  # компьютер не на связи — отвечает мозг, а не служебный текст
        REPLY[0] = "Компьютер сейчас выключен, сэр."
        st, _, b = ask("Включи музыку")
        assert json.loads(b)["text"] == "Компьютер сейчас выключен, сэр." and len(LOG["ask"]) == asked + 1
    finally:
        S.FORWARD = None
        FAST.pop("включи музыку", None)
        REPLY[0] = "Готово."


@test
def links_for_the_phone_itself():
    from core import phone_actions as A
    assert A.build("youtube", "lofi hip hop")["url"] == "https://www.youtube.com/results?search_query=lofi+hip+hop"
    assert A.build("спотифай", "Imagine Dragons")["url"] == "https://open.spotify.com/search/Imagine%20Dragons"
    assert A.build("site", url="habr.com/ru")["url"] == "https://habr.com/ru"
    assert A.build(url="javascript:alert(1)") == {}, "только https, звонок, смс и почта"
    assert A.build("call", phone="+996 555 12-34-56")["url"] == "tel:+996555123456"
    assert A.build("неизвестное", "SAT practice test")["url"].startswith("https://www.google.com/search?q=SAT")
    assert A.build("карты", "Дордой Плаза")["label"] == "Карты: Дордой Плаза"


@test
def answer_brings_a_link_to_open_on_the_phone():
    from core import phone_actions
    brain = sys.modules["ai_brain"]
    saved = brain.ask_ai

    def ask_ai(q, speech=None):
        phone_actions.open_on_phone("youtube", "lofi")
        return "Открываю YouTube."
    brain.ask_ai = ask_ai
    try:
        st, _, b = ask("включи lofi на ютубе")
        r = json.loads(b)
        assert r["text"] == "Открываю YouTube." and r["actions"] == [
            {"label": "YouTube: lofi", "url": "https://www.youtube.com/results?search_query=lofi"}], r
        brain.ask_ai = saved
        st, _, b = ask("какая погода")
        assert json.loads(b)["actions"] == [], "ссылка не переходит в следующий ответ"
    finally:
        brain.ask_ai = saved


@test
def voice_comes_as_a_stream_and_text_right_away():
    n = len(LOG["tts"])
    st, _, b = call("/api/ask", "POST", json.dumps({"text": "какая погода"}).encode(),
                    {"Content-Type": "application/json", "X-Atlas-Key": TOKEN, "X-Atlas-Audio": "stream"})
    r = json.loads(b)
    assert st == 200 and r["text"] == "Готово." and r["audio"] is None and r["say"], r
    assert len(LOG["tts"]) == n, "текст пришёл раньше, чем начался голос"
    st, h, audio = call("/api/say/" + r["say"])
    assert st == 200 and audio == b"MP3DATA" and h.get("Content-Type") == "audio/mpeg", (st, h)
    st, _, _ = call("/api/say/" + r["say"])
    assert st == 404, "ссылка на звук одноразовая"
    st, _, _ = call("/api/say/nonexistent123")
    assert st == 404


@test
def muted_phone_gets_no_voice_at_all():
    n = len(LOG["tts"])
    st, _, b = call("/api/ask", "POST", json.dumps({"text": "какая погода"}).encode(),
                    {"Content-Type": "application/json", "X-Atlas-Key": TOKEN, "X-Atlas-Audio": "none"})
    r = json.loads(b)
    assert r["audio"] is None and "say" not in r and len(LOG["tts"]) == n, "озвучка выключена — голос не делаем"


def main():
    global S, BASE, TOKEN
    install()
    from phone import server as S
    S.PAIR_FILE = os.path.join(tempfile.mkdtemp(), "phone_pairing.json")
    TOKEN = S.pairing_token(create=True)
    port = S.start(0)
    BASE = f"http://127.0.0.1:{port}"
    ok = 0
    for t in TESTS:
        try:
            t()
            ok += 1
            print(f"  ✓ {t.__name__}")
        except Exception:
            print(f"  ✗ {t.__name__}\n" + "".join("      " + ln for ln in traceback.format_exc().splitlines(True)[-5:]))
    S.stop()
    print(f"\nТестов пройдено: {ok} из {len(TESTS)}")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if ok == len(TESTS) else 1)


if __name__ == "__main__":
    main()
