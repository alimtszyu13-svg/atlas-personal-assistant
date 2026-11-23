"""
Телефонная книга и открытие чата ПО НОМЕРУ (whatsapp://send?phone=…, tg://resolve?phone=…) вместо поиска на экране.

    python tests/test_contacts.py
"""
import os
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ATLAS_TESTING", "1")
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TMP = tempfile.mkdtemp(prefix="atlas_contacts_")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class FakeWA:
    """Окно WhatsApp: список чатов слева, заголовок открытого чата справа. Поиск можно «сломать» —
    как в логе 24.10, когда запрос не попадал в строку поиска."""

    def __init__(self, chats, phones, search_works=True, links_work=True, visible=3):
        self.chats, self.phones = chats, phones          # имя чата → [(кто, текст)], номер → имя чата
        self.search_works, self.links_work, self.visible = search_works, links_work, visible
        self.open, self.query, self.focused, self.typed = None, None, False, ""
        self.sent, self.urls, self.log, self.fg = [], [], [], 0

    def windows(self):
        return [{"hwnd": 5, "app": "WhatsApp.Root", "title": "WhatsApp", "min": False, "max": False,
                 "rect": (0, 0, 1000, 800)}]

    def launch(self, app):
        pass

    def open_url(self, url):
        from urllib.parse import urlparse, parse_qs
        self.urls.append(url)
        if self.links_work:
            q = parse_qs(urlparse(url).query)
            num = (q.get("phone") or [""])[0]
            if num in self.phones:
                self.open, self.query = self.phones[num], None
                self.typed = (q.get("text") or [""])[0]           # WhatsApp сам кладёт текст из ссылки в поле

    def activate(self, h):
        self.fg = h
        return True

    def foreground(self):
        return self.fg

    def rect(self, h):
        return (0, 0, 1000, 800)

    def ocr(self, rect):
        from core import messengers as M
        lines = [{"text": self.query or "Поиск", "x": 20, "y": 70, "w": 120, "h": 20}]
        names = list(self.chats)[:self.visible]
        if self.query is not None:
            st = M.stems(self.query) or [self.query.lower()]
            names = [n for n in self.chats if M.line_matches(n, st)]
        for i, n in enumerate(names):
            lines.append({"text": n, "x": 70, "y": 120 + i * 70, "w": 150, "h": 20})
        if self.open:
            lines.append({"text": self.open, "x": 470, "y": 30, "w": 140, "h": 22})
            for i, (who, t) in enumerate(self.chats[self.open][-6:]):
                lines.append({"text": t, "x": 760 if who == "me" else 480, "y": 200 + i * 60, "w": 180, "h": 20})
        return {"ru": lines, "en": []}

    def click(self, x, y):
        self.log.append(("click", x, y))
        if x < 450 and y > 100:
            hit = next((ln for ln in self.ocr(None)["ru"] if ln["text"] in self.chats
                        and ln["x"] <= x <= ln["x"] + ln["w"] and ln["y"] <= y <= ln["y"] + ln["h"]), None)
            if hit:
                self.open, self.query = hit["text"], None
        elif x < 450:
            self.focused = True

    def keys(self, combo):
        self.log.append(("keys", combo))
        if combo == "ctrl+f":
            self.focused = True
        if combo == "enter" and self.typed and self.open:
            self.chats[self.open].append(("me", self.typed))
            self.sent.append((self.open, self.typed))
            self.typed = ""

    def paste(self, text):
        self.log.append(("paste", text))
        if self.focused and not self.open:
            if self.search_works:
                self.query = text
            self.focused = False
        elif self.open:
            self.typed += text

    def focus_edit(self, h, rx):
        if "search" in rx:
            self.focused, self.open = True, None
        return "search" in rx

    def sleep(self, s):
        pass


def _fresh():
    from core import messengers as M, contacts as C
    M.FILE = os.path.join(TMP, "messengers.json")
    C.FILE = os.path.join(TMP, "contacts.json")
    for f in (M.FILE, C.FILE):
        if os.path.exists(f):
            os.remove(f)
    C._cache.update(data=None, mtime=None)
    M._s.update(pending=None, said=None, last_sent=None, incoming=None)
    return M, C


def _ask(system, user, max_tokens=0):
    return "Привет!"


CHATS = lambda: {"Папа": [("them", "Ок")], "Мама": [("them", "Купи хлеб")], "Алибек": [("them", "Никто не знает")],
                 "Альтаир": [("them", "хз")], "Назар Т": [("them", "Здарова")]}


