"""
Тесты одной памяти на все устройства (core/cloud_sync.py).

    python tests/test_cloud_sync.py

Два «устройства» — две отдельные базы memory.db и два файла заметок; «облако» — подставное, но
ведёт себя как таблица Supabase из cloud/supabase_setup.sql: принимает только более свежие изменения
и отдаёт их по времени попадания в облако. Последний тест проверяет настоящие HTTP-запросы.
"""
import http.server
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("cloud_sync", os.path.join(ROOT, "core", "cloud_sync.py"))
CS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CS)


class FakeCloud:
    """Как atlas_rows + atlas_push в Supabase."""

    def __init__(self):
        self.rows, self.clock, self.down = {}, datetime(2026, 10, 6, tzinfo=timezone.utc), False

    def _tick(self):
        self.clock += timedelta(milliseconds=1)
        return self.clock.isoformat()

    def push(self, rows):
        if self.down:
            raise OSError("нет сети")
        n = 0
        for r in rows:
            cur = self.rows.get(r["uid"])
            if cur is None or r["updated_at"] > cur["updated_at"]:
                self.rows[r["uid"]] = dict(r, synced_at=self._tick())
                n += 1
        return n

    def pull(self, since_iso, device):
        if self.down:
            raise OSError("нет сети")
        since = datetime.fromisoformat(since_iso)
        got = [r for r in self.rows.values()
               if datetime.fromisoformat(r["synced_at"]) > since and r.get("device") != device]
        return sorted(got, key=lambda r: r["synced_at"])[:CS.BATCH]


class Device:
    def __init__(self, name):
        d = tempfile.mkdtemp(prefix=f"atlas_{name}_")
        self.db, self.state, self.notes = (os.path.join(d, "memory.db"), os.path.join(d, "state.json"),
                                           os.path.join(d, "atlas_data.json"))
        self.reminders, self.push = os.path.join(d, "atlas_reminders.json"), os.path.join(d, "atlas_push.json")
        c = sqlite3.connect(self.db)
        for sql in CS._SCHEMA.values():
            c.execute(sql)
        c.commit()
        c.close()

    def sync(self, cloud):
        CS.STATE, CS.NOTES = self.state, self.notes
        CS.REMINDERS, CS.PUSH = self.reminders, self.push    # настоящие файлы компьютера в тест не попадают
        return CS.sync_once(cloud, self.db)

    def sql(self, q, args=()):
        c = sqlite3.connect(self.db)
        try:
            r = c.execute(q, args).fetchall()
            c.commit()
            return r
        finally:
            c.close()

    # как это делают memory.py и study.py
    def node(self, label):
        name = label.lower()
        r = self.sql("SELECT id FROM nodes WHERE name=?", (name,))
        if r:
            return r[0][0]
        self.sql("INSERT INTO nodes (name, label, emb) VALUES (?,?,?)", (name, label, b"\x00\x00\x80\x3f" * 4))
        return self.sql("SELECT id FROM nodes WHERE name=?", (name,))[0][0]

    def fact(self, s, r, o, single=True):
        sid, oid = self.node(s), self.node(o)
        if single:
            self.sql("UPDATE edges SET active=0 WHERE src=? AND rel=? AND active=1", (sid, r))
        self.sql("INSERT INTO edges (src, rel, dst, conf, updated) VALUES (?,?,?,?,?)", (sid, r, oid, 0.8, time.time()))

    def facts(self):
        return sorted(self.sql("SELECT s.label, e.rel, d.label FROM edges e JOIN nodes s ON s.id=e.src "
                               "JOIN nodes d ON d.id=e.dst WHERE e.active=1"))


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def existing_memory_is_uploaded_once():
    cloud, a = FakeCloud(), Device("a")
    a.fact("User", "prepares_for", "SAT")
    a.sql("INSERT INTO turns (ts, role, text) VALUES (?,?,?)", (time.time(), "user", "привет"))
    r1 = a.sync(cloud)
    assert r1["pushed"] == 4 and len(cloud.rows) == 4, (r1, len(cloud.rows))   # 2 узла, связь, реплика
    assert a.sync(cloud)["pushed"] == 0, "второй раз отправлять нечего"


@test
def facts_and_conversations_arrive_with_links_intact():
    cloud, a, b = FakeCloud(), Device("a"), Device("b")
    a.fact("User", "lives_in", "Bishkek")
    a.fact("User", "exam_date", "2026-12-05")
    a.sql("INSERT INTO episodes (started, ended, summary, emb) VALUES (1,2,'Обсуждали SAT',?)", (b"\x01" * 16,))
    ep = a.sql("SELECT id FROM episodes")[0][0]
    a.sql("INSERT INTO turns (ts, role, text, episode) VALUES (1,'user','как подготовиться к SAT?',?)", (ep,))
    a.sync(cloud)
    b.sync(cloud)
    assert b.facts() == a.facts(), (b.facts(), a.facts())
    assert b.sql("SELECT t.text, e.summary FROM turns t JOIN episodes e ON e.id=t.episode") == \
        [("как подготовиться к SAT?", "Обсуждали SAT")]
    assert b.sql("SELECT emb FROM episodes")[0][0] == b"\x01" * 16, "вектор смысла дошёл без искажений"


