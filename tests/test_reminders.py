"""
Тесты напоминаний и уведомлений на телефон.

    python tests/test_reminders.py

Файлы напоминаний и подписок — временные, в интернет ничего не отправляется.
"""
import json
import os
import sys
import tempfile
import time
import traceback

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_SERVICE_KEY", None)
TMP = tempfile.mkdtemp(prefix="atlas_rem_")

import reminders  # noqa: E402
from core import cloud_sync, push  # noqa: E402

reminders.FILE = os.path.join(TMP, "atlas_reminders.json")
push.FILE = os.path.join(TMP, "atlas_push.json")
cloud_sync.REMINDERS, cloud_sync.PUSH = reminders.FILE, push.FILE
cloud_sync.NOTES = os.path.join(TMP, "atlas_data.json")
try:
    from core import webpush
    CRYPTO = True
except ImportError:                       # без cryptography уведомления не работают — тесты шифрования пропускаем
    CRYPTO = False

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def reset():
    for p in (reminders.FILE, push.FILE):
        if os.path.exists(p):
            os.remove(p)
    reminders._fired.clear()


# ---------------------------------------------------------------------------
# Напоминания
# ---------------------------------------------------------------------------
@test
def reminder_at_a_time_today_or_tomorrow():
    reset()
    now = reminders._now()
    later = (now.replace(second=0, microsecond=0).timestamp() + 2 * 3600)
    hhmm = time.strftime("%H:%M", time.gmtime(later + now.utcoffset().total_seconds()))
    r = reminders.set_reminder("позвонить маме", hhmm)
    assert r.startswith("Reminder set for"), r
    due = reminders._active(reminders._load())[0][1]["due"]
    assert 0 < due - time.time() <= 2 * 3600 + 60, due - time.time()
    past = time.strftime("%H:%M", time.gmtime(time.time() - 3600 + now.utcoffset().total_seconds()))
    reminders.set_reminder("завтрашнее", past)                     # время уже прошло — значит, завтра
    tomorrow = [v for _, v in reminders._active(reminders._load()) if v["text"] == "завтрашнее"][0]
    assert 22 * 3600 < tomorrow["due"] - time.time() < 24 * 3600


@test
def reminder_on_a_date_in_your_time_zone():
    reset()
    year = reminders._now().year + 1
    assert reminders.set_reminder("SAT", f"{year}-12-05 08:30").startswith("Reminder set")
    v = reminders._active(reminders._load())[0][1]
    d = reminders.datetime.fromtimestamp(v["due"], reminders._tz())
    assert (d.year, d.month, d.day, d.hour, d.minute) == (year, 12, 5, 8, 30)
    assert "Couldn't understand" in reminders.set_reminder("x", "когда-нибудь")
    assert "already passed" in reminders.set_reminder("x", "2020-01-01 10:00")
    assert reminders.set_reminder("чай", "in 20 minutes").startswith("Reminder set")


@test
def timers_list_and_cancel():
    reset()
    assert reminders.set_timer(10, "чай") == "Timer set for 10 minutes."
    assert "Couldn't" in reminders.set_timer(0)
    reminders.set_timer(30, "стирка")
    lst = reminders.list_timers()
    assert "1. 'чай'" in lst and "2. 'стирка'" in lst, lst
    assert reminders.cancel_reminder("2") == "Cancelled: 'стирка'."
    assert reminders.cancel_reminder("чай") == "Cancelled: 'чай'."
    assert reminders.list_timers() == "You have no active timers or reminders."
    assert "No active reminder" in reminders.cancel_reminder("гости")


@test
def due_reminder_fires_once_and_old_ones_are_skipped():
    reset()
    doc = {"items": {
        "a": {"text": "сейчас", "due": time.time() - 5, "kind": "reminder", "updated": 1, "done": False, "deleted": False},
        "b": {"text": "давно", "due": time.time() - 7 * 3600, "kind": "reminder", "updated": 1, "done": False, "deleted": False},
        "c": {"text": "потом", "due": time.time() + 600, "kind": "timer", "updated": 1, "done": False, "deleted": False}}}
    reminders._save(doc)
    got = reminders.due_now()
    assert [g["text"] for g in got] == ["сейчас"], got
    assert reminders.due_now() == [], "второй раз не срабатывает"
    left = [v["text"] for _, v in reminders._active(reminders._load())]
    assert left == ["потом"], left
    said, pushed = [], []
    saved = push.notify
    push.notify = lambda title, body, tag=None, url="/": pushed.append((title, body)) or 1
    try:
        reminders.fire(got[0], said.append, push=True)
    finally:
        push.notify = saved
    assert said == ["Напоминание: сейчас"] and pushed == [("Atlas · напоминание", "сейчас")]