@test
def phone_numbers_normalized():
    _, C = _fresh()
    assert C.normalize_phone("+996 (707) 05-08-09") == "996707050809"
    assert C.normalize_phone("0707 050 809") == "996707050809"
    assert C.normalize_phone("707050809") == "996707050809"
    assert C.normalize_phone("00996707050809") == "996707050809"
    assert C.normalize_phone("+1 415 555 0100") == "14155550100"
    assert C.normalize_phone("123") == "" and C.normalize_phone("") == ""


@test
def vcf_and_voice_add_and_lookup():
    M, C = _fresh()
    vcf = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Назар Т\r\nTEL;TYPE=CELL:+996 707 111 222\r\nEND:VCARD\r\n"
           "BEGIN:VCARD\r\nN:Иванов;Петр;;;\r\nTEL:0555 123 456\r\nEND:VCARD\r\n"
           "BEGIN:VCARD\r\nFN:Без номера\r\nEND:VCARD\r\n")
    assert C._merge(C.parse_vcf(vcf), "vcf") == 2
    assert C.lookup("Назару") == ("one", "Назар Т", "996707111222")
    assert C.lookup("Петру") == ("one", "Петр Иванов", "996555123456")
    assert C.lookup("Бекзату") is None
    assert C.intent("запомни номер мамы 0700 123 456") == ("add", "Мама", "0700 123 456")
    assert "Мама" in M.handle("запомни номер мамы 0700 123 456")
    assert C.lookup("маме") == ("one", "Мама", "996700123456")
    assert C.lookup("Мадине") is None, "«маме» ≠ «Мадина» и наоборот"
    C.add("Назар Б", "0777 000 111")
    assert C.lookup("Назару")[0] == "many", "два Назара — не угадываем, ищем на экране и спрашиваем"
    assert C._merge(C.parse_vcf(vcf), "vcf") == 2 and len(C.people()) == 4, "повторный импорт не плодит дубли"
    assert M.intent("Атлас, обнови контакты") == ("contacts",)


@test
def whatsapp_opens_by_number_when_search_is_broken():
    """Лог 24.10: строка поиска не получала текст. По номеру поиск вообще не нужен."""
    M, C = _fresh()
    C.add("Назар", "0707 111 222")
    ui = FakeWA(CHATS(), {"996707111222": "Назар Т"}, search_works=False)
    said = M.handle("напиши Назару привет в ватсапе", ui=ui, ask=_ask)
    assert ui.urls == ["whatsapp://send?phone=996707111222"], ui.urls
    assert said.startswith("Пишу в WhatsApp, чат «Назар Т»") and said.endswith("Отправить?"), said
    assert not any(k == ("keys", "ctrl+f") for k in ui.log), "поиск на экране не трогали"
    out = M.handle("да", last=said, ui=ui)
    assert ui.sent == [("Назар Т", "Привет!")] and out == "Отправил.", (ui.sent, out)
    # живой тест 29.10: вставка через буфер ни разу не дошла до поля — текст теперь едет в самой ссылке
    assert ui.urls[-1] == "whatsapp://send?phone=996707111222&text=%D0%9F%D1%80%D0%B8%D0%B2%D0%B5%D1%82%21", ui.urls
    assert not any(e[0] == "paste" for e in ui.log), ui.log


@test
def number_link_failed_falls_back_to_screen_search():
    M, C = _fresh()
    C.add("Назар", "0707 111 222")
    ui = FakeWA(CHATS(), {"996707111222": "Назар Т"}, links_work=False)
    said = M.handle("напиши Назару привет в ватсапе", ui=ui, ask=_ask)
    assert ui.urls and ui.open == "Назар Т" and said.endswith("Отправить?"), (said, ui.log)


@test
def no_phonebook_works_as_before():
    M, C = _fresh()
    ui = FakeWA(CHATS(), {}, visible=5)
    said = M.handle("напиши Альтаиру привет в ватсапе", ui=ui, ask=_ask)
    assert not ui.urls and ui.open == "Альтаир" and said.endswith("Отправить?"), said


@test
def log_phrases_2410():
    M, _ = _fresh()
    assert M.intent("Открой WhatsApp и напиши контакту «Обэй привет».") == ("reply", "whatsapp", "Обэй", "", "привет")
    assert M.intent("напиши контакту Назару: буду в 7")[2] == "Назару"
    assert M.intent("Мне самому напиши привет.")[2:4] == ("себе", "привет")
    assert M.intent("Открой видео с Витей на YouTube.") is None, "это YouTube, а не чат в Discord"
    assert M.intent("открой чат с папой")[0] == "open"
    M._s["pending"] = {"need_app": True, "contact": "себе", "gist": "привет", "exact": None, "created": 1e12, "ru": True}
    M._s["said"] = "В каком мессенджере?"
    assert M.intent("имею ввиду ватсапе") == ("pick_app", "whatsapp")


