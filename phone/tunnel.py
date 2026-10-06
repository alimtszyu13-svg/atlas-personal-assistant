"""
Защищённый адрес https://… для телефона — дома и вне дома.

    Tailscale  (рекомендуется) — постоянный адрес https://<компьютер>.<сеть>.ts.net, доступен только
               твоим устройствам. Нужно: Tailscale на компьютере и на телефоне, один аккаунт,
               в админке Tailscale включены MagicDNS и HTTPS-сертификаты.
    Cloudflare (быстрый старт) — адрес https://….trycloudflare.com без регистрации и без приложения
               на телефоне, но он меняется при каждом запуске Atlas (QR придётся сканировать заново).

PHONE_TUNNEL в .env: auto (по умолчанию: Tailscale, иначе Cloudflare) | tailscale | cloudflare | none.
"""
import atexit
import json
import os
import re
import shutil
import subprocess
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_procs = []
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _find(exe: str, extra=()) -> str:
    for cand in (shutil.which(exe), *extra):
        if cand and os.path.exists(cand):
            return cand
    return ""


def _tailscale_exe() -> str:
    return _find("tailscale", (r"C:\Program Files\Tailscale\tailscale.exe",
                               os.path.join(ROOT, "tools", "tailscale.exe")))


def _cloudflared_exe() -> str:
    return _find("cloudflared", (os.path.join(ROOT, "tools", "cloudflared.exe"),
                                 r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
                                 r"C:\Program Files\cloudflared\cloudflared.exe"))


def _run(args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=_NO_WINDOW)


def tailscale(port: int):
    """→ (адрес, подсказка). Адрес — https://<имя>.ts.net, если Tailscale есть, вошли и HTTPS включён."""
    exe = _tailscale_exe()
    if not exe:
        return None, "Tailscale не установлен"
    try:
        st = json.loads(_run([exe, "status", "--json"]).stdout or "{}")
    except Exception as e:
        return None, f"Tailscale не отвечает ({e})"
    if st.get("BackendState") not in ("Running", None):
        return None, "Tailscale установлен, но не подключён — войди в аккаунт в приложении Tailscale"
    dns = ((st.get("Self") or {}).get("DNSName") or "").rstrip(".")
    if not dns:
        return None, "Tailscale: у компьютера нет имени — включи MagicDNS в админке Tailscale"
    r = _run([exe, "serve", "--bg", str(port)], timeout=40)
    out = (r.stdout + r.stderr).lower()
    if r.returncode != 0:
        if "https" in out or "cert" in out:
            return None, "Tailscale: включи HTTPS-сертификаты в админке (DNS → HTTPS Certificates)"
        return None, f"Tailscale serve не запустился: {(r.stdout + r.stderr).strip()[:160]}"
    return f"https://{dns}", ""


def cloudflare(port: int, timeout: float = 30.0):
    """→ (адрес, подсказка). Быстрый туннель Cloudflare: адрес новый при каждом запуске."""
    exe = _cloudflared_exe()
    if not exe:
        return None, "cloudflared не установлен"
    proc = subprocess.Popen([exe, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", creationflags=_NO_WINDOW)
    _procs.append(proc)
    found = {"url": None}

    def read():
        for line in proc.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and not found["url"]:
                found["url"] = m.group(0)
    threading.Thread(target=read, daemon=True).start()
    until = time.time() + timeout
    while time.time() < until and not found["url"] and proc.poll() is None:
        time.sleep(0.2)
    if not found["url"]:
        proc.kill()
        return None, "Cloudflare не выдал адрес (нет интернета?)"
    return found["url"], ""


def start(port: int):
    """→ (адрес или None, 'tailscale'|'cloudflare'|None, подсказка что установить)."""
    mode = (os.getenv("PHONE_TUNNEL") or "auto").strip().lower()
    hints = []
    if mode in ("auto", "tailscale"):
        url, hint = tailscale(port)
        if url:
            return url, "tailscale", ""
        hints.append(hint)
    if mode in ("auto", "cloudflare"):
        url, hint = cloudflare(port)
        if url:
            return url, "cloudflare", ""
        hints.append(hint)
    return None, None, "; ".join(h for h in hints if h) or "туннель выключен (PHONE_TUNNEL=none)"


@atexit.register
def _cleanup():
    for p in _procs:
        try:
            p.kill()
        except Exception:
            pass
