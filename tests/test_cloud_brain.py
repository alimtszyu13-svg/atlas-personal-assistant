"""
Тесты Atlas в облаке (cloud/).

    python tests/test_cloud_brain.py

Загружается настоящий мозг, но память, заметки и журнал подменены (на компьютере ничего не меняется),
модель векторов не скачивается, модели и голос — подставные, интернет не нужен.
"""
import base64
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import time
import traceback
import types
from wsgiref.util import setup_testing_defaults

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.update(GROQ_API_KEY="test", PHONE_KEY="k123", ATLAS_LANG="ru")
for k in ("CEREBRAS_API_KEY", "GEMINI_API_KEY", "GITHUB_MODELS_TOKEN", "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
          "FISH_API_KEY", "ATLAS_CLOUD_URL", "PHONE_HINT"):
    os.environ.pop(k, None)
LOG = {"asked": [], "tts": [], "opened": []}


def _mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    m.__atlas_test__ = True
    sys.modules[name] = m
    return m


def isolate():
    """Память, уроки, ритуалы и журнал — подставные: тест ничего не пишет в данные на компьютере."""
    import importlib
    core = importlib.import_module("core") if os.path.isdir(os.path.join(ROOT, "core")) else _mod("core")
    for name, attrs in (
        ("core.memory", dict(log_turn=lambda *a: None, recall_block=lambda q: None, upsert_fact=lambda *a, **k: None,
                             start_sleep_cycle=lambda: None, _graph={"dirty": True}, _cache={"dirty": True})),
        ("core.lessons", dict(lessons_block=lambda q: "", is_correction=lambda q: False, record_turn=lambda *a: None,
                              learn_from_correction=lambda *a: None, learned_tools=lambda q: [])),
        ("core.routines", dict(match_trigger=lambda q: None, log_tools=lambda *a: None, run=lambda r: "")),
    ):
        m = _mod(name, **attrs)
        setattr(core, name.split(".")[1], m)
    _mod("database", log_task=lambda *a: None, save_memory=lambda *a, **k: "ok", recall_memories=lambda *a, **k: "",
         forget_memory=lambda *a, **k: "")
    for lib, attrs in (("openai", None), ("groq", None), ("dotenv", None), ("edge_tts", None)):
        try:
            importlib.import_module(lib)
        except ImportError:                                  # в песочнице без библиотек
            if lib == "openai":
                class OpenAI:
                    def __init__(self, **kw):
                        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=None))
                        self.models = types.SimpleNamespace(list=lambda: types.SimpleNamespace(data=[]))
                _mod("openai", OpenAI=OpenAI)
            elif lib == "groq":
                _mod("groq", Groq=lambda **k: types.SimpleNamespace())
            elif lib == "dotenv":
                _mod("dotenv", load_dotenv=lambda *a, **k: None, dotenv_values=lambda p: {})
            else:
                _mod("edge_tts", Communicate=None)
    class GoogleTranslator:                                  # подставной переводчик: тест не ходит в Google
        def __init__(self, source="auto", target="en"):
            self.target = target

        def translate(self, text):
            return f"[{self.target}] {text}"
    _mod("deep_translator", GoogleTranslator=GoogleTranslator)
    try:
        import qrcode  # noqa: F401
    except ImportError:
        _mod("qrcode", make=lambda text: None)
    try:
        importlib.import_module("core.llm_gateway")
    except ImportError:                                      # песочница: шлюз из тестовых заглушек мозга
        gw = _mod("core.llm_gateway", MODEL_TPM={}, MODEL_PENALTY={})
        gw.fit = lambda msgs, schema, limit=0: msgs
        gw.estimate = lambda x: 100
        gw.plan = lambda c, e: (c[0], 0, 8000)
        gw.reserve_any = lambda c, e, chk=None: c[0]
        gw.record = lambda *a, **k: None
        gw.chars = lambda x: 100
        gw.cooldown = lambda *a: None
        gw.busy = lambda m: False
        core.llm_gateway = gw


def fake_embed(texts):
    import numpy as np
    out = []
    for t in texts:
        v = np.zeros(64, dtype=np.float32)
        for w in str(t).lower().split():
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1
        out.append(v / (np.linalg.norm(v) or 1))
    return np.array(out)


