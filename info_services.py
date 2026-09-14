import requests
import feedparser  

def get_weather(city: str = "Bishkek") -> str:
    """
    Запрашивает погоду через Open-Meteo (основной, бесплатный, без ключа).
    Если не получилось — пробует wttr.in как запасной вариант.
    """
    try:
        # Шаг 1 — превращаем название города в координаты (geocoding)
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
        geo_response = requests.get(geo_url, timeout=8)
        geo_data = geo_response.json()

        if not geo_data.get("results"):
            return f"Couldn't find location '{city}'."

        location = geo_data["results"][0]
        lat, lon = location["latitude"], location["longitude"]

        # Шаг 2 — запрашиваем текущую погоду по координатам
        weather_url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}&current=temperature_2m,weather_code"
        )
        weather_response = requests.get(weather_url, timeout=8)
        weather_data = weather_response.json()

        temp = weather_data["current"]["temperature_2m"]
        return f"It's currently {temp}°C in {city}."

    except Exception as e:
        print(f"[Ошибка get_weather via Open-Meteo]: {e}")
        # Fallback — пробуем старый сервис, вдруг именно Open-Meteo сейчас недоступен
        try:
            url = f"https://wttr.in/{city}?format=3&lang=ru"
            response = requests.get(url, timeout=8)
            if response.status_code == 200:
                return response.text.strip()
        except requests.RequestException as e2:
            print(f"[Ошибка get_weather via wttr.in fallback]: {e2}")

        return "Both weather services appear to be unavailable right now, sir."

    
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