@test
def phone_screen_shows_and_deletes_reminders():
    reset()
    reminders.set_timer(5, "чай")
    items = reminders.upcoming()
    assert items[0]["text"] == "чай" and items[0]["when"].startswith(("сегодня", "завтра")), items
    from phone import panels
    saved = panels._notes
    panels._notes = lambda: type("N", (), {"_load": staticmethod(lambda: {"todos": [], "notes": []})})
    try:
        h = panels.home()
        assert h["reminders"][0]["text"] == "чай"
        h = panels.reminder_delete({"id": h["reminders"][0]["id"]})
        assert h["reminders"] == []
    finally:
        panels._notes = saved


# ---------------------------------------------------------------------------
# Общая память: компьютер и облако добавили напоминания одновременно — не теряется ни одно
# ---------------------------------------------------------------------------
@test
def reminders_from_two_devices_are_merged_not_overwritten():
    reset()
    reminders.set_timer(10, "с компьютера")
    pc_doc = reminders._load()
    remote = {"items": {"zzz": {"text": "с телефона", "due": time.time() + 900, "kind": "reminder",
                                "updated": time.time() + 1, "done": False, "deleted": False}}}
    item = {"uid": "kv:reminders", "tbl": "kv", "data": {"json": json.dumps(remote)}, "updated_at": time.time() + 1}
    state = {}
    assert cloud_sync._apply_kv(item, state) is True
    texts = sorted(v["text"] for _, v in reminders._active(reminders._load()))
    assert texts == ["с компьютера", "с телефона"], texts
    assert "sent:kv:reminders" not in state, "слитое отличается от облака — уйдёт обратно при следующей синхронизации"
    # удаление на другом устройстве (новее) побеждает
    rid = next(k for k, v in pc_doc["items"].items())
    gone = {"items": {rid: dict(pc_doc["items"][rid], deleted=True, updated=time.time() + 5)}}
    cloud_sync._apply_kv(dict(item, data={"json": json.dumps(gone)}, updated_at=time.time() + 5), state)
    assert [v["text"] for _, v in reminders._active(reminders._load())] == ["с телефона"]


@test
def sync_sends_reminders_and_push_docs():
    reset()
    reminders.set_timer(3, "чай")
    sent = []

    class Store:
        def push(self, rows):
            sent.extend(rows)
            return len(rows)

        def pull(self, since, device):
            return []
    saved_state = cloud_sync.STATE
    cloud_sync.STATE = os.path.join(TMP, "state.json")
    try:
        cloud_sync.sync_once(Store(), db=os.path.join(TMP, "m.db"))
        uids = [r["uid"] for r in sent if r["tbl"] == "kv"]
        assert "kv:reminders" in uids, uids
        sent.clear()
        cloud_sync.sync_once(Store(), db=os.path.join(TMP, "m.db"))
        assert not [r for r in sent if r["tbl"] == "kv"], "без изменений повторно не отправляется"
    finally:
        cloud_sync.STATE = saved_state


# ---------------------------------------------------------------------------
# Уведомления
# ---------------------------------------------------------------------------
def _ua():
    """Телефон: свои ключи подписки."""
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    auth = os.urandom(16)
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {
        "p256dh": webpush.b64u(webpush._raw_public(priv.public_key())), "auth": webpush.b64u(auth)}}
    return priv, auth, sub


def _decrypt(body: bytes, priv, auth: bytes) -> bytes:
    """Как это делает телефон (RFC 8291) — проверяем, что зашифровано правильно."""
    import struct
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, (rs, idlen) = body[:16], struct.unpack("!IB", body[16:21])
    as_public = body[21:21 + idlen]
    record = body[21 + idlen:]
    ua_public = webpush._raw_public(priv.public_key())
    shared = priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = webpush._hkdf(auth, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = webpush._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = webpush._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, record, None)
    assert rs == 4096 and plain.endswith(b"\x02")
    return plain[:-1]


