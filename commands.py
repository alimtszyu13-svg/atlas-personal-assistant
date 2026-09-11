from file_control import open_file, create_folder, delete_file, locate_file

def route_command(text: str) -> str:
    original_text = text
    text = text.lower()

    if "найди" in text:
        target = text.split("найди", 1)[1].strip()
        # убираем возможное слово "папку"/"файл" перед именем, если сказано так
        target = target.replace("папку", "").replace("файл", "").strip()
        return locate_file(target)

    if "открой" in text:
        target = text.split("открой", 1)[1].strip()
        result = open_app(target)
        if "Не знаю приложения" in result:
            return open_file(target)
        return result

    if "закрой" in text:
        app_name = text.split("закрой", 1)[1].strip()
        return close_app(app_name)

    if "создай папку" in text:
        folder_name = text.split("создай папку", 1)[1].strip()
        return create_folder(folder_name)

    if "удали" in text:
        target = text.split("удали", 1)[1].strip()
        return delete_file(target)

    if "погода" in text:
        return get_weather()

    if "новости" in text:
        return get_news()

    return ask_ai(original_text)