"""
Живая проверка Atlas на ЭТОМ компьютере — без голоса и без действий.

    python tests/live_check.py        (или в VS Code: Run Python File)

Только смотрит и измеряет: ничего не отправляет, не кликает, не удаляет и не пишет в настройки.
Результат — на экран и в logs/live_check.txt (его можно прислать / прочитать целиком).

Что проверяется (то, чего нет в doctor.py):
  • база уведомлений Windows — видит ли Atlas сообщения Telegram/WhatsApp (для «кто мне писал?»);
  • окна мессенджеров — читаются ли имена чатов из структуры окна, где граница панелей, есть ли чат с собой;
  • поиск файлов по имени — сколько занимает (раньше до 35 с);
  • синтез речи — сколько ждать первого звука на коротком и длинном куске (без воспроизведения);
  • календарь и почта — работает ли вход Google (без окна входа и без записи токена).
"""
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
try:
    from core import vault
    vault.load()
except Exception:
    pass
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

LINES = []


def out(s=""):
    print(s, flush=True)
    LINES.append(s)


def timed(name, fn, timeout=40):
    box = {}

    def run():
        try:
            box["r"] = fn()
        except Exception as e:
            box["e"] = f"{type(e).__name__}: {str(e)[:200]}"
    t0 = time.time()
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout)
    dt = time.time() - t0
    if th.is_alive():
        out(f"  ✗ {name}: не ответило за {timeout} с")
        return None
    if "e" in box:
        out(f"  ✗ {name}: {box['e']}  ({dt:.1f} с)")
        return None
    r = box.get("r")
    out(f"  ✓ {name}: {r}  ({dt:.1f} с)" if not isinstance(r, tuple) else f"  {r[0]} {name}: {r[1]}  ({dt:.1f} с)")
    return r


# --- 1. уведомления --------------------------------------------------------------------------------
def notifications():
    from core import msg_watch as W
    src = W.Source()
    top = src.last_id()
    rows = src.recent(72)
    by = {}
    for _id, app, texts, _at in rows:
        by[app] = by.get(app, 0) + 1
    hidden = sum(1 for _i, _a, t, _t in rows if W.classify(*(W.split_message(t)[:2])) == "hidden")
    return (("✓" if rows else "⚠"),
            f"база читается, последний номер {top}; за 3 дня уведомлений мессенджеров: {by or 'нет'}"
            + (f"; без текста (скрыт настройками): {hidden}" if hidden else ""))


# --- 2. окна мессенджеров ----------------------------------------------------------------------------
def messenger(app):
    from core import messengers as M
    ui = M.UI()
    w = M.find_window(app, ui.windows())
    if not w:
        return ("·", "окно не открыто — пропускаю")
    t0 = time.time()
    uia = ui.uia_lines(w["hwnd"], budget=6)
    t_uia = time.time() - t0
    rect = ui.rect(w["hwnd"])
    rg = M._regions(rect, w.get("web", False), w["hwnd"])
    div = M._DIV.get(w["hwnd"])
    in_list = [ln for ln in uia if M._cx(ln) < rg["list_x"] and ln["y"] > rg["top"]]
    names = list(dict.fromkeys(ln["text"] for ln in in_list if len(ln["text"].split()) <= 4))
    head = [ln["text"] for ln in M._header(uia, rg) if len(ln["text"]) < 40][:3]
    selfchat = any(M._SELF_CHAT.search(n) for n in names)
    kin = [n for n in names if M.is_kin(n)]
    conv = M._uia_conv(uia, rg, rect) if uia else []          # открытый чат: сколько сообщений прочиталось (без текста)
    mine = sum(1 for w_, _t in conv if w_ == "me")
    ok = "✓" if len(names) >= 3 else "⚠"
    return (ok, f"{'браузер' if w.get('web') else 'приложение'}; структура окна: {len(uia)} элементов за {t_uia:.1f} с; "
                f"граница панелей: {div or 'не найдена (доли окна)'}; имён в списке: {len(names)}; "
                f"родные в списке: {kin[:4]}; чат с собой: {'есть' if selfchat else 'не виден'}; заголовок: {head}; "
                f"переписка открытого чата по структуре: {len(conv)} сообщ. (своих {mine}, чужих {len(conv) - mine})")