@test
def same_person_created_on_both_devices_is_one_node():
    cloud, a, b = FakeCloud(), Device("a"), Device("b")
    a.fact("User", "likes", "jazz", single=False)
    b.fact("User", "studies_at", "School 61")                       # «User» создан и на втором устройстве
    a.sync(cloud), b.sync(cloud), a.sync(cloud), b.sync(cloud)
    for d in (a, b):
        assert d.sql("SELECT COUNT(*) FROM nodes WHERE name='user'")[0][0] == 1
        assert d.facts() == [("User", "likes", "jazz"), ("User", "studies_at", "School 61")], d.facts()


@test
def study_progress_follows_you_between_devices():
    cloud, a, b = FakeCloud(), Device("a"), Device("b")
    a.sql("INSERT INTO study_cards (deck, front, back, due, created) VALUES ('SAT','ubiquitous','вездесущий',?,?)",
          (time.time(), time.time()))
    a.sync(cloud), b.sync(cloud)
    card = b.sql("SELECT id FROM study_cards")[0][0]
    time.sleep(0.01)
    b.sql("UPDATE study_cards SET interval=3, reps=2, due=? WHERE id=?", (time.time() + 3 * 86400, card))  # повторил на телефоне
    b.sql("INSERT INTO study_reviews (ts, card, grade) VALUES (?,?,5)", (time.time(), card))
    b.sync(cloud), a.sync(cloud)
    assert a.sql("SELECT interval, reps FROM study_cards") == [(3.0, 2)]
    assert a.sql("SELECT r.grade, c.front FROM study_reviews r JOIN study_cards c ON c.id=r.card") == [(5, "ubiquitous")]


@test
def newer_change_wins_on_both_devices():
    cloud, a, b = FakeCloud(), Device("a"), Device("b")
    a.sql("INSERT INTO study_cards (deck, front, back, due, created) VALUES ('SAT','arcane','тайный',0,0)")
    a.sync(cloud), b.sync(cloud)
    b.sql("UPDATE study_cards SET reps=1")                            # раньше
    time.sleep(0.02)
    a.sql("UPDATE study_cards SET reps=7")                            # позже — этот должен победить
    b.sync(cloud), a.sync(cloud), b.sync(cloud)
    assert a.sql("SELECT reps FROM study_cards") == [(7,)] and b.sql("SELECT reps FROM study_cards") == [(7,)]


@test
def deletions_and_forgotten_facts_spread_without_echo():
    cloud, a, b = FakeCloud(), Device("a"), Device("b")
    a.sql("INSERT INTO study_cards (deck, front, back, due, created) VALUES ('Old','x','y',0,0)")
    a.fact("User", "likes", "Iron Man 3", single=False)
    a.sync(cloud), b.sync(cloud)
    a.sql("DELETE FROM study_cards WHERE deck='Old'")                 # удалил колоду
    a.sql("UPDATE edges SET active=0")                               # «проверь память» убрал мусор
    a.sync(cloud), b.sync(cloud)
    assert b.sql("SELECT COUNT(*) FROM study_cards")[0][0] == 0 and b.facts() == []
    assert b.sql("SELECT COUNT(*) FROM sync_tombstones")[0][0] == 0, "удаление из облака не уходит обратно"
    assert b.sync(cloud)["pushed"] == 0 and a.sync(cloud)["pushed"] == 0, "никакого эха"


@test
def notes_and_todos_are_shared():
    cloud, a, b = FakeCloud(), Device("a"), Device("b")
    with open(a.notes, "w", encoding="utf-8") as f:
        json.dump({"notes": ["купить молоко"], "todos": []}, f, ensure_ascii=False)
    a.sync(cloud), b.sync(cloud)
    assert json.load(open(b.notes, encoding="utf-8"))["notes"] == ["купить молоко"]
    time.sleep(0.02)
    with open(b.notes, "w", encoding="utf-8") as f:
        json.dump({"notes": ["купить молоко"], "todos": [{"task": "SAT тест", "done": False}]}, f, ensure_ascii=False)
    b.sync(cloud), a.sync(cloud)
    assert json.load(open(a.notes, encoding="utf-8"))["todos"][0]["task"] == "SAT тест"
    assert a.sync(cloud)["pushed"] == 0 and b.sync(cloud)["pushed"] == 0, "заметки не ходят туда-обратно"


