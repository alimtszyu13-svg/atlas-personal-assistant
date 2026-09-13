import os
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Просим ТОЛЬКО право читать — Атлас физически не сможет отправлять
# или удалять письма, даже если что-то пойдёт не так
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

_gmail_service = None  # кэшируем подключение, чтобы не логиниться на каждый запрос


def _get_gmail_service():
    """
    Авторизуется через OAuth и возвращает объект для работы с Gmail API.
    При первом запуске откроет браузер для входа в аккаунт Google —
    дальше токен сохранится в token.json, и повторный вход не понадобится,
    пока токен не истечёт.
    """
    global _gmail_service
    if _gmail_service is not None:
        return _gmail_service

    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token_file:
            token_file.write(creds.to_json())

    _gmail_service = build("gmail", "v1", credentials=creds)
    return _gmail_service


def _decode_snippet(message_id: str, service) -> str:
    """Достаёт короткий превью-текст письма (snippet), который Gmail сам готовит."""
    msg = service.users().messages().get(userId="me", id=message_id, format="metadata",
                                          metadataHeaders=["Subject", "From"]).execute()
    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    subject = headers.get("Subject", "No subject")
    sender = headers.get("From", "Unknown sender")
    snippet = msg.get("snippet", "")
    return f"From {sender}: '{subject}' — {snippet}"


def get_recent_emails(count: int = 5) -> str:
    """Читает N последних писем из входящих и даёт краткую сводку по каждому."""
    try:
        service = _get_gmail_service()
        results = service.users().messages().list(userId="me", labelIds=["INBOX"],
                                                    maxResults=count).execute()
        messages = results.get("messages", [])

        if not messages:
            return "Your inbox is empty."

        summaries = []
        for msg in messages:
            summaries.append(_decode_snippet(msg["id"], service))

        return " | ".join(summaries)
    except Exception as e:
        print(f"[Ошибка get_recent_emails]: {e}")
        return "Couldn't check your email right now, there might be a connection or authorization issue."


def get_unread_count() -> str:
    """Сообщает, сколько непрочитанных писем во входящих."""
    try:
        service = _get_gmail_service()
        results = service.users().messages().list(userId="me", labelIds=["INBOX", "UNREAD"]).execute()
        count = results.get("resultSizeEstimate", 0)
        return f"You have {count} unread emails."
    except Exception as e:
        print(f"[Ошибка get_unread_count]: {e}")
        return "Couldn't check your unread emails right now."