# --- 3. поиск файлов -----------------------------------------------------------------------------------
def find(name):
    import file_control as F
    t0 = time.time()
    p = F.find_file(name)
    return ("✓" if time.time() - t0 < 13 else "✗", f"{'нашёл ' + p if p else 'не нашёл'}")


# --- 4. синтез речи ------------------------------------------------------------------------------------
def tts(text):
    import voice
    voice._response_language["lang"] = "ru"
    fn = os.path.join(ROOT, f"temp_live_check{time.time_ns()}.mp3")
    t0 = time.time()
    real = voice._generate_any(text, fn)
    dt = time.time() - t0
    for f in {fn, real}:
        try:
            if f and os.path.exists(f):
                os.remove(f)
        except Exception:
            pass
    return ("✓" if dt < 1.5 else "⚠", f"{len(text)} символов → {dt:.2f} с до готового звука")


# --- 5. Google: календарь и почта (без окна входа и без записи token.json) ---------------------------
def google():
    if not os.path.exists("token.json"):
        return ("⚠", "token.json нет — календарь и почта не подключены")
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    import google_auth
    creds = Credentials.from_authorized_user_file("token.json", google_auth.SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())            # только в памяти — файл не трогаем
            except Exception as e:
                return ("✗", f"вход Google истёк ({str(e)[:80]}) — нужен новый вход: скажи «что у меня в календаре» при ПК")
        else:
            return ("✗", "вход Google недействителен — нужен новый вход")
    from googleapiclient.discovery import build
    t0 = time.time()
    cal = build("calendar", "v3", credentials=creds, cache_discovery=False)
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    ev = cal.events().list(calendarId="primary", timeMin=now.isoformat(), timeMax=(now + timedelta(days=7)).isoformat(),
                           singleEvents=True, maxResults=5).execute().get("items", [])   # как сам Atlas
    t_cal = time.time() - t0
    t0 = time.time()
    gm = build("gmail", "v1", credentials=creds, cache_discovery=False)
    lbl = gm.users().labels().get(userId="me", id="INBOX").execute()
    t_mail = time.time() - t0
    return ("✓", f"календарь отвечает за {t_cal:.1f} с (событий на неделю: {len(ev)}); почта за {t_mail:.1f} с, непрочитанных во входящих: "
                 f"{lbl.get('messagesUnread', '?')}")


def main():
    out(f"Живая проверка Atlas · {time.strftime('%Y-%m-%d %H:%M')} · Python {sys.version.split()[0]}")
    out("")
    out("Уведомления (для «кто мне писал?» и объявлений):")
    timed("база уведомлений Windows", notifications, 20)
    out("")
    out("Окна мессенджеров (только чтение, без кликов):")
    time.sleep(float(os.getenv("LIVE_CHECK_WAIT", "25")))   # если запускали через управление экраном — окна возвращаются
    for app in ("whatsapp", "telegram"):
        timed(app, lambda a=app: messenger(a), 30)
    out("")
    out("Поиск файла по имени (раньше — до 35 с):")
    timed("requirements.txt", lambda: find("requirements.txt"), 30)
    timed("несуществующий файл (худший случай)", lambda: find("такого_файла_точно_нет_123.docx"), 30)
    out("")
    out("Синтез речи (без воспроизведения):")
    timed("короткий кусок", lambda: tts("Готово, сэр."), 40)
    timed("средний кусок", lambda: tts("Мысль записана: дедлайн курсовой двадцатого ноября."), 40)
    timed("длинный кусок", lambda: tts("Мысль записана: дедлайн курсовой двадцатого ноября, идея сделать "
                                       "видео про проект, и ещё напомнить завтра утром про встречу."), 40)
    out("")
    out("Календарь и почта:")
    timed("Google", google, 30)
    out("")
    path = os.path.join(ROOT, "logs", "live_check.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(LINES))
    out(f"Сохранено: {path}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
