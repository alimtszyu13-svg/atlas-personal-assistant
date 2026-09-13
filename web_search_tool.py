from ddgs import DDGS


def search_web(query: str, num_results: int = 3) -> str:
    """
    Ищет в интернете через DuckDuckGo и возвращает краткую сводку
    из нескольких первых результатов — без API-ключа.
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=num_results))

        if not results:
            return f"No results found for '{query}'."

        # Собираем короткую текстовую сводку — заголовок + краткое описание
        # каждого результата, без ссылок (их всё равно не озвучить голосом)
        summary_parts = []
        for r in results:
            title = r.get("title", "")
            body = r.get("body", "")
            summary_parts.append(f"{title}: {body}")

        return " | ".join(summary_parts)
    except Exception as e:
        print(f"[Ошибка search_web]: {e}")
        return "Couldn't search the web right now, there might be a connection issue."