"""
Важные сообщения вслух: база уведомлений Windows (настоящий формат SQLite), кто важный, пачки сообщений,
тишина в фокусе, ответ голосом «скажи, что куплю» — без Windows, сети и окон.

    python tests/test_msg_watch.py
"""
import os
import sqlite3
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TMP = tempfile.mkdtemp(prefix="atlas_msgw_")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


TG = "TelegramDesktop.TelegramDesktop.Store"
WA = "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"


class FakeDB:
    """Та же схема, что у %LOCALAPPDATA%\\Microsoft\\Windows\\Notifications\\wpndatabase.db."""
    def __init__(self):
        self.path = os.path.join(TMP, f"wpn_{len(os.listdir(TMP))}.db")
        c = sqlite3.connect(self.path)
        c.execute("CREATE TABLE NotificationHandler (RecordId INTEGER PRIMARY KEY, PrimaryId TEXT, HandlerType TEXT)")
        c.execute("CREATE TABLE Notification (Id INTEGER PRIMARY KEY, HandlerId INTEGER, Type TEXT, Payload BLOB, "
                  "ArrivalTime INTEGER)")
        for rid, pid in ((1, TG), (2, WA), (3, "Microsoft.Windows.Explorer")):
            c.execute("INSERT INTO NotificationHandler VALUES (?, ?, 'app:desktop')", (rid, pid))
        c.commit()
        c.close()

    def push(self, handler: int, *texts, kind="toast"):
        xml = ('<toast launch="x"><visual><binding template="ToastGeneric">'
               + "".join(f"<text>{t}</text>" for t in texts) + "</binding></visual></toast>")
        c = sqlite3.connect(self.path)
        c.execute("INSERT INTO Notification (HandlerId, Type, Payload, ArrivalTime) VALUES (?, ?, ?, 0)",
                  (handler, kind, xml.encode("utf-8")))
        c.commit()
        c.close()


class Env:
    def __init__(self):
        self.t, self.busy_, self.quiet_, self.looking, self.listened = 1000.0, False, False, set(), 0
        self.call_ = False

    def in_call(self):
        return self.call_

    def busy(self):
        return self.busy_

    def quiet(self):
        return self.quiet_

    def looking_at(self, app):
        return app in self.looking

    def listen_now(self):
        self.listened += 1

    def now(self):
        return self.t


def fresh(db=None):
    from core import messengers as M, msg_watch as W
    W.CONF = os.path.join(TMP, f"conf_{len(os.listdir(TMP))}.json")
    M.FILE = os.path.join(TMP, f"msg_{len(os.listdir(TMP))}.json")
    M._s.update(pending=None, said=None, incoming=None)
    W._s.update(last_id=None, queue={}, last_said={}, missed=[], src=None, env=None, seen={}, calls=[], log=[])
    from core import call_secretary as C
    C._s.update(ring=None, hint=None, active=None, announced={})
    C._ru = lambda: True
    W._ru = lambda: True
    db = db or FakeDB()
    return W, M, db, W.Source(db.path), Env()


@test
def reads_windows_notification_database():
    W, M, db, src, env = fresh()
    db.push(1, "Мама ❤", "Купи хлеб")
    db.push(3, "Загрузки", "Файл скачан")
    rows = src.since(0)
    assert [(a, t) for _, a, t in rows] == [("telegram", ["Мама ❤", "Купи хлеб"]), (None, ["Загрузки", "Файл скачан"])], rows
    assert W.split_message(["Мама", "Купи хлеб"]) == ("Мама", "Купи хлеб", False)
    assert W.split_message(["Класс", "Дана", "завтра ко второму"]) == ("Класс", "Дана: завтра ко второму", True)
    assert W.split_message(["Мама", "Купи хлеб.", "И молоко"])[2] is False, "две фразы — не группа"


@test
def announces_only_important_new_messages_in_batches():
    W, M, db, src, env = fresh()
    said = []
    db.push(1, "Мама ❤", "старое, до запуска")
    assert W.tick(src, env, said.append) == "" and W._s["last_id"] == 1, "первый проход — только запомнить, где мы"
    db.push(1, "Мама ❤", "Купи хлеб")
    db.push(2, "Магазин техники", "Скидки до 50%!")                  # не важный
    db.push(1, "Мама ❤", "И молоко")
    assert W.tick(src, env, said.append) == "", "ждём, не придёт ли продолжение"
    env.t += 5
    out = W.tick(src, env, said.append)
    assert out == "Сэр, мама пишет в Telegram: «Купи хлеб» — и ещё одно сообщение: «И молоко»", out
    assert env.listened == 1, "после объявления — сразу слушаем ответ"
    assert M._s["incoming"]["title"] == "Мама ❤" and M._s["incoming"]["app"] == "telegram"
    env.t += 30
    assert W.tick(src, env, said.append) == "" and len(said) == 1, "магазин так и не объявили"