@test
def push_message_is_encrypted_for_the_phone():
    if not CRYPTO:
        print("    (пропущено: нет cryptography)")
        return
    priv, auth, sub = _ua()
    msg = json.dumps({"title": "Atlas", "body": "Пора на SAT"}, ensure_ascii=False).encode()
    body = webpush.encrypt(msg, sub["keys"]["p256dh"], sub["keys"]["auth"])
    assert _decrypt(body, priv, auth) == msg


@test
def vapid_signature_is_valid():
    if not CRYPTO:
        return
    import base64
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    keys = webpush.new_keys()
    h = webpush.vapid_header("https://fcm.googleapis.com/fcm/send/abc", keys, "mailto:a@b.c")
    t = h.split("t=")[1].split(",")[0]
    assert h.endswith("k=" + keys["public"])
    head, claims, sig = t.split(".")
    c = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
    assert c["aud"] == "https://fcm.googleapis.com" and c["sub"] == "mailto:a@b.c" and c["exp"] > time.time()
    raw = webpush.unb64u(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.unb64u(keys["public"]))
    pub.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))       # бросит, если подпись неверна


@test
def phone_subscribes_and_gets_notifications_dead_ones_removed():
    if not CRYPTO:
        return
    reset()
    k1 = push.public_key()
    assert push.public_key() == k1, "ключ создаётся один раз"
    priv, auth, sub = _ua()
    _, _, old = _ua()
    old["endpoint"] = "https://fcm.googleapis.com/fcm/send/old"
    assert push.subscribe(sub, "Android") and push.subscribe(old, "старый")
    assert not push.subscribe({"endpoint": "http://evil"}), "только https и с ключами"
    got = []

    def fake_send(s, message, keys, subject):
        got.append((s["endpoint"], message, keys["public"], subject))
        return 410 if s["endpoint"].endswith("/old") else 201
    saved = webpush.send
    webpush.send = fake_send
    try:
        assert push.notify("Atlas", "Пора", tag="x") == 1
        assert len(push.active()) == 1, "подписка, которой больше нет (410), убрана"
        assert got[0][1] == {"title": "Atlas", "body": "Пора", "tag": "x", "url": "/"} and got[0][2] == k1
    finally:
        webpush.send = saved
    from phone import panels
    assert panels.push_key()["key"] == k1


# ---------------------------------------------------------------------------
# Утренняя сводка
# ---------------------------------------------------------------------------
@test
def morning_brief_says_your_day_in_one_message():
    reset()
    from core import briefing as B
    saved = B.weather, B.events, B.cards_due, B.todos_open
    B.weather = lambda: "+8°, ясно, днём до 15°, ночью 3°"
    B.events = lambda: [("09:00", "SAT practice"), ("весь день", "День учителя")]
    B.cards_due = lambda: 14
    B.todos_open = lambda: 3
    reminders.set_reminder("позвонить маме", "23:59") if reminders._now().strftime("%H:%M") < "23:58" else None
    try:
        text = B.build()
    finally:
        B.weather, B.events, B.cards_due, B.todos_open = saved
    assert "Погода: +8°, ясно" in text and "09:00 — SAT practice; весь день — День учителя" in text, text
    assert "К повторению 14 карточек." in text and "Открытых дел: 3." in text, text
    B.events = lambda: (_ for _ in ()).throw(RuntimeError("нет Google"))
    try:
        B.weather = lambda: "+1°, снег, днём до 2°, ночью -4°"
        text = B.build()
        assert "Погода: +1°" in text and "календар" not in text.lower(), "часть не получилась — сводка без неё"
    finally:
        B.weather, B.events = saved[0], saved[1]


@test
def morning_brief_comes_once_a_day_at_your_time():
    reset()
    from core import briefing as B
    tz = reminders._tz()
    morning = reminders.datetime(2026, 10, 9, 7, 40, tzinfo=tz)
    assert B.settings() == {"on": True, "time": "07:30", "sent": ""}
    assert B.due(morning) and not B.due(morning.replace(hour=7, minute=0)) and not B.due(morning.replace(hour=11))
    B._save(sent="2026-10-09")
    assert not B.due(morning), "сегодня уже отправлена"
    assert B.due(morning + reminders.timedelta(days=1))
    assert B.set_settings(on=True, at="8:05")["time"] == "08:05"
    assert B.set_settings(on=False)["on"] is False and not B.due(morning + reminders.timedelta(days=1, hours=1))
    from phone import panels
    assert panels.brief_set({"on": True, "time": "06:45"})["time"] == "06:45" and panels.brief_get()["on"] is True


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