@test
def focus_stolen_once_still_sends_and_header_is_clean():
    """Живой тест 28.10: «Окно мессенджера ушло с экрана» дважды; имя чата «Назар нажмите здесь, чтобы…»."""
    M, C = _fresh()
    C.add("Назар", "0707 111 222")
    ui = FakeWA(CHATS(), {"996707111222": "Назар Т"}, search_works=False)
    real_ocr = ui.ocr

    def ocr(rect):                                         # WhatsApp подписывает заголовок-кнопку
        res = real_ocr(rect)
        for ln in res["ru"]:
            if ln["x"] == 470:
                ln["text"] += " нажмите здесь, чтобы посмотреть данные контакта"
        return res
    ui.ocr = ocr
    said = M.handle("напиши Назару привет в ватсапе", ui=ui, ask=_ask)
    assert "чат «Назар Т»" in said and "нажмите" not in said, said
    steals = {"n": 2}
    real_fg = ui.foreground

    def foreground():                                      # интерфейс Atlas дважды забирает фокус после «да»
        if steals["n"] > 0:
            steals["n"] -= 1
            return 77
        return real_fg()
    ui.foreground = foreground
    out = M.handle("да", last=said, ui=ui)
    assert out == "Отправил." and ui.sent == [("Назар Т", "Привет!")], (out, ui.sent)


# строки из настоящего снимка структуры окна WhatsApp (logs/whatsapp_probe.txt) + входящие без статуса
_PROBE = """ButtonControl 463 45 1335 37 Назар нажмите здесь, чтобы посмотреть данные контакта
TextControl 1134 129 41 14 суббота
ButtonControl 1604 160 217 289 Открыть изображение
ButtonControl 1772 432 45 14 13:12 Прочитано
TextControl 1772 431 28 14 13:12
TextControl 1340 459 151 16 Мотивационное письмо
TextControl 1340 495 478 16 Максим горький - русский писатель
ButtonControl 1773 593 45 14 14:22 Прочитано
TextControl 1773 592 28 14 14:22
TextControl 1127 640 56 14 Сегодня
TextControl 480 680 120 16 Понял
TextControl 610 682 28 14 22:16
TextControl 480 720 200 16 Пересланное сообщение
TextControl 480 740 220 16 https://youtube.com/shorts/x
HyperlinkControl 480 740 220 16 https://youtube.com/shorts/x
TextControl 710 742 28 14 22:20
TextControl 1430 800 339 16 Теперь играю на игровом пк
ButtonControl 1773 808 45 15 22:21 Доставлено
TextControl 1773 808 28 14 22:21"""


@test
def chat_read_from_window_structure():
    """Кто что написал — по статусу «Прочитано/Доставлено» у своих сообщений, текст — дословно, без OCR."""
    from core import messengers as M
    items = []
    for row in _PROBE.splitlines():
        typ, x, y, w, h, text = row.split(" ", 5)
        items.append({"uia": typ, "x": int(x), "y": int(y), "w": int(w), "h": int(h), "text": text})
    rect = (-8, -8, 1928, 1040)
    M._DIV[4242] = 410
    conv = M._uia_conv(items, M._regions(rect, False, 4242), rect)
    assert conv == [("me", "[фото] Мотивационное письмо Максим горький - русский писатель"),
                    ("them", "Понял https://youtube.com/shorts/x"),
                    ("me", "Теперь играю на игровом пк")], conv


@test
def short_texts_go_without_model():
    from core import messengers as M
    assert M._plain("привет") == "Привет!" and M._plain("буду в 7") == "Буду в 7"
    assert M._plain("тест АТЛАС 1 попытка 27") is None, "длинное — через модель"
    for g in ("он молодец", "спроси когда сдавать", "что опоздаю", "поздравь с днём рождения", ""):
        assert M._plain(g) is None, g
    M2, C = _fresh()
    C.add("Назар", "0707 111 222")
    ui, calls = FakeWA(CHATS(), {"996707111222": "Назар Т"}), []
    said = M2.handle("напиши Назару привет в ватсапе", ui=ui, ask=lambda s, u, max_tokens=0: calls.append(u) or "x")
    assert said.endswith("«Привет!». Отправить?") and not calls, (said, calls)
    said = M2.handle("сделай вежливее", last=said, ui=ui, ask=lambda s, u, max_tokens=0: calls.append(u) or "Здравствуй!")
    assert said.endswith("«Здравствуй!». Отправить?") and calls, said