@test
def important_list_and_commands():
    W, M, db, src, env = fresh()
    assert not W.important("Алим Сыдыков", "Завтра в 7?")
    assert W.handle("сообщай о сообщениях от Алима") == "Запомнил: о сообщениях от «Алима» буду говорить сразу."
    assert W.important("Алим Сыдыков", "Завтра в 7?")
    assert W.important("Мама", "?") and W.important("Папа 👨", "?"), "родные — важные всегда"
    assert not W.important("Мама Данияра", "?"), "мама друга — не твоя мама"
    W.handle("не сообщай о сообщениях от Алима")
    assert not W.important("Алим Сыдыков", "Завтра в 7?")
    W.handle("сообщай обо всех сообщениях")
    assert W.important("Дана", "Привет") and not W.important("Алим Сыдыков", "?"), "все, кроме заглушённых"
    assert W.handle("не сообщай о сообщениях").startswith("Хорошо, о сообщениях молчу")
    assert not W.important("Мама", "Купи хлеб")
    W.handle("включи уведомления о сообщениях")
    assert W.settings()["mode"] == "important"
    W.handle("добавь срочно в важные")
    assert W.important("Группа класса", "Срочно: завтра контрольная"), "слово-маркер в тексте"
    M._remember("Дане", "telegram", "Дана Кадырова")
    assert W.important("Дана Кадырова", "привет"), "кому писал через Atlas — важные"
    assert M.intent("сообщай о сообщениях от Даны") == ("watch",)
    assert W.intent("что я пропустил?") == ("missed",)
    for other in ("сообщай погоду", "напиши маме что скоро буду", "какая погода"):
        assert W.intent(other) is None, other


@test
def quiet_while_focused_then_summary():
    W, M, db, src, env = fresh()
    said = []
    W.tick(src, env, said.append)
    env.quiet_ = True
    db.push(1, "Мама", "Ты где?")
    W.tick(src, env, said.append)
    env.t += 5
    assert W.tick(src, env, said.append) == "" and not said, "в фокусе — молчим"
    env.quiet_ = False
    env.t += 1
    out = W.tick(src, env, said.append)
    assert out == "Пока ты был занят, писали: мама — «Ты где?».", out
    assert M._s["incoming"]["title"] == "Мама", "пропущенное от одного чата — тоже можно ответить"


@test
def skips_when_looking_at_messenger_or_busy():
    W, M, db, src, env = fresh()
    said = []
    W.tick(src, env, said.append)
    env.looking = {"telegram"}
    db.push(1, "Мама", "Ты где?")
    env.t += 5
    W.tick(src, env, said.append)
    assert not said and not W._s["queue"], "ты и так смотришь в Telegram"
    env.looking = set()
    env.busy_ = True
    db.push(2, "Мама", "Позвони")
    W.tick(src, env, said.append)
    env.t += 5
    assert W.tick(src, env, said.append) == "", "Atlas говорит — подождём"
    env.busy_ = False
    assert W.tick(src, env, said.append) == "Сэр, мама пишет в WhatsApp: «Позвони»"


@test
def long_message_is_cut():
    W, M, db, src, env = fresh()
    text = "Слушай, " + "очень длинное сообщение " * 20
    out = W.line("telegram", "Алим Сыдыков", [text], True)
    assert out.startswith("Сэр, Алим Сыдыков пишет в Telegram: «Слушай, очень") and out.endswith("…»") and len(out) < 230, out


@test
def reply_by_voice_right_after_announcement():
    W, M, db, src, env = fresh()
    from tests.test_pc_power import FakeTG
    said = []
    W.tick(src, env, said.append)
    db.push(1, "Мама ❤", "Купи хлеб")
    W.tick(src, env, said.append)
    env.t += 5
    assert W.tick(src, env, said.append) == "Сэр, мама пишет в Telegram: «Купи хлеб»"
    import time
    M._s["incoming"]["ts"] = time.time()
    assert M.intent("скажи, что куплю") == ("reply_incoming", "куплю", None)
    assert M.intent("ответь: буду в 7") == ("reply_incoming", "", "буду в 7")
    assert M.intent("напиши Алиму, что буду в 7")[0] == "reply", "другой адресат — обычная команда"
    ui = FakeTG()
    out = M.handle("скажи, что куплю", ui=ui, ask=lambda s, u, max_tokens=0: "Куплю!")
    assert ui.open == "Мама ❤" and out == "Пишу в Telegram, чат «Мама ❤»: «Куплю!». Отправить?", (ui.open, out)
    M.handle("да", last=out, ui=ui)
    assert ui.sent == [("Мама ❤", "Куплю!")], ui.sent
    M._s["incoming"] = {"app": "telegram", "title": "Мама ❤", "ts": time.time() - 600}
    assert M.intent("скажи, что куплю") is None, "через 10 минут «скажи, что…» уже не про маму"


def _started():
    W, M, db, src, env = fresh()
    said = []
    W.tick(src, env, said.append)
    return W, M, db, src, env, said


