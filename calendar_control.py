import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from googleapiclient.discovery import build
from google_auth import get_credentials

_calendar_service = None
TZ_NAME = os.getenv("ATLAS_TZ") or "Asia/Bishkek"   # «сегодня» и время событий — по твоему поясу, а не по UTC


_FIXED_HOURS = {"Asia/Bishkek": 6, "Asia/Almaty": 5, "Asia/Tashkent": 5, "Europe/Moscow": 3, "UTC": 0}


def _tz():
    try:
        return ZoneInfo(TZ_NAME)
    except Exception:          # на Windows без пакета tzdata базы поясов нет — берём постоянное смещение
        return timezone(timedelta(hours=_FIXED_HOURS.get(TZ_NAME, 0)))


def _now():
    return datetime.now(_tz())


def _get_service():
    global _calendar_service
    if _calendar_service is None:
        _calendar_service = build("calendar", "v3", credentials=get_credentials())
    return _calendar_service


def list_today_events(count: int = None) -> str:
    """Lists all calendar events scheduled for today."""
    service = _get_service()
    day = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    start, end = day.isoformat(), (day + timedelta(days=1)).isoformat()

    events = service.events().list(calendarId="primary", timeMin=start, timeMax=end,
                                     singleEvents=True, orderBy="startTime").execute().get("items", [])
    if not events:
        return "You have no events scheduled for today."
    parts = [f"{e['summary']} at {e['start'].get('dateTime', e['start'].get('date'))}" for e in events]
    return "Today's events: " + " | ".join(parts)


def list_upcoming_events(days: int = 7) -> str:
    """Lists upcoming calendar events within the next N days."""
    service = _get_service()
    now = _now().isoformat()
    end = (_now() + timedelta(days=days)).isoformat()

    events = service.events().list(calendarId="primary", timeMin=now, timeMax=end,
                                     singleEvents=True, orderBy="startTime").execute().get("items", [])
    if not events:
        return f"No events in the next {days} days."
    parts = [f"{e['summary']} on {e['start'].get('dateTime', e['start'].get('date'))}" for e in events]
    return "Upcoming: " + " | ".join(parts)


def create_event(title: str, date: str, time: str = "09:00", duration_minutes: int = 60) -> str:
    """Creates a calendar event. date format 'YYYY-MM-DD', time format 'HH:MM' (24h)."""
    try:
        start_dt = datetime.fromisoformat(f"{date}T{time}:00")
        end_dt = start_dt + timedelta(minutes=duration_minutes)
    except ValueError:
        return "Couldn't understand that date or time format."

    service = _get_service()
    event = {
        "summary": title,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": TZ_NAME},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": TZ_NAME},
    }
    service.events().insert(calendarId="primary", body=event).execute()
    return f"Created '{title}' on {date} at {time}."


def delete_event(title: str) -> str:
    """Deletes the next upcoming event matching the given title."""
    service = _get_service()
    now = _now().isoformat()
    events = service.events().list(calendarId="primary", timeMin=now, q=title,
                                     singleEvents=True, orderBy="startTime", maxResults=1).execute().get("items", [])
    if not events:
        return f"Couldn't find an upcoming event called '{title}'."
    service.events().delete(calendarId="primary", eventId=events[0]["id"]).execute()
    return f"Deleted '{events[0]['summary']}'."