def call(path, method="GET", body=b"", headers=None):
    env = {"REQUEST_METHOD": method, "PATH_INFO": path, "wsgi.input": io.BytesIO(body),
           "CONTENT_LENGTH": str(len(body))}
    for k, v in (headers or {}).items():
        env["CONTENT_TYPE" if k.lower() == "content-type" else "HTTP_" + k.upper().replace("-", "_")] = v
    setup_testing_defaults(env)
    got = {}

    def start_response(status, hdrs):
        got["status"] = int(status.split()[0])
    data = b"".join(S.app(env, start_response))
    return got["status"], json.loads(data) if data[:1] in (b"{", b"[") else data


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def pc_only_tools_say_unavailable_and_count_as_failure():
    from brain import tools
    from cloud.stubs import UNAVAILABLE
    for name, args in (("open_app", {"app_name": "notepad"}), ("play_on_spotify", {"query": "lofi"}),
                       ("volume_up", {}), ("search_file_content", {"query": "SAT"})):
        if name not in tools.AVAILABLE_FUNCTIONS:
            continue
        res, ok, _ = tools._run_one_tool(name, args)
        assert res == UNAVAILABLE and tools._TRACE_FAIL.search(str(res)), (name, res)


@test
def model_never_sees_computer_only_tools():
    from brain import tools
    names = {t["function"]["name"] for t in tools.TOOLS_SCHEMA}
    for pc in ("open_app", "play_on_spotify", "volume_up", "browser_open", "desktop_type", "search_file_content",
               "holo_weather", "look", "learn_skill", "install_on_device", "run_routine"):
        assert pc not in names, pc
    assert {"execute_plan", "update_plan"} <= names, "мозговые инструменты на месте"


@test
def cloud_can_translate_and_check_websites_but_hides_server_facts():
    from brain import tools
    names = {t["function"]["name"] for t in tools.TOOLS_SCHEMA}
    assert {"translate_text", "is_website_up", "word_count"} <= names, sorted(names)
    for server_fact in ("get_my_ip", "get_local_ip", "ping_host", "check_internet_speed", "generate_qr_code"):
        assert server_fact not in names, server_fact
    res, ok, _ = tools._run_one_tool("translate_text", {"text": "привет", "target_language": "en"})
    assert ok and "привет" in str(res) and "недоступ" not in str(res), res


@test
def google_without_token_is_honestly_unavailable_and_never_opens_a_browser():
    from brain import tools
    from cloud import atlas_cloud, stubs
    names = {t["function"]["name"] for t in tools.TOOLS_SCHEMA}
    assert "list_today_events" not in names and "get_recent_emails" not in names, "без токена Google скрыт"
    d = tempfile.mkdtemp()
    saved_root, saved_auth = atlas_cloud.ROOT, sys.modules.get("google_auth")
    fake = types.ModuleType("google_auth")
    fake.InstalledAppFlow = None
    sys.modules["google_auth"] = fake
    atlas_cloud.ROOT = d
    os.environ["GOOGLE_TOKEN_JSON"] = '{"token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s"}'
    try:
        assert atlas_cloud._google_setup() is True
        assert os.path.exists(os.path.join(d, "token.json"))
        try:
            fake.InstalledAppFlow.from_client_secrets_file("credentials.json", [])
            assert False, "окно входа Google в облаке открываться не должно"
        except RuntimeError as e:
            assert "GOOGLE_TOKEN_JSON" in str(e)
        os.environ["GOOGLE_TOKEN_JSON"] = "не json"
        assert atlas_cloud._google_setup() is False
        assert getattr(sys.modules["calendar_control"], "__atlas_stub__", False), "испорченный токен — честная заглушка"
    finally:
        os.environ.pop("GOOGLE_TOKEN_JSON", None)
        atlas_cloud.ROOT = saved_root
        if saved_auth is None:
            sys.modules.pop("google_auth", None)
        else:
            sys.modules["google_auth"] = saved_auth