def _settle(W, src, env, said):
    W.tick(src, env, said.append)
    env.t += 5
    return W.tick(src, env, said.append)


@test
def call_toast_goes_to_secretary_not_read_as_message():
    W, M, db, src, env, said = _started()
    from core import call_secretary as C
    db.push(2, "Мама ❤", "Входящий аудиозвонок")
    assert _settle(W, src, env, said) == "" and not said, "звонок — не сообщение"
    assert C._s["hint"]["caller"] == "Мама ❤" and C._s["hint"]["app"] == "WhatsApp"


@test
def silent_during_call_then_summary():
    W, M, db, src, env, said = _started()
    env.call_ = True
    db.push(1, "Папа", "Перезвони, как освободишься")
    assert _settle(W, src, env, said) == "" and not said, "во время звонка — молчим"
    env.call_ = False
    env.t += 1
    assert W.tick(src, env, said.append) == "Пока ты был занят, писали: папа — «Перезвони, как освободишься».", said


@test
def missed_call_announced_once():
    W, M, db, src, env, said = _started()
    db.push(2, "Мама ❤", "Пропущенный аудиозвонок")
    assert W.tick(src, env, said.append) == "Сэр, пропущенный звонок от мамы в WhatsApp.", said
    assert M._s["incoming"]["title"] == "Мама ❤", "можно сразу ответить: «напиши, что перезвоню»"
    from core import call_secretary as C
    C._s["announced"]["мама"] = env.t                                   # секретарь только что объявил звонок
    import time
    C._s["announced"]["мама"] = time.time()
    db.push(2, "Мама ❤", "Пропущенный аудиозвонок")
    env.t += 100
    assert W.tick(src, env, said.append) == "", "про этот звонок уже сказали — «пропущенный» не повторяем"
    db.push(2, "Магазин", "Missed voice call")
    env.t += 100
    assert W.tick(src, env, said.append) == "", "пропущенный от неважного — молчим"


@test
def duplicates_hidden_previews_and_db_reset():
    W, M, db, src, env, said = _started()
    db.push(1, "Мама", "Купи хлеб")
    db.push(1, "Мама", "Купи хлеб")                                      # Windows показал дважды
    assert _settle(W, src, env, said) == "Сэр, мама пишет в Telegram: «Купи хлеб»", said
    env.t += 100
    db.push(1, "Мама", "Новое сообщение")                                # текст скрыт в настройках Telegram
    db.push(1, "Telegram", "У вас 3 новых сообщения")
    env.t += 100
    assert _settle(W, src, env, said) == "", "нечего читать — молчим, а не «Новое сообщение»"
    db2 = FakeDB()                                                       # Windows очистил базу: номера с нуля
    W._s["src"] = src2 = W.Source(db2.path)
    db2.push(3, "Загрузки", "Файл скачан")
    W.tick(src2, env, said.append)                                       # тут и замечаем сброс (проход раз в 2 с)
    db2.push(1, "Мама", "Ты где?")
    env.t += 100
    assert _settle(W, src2, env, said) == "Сэр, мама пишет в Telegram: «Ты где?»", "после сброса базы не оглохли"


@test
def group_message_names_sender():
    W, M, db, src, env, said = _started()
    W.handle("сообщай о сообщениях от Класса")
    db.push(1, "Класс", "Дана", "Завтра ко второму уроку")
    assert _settle(W, src, env, said) == "Сэр, в «Класс» Дана пишет: «Завтра ко второму уроку»", said



@test
def who_wrote_me_from_notifications():
    W, M, db, src, env, said = _started()
    W._s["log"] = []
    db.push(1, "Магазин техники", "Скидки до 50%!")
    db.push(2, "Алибек", "ок спасибо")
    db.push(1, "Мама ❤", "Купи хлеб")
    _settle(W, src, env, said)
    assert W.intent("Мне кто-то что-то писал.") == ("recent",) and W.intent("есть новые сообщения?") == ("recent",)
    out = W.handle("Мне кто-то что-то писал?")
    assert out.startswith("Писали: мама в Telegram — «Купи хлеб»") and "Алибек в WhatsApp — «ок спасибо»" in out, out
    assert "только что" in out
    assert W.app_of("папа") is None and W.app_of("Алибек") == "whatsapp"
    assert W.recent_line("whatsapp", True).startswith("Писали: Алибек — «ок спасибо»")
    W._s["log"] = []
    assert W.handle("кто мне писал?") == "Никто не писал — новых уведомлений не было."


def main():
    ok = 0
    for t in TESTS:
        try:
            t()
            ok += 1
            print(f"  ✓ {t.__name__}")
        except Exception:
            print(f"  ✗ {t.__name__}")
            traceback.print_exc()
    print(f"\nТестов пройдено: {ok} из {len(TESTS)}")
    sys.stdout.flush()
    os._exit(0 if ok == len(TESTS) else 1)


if __name__ == "__main__":
    main()
