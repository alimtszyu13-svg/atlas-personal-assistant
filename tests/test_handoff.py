"""
Тесты связки компьютер ↔ телефон (core/handoff.py): экран на телефон, «продолжи на телефоне»,
уведомления с компьютера (загрузки, батарея).

    python tests/test_handoff.py
"""
import json
import os
import sys
import time
import traceback

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop("ATLAS_CLOUD_URL", None)
from core import file_drop, handoff, phone_actions  # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


HIST = lambda t0, t1: [(time.time() - 60, "Chrome", "Linear equations | Khan Academy",
                        "https://www.khanacademy.org/math/algebra/x2f8bb11595b61c86:solve")]


@test
def open_page_continues_on_the_phone_as_a_button():
    phone_actions.take()
    reply = file_drop.run_phone_job(
        lambda t: handoff.continue_on_phone(active=lambda: ("Chrome", "Linear equations | Khan Academy - Google Chrome"),
                                            history=HIST), "продолжи на телефоне")
    assert reply.startswith("Opened 'Linear equations | Khan Academy' for the phone"), reply
    assert "[[link:https://www.khanacademy.org/math/algebra/x2f8bb11595b61c86:solve|Linear equations / Khan Academy]]" in reply, reply
    from core import pc_link
    text = pc_link._with_files(reply)
    assert "[[" not in text and phone_actions.take() == [
        {"label": "Linear equations / Khan Academy", "url": "https://www.khanacademy.org/math/algebra/x2f8bb11595b61c86:solve"}]


@test
def open_document_is_sent_and_other_windows_explained():
    sent = []
    r = handoff.continue_on_phone(active=lambda: ("Word", "Эссе про климат.docx - Word"), history=HIST,
                                  sender=lambda q: (sent.append(q), "Sent Эссе про климат.docx")[1])
    assert sent == ["Эссе про климат.docx"] and r.startswith("Sent"), (sent, r)
    r = handoff.continue_on_phone(active=lambda: ("Steam", "Steam"), history=HIST)
    assert "can't be moved to the phone" in r
    r = handoff.continue_on_phone(active=lambda: ("Chrome", "Неизвестная - Google Chrome"), history=HIST)
    assert "wasn't found in the browser history" in r


@test
def direct_connection_puts_the_button_in_the_answer():
    phone_actions.take()
    handoff.continue_on_phone(active=lambda: ("Chrome", "Linear equations | Khan Academy - Google Chrome"), history=HIST)
    acts = phone_actions.take()
    assert acts and acts[0]["url"].startswith("https://www.khanacademy.org/"), acts


@test
def screen_goes_to_the_phone_with_its_text():
    shots = []
    r = handoff.screen_to_phone(grab=lambda p: shots.append(p),
                                reader=lambda: "Window: VS Code\nmain.py — SyntaxError: invalid syntax (line 12)",
                                sender=lambda p: f"Sent {os.path.basename(p)} to the phone.")
    assert shots and shots[0].endswith(".jpg") and "Экран" in os.path.basename(shots[0])
    assert r.startswith("Sent Экран") and "SyntaxError" in r, r
    r = handoff.screen_to_phone(grab=lambda p: (_ for _ in ()).throw(OSError("нет экрана")))
    assert r.startswith("Couldn't take a screenshot")


@test
def downloads_and_battery_alerts():
    assert handoff.downloads_done({"a.pdf", "b.mp4.crdownload"}, {"a.pdf", "b.mp4", "c.zip.part", ".tmp1"}) == ["b.mp4"]
    handoff._alert_state["battery_warned"] = False
    assert handoff.battery_alert(14, False) is True and handoff.battery_alert(12, False) is False, "один раз"
    assert handoff.battery_alert(40, True) is False and handoff.battery_alert(10, False) is True, "после зарядки — снова"
    assert handoff.battery_alert(None, False) is False


@test
def computer_asks_the_cloud_to_notify_the_phone():
    os.environ["ATLAS_CLOUD_URL"] = "https://atlas.example"
    from phone import server
    saved = server.pairing_token
    server.pairing_token = lambda create=False: "k123"
    got = []
    try:
        ok = handoff.notify_phone("Atlas · загрузка готова", "SAT_practice.pdf",
                                  poster=lambda req: (got.append(req), True)[1])
    finally:
        server.pairing_token = saved
        os.environ.pop("ATLAS_CLOUD_URL", None)
    req = got[0]
    assert ok and req.full_url == "https://atlas.example/api/pc/notify" and req.get_header("X-atlas-key") == "k123"
    assert json.loads(req.data) == {"title": "Atlas · загрузка готова", "body": "SAT_practice.pdf", "url": "/"}


@test
def tools_are_registered():
    from core.skills import REGISTRY
    import skills.handoff  # noqa: F401
    assert REGISTRY["show_screen_on_phone"]["group"] == "handoff" and "continue_on_phone" in REGISTRY
    import tool_router
    assert "handoff" in tool_router._detect("что на экране компа?")
    assert "handoff" in tool_router._detect("продолжи на телефоне")


def main():
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
    os._exit(0 if ok == len(TESTS) else 1)


if __name__ == "__main__":
    main()
