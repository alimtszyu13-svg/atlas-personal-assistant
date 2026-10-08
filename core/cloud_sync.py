"""
Одна память на все устройства.

У каждого устройства — своя локальная копия (memory.db, atlas_data.json): Atlas работает так же
быстро и даже без интернета. Supabase — общий центр: раз в SYNC_EVERY секунд устройство отправляет
туда свои изменения и забирает чужие. Если одно и то же изменили в двух местах — побеждает более
свежее изменение (у карточек SAT это дата следующего повторения, у фактов — активен ли он).

Что синхронизируется: граф фактов (nodes, edges), эпизоды и реплики разговоров, карточки и история
повторений, заметки и дела (atlas_data.json целиком).

Как отслеживаются изменения: в таблицы memory.db добавляются три служебные колонки — uid (общий
для всех устройств номер строки), updated_at, dirty — и триггеры, которые их ставят. Код, который
пишет в память (memory.py, study.py…), менять не нужно.

    SUPABASE_URL, SUPABASE_SERVICE_KEY в .env — без них синхронизация просто выключена.
    Таблица в Supabase создаётся скриптом cloud/supabase_setup.sql (один раз, в SQL Editor).
"""
import base64
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "memory.db")
NOTES = os.path.join(ROOT, "atlas_data.json")
REMINDERS = os.path.join(ROOT, "atlas_reminders.json")      # таймеры и напоминания (reminders.py)
PUSH = os.path.join(ROOT, "atlas_push.json")                # ключи и подписки уведомлений (core/push.py)
STATE = os.path.join(ROOT, "cloud_sync_state.json")
SYNC_EVERY = float(os.getenv("CLOUD_SYNC_EVERY") or 20)
BATCH = 500

# таблица → колонки (без id), ссылки на другие таблицы, двоичные поля
SPEC = {
    "nodes": {"cols": ["name", "label", "emb"], "fk": {}, "blob": ["emb"]},
    "episodes": {"cols": ["started", "ended", "summary", "emb"], "fk": {}, "blob": ["emb"]},
    "edges": {"cols": ["src", "rel", "dst", "conf", "updated", "active"], "fk": {"src": "nodes", "dst": "nodes"}, "blob": []},
    "turns": {"cols": ["ts", "role", "text", "episode"], "fk": {"episode": "episodes"}, "blob": []},
    "study_cards": {"cols": ["deck", "front", "back", "ease", "interval", "due", "reps", "lapses", "created"],
                    "fk": {}, "blob": []},
    "study_reviews": {"cols": ["ts", "card", "grade"], "fk": {"card": "study_cards"}, "blob": []},
}
ORDER = ["nodes", "episodes", "study_cards", "edges", "turns", "study_reviews"]     # сначала то, на что ссылаются

# те же схемы, что создают memory.py и study.py, — на случай, если таблиц ещё нет
_SCHEMA = {
    "turns": "CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY, ts REAL, role TEXT, text TEXT, episode INTEGER)",
    "episodes": "CREATE TABLE IF NOT EXISTS episodes (id INTEGER PRIMARY KEY, started REAL, ended REAL, summary TEXT, emb BLOB)",
    "nodes": "CREATE TABLE IF NOT EXISTS nodes (id INTEGER PRIMARY KEY, name TEXT UNIQUE, label TEXT, emb BLOB)",
    "edges": "CREATE TABLE IF NOT EXISTS edges (id INTEGER PRIMARY KEY, src INTEGER, rel TEXT, dst INTEGER, conf REAL, "
             "updated REAL, active INTEGER DEFAULT 1)",
    "study_cards": "CREATE TABLE IF NOT EXISTS study_cards (id INTEGER PRIMARY KEY, deck TEXT, front TEXT, back TEXT, "
                   "ease REAL DEFAULT 2.5, interval REAL DEFAULT 0, due REAL, reps INTEGER DEFAULT 0, "
                   "lapses INTEGER DEFAULT 0, created REAL)",
    "study_reviews": "CREATE TABLE IF NOT EXISTS study_reviews (id INTEGER PRIMARY KEY, ts REAL, card INTEGER, grade INTEGER)",
}
_NOW = "((julianday('now') - 2440587.5) * 86400.0)"
_NEW_UID = "lower(hex(randomblob(16)))"
_lock = threading.Lock()
_status = {"on": False, "last": 0.0, "pushed": 0, "pulled": 0, "error": ""}


