"""
Web Push без сторонних обёрток: шифрование сообщения (RFC 8291, aes128gcm) и подпись VAPID (RFC 8292).
Нужна только библиотека cryptography.

    keys = new_keys()                                   # {"private": ..., "public": ...} — base64url
    send(subscription, {"title": "Atlas", "body": "Пора"}, keys, "mailto:me@example.com")
        subscription — то, что выдал телефон: {"endpoint": ..., "keys": {"p256dh": ..., "auth": ...}}
        → код ответа сервиса уведомлений (201 — доставлено; 404/410 — подписки больше нет)
"""
import base64
import hashlib
import hmac
import json
import os
import struct
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

RECORD_SIZE = 4096


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(text: str) -> bytes:
    text = (text or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _raw_public(pub) -> bytes:
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def new_keys() -> dict:
    """Пара ключей VAPID: приватный (32 байта) и публичный (65 байт) в base64url."""
    priv = ec.generate_private_key(ec.SECP256R1())
    d = priv.private_numbers().private_value.to_bytes(32, "big")
    return {"private": b64u(d), "public": b64u(_raw_public(priv.public_key()))}


def _private(keys: dict):
    return ec.derive_private_key(int.from_bytes(unb64u(keys["private"]), "big"), ec.SECP256R1())


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def encrypt(payload: bytes, p256dh: str, auth: str, salt: bytes = None, server_key=None) -> bytes:
    """Тело запроса aes128gcm: заголовок (соль, размер записи, ключ сервера) + зашифрованная запись."""
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _raw_public(server_key.public_key())
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = server_key.exchange(ec.ECDH(), peer)
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    if len(payload) > RECORD_SIZE - 17 - 86:
        raise ValueError("сообщение слишком длинное для одного уведомления")
    record = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public + record


def vapid_header(endpoint: str, keys: dict, subject: str, ttl_hours: int = 12) -> str:
    """Authorization: vapid t=<JWT ES256>, k=<публичный ключ>."""
    u = urlsplit(endpoint)
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + ttl_hours * 3600, "sub": subject}
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps(claims, separators=(",", ":")).encode())
    der = _private(keys).sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={head}.{body}.{sig}, k={keys['public']}"


def send(subscription: dict, message: dict, keys: dict, subject: str, ttl: int = 86400, timeout: float = 15) -> int:
    endpoint = subscription["endpoint"]
    k = subscription.get("keys") or {}
    data = encrypt(json.dumps(message, ensure_ascii=False).encode("utf-8"), k["p256dh"], k["auth"])
    req = urllib.request.Request(endpoint, data=data, method="POST", headers={
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(ttl),
        "Urgency": "high", "Authorization": vapid_header(endpoint, keys, subject)})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
