from system_control import open_app, close_app
from info_services import get_weather, get_news

def route_command(text: str) -> str:
    text = text.lower()

    if "открой" in text:
        app_name = text.split("открой", 1)[1].strip()
        return open_app(app_name)

    if "закрой" in text:
        app_name = text.split("закрой", 1)[1].strip()
        return close_app(app_name)

    if "погода" in text:
        return get_weather()  # по умолчанию Бишкек, можно расширить парсинг города позже

    if "новости" in text:
        return get_news()

    return "Не понял команду."