@test
def offline_nothing_is_lost():
    cloud, a, b = FakeCloud(), Device("a"), Device("b")
    a.fact("User", "goal", "IELTS 7.5")
    cloud.down = True
    try:
        a.sync(cloud)
        assert False, "без сети синхронизация должна сообщить об ошибке"
    except OSError:
        pass
    assert a.facts() == [("User", "goal", "IELTS 7.5")], "локальная память цела"
    cloud.down = False
    a.sync(cloud), b.sync(cloud)
    assert b.facts() == [("User", "goal", "IELTS 7.5")], "отправилось, когда сеть вернулась"


@test
def many_rows_at_once_are_all_received_and_sync_never_gets_stuck():
    cloud, a, b = FakeCloud(), Device("a"), Device("b")
    saved = CS.BATCH
    CS.BATCH = 50                                                  # как 500, только быстрее
    try:
        for i in range(120):                                       # всё сразу, как первая отправка памяти
            a.sql("INSERT INTO turns (ts, role, text) VALUES (?,?,?)", (i, "user", f"реплика {i}"))
        a.sync(cloud)
        b.sync(cloud)
        assert b.sql("SELECT COUNT(*) FROM turns")[0][0] == 120, "получено не всё"
        a.sql("INSERT INTO turns (ts, role, text) VALUES (999,'user','новая реплика')")
        a.sync(cloud)
        b.sync(cloud)
        assert b.sql("SELECT COUNT(*) FROM turns WHERE text='новая реплика'")[0][0] == 1, "синхронизация застряла"
    finally:
        CS.BATCH = saved


@test
def computer_fills_vectors_for_memory_made_in_the_cloud():
    cloud, cl, pc = FakeCloud(), Device("cloud"), Device("pc")
    cl.sql("INSERT INTO nodes (name, label, emb) VALUES ('ielts','IELTS',?)", (b"\x00" * 16,))   # лёгкий режим облака
    os.environ["ATLAS_EMBED"] = "off"
    cl.sync(cloud)
    os.environ.pop("ATLAS_EMBED", None)
    saved = CS._fill_vector
    CS._fill_vector = lambda text: b"\x01" * 16 if text == "IELTS" else None
    try:
        pc.sync(cloud)                                             # компьютер получил и досчитал
        assert pc.sql("SELECT emb FROM nodes WHERE name='ielts'")[0][0] == b"\x01" * 16
        assert pc.sync(cloud)["pushed"] == 1, "досчитанный вектор ушёл в общую память"
        assert pc.sync(cloud)["pushed"] == 0, "и больше не ходит туда-обратно"
    finally:
        CS._fill_vector = saved
    os.environ["ATLAS_EMBED"] = "off"
    try:
        cl.sync(cloud)
    finally:
        os.environ.pop("ATLAS_EMBED", None)
    assert cl.sql("SELECT emb FROM nodes WHERE name='ielts'")[0][0] == b"\x01" * 16, "облако получило вектор"


@test
def real_http_requests_to_supabase():
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen.append(("POST", self.path, {k.lower(): v for k, v in self.headers.items()}, json.loads(self.rfile.read(n))))
            self._reply(1)

        def do_GET(self):
            seen.append(("GET", self.path, {k.lower(): v for k, v in self.headers.items()}, None))
            self._reply([])

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        st = CS.SupabaseStore(f"http://127.0.0.1:{srv.server_port}/", "secret-key")
        assert st.push([{"uid": "u1", "tbl": "nodes", "data": {}, "updated_at": 1.0, "deleted": False, "device": "d"}]) == 1
        assert st.pull("2026-10-06T00:00:00+00:00", "d") == []
    finally:
        srv.shutdown()
    (m1, p1, h1, b1), (m2, p2, h2, _) = seen
    assert m1 == "POST" and p1 == "/rest/v1/rpc/atlas_push" and b1["rows"][0]["uid"] == "u1"
    assert h1["apikey"] == "secret-key" and h1["authorization"] == "Bearer secret-key"
    assert m2 == "GET" and p2.startswith("/rest/v1/atlas_rows?") and "device=neq.d" in p2 and "synced_at=gt." in p2, p2
    seen.clear()
    srv2 = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv2.serve_forever, daemon=True).start()
    try:
        CS.SupabaseStore(f"http://127.0.0.1:{srv2.server_port}", "sb_secret_abc").pull("2026-10-06T00:00:00+00:00", "d")
    finally:
        srv2.shutdown()
    assert seen[0][2]["apikey"] == "sb_secret_abc" and "authorization" not in seen[0][2], "новый ключ — только в apikey"


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
    sys.stderr.flush()
    os._exit(0 if ok == len(TESTS) else 1)


if __name__ == "__main__":
    main()