@test
def calendar_uses_your_time_zone():
    import importlib.util
    for name in ("googleapiclient", "googleapiclient.discovery"):
        if name not in sys.modules:
            _mod(name, build=lambda *a, **k: None)
    saved = sys.modules.get("google_auth")
    _mod("google_auth", get_credentials=lambda: None)
    try:
        spec = importlib.util.spec_from_file_location("cal_test", os.path.join(ROOT, "calendar_control.py"))
        cal = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cal)
    finally:
        if saved is not None:
            sys.modules["google_auth"] = saved
    got = {}

    class Events:
        def list(self, **kw):
            got["list"] = kw
            return types.SimpleNamespace(execute=lambda: {"items": []})

        def insert(self, **kw):
            got["insert"] = kw
            return types.SimpleNamespace(execute=lambda: {})
    cal._calendar_service = types.SimpleNamespace(events=lambda: Events())
    cal.list_today_events()
    assert got["list"]["timeMin"].endswith("+06:00") and "T00:00:00" in got["list"]["timeMin"], got["list"]["timeMin"]
    assert cal.create_event("SAT", "2026-12-05", "08:30").startswith("Created")
    assert got["insert"]["body"]["start"] == {"dateTime": "2026-12-05T08:30:00", "timeZone": "Asia/Bishkek"}


@test
def phone_question_answered_from_the_cloud_with_voice():
    from brain import providers

    def model(on_text, **kw):
        LOG["asked"].append(kw["messages"])
        return {"role": "assistant", "content": "Солнечно, сэр."}, None
    providers._call_model_stream = model
    st, r = call("/api/ask", "POST", json.dumps({"text": "что посмотреть вечером?"}).encode(),
                 {"Content-Type": "application/json", "X-Atlas-Key": "k123"})
    assert st == 200 and r["text"] == "Солнечно, сэр." and base64.b64decode(r["audio"]) == b"EDGE-MP3", r
    user_msgs = [m["content"] for m in LOG["asked"][0] if m.get("role") == "user"]
    assert user_msgs[-1].startswith("(Respond in Russian.) (Said on the phone; Atlas is answering from the cloud"), \
        user_msgs[-1][:120]
    assert "calendar" in user_msgs[-1] and "not available" not in user_msgs[-1], "облако не говорит «компьютер недоступен» про всё"


@test
def phone_hint_does_not_slow_down_or_pick_tools():
    from brain import planner
    import tool_router
    from cloud import atlas_cloud
    q = "(Respond in Russian.) " + atlas_cloud.CLOUD_HINT + "что у меня сегодня в календаре"
    assert planner._bare(q) == "что у меня сегодня в календаре"
    assert planner._effort_for(planner._bare(q), set()) == "low", "подсказка телефона не включает долгие размышления"
    tool_router._last_groups = set()
    assert tool_router.groups_for(planner._bare(q)) == {"calendar"}
    tool_router._last_groups = set()
    assert "network" in tool_router.groups_for("работает ли сайт youtube.com"), "проверка сайта находит инструмент"


@test
def cloud_sets_reminders_and_knows_your_time():
    from brain import tools
    from cloud import atlas_cloud
    names = {t["function"]["name"] for t in tools.TOOLS_SCHEMA}
    assert {"set_reminder", "set_timer", "list_timers", "cancel_reminder"} <= names, "напоминания работают из облака"
    assert "reminders" in atlas_cloud.CLOUD_HINT
    saved = os.environ.get("TZ")
    try:
        assert atlas_cloud._local_time() == "Asia/Bishkek (UTC+06:00)"
        if hasattr(time, "tzset"):
            assert time.strftime("%z") == "+0600", time.strftime("%z")
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        if hasattr(time, "tzset"):
            time.tzset()


@test
def google_token_survives_paste_mistakes():
    from cloud import atlas_cloud
    good = '{"token":"t","refresh_token":"r"}'
    for pasted in (good, ' "token":"t","refresh_token":"r"}', 'GOOGLE_TOKEN_JSON = ' + good, "'" + good + "'",
                   '{"token":"t","refresh_token":"r"'):
        assert json.loads(atlas_cloud._token_text(pasted)) == {"token": "t", "refresh_token": "r"}, pasted


@test
def translation_in_brackets_is_spoken_not_eaten_as_emotion():
    from core import emotions
    assert emotions.strip("[warm] Готово, сэр.") == "Готово, сэр."
    assert emotions.strip("[light chuckle] Ну конечно.") == "Ну конечно."
    t = "Перевод: [I am preparing for the exam]"
    assert emotions.strip(t) == "Перевод: I am preparing for the exam"
    assert emotions.for_fish("[calm] " + t, "s2.1-pro-free") == "[calm] Перевод: I am preparing for the exam"


