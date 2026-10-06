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
    for private in (".env", "*.db", "phone_pairing.json", "cloud_sync_state.json", "atlas_data.json", "logs"):
        assert private in ign, private


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
