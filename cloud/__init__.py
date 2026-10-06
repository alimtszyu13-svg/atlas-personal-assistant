"""
Atlas в облаке (Hugging Face Spaces): тот же мозг, что на компьютере, без того, что умеет только Windows.

    stubs.py             — модули «только для компьютера» → вежливые заглушки «недоступно из облака»
    voice_cloud.py       — распознавание (Groq Whisper) и голос (Fish или Edge) без микрофона и колонок
    file_search_cloud.py — только «векторы смысла» той же моделью, что на компьютере (память совпадает)
    atlas_cloud.py       — запуск: память из Supabase → мозг → сервер для телефона на порту 7860
    space/               — два файла для Hugging Face (Dockerfile, README.md)
"""
