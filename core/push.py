"""
Уведомления на телефон (Web Push): ключи VAPID и подписки телефонов.

Всё лежит в atlas_push.json и синхронизируется через общую память, поэтому облако и компьютер
используют одни и те же ключи: телефон подписывается один раз. Ключ создаётся сам при первой подписке.

    public_key()            — для телефона (applicationServerKey)
    subscribe(sub, name)    — сохранить подписку телефона
    notify(title, body)     — разослать уведомление → сколько доставлено
"""
import hashlib
import json
import os
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "atlas_push.json")
_lock = threading.RLock()


def available() -> bool:
    try:
        from core import webpush  # noqa: F401  (нужна библиотека cryptography)
        return True
    except Exception:
        return False


def _load() -> dict:
    try:
        with open(FILE, encoding="utf-8") as f:
            doc = json.load(f)
        if isinstance(doc, dict):
            doc.setdefault("subs", {})
            return doc
    except Exception:
        pass
    return {"subs": {}}


def _save(doc: dict) -> None:
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    os.replace(tmp, FILE)


def keys() -> dict:
    """Ключи VAPID; если их ещё нет нигде (ни здесь, ни в общей памяти) — создаются."""
    from core import webpush
    with _lock:
        doc = _load()
        v = doc.get("vapid") or {}
        if not v.get("private") or not v.get("public"):
            v = dict(webpush.new_keys(), updated=time.time())
            doc["vapid"] = v
            _save(doc)
            print("[уведомления] созданы ключи VAPID — разойдутся по устройствам через общую память")
        return {"private": v["private"], "public": v["public"]}


def public_key() -> str:
    return keys()["public"]


def _sid(endpoint: str) -> str:
    return hashlib.sha1(endpoint.encode("utf-8")).hexdigest()[:16]


def subscribe(sub: dict, name: str = "") -> bool:
    if not isinstance(sub, dict) or not str(sub.get("endpoint", "")).startswith("https://"):
        return False
    k = sub.get("keys") or {}
    if not k.get("p256dh") or not k.get("auth"):
        return False
    pub = public_key()
    with _lock:
        doc = _load()
        doc["subs"][_sid(sub["endpoint"])] = {
            "sub": {"endpoint": sub["endpoint"], "keys": {"p256dh": k["p256dh"], "auth": k["auth"]}},
            "key": pub, "name": str(name or "")[:60], "updated": time.time(), "deleted": False}
        _save(doc)
    return True


def unsubscribe(endpoint: str) -> bool:
    with _lock:
        doc = _load()
        it = doc["subs"].get(_sid(str(endpoint or "")))
        if not it:
            return False
        it.update(deleted=True, updated=time.time())
        _save(doc)
    return True


def active() -> list:
    """Живые подписки под текущий ключ (подписки под старый ключ телефон уже не примет)."""
    with _lock:
        doc = _load()
    pub = (doc.get("vapid") or {}).get("public")
    return [(sid, s) for sid, s in doc["subs"].items() if not s.get("deleted") and s.get("key") == pub]


def subject() -> str:
    c = (os.getenv("PUSH_CONTACT") or "").strip()
    return c if c.startswith(("mailto:", "https://")) else "mailto:" + (c or "atlas@example.com")


def notify(title: str, body: str, tag: str = None, url: str = "/") -> int:
    """Уведомление на все подписанные телефоны. Подписки, которых больше нет (404/410), убираются."""
    from core import webpush
    subs = active()
    if not subs:
        return 0
    k = keys()
    msg = {"title": title, "body": body, "tag": tag or "atlas", "url": url}
    sent, gone = 0, []
    for sid, s in subs:
        try:
            code = webpush.send(s["sub"], msg, k, subject())
        except Exception as e:
            print(f"[уведомления] не отправилось ({e})")
            continue
        if 200 <= code < 300:
            sent += 1
        elif code in (404, 410):
            gone.append(sid)
        else:
            print(f"[уведомления] сервис уведомлений ответил {code}")
    if gone:
        with _lock:
            doc = _load()
            for sid in gone:
                if sid in doc["subs"]:
                    doc["subs"][sid].update(deleted=True, updated=time.time())
            _save(doc)
    return sent