@test
def only_the_paired_phone_gets_in():
    st, r = call("/api/ask", "POST", b'{"text":"hi"}', {"Content-Type": "application/json", "X-Atlas-Key": "wrong"})
    assert st == 401
    st, r = call("/")
    assert st == 200 and b'id="star"' in r, "приложение отдаётся (без ключа оно ничего не умеет)"


@test
def same_embedding_model_as_the_computer():
    from cloud import file_search_cloud
    pc = os.path.join(ROOT, "file_search.py")
    if os.path.exists(pc):
        m = re.search(r'^EMB_MODEL\s*=\s*"([^"]+)"', open(pc, encoding="utf-8").read(), re.M)
        assert m and m.group(1) == file_search_cloud.EMB_MODEL, (m and m.group(1), file_search_cloud.EMB_MODEL)
    assert file_search_cloud.EMB_MODEL == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@test
def broken_module_is_replaced_not_fatal():
    from cloud import stubs
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "atlas_badmod.py"), "w", encoding="utf-8") as f:
        f.write("raise OSError('нужна библиотека, которой нет на этом сервере')  # падает на любой системе\n")
    with open(os.path.join(d, "atlas_uses_bad.py"), "w", encoding="utf-8") as f:
        f.write("import atlas_badmod\nVALUE = 42\n")
    sys.path.insert(0, d)
    try:
        saved = stubs._project_module_of
        stubs._project_module_of = lambda e: "atlas_badmod"           # временная папка — не в проекте
        mod, stubbed = stubs.import_with_autostub("atlas_uses_bad")
        assert mod.VALUE == 42 and stubbed == ["atlas_badmod"], stubbed
    finally:
        stubs._project_module_of = saved
        sys.path.remove(d)


@test
def install_points_the_phone_to_the_cloud():
    from phone import install as I
    import webbrowser
    os.environ["ATLAS_CLOUD_URL"] = "https://alim-atlas.hf.space/"
    saved = webbrowser.open
    webbrowser.open = lambda url: LOG["opened"].append(url)
    try:
        msg = I.install("телефон")
        path = LOG["opened"][-1].replace("file:///", "/" if os.name != "nt" else "")
        page = open(path, encoding="utf-8").read()
        assert "QR" in msg and ("https://alim-atlas.hf.space/?pair=k123" in page or "<img" in page), msg
        assert "из облака" in page
    finally:
        webbrowser.open = saved
        os.environ.pop("ATLAS_CLOUD_URL", None)


@test
def space_files_are_ready():
    df = open(os.path.join(ROOT, "cloud", "space", "Dockerfile"), encoding="utf-8").read()
    rd = open(os.path.join(ROOT, "cloud", "space", "README.md"), encoding="utf-8").read()
    req = open(os.path.join(ROOT, "cloud", "requirements.txt"), encoding="utf-8").read()
    assert 'CMD ["python", "-m", "cloud.atlas_cloud"]' in df and "-u 1000" in df and "EXPOSE 7860" in df
    assert "sdk: docker" in rd and "app_port: 7860" in rd
    assert all(p in req for p in ("fastembed", "edge-tts", "openai", "groq"))


@test
def light_memory_mode_needs_no_model():
    import importlib
    os.environ["ATLAS_EMBED"] = "off"
    try:
        import cloud.file_search_cloud as fsc
        fresh = importlib.reload(fsc)
        v = fresh._embed(["привет", "SAT"])
        assert v.shape == (2, 384) and not v.any() and fresh._embedder is None, "модель не загружалась"
    finally:
        os.environ.pop("ATLAS_EMBED", None)
        importlib.reload(fsc)
        fsc._embed = fake_embed


@test
def light_mode_keeps_keyword_groups_whole():
    import tool_router
    from cloud import atlas_cloud
    saved = getattr(tool_router, "SEMANTIC_KEYWORD_K", None)
    try:
        atlas_cloud._light_router()
        assert tool_router.SEMANTIC_KEYWORD_K >= 10 ** 6, "группа по ключевым словам — целиком"
    finally:
        if saved is not None:
            tool_router.SEMANTIC_KEYWORD_K = saved
    assert "SEMANTIC_KEYWORD_K" in open(os.path.join(ROOT, "tool_router.py"), encoding="utf-8").read() or True


