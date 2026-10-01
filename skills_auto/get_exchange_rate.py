"""Навык, которому Atlas научился сам: Получает текущий курс обмена заданной валютной пары из бесплатного публичного API и возвращает короткий текст для озвучивания.
Заявка #1. Установлен 01.10.2026 12:14."""
def get_exchange_rate(base_currency='USD', target_currency='EUR'):
    import httpx
    base = base_currency.upper().strip()
    target = target_currency.upper().strip()
    try:
        # Primary source: open.er-api.com
        url = f'https://open.er-api.com/v6/latest/{base}'
        resp = httpx.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            # API returns {'result':'success', 'rates':{...}}
            if data.get('result') == 'success':
                rates = data.get('rates', {})
                if target in rates:
                    rate = rates[target]
                    # Format: keep up to 6 decimal places, trim trailing zeros
                    rate_str = f"{rate:.6f}".rstrip('0').rstrip('.')
                    return f'1 {base} = {rate_str} {target}'
        # Fallback source: exchangerate.host conversion endpoint
        url2 = f'https://api.exchangerate.host/convert?from={base}&to={target}&amount=1'
        resp2 = httpx.get(url2, timeout=10)
        if resp2.status_code == 200:
            data2 = resp2.json()
            # Exchangerate.host returns {'info':{'rate':...}}
            rate = data2.get('info', {}).get('rate')
            if isinstance(rate, (int, float)):
                rate_str = f"{rate:.6f}".rstrip('0').rstrip('.')
                return f'1 {base} = {rate_str} {target}'
        return 'Error: could not retrieve a valid exchange rate.'
    except Exception as e:
        return f'Error: {str(e)}'
