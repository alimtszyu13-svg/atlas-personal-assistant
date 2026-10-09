"""
Компьютер ↔ телефон: экран, «продолжи на телефоне» и уведомления с компьютера.

    screen_to_phone()      → снимок экрана компьютера на телефон + что на нём написано (для ответа)
    continue_on_phone()    → открытая на компьютере страница (или документ) открывается на телефоне
    notify_phone(...)      → уведомление на телефон от компьютера (через облако)
    start_alerts()         → фоновые уведомления: загрузка закончилась, батарея садится — пока тебя нет

Ссылки и файлы идут тем же путём, что «пришли мне файл»: если просьба пришла с телефона — кнопкой в ответе,
иначе — уведомлением.
"""
import json
import os
import tempfile
import threading
import time
import urllib.request

from core import file_drop, worklog

ALERT_IDLE_S = 120             # ты отошёл от компьютера — тогда уведомления на телефон имеют смысл
_alert_state = {"battery_warned": False}


# ---------------------------------------------------------------------------
# Связь с облаком
# ---------------------------------------------------------------------------
def _cloud():
    url = (os.getenv("ATLAS_CLOUD_URL") or "").strip().rstrip("/")
    if not url.startswith("https://"):
        return None, None
    from phone import server
    return url, server.pairing_token()


def notify_phone(title: str, body: str, url: str = "/", poster=None) -> bool:
    """Уведомление на телефон от компьютера (облако рассылает его подписанным телефонам)."""
    base, key = _cloud()
    if not base or not key:
        try:                                           # без облака — сами, если есть подписка и ключи
            from core import push
            return push.notify(title, body, url=url) > 0
        except Exception:
            return False
    data = json.dumps({"title": title, "body": body, "url": url}).encode("utf-8")
    req = urllib.request.Request(base + "/api/pc/notify", data=data, method="POST",
                                 headers={"Content-Type": "application/json", "X-Atlas-Key": key})
    try:
        if poster:
            return poster(req)
        with urllib.request.urlopen(req, timeout=20) as r:
            return bool(json.loads(r.read()).get("sent"))
    except Exception as e:
        print(f"[телефон] уведомление не ушло: {e}")
        return False


def _link(url: str, label: str) -> str:
    """Ссылка на телефон: в ответе (просьба с телефона) или уведомлением."""
    if file_drop._job["phone"]:
        file_drop._job.setdefault("links", []).append((url, label))
        return "with an Open button in the phone app"
    base, _ = _cloud()
    if not base:                                       # телефон подключён к компьютеру напрямую
        from core import phone_actions
        phone_actions._pending.append({"label": label, "url": url})
        return "with an Open button in the phone app"
    notify_phone("Atlas · с компьютера", label, url=url)
    return "as a phone notification"


# ---------------------------------------------------------------------------
# Экран
# ---------------------------------------------------------------------------
def _screenshot(path: str) -> str:
    from PIL import ImageGrab
    img = ImageGrab.grab(all_screens=False)
    if img.width > 1600:
        img = img.resize((1600, int(img.height * 1600 / img.width)))
    img.convert("RGB").save(path, "JPEG", quality=80)
    return path


def _screen_text() -> str:
    try:
        from skills.screen import read_screen
        return read_screen()
    except Exception as e:
        return f"(screen text unavailable: {e})"


def screen_to_phone(grab=None, reader=None, sender=None) -> str:
    path = os.path.join(tempfile.mkdtemp(prefix="atlas_screen_"), time.strftime("Экран %H-%M.jpg"))
    try:
        (grab or _screenshot)(path)
    except Exception as e:
        return f"Couldn't take a screenshot: {e}"
    sent = (sender or file_drop.send)(path)
    text = (reader or _screen_text)()
    return f"{sent} What is on the screen: {text[:900]}"


# ---------------------------------------------------------------------------
# «Продолжи на телефоне»
# ---------------------------------------------------------------------------
def _active():
    try:
        app, title, _ = worklog._foreground()
        return app, title
    except Exception:
        return "", ""


def continue_on_phone(active=None, history=None, sender=None) -> str:
    """Страница из браузера → ссылка на телефон; документ → файл на телефон."""
    app, title = (active or _active)()
    if not title:
        return "Couldn't see what is open on the computer right now."
    low = (app or "").lower()
    if any(b in low for b in worklog._BROWSERS):
        page = title.rsplit(" - ", 1)[0].strip()
        now = time.time()
        rows = (history or worklog.browser_history)(now - 3600, now)
        url = next((r[3] for r in rows if r[2] and r[2].strip() == page), None) or \
            next((r[3] for r in rows if r[2] and page and (page in r[2] or r[2] in page)), None)
        if not url:
            return f"The page '{page}' is open, but its address wasn't found in the browser history."
        how = _link(url, page[:80] or worklog._site(url))
        return f"Opened '{page}' for the phone — it arrives {how}."
    doc = worklog.doc_of(title)
    if doc:
        return (sender or file_drop.send)(doc)
    return f"'{title[:80]}' ({app}) can't be moved to the phone — only web pages and documents can."


# ---------------------------------------------------------------------------
# Фоновые уведомления: загрузки и батарея
# ---------------------------------------------------------------------------
_PARTIAL = (".crdownload", ".part", ".tmp", ".download", ".partial")


def downloads_done(before: set, now_files: set) -> list:
    """Новые готовые файлы (без недокачанных)."""
    return sorted(f for f in now_files - before if not f.lower().endswith(_PARTIAL) and not f.startswith("."))


def battery_alert(percent, plugged) -> bool:
    if percent is None or plugged:
        _alert_state["battery_warned"] = False
        return False
    if percent <= 15 and not _alert_state["battery_warned"]:
        _alert_state["battery_warned"] = True
        return True
    return False


def start_alerts(every_s: int = 10) -> threading.Thread:
    if (os.getenv("PC_ALERTS") or "on").lower() in ("off", "0", "no", "нет"):
        return None
    from core import file_plans
    folder = file_plans.folder_path("downloads")

    def listing():
        try:
            return set(os.listdir(folder))
        except OSError:
            return set()

    def loop():
        seen = listing()
        while True:
            time.sleep(every_s)
            try:
                cur = listing()
                fresh = downloads_done(seen, cur)
                seen = cur
                away = worklog._idle() >= ALERT_IDLE_S
                if fresh and away:
                    names = ", ".join(fresh[:3]) + (f" и ещё {len(fresh) - 3}" if len(fresh) > 3 else "")
                    notify_phone("Atlas · загрузка готова", names)
                try:
                    import psutil
                    b = psutil.sensors_battery()
                    if b and battery_alert(b.percent, b.power_plugged) and away:
                        notify_phone("Atlas · компьютер разряжается", f"Осталось {int(b.percent)}% — поставь на зарядку.")
                except Exception:
                    pass
            except Exception as e:
                print(f"[уведомления ПК] {e}")
                time.sleep(60)
    t = threading.Thread(target=loop, daemon=True, name="pc-alerts")
    t.start()
    return t