@test
def write_to_myself_by_own_number_2910():
    """Живой тест 29.10: «напиши мне привет» → WhatsApp → «чат с собой не найден» (окно не прогрузилось).
    Свой номер известен — чат с собой открывается по нему. И фраза с номером — не «своя команда»."""
    M, C = _fresh()
    from core import aliases as A
    said_num = "когда я говорю напиши мне, я имею ввиду контакт с номером +996707050809, запомни"
    assert A.parse_add(said_num) is None, "раньше записалось как команда «напиши мне» → мусор во всех «напиши мне…»"
    assert M.intent(said_num) == ("contacts",)
    assert "+996707050809" in M.handle(said_num) and C.self_phone() == "996707050809"
    assert C.intent("его номер 0555 123 456") is None, "чужой номер без имени — не свой"
    chats = dict(CHATS(), **{"+996 707 050 809 (Вы)": [("me", "заметка")]})
    ui = FakeWA(chats, {"996707050809": "+996 707 050 809 (Вы)"}, search_works=False)
    said = M.handle("напиши мне привет в ватсапе", ui=ui, ask=_ask)
    assert ui.urls == ["whatsapp://send?phone=996707050809"] and said.endswith("«Привет!». Отправить?"), (said, ui.urls)


@test
def own_number_learned_from_window():
    M, C = _fresh()
    chats = dict(CHATS(), **{"+996 707 050 809 (Вы)": [("me", "заметка")]})
    chats = {"+996 707 050 809 (Вы)": chats.pop("+996 707 050 809 (Вы)"), **chats}
    ui = FakeWA(chats, {}, visible=6)
    said = M.handle("напиши себе тест в ватсапе", ui=ui, ask=_ask)
    assert said.endswith("Отправить?") and C.self_phone() == "996707050809", (said, C.self_phone())


@test
def lowercase_name_from_phonebook_and_whatsapp_by_default():
    """«Напиши назару привет, это Атлас» уходило в модель, а та открывала Telegram, которого нет."""
    M, C = _fresh()
    C.add("Назар", "0707 111 222")
    assert M.intent("Напиши назару привет, это Атлас") == ("reply", None, "назару", "привет, это Атлас", None)
    assert M.intent("напиши бекзату привет") is None, "нет в книге и с маленькой — не угадываем"

    class NoWindows:
        def windows(self):
            return []
    assert M._resolve_app("назару", NoWindows()) == "whatsapp"
    assert M._resolve_app("бекзату", NoWindows()) is None


@test
def waits_for_whatsapp_to_load_2910():
    """«Пусть ждёт загрузки WhatsApp, а не отказывает сразу»: сначала в окне 14 элементов, потом список чатов."""
    from core import messengers as M

    class Loading:
        def __init__(self):
            self.calls, self.slept = 0, 0.0

        def uia_lines(self, hwnd, budget=2.5):
            self.calls += 1
            if self.calls < 4:                                     # первые три взгляда — WhatsApp ещё пустой
                return [{"text": "WhatsApp", "x": 40, "y": 9, "w": 50, "h": 14, "uia": "TextControl"}]
            return [{"text": n, "x": 58, "y": 180 + i * 68, "w": 332, "h": 66, "uia": "DataItemControl"}
                    for i, n in enumerate(("Папа", "Мама", "Назар", "Алибек"))]

        def rect(self, h):
            return (0, 0, 1900, 1040)

        def activate(self, h):
            return True

        def sleep(self, s):
            self.slept += s
    ui = Loading()
    M._READY.clear()
    assert M._wait_ready("whatsapp", ui, {"hwnd": 31, "web": False}) is True and ui.calls == 4, ui.calls
    assert M._wait_ready("whatsapp", ui, {"hwnd": 31, "web": False}) and ui.calls == 4, "только что был готов — не ждём"

    class Never(Loading):
        def uia_lines(self, hwnd, budget=2.5):
            self.calls += 1
            return []
    ui2 = Never()
    assert M._wait_ready("whatsapp", ui2, {"hwnd": 32, "web": False}) is False and ui2.calls <= 32


