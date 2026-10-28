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
        self.urls.append(url)
        if self.links_work:
            num = url.split("phone=")[1]
            if num in self.phones:
                self.open, self.query = self.phones[num], None

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
