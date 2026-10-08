import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

_creds = None


def get_credentials():
    """Единая точка авторизации — и Gmail, и Calendar используют один и тот же токен."""
    global _creds
    if _creds is not None and _creds.valid:
        return _creds

    creds = None
    if os.path.exists("token.json"):
        try:
            creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        except Exception as e:                      # пустой или испорченный файл — просто войти заново
            print(f"[google] token.json не читается ({e}) — нужен новый вход")

    if creds and not creds.valid and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:                      # invalid_grant: доступ отозван или истёк — войти заново
            print(f"[google] токен больше не действует ({e}) — нужен новый вход")
            creds = None

    if not creds or not creds.valid:                # окно входа Google (в облаке вместо него — понятная ошибка)
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        creds = flow.run_local_server(port=0)
    with open("token.json", "w") as f:
        f.write(creds.to_json())

    _creds = creds
    return creds