# =============================================================================
# Облако: Supabase (PostgREST)
# =============================================================================
class SupabaseStore:
    """push(rows) → сколько принято; pull(since_iso, device) → строки, изменённые другими устройствами."""

    def __init__(self, url: str, key: str, timeout: float = 20):
        self.url, self.key, self.timeout = url.rstrip("/"), key, timeout

    def _req(self, method, path, body=None):
        headers = {"apikey": self.key, "Content-Type": "application/json", "Accept": "application/json"}
        if not self.key.startswith("sb_"):         # старый ключ service_role (eyJ…) — ещё и как Bearer;
            headers["Authorization"] = f"Bearer {self.key}"   # новый sb_secret_… — только в apikey
        req = urllib.request.Request(self.url + path, method=method,
                                     data=json.dumps(body).encode("utf-8") if body is not None else None,
                                     headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    def push(self, rows: list) -> int:
        return int(self._req("POST", "/rest/v1/rpc/atlas_push", {"rows": rows}) or 0)

    def pull(self, since_iso: str, device: str) -> list:
        q = urllib.parse.urlencode({"select": "uid,tbl,data,updated_at,deleted,synced_at",
                                    "synced_at": f"gt.{since_iso}", "device": f"neq.{device}",
                                    "order": "synced_at.asc", "limit": str(BATCH)})
        return self._req("GET", f"/rest/v1/atlas_rows?{q}") or []


def store_from_env():
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY")
    return SupabaseStore(url, key) if url and key else None


# =============================================================================
# Локальная копия: служебные колонки и триггеры
# =============================================================================
def _connect(db: str):
    c = sqlite3.connect(db, timeout=30, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def prepare(c) -> None:
    """Один раз (и безопасно повторно): служебные колонки, триггеры, журнал удалений."""
    c.execute("CREATE TABLE IF NOT EXISTS sync_tombstones (uid TEXT PRIMARY KEY, tbl TEXT, ts REAL)")
    for t in ORDER:
        c.execute(_SCHEMA[t])
        have = {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
        for col, decl in (("uid", "TEXT"), ("updated_at", "REAL"), ("dirty", "INTEGER DEFAULT 0")):
            if col not in have:
                c.execute(f"ALTER TABLE {t} ADD COLUMN {col} {decl}")
        # всё, что было до синхронизации, — свои изменения: отправить
        c.execute(f"UPDATE {t} SET uid={_NEW_UID}, updated_at={_NOW}, dirty=1 WHERE uid IS NULL")
        c.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {t}_uid ON {t}(uid)")
        # новая строка от программы (uid пустой) — получает uid и помечается «отправить»;
        # строка из облака приходит уже с uid — её не трогаем
        c.execute(f"CREATE TRIGGER IF NOT EXISTS {t}_sync_ins AFTER INSERT ON {t} WHEN NEW.uid IS NULL BEGIN "
                  f"UPDATE {t} SET uid={_NEW_UID}, updated_at={_NOW}, dirty=1 WHERE rowid=NEW.rowid; END")
        # правка программой (updated_at и dirty не меняла) — «отправить»; правки синхронизации меняют
        # updated_at или dirty и триггер не задевают
        c.execute(f"CREATE TRIGGER IF NOT EXISTS {t}_sync_upd AFTER UPDATE ON {t} "
                  f"WHEN NEW.updated_at IS OLD.updated_at AND NEW.dirty IS OLD.dirty BEGIN "
                  f"UPDATE {t} SET updated_at={_NOW}, dirty=1 WHERE rowid=NEW.rowid; END")
        # удаление программой — в журнал удалений; удаление синхронизацией (dirty=-1) — нет
        c.execute(f"CREATE TRIGGER IF NOT EXISTS {t}_sync_del AFTER DELETE ON {t} "
                  f"WHEN OLD.uid IS NOT NULL AND OLD.dirty >= 0 BEGIN "
                  f"INSERT OR REPLACE INTO sync_tombstones (uid, tbl, ts) VALUES (OLD.uid, '{t}', {_NOW}); END")
    c.commit()


# =============================================================================
# Отправка
# =============================================================================
def _uid_of(c, table: str, local_id):
    if local_id in (None, 0):
        return None
    r = c.execute(f"SELECT uid FROM {table} WHERE id=?", (local_id,)).fetchone()
    return r[0] if r else None


def _encode(c, t: str, row: dict) -> dict:
    spec, data = SPEC[t], {}
    for col in spec["cols"]:
        v = row[col]
        if col in spec["blob"]:
            data[col] = base64.b64encode(v).decode("ascii") if v is not None else None
        elif col in spec["fk"]:
            data[col + "_uid"] = _uid_of(c, spec["fk"][col], v)
            if t == "edges":                       # имя узла — запасной способ найти его на другом устройстве
                n = c.execute("SELECT name, label FROM nodes WHERE id=?", (v,)).fetchone()
                data[col + "_name"], data[col + "_label"] = (n[0], n[1]) if n else (None, None)
        else:
            data[col] = v
    return data


def _collect(c, device: str) -> list:
    out = []
    for t in ORDER:
        cols = ", ".join(["uid", "updated_at"] + SPEC[t]["cols"])
        for r in c.execute(f"SELECT {cols} FROM {t} WHERE dirty=1"):
            row = dict(zip(["uid", "updated_at"] + SPEC[t]["cols"], r))
            out.append({"uid": row["uid"], "tbl": t, "data": _encode(c, t, row),
                        "updated_at": row["updated_at"], "deleted": False, "device": device})
    for uid, t, ts in c.execute("SELECT uid, tbl, ts FROM sync_tombstones"):
        out.append({"uid": uid, "tbl": t, "data": {}, "updated_at": ts, "deleted": True, "device": device})
    return out


def _mark_sent(c, rows: list) -> None:
    for r in rows:
        if r["deleted"]:
            c.execute("DELETE FROM sync_tombstones WHERE uid=? AND ts=?", (r["uid"], r["updated_at"]))
        elif r["tbl"] in SPEC:
            # только если с тех пор не меняли — иначе уйдёт в следующий раз
            c.execute(f"UPDATE {r['tbl']} SET dirty=0 WHERE uid=? AND updated_at=?", (r["uid"], r["updated_at"]))
    c.commit()


# =============================================================================
# Приём
# =============================================================================
def _local_id(c, table: str, uid):
    if not uid:
        return None
    r = c.execute(f"SELECT id FROM {table} WHERE uid=?", (uid,)).fetchone()
    return r[0] if r else None


def _node_id(c, uid, name, label):
    """Узел по uid; иначе по имени (один и тот же человек/предмет, созданный на двух устройствах)."""
    nid = _local_id(c, "nodes", uid)
    if nid or not name:
        return nid
    r = c.execute("SELECT id FROM nodes WHERE name=?", (name,)).fetchone()
    if r:
        return r[0]
    return c.execute("INSERT INTO nodes (name, label, uid, updated_at, dirty) VALUES (?,?,?,?,0)",
                     (name, label or name, uid or uuid.uuid4().hex, time.time())).lastrowid


def _decode(c, t: str, data: dict) -> dict:
    spec, row = SPEC[t], {}
    for col in spec["cols"]:
        if col in spec["blob"]:
            v = data.get(col)
            row[col] = base64.b64decode(v) if v else None
        elif col in spec["fk"]:
            target = spec["fk"][col]
            if target == "nodes":
                row[col] = _node_id(c, data.get(col + "_uid"), data.get(col + "_name"), data.get(col + "_label"))
            else:
                row[col] = _local_id(c, target, data.get(col + "_uid")) or (0 if col == "episode" and
                                                                            data.get(col + "_uid") is None else None)
        else:
            row[col] = data.get(col)
    return row


def _apply(c, item: dict) -> bool:
    """Изменение из облака → в локальную копию. Своё более свежее изменение не перетирается."""
    t, uid, ts = item["tbl"], item["uid"], float(item["updated_at"])
    if t not in SPEC:
        return False
    local = c.execute(f"SELECT id, updated_at FROM {t} WHERE uid=?", (uid,)).fetchone()
    if local and (local[1] or 0) >= ts:
        return False
    if item.get("deleted"):
        if local:
            c.execute(f"UPDATE {t} SET dirty=-1 WHERE id=?", (local[0],))
            c.execute(f"DELETE FROM {t} WHERE id=?", (local[0],))
        return bool(local)
    row = _decode(c, t, item.get("data") or {})
    if t == "nodes" and not local:
        same = c.execute("SELECT id, uid FROM nodes WHERE name=?", (row["name"],)).fetchone()
        if same:                                   # один узел, созданный на двух устройствах: берём меньший uid
            if uid < (same[1] or "~"):
                c.execute("UPDATE nodes SET uid=?, updated_at=?, dirty=0 WHERE id=?", (uid, ts, same[0]))
            return False
    if t in ("nodes", "episodes") and row.get("emb") is not None and not any(row["emb"]):
        filled = _fill_vector(row["label"] if t == "nodes" else row["summary"])
        if filled is not None:                     # запись из облака без вектора — досчитываем здесь
            row["emb"], ts = filled, max(ts, time.time())
            item = dict(item, _refill=True)
    cols = SPEC[t]["cols"]
    if local:
        c.execute(f"UPDATE {t} SET {', '.join(f'{k}=?' for k in cols)}, updated_at=?, dirty=0 WHERE id=?",
                  [row[k] for k in cols] + [ts, local[0]])
    else:
        c.execute(f"INSERT INTO {t} ({', '.join(cols)}, uid, updated_at, dirty) VALUES ({', '.join('?' * len(cols))},?,?,0)",
                  [row[k] for k in cols] + [uid, ts])
    if item.get("_refill"):
        c.execute(f"UPDATE {t} SET dirty=1 WHERE uid=?", (uid,))
    return True


def _fill_vector(text):
    """Вектор смысла той моделью, что есть на этом устройстве; None — если модели нет (лёгкий режим облака)."""
    if not text or (os.getenv("ATLAS_EMBED") or "local").strip().lower() in ("off", "0", "false", "no"):
        return None
    try:
        from file_search import _embed
        import numpy as np
        return _embed([text])[0].astype(np.float32).tobytes()
    except Exception:
        return None


# =============================================================================
# Документы целиком: заметки и дела, напоминания, подписки на уведомления
# =============================================================================
def _kv_docs() -> dict:
    """uid → файл. Заметки: новее — побеждает. Остальные сливаются по записям (у каждой своё «updated»)."""
    return {"kv:notes": NOTES, "kv:reminders": REMINDERS, "kv:push": PUSH}


def _sent_key(uid: str) -> str:
    return "notes_sent" if uid == "kv:notes" else "sent:" + uid


def _kv_item(uid: str, path: str, state: dict, device: str):
    if not os.path.exists(path):
        return None
    mtime = os.path.getmtime(path)
    if mtime <= state.get(_sent_key(uid), 0):
        return None
    with open(path, encoding="utf-8") as f:
        content = f.read()
    return {"uid": uid, "tbl": "kv", "data": {"json": content}, "updated_at": mtime, "deleted": False,
            "device": device}


def _notes_item(state: dict, device: str):
    return _kv_item("kv:notes", NOTES, state, device)


def merge(a, b):
    """Слить два документа: запись с полем «updated» — новее побеждает; словарь записей — по ключам."""
    if isinstance(a, dict) and isinstance(b, dict):
        if "updated" in a or "updated" in b:
            return a if float(a.get("updated") or 0) >= float(b.get("updated") or 0) else b
        out = dict(a)
        for k, v in b.items():
            out[k] = merge(a[k], v) if k in a else v
        return out
    return b


def _write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _apply_kv(item: dict, state: dict) -> bool:
    uid = item["uid"]
    path = _kv_docs().get(uid)
    if not path:
        return False
    ts = float(item["updated_at"])
    raw = (item.get("data") or {}).get("json")
    if uid == "kv:notes":
        if os.path.exists(path) and os.path.getmtime(path) >= ts:
            return False
        _write(path, raw or '{"notes": [], "todos": []}')
        os.utime(path, (ts, ts))                   # время из облака — чтобы не отправить обратно
        state[_sent_key(uid)] = ts
        return True
    try:
        remote = json.loads(raw or "{}")
    except ValueError:
        return False
    local = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                local = json.load(f)
        except Exception:
            local = None
    merged = merge(local, remote) if isinstance(local, dict) else remote
    if merged == local:
        return False                               # ничего нового (своё отправится, если оно новее)
    _write(path, json.dumps(merged, ensure_ascii=False))
    if merged == remote:                           # совпало с облаком — обратно не отправляем
        os.utime(path, (ts, ts))
        state[_sent_key(uid)] = max(ts, state.get(_sent_key(uid), 0))
    return True


def _apply_notes(item: dict, state: dict) -> bool:
    return _apply_kv(dict(item, uid="kv:notes"), state)


# =============================================================================
# Один круг синхронизации
# =============================================================================
def _load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def _ts(iso: str) -> float:
    """Время из ответа Supabase («2026-10-06T09:00:00.123456+00:00», бывает с «Z») → секунды."""
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def sync_once(store, db: str = None) -> dict:
    """Отправить своё, забрать чужое. → {'pushed': n, 'pulled': n}."""
    state = _load_state()
    device = state.setdefault("device", uuid.uuid4().hex[:12])
    with _lock:
        c = _connect(db or DB)
        try:
            prepare(c)
            rows = _collect(c, device)
            docs = [d for d in (_kv_item(uid, path, state, device) for uid, path in _kv_docs().items()) if d]
            rows += docs
            for i in range(0, len(rows), BATCH):
                part = rows[i:i + BATCH]
                store.push(part)
                _mark_sent(c, part)
                for d in docs:
                    if d in part:
                        state[_sent_key(d["uid"])] = d["updated_at"]
            pulled, cursor = 0, state.get("pulled_until", "1970-01-01T00:00:00+00:00")
            overlap = True
            while True:
                # первая страница — с нахлёстом 5 с (изменения, записанные одновременно, не теряются);
                # следующие — строго после последней полученной: иначе, когда за 5 с записали больше
                # одной страницы, листание стояло бы на одном месте
                since = _iso(max(0.0, _ts(cursor) - 5)) if overlap else cursor
                items = store.pull(since, device)
                fresh = [it for it in items if _ts(it["synced_at"]) > _ts(cursor)]
                for it in items:
                    if it["tbl"] == "kv":
                        pulled += _apply_kv(it, state)
                    else:
                        pulled += _apply(c, it)
                c.commit()
                if fresh:
                    cursor = max((it["synced_at"] for it in fresh), key=_ts)
                if len(items) < BATCH or (not fresh and not overlap):
                    break
                overlap = False
            state["pulled_until"] = cursor
        finally:
            c.close()
        _save_state(state)
    if pulled:
        try:                                       # кэши памяти пересоберутся с новыми данными
            from core import memory
            memory._graph["dirty"] = True
            memory._cache["dirty"] = True
        except Exception:
            pass
    return {"pushed": len(rows), "pulled": pulled}


def status() -> dict:
    return dict(_status)


def start() -> bool:
    """Фоновая синхронизация, если в .env есть ключи Supabase."""
    store = store_from_env()
    if store is None:
        print("[облако] синхронизация памяти выключена: нет SUPABASE_URL / SUPABASE_SERVICE_KEY в .env")
        return False
    _status["on"] = True

    def loop():
        while True:
            try:
                r = sync_once(store)
                _status.update(last=time.time(), error="", pushed=_status["pushed"] + r["pushed"],
                               pulled=_status["pulled"] + r["pulled"])
                if r["pushed"] or r["pulled"]:
                    print(f"[облако] память: отправлено {r['pushed']}, получено {r['pulled']}")
            except urllib.error.HTTPError as e:
                _status["error"] = f"{e.code}: {e.read()[:200].decode('utf-8', 'replace')}"
                print(f"[облако] Supabase ответил ошибкой {_status['error']}")
            except Exception as e:
                _status["error"] = str(e)[:200]
                print(f"[облако] нет связи с Supabase ({e}) — память работает локально, попробую позже")
            time.sleep(SYNC_EVERY)
    threading.Thread(target=loop, daemon=True, name="cloud-sync").start()
    print("[облако] синхронизация памяти включена")
    return True


if __name__ == "__main__":
    # python core/cloud_sync.py — проверить связь с Supabase и синхронизировать память один раз
    for _stream in (__import__("sys").stdout, __import__("sys").stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
    except Exception:
        pass
    st = store_from_env()
    if st is None:
        print("Нет SUPABASE_URL или SUPABASE_SERVICE_KEY в .env — см. инструкцию.")
        raise SystemExit(1)
    try:
        r = sync_once(st)
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode("utf-8", "replace")
        hint = (" — похоже, не выполнен cloud/supabase_setup.sql" if e.code == 404 or "atlas_" in body
                else " — проверь ключ: нужен service_role, а не anon" if e.code in (401, 403) else "")
        print(f"✗ Supabase ответил ошибкой {e.code}: {body}{hint}")
        raise SystemExit(1)
    except Exception as e:
        print(f"✗ Нет связи с Supabase: {e}")
        raise SystemExit(1)
    print(f"✓ Связь с Supabase есть. Отправлено: {r['pushed']}, получено: {r['pulled']}.")