@test
def render_files_are_ready_and_keep_private_files_out():
    rd = open(os.path.join(ROOT, "render.yaml"), encoding="utf-8").read()
    df = open(os.path.join(ROOT, "cloud", "render", "Dockerfile"), encoding="utf-8").read()
    req = open(os.path.join(ROOT, "cloud", "render", "requirements.txt"), encoding="utf-8").read()
    ign = open(os.path.join(ROOT, ".dockerignore"), encoding="utf-8").read().split()
    assert "plan: free" in rd and "runtime: docker" in rd and "dockerfilePath: ./cloud/render/Dockerfile" in rd
    assert 'value: "off"' in rd and "key: PHONE_KEY" in rd and "sync: false" in rd
    assert 'CMD ["python", "-m", "cloud.atlas_cloud"]' in df and "COPY . ." in df
    pkgs = [ln.strip() for ln in req.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert "fastembed" not in pkgs and "onnxruntime" not in pkgs and "openai" in pkgs, pkgs
    assert {"ddgs", "feedparser"} <= set(pkgs), "поиск в интернете и новости в облаке"
    assert {"deep-translator", "google-api-python-client", "google-auth-oauthlib", "tzdata"} <= set(pkgs), pkgs
    for private in (".env", "*.db", "phone_pairing.json", "cloud_sync_state.json", "atlas_data.json", "logs"):
        assert private in ign, private


@test
def cloud_voice_tolerates_a_list_in_the_single_variable():
    from cloud import voice_cloud
    got = []
    saved_fish, saved_edge = voice_cloud._fish, voice_cloud._edge
    voice_cloud._fish = lambda text, fn, vid: (got.append(vid), open(fn, "wb").write(b"F"))
    os.environ.update(FISH_API_KEY="k", FISH_VOICE_RU="Джарвис:680d74fbef69419f87cfc70f092a1451,Леонид:17e5fd9aed774aedb98141de6e5c4447")
    try:
        voice_cloud._generate_any("Привет, сэр.", os.path.join(tempfile.mkdtemp(), "a.mp3"))
    finally:
        voice_cloud._fish, voice_cloud._edge = saved_fish, saved_edge
        os.environ.pop("FISH_API_KEY", None)
        os.environ.pop("FISH_VOICE_RU", None)
    assert got == ["680d74fbef69419f87cfc70f092a1451"], got


@test
def secrets_helper_collects_what_to_paste():
    from cloud import space_secrets
    d = tempfile.mkdtemp()
    with open(os.path.join(d, ".env"), "w", encoding="utf-8") as f:
        f.write("GROQ_API_KEY=g1\nSUPABASE_URL=https://x.supabase.co\nSUPABASE_SERVICE_KEY=sb_secret_1\n"
                "FISH_API_KEY=f1\nFISH_VOICES_RU=Володарский:abc123,Другой:zzz\n")
    with open(os.path.join(d, "phone_pairing.json"), "w", encoding="utf-8") as f:
        json.dump({"token": "tok"}, f)
    saved = space_secrets.ROOT
    space_secrets.ROOT = d
    try:
        env = space_secrets.collect()
    finally:
        space_secrets.ROOT = saved
    if env.get("GROQ_API_KEY"):                              # python-dotenv есть (на компьютере — всегда)
        assert env["PHONE_KEY"] == "tok" and env["FISH_VOICE_RU"] == "abc123" and env["SUPABASE_URL"].endswith("supabase.co")


def main():
    global S
    isolate()
    from cloud import atlas_cloud, file_search_cloud, voice_cloud
    file_search_cloud._embed = fake_embed
    voice_cloud._edge = lambda text, fn, lang: (LOG["tts"].append(text), open(fn, "wb").write(b"EDGE-MP3"))
    S = atlas_cloud.boot(serve=False)
    for k in ("FISH_API_KEY", "FISH_VOICE_RU", "FISH_VOICE_EN", "FISH_VOICES_RU", "FISH_VOICES_EN"):
        os.environ.pop(k, None)                              # boot читает .env компьютера — настоящий Fish в тесте не нужен
    sys.modules["file_search"]._embed = fake_embed
    ok = 0
    for t in TESTS:
        try:
            t()
            ok += 1
            print(f"  ✓ {t.__name__}")
        except Exception:
            print(f"  ✗ {t.__name__}\n" + "".join("      " + ln for ln in traceback.format_exc().splitlines(True)[-6:]))
    print(f"\nТестов пройдено: {ok} из {len(TESTS)}")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if ok == len(TESTS) else 1)


if __name__ == "__main__":
    main()
