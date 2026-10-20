"""Calendar-day status in the event's own time zone."""
import datetime
import pytz


def day(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def status(event, now=None):
    get = event.get if isinstance(event, dict) else lambda key: getattr(event, key, None)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    try:
        zone = pytz.timezone(get('timezone') or 'UTC')
    except (pytz.UnknownTimeZoneError, AttributeError):
        zone = pytz.UTC
    today = now.astimezone(zone).date()
    start = day(get('start_date'))
    end = day(get('end_date')) or start
    if end is None:
        return 'undated'
    if end < today:
        return 'past'
    if start and start < today:
        return 'ongoing'
    return 'upcoming'
