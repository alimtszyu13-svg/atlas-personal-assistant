import requests
import feedparser  

def get_weather(city: str = "Bishkek") -> str:
    """
    Запрашивает погоду через wttr.in — не нужен API-ключ,
    сервис сам определяет параметры по URL.
    """
    try:
        # format=3 — компактный однострочный формат: "Город: ☀️ +25°C"
        # lang=ru — комментарии/описание на русском
        url = f"https://wttr.in/{city}?format=3&lang=ru"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            return response.text.strip()
        return "Не удалось получить погоду."
    except requests.RequestException as e:
        print(f"[Ошибка get_weather]: {e}")
        return "Нет связи с сервисом погоды."

    
def get_news(count: int = 3) -> str:
    """
    Берёт последние заголовки из RSS-ленты РИА Новости.
    feedparser сам парсит XML в удобный объект.
    """
    try:
        feed = feedparser.parse("https://ria.ru/export/rss2/archive/index.xml")
        if not feed.entries:
            return "Не удалось получить новости."

        headlines = [entry.title for entry in feed.entries[:count]]
        return "Вот последние новости: " + ". ".join(headlines)
    except Exception as e:
        print(f"[Ошибка get_news]: {e}")
        return "Нет связи с сервисом новостей."