@test
def yes_after_didnt_catch_and_brain_cannot_switch_recipient_2910():
    """Живой тест 29.10: черновик маме → «Didn't catch that» → «да отправляй» ушло в модель,
    а модель отправила «Привет!» на номер из памяти (себе). Теперь «да отправляй» — это отправка маме."""
    M, C = _fresh()
    C.add("Мама", "0700 123 456")
    ui = FakeWA(CHATS(), {"996700123456": "Мама"}, search_works=False)
    said = M.handle("Напиши маме на WhatsApp привет.", ui=ui, ask=_ask)
    assert said.endswith("«Привет!». Отправить?"), said
    assert M.intent("да отправляй", last="Didn't catch that, try again.") == ("send",)
    assert M.intent("да", last="Didn't catch that, try again.") == ("send",)
    assert M.intent("да", last="Память занята на 90%") is None, "просто «да» после другого разговора — не наше"
    out = M.tool_send_message("whatsapp", "+996707050809", "Привет!", True)
    assert out.startswith("A DRAFT IS ALREADY WAITING for “Мама”") and not ui.sent, out
    assert M.handle("да отправляй", last="Didn't catch that, try again.", ui=ui) == "Отправил."
    assert ui.sent == [("Мама", "Привет!")], ui.sent


@test
def phone_number_as_contact_opens_by_number():
    M, C = _fresh()
    ui = FakeWA(CHATS(), {"996707111222": "Назар Т"}, search_works=False)
    said = M.handle("напиши +996 707 111 222 привет в ватсапе", ui=ui, ask=_ask)
    assert ui.urls == ["whatsapp://send?phone=996707111222"] and "Назар Т" in said, (said, ui.urls)


@test
def nazar_session_2910_evening():
    """«Сообщение Назару. Привет…» ушло в модель; модель написала «Nazar» латиницей — поиск на экране нажал группу
    «Гонконг» из-за строки «~Nazar также состоит в этой группе»; «WhatsApp» распознался как «What's up?»."""
    M, C = _fresh()
    C.add("Назар", "0707 111 222")
    assert M.intent("Сообщение Назару. Привет, это Атлас, моё первое сообщение.")[1:4] == \
        (None, "Назару", "Привет, это Атлас, моё первое сообщение")
    it = M.intent("Атлас, мы не договорили. Отправь сообщение Назару. Привет. Это моё первое сообщение. Это Атлас по WhatsApp.")
    assert it[1:3] == ("whatsapp", "Назару") and it[3].endswith("Это Атлас"), it
    assert M.intent("напиши письмо маме") is None
    assert C.to_cyrillic("Nazar") == "назар" and C.lookup("Nazar") == ("one", "Назар", "996707111222")
    ui = FakeWA(CHATS(), {"996707111222": "Назар Т"}, search_works=False)
    seen, real = [], M.prepare
    M.prepare = lambda app, contact, *a, **k: seen.append(contact) or "Отправить?"
    try:
        M.tool_send_message("whatsapp", "Nazar", "Привет", True)
    finally:
        M.prepare = real
    assert seen == ["Назар"], seen                       # модель сказала «Nazar» — ищем «Назар» из книги
    said = M.prepare("whatsapp", "Назар", "", "Привет", True, ui, _ask)
    assert ui.urls and ui.urls[0].startswith("whatsapp://send?phone=996707111222"), ui.urls
    rg = {"list_x": 400, "top": 65, "chat_x": 404, "head_y": 160, "foot_y": 950, "W": 1900, "H": 1050}
    group = [{"text": "Гонконг 2026-2027", "x": 135, "y": 520, "w": 200, "h": 20},
             {"text": "~Nazar", "x": 135, "y": 545, "w": 50, "h": 18},
             {"text": "~Nazar также состоит в этой группе", "x": 135, "y": 545, "w": 220, "h": 18}]
    assert M._choose(group, rg, M.stems("Nazar"), False, None, "nazar") is None, "участник группы — не чат"
    M._s["pending"] = {"need_app": True, "contact": "Назару", "gist": "привет", "exact": None, "created": 1e12, "ru": True}
    M._s["said"] = "В каком мессенджере написать Назару?"
    assert M.intent("What's up?") == ("pick_app", "whatsapp")


def main():
    ok = 0
    for fn in TESTS:
        try:
            fn()
            ok += 1
            print(f"  ✓ {fn.__name__}")
        except Exception:
            print(f"  ✗ {fn.__name__}")
            traceback.print_exc()
    print(f"Тестов пройдено: {ok} из {len(TESTS)}")
    sys.exit(0 if ok == len(TESTS) else 1)


if __name__ == "__main__":
    main()
