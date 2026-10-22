"""
Твои настоящие фразы из журналов (5–22.10) — куда Atlas их отправляет: модуль, быстрая команда, мгновенный ответ
или модель. Любая правка, которая уводит частую команду не туда, сразу видна здесь.
Личное (даты, тексты, названия работ) из фраз убрано — только команды.

    python tests/test_real_phrases.py

Как читать ожидание:
  messengers / window_layout / … — модуль Atlas взял фразу сам (без модели)
  fast:<функция>               — быстрая команда, сразу
  instant                      — мгновенный ответ (погода и т.п.)
  brain                        — нужна модель (разговор, рекомендации, вопросы про почту/календарь)
Если поведение поменяли НАМЕРЕННО — поменяй ожидание здесь же, с причиной в комментарии.
"""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

EXPECTED = [
    ('Напиши маме «тест 1».', 'messengers'),
    ('Чат открыт. Пиши тест 1.', 'brain'),
    ('Напиши мне самому привет.', 'messengers'),
    ('На WhatsApp, а не сюда.', 'brain'),
    ('Мне кто-то что-то писал.', 'messengers'),
    ('Открой фотографию, что прислал папа.', 'messengers'),
    ('Включи какую-нибудь музыку.', 'fast:play_on_spotify'),
    ('Поставь что-нибудь повеселее.', 'brain'),
    ('Поставь что-нибудь от Эминема.', 'brain'),
    ('Останови музыку.', 'fast:speak'),
    ('Какие новости на сегодня?', 'brain'),
    ('Какие игры у меня есть в библиотеке Steam?', 'fast:list_steam_games'),
    ('Открой Marvel Rivals игра.', 'fast:open_app'),
    ('Поменяй музыку.', 'fast:next_track'),
    ('Поставь какую-нибудь веселую музыку.', 'fast:play_on_spotify'),
    ('Включи что-нибудь поживее.', 'brain'),
    ('Давай что-нибудь другое.', 'brain'),
    ('Сделаем музыку погромче.', 'fast:volume_up'),
    ('Перемотай на 15 секунд вперед.', 'fast:spotify_seek'),
    ('Сделай звук погромче.', 'fast:volume_up'),
    ('Сделай звук в Spotify погромче, скажем на 80.', 'fast:spotify_volume'),
    ('Установи себя на телефон.', 'fast:install'),
    ('Что с погодой?', 'instant'),
    ('Что у меня в календаре?', 'brain'),
    ('Есть ли у меня что-то важное в почте?', 'brain'),
    ('Найди эссе про путешествия.', 'fast:search_file_content'),
    ('Найди документ про путешествия на компьютере.', 'fast:search_file_content'),
    ('Начни запись экрана.', 'meeting_notes'),
    ('How are you doing, Atlas?', 'brain'),
    ('Открой WhatsApp и напиши моей маме, что я скоро буду.', 'messengers'),
    ('Напиши маме, что я скоро буду.', 'messengers'),
    ('Открой калькулятор.', 'fast:open_app'),
    ('Поставь громкость 30.', 'fast:set_volume'),
    ('Что я записывал про курсовую?', 'knowledge'),
    ('Насколько ты быстрый?', 'speed_stats'),
    ('Что у тебя работает в фоне и есть ли обновления?', 'background'),
    ('Есть ли обновления?', 'updater'),
    ('Закинь хром в левую часть.', 'window_layout'),
    ('Поставь WhatsApp справа.', 'window_layout'),
    ('Как дела, Атлас?', 'brain'),
    ('пауза', 'fast:play_pause_media'),
    ('Погода.', 'instant'),
    ('Атлас, напиши маме, что я скоро уйду.', 'messengers'),
    ('Привет!', 'brain'),
]

# Фразы, которые раньше шли не туда (журналы) — отдельно, чтобы было видно, зачем правили
FIXED = {
    "Открой WhatsApp и напиши моей маме, что я скоро буду.": "19.10: уходило в модель, «моей маме» не находило чат «Мама»",
    "Напиши мне самому привет.": "20.10: модель сделала заметку вместо сообщения",
    "Мне кто-то что-то писал.": "20.10: модель читала старые сообщения из списка чатов",
    "Открой фотографию, что прислал папа.": "20.10: модель 35 с искала файл на диске",
    "Поставь громкость 30.": "уходило в модель (2–3 с)",
    "Останови музыку.": "уходило в модель",
    "Поменяй музыку.": "уходило в модель",
    "Что с погодой?": "уходило в модель, хотя «Погода.» отвечалась мгновенно",
}


def main():
    from tests.real_phrases_lib import routes
    got = routes([p for p, _ in EXPECTED])
    bad = [(p, want, got[p]) for p, want in EXPECTED if got[p] != want]
    for p, want, g in bad:
        why = f"  ({FIXED[p]})" if p in FIXED else ""
        print(f"  ✗ «{p}»: ждали {want}, сейчас {g}{why}")
    ok = len(EXPECTED) - len(bad)
    stats = {}
    for _p, want in EXPECTED:
        k = "brain" if want == "brain" else "без модели"
        stats[k] = stats.get(k, 0) + 1
    print(f"  фразы без модели: {stats.get('без модели', 0)} из {len(EXPECTED)}")
    print(f"\nТестов пройдено: {ok} из {len(EXPECTED)}")
    sys.stdout.flush()
    os._exit(0 if not bad else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        os._exit(1)
