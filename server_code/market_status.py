"""Current scheduled US equity-market status, including exchange holidays."""

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import anvil.server
import pandas_market_calendars as market_calendars


_NEW_YORK = ZoneInfo("America/New_York")
_CALENDAR = market_calendars.get_calendar("NYSE")
_SCHEDULE_SOURCE = "pandas_market_calendars %s · NYSE regular trading calendar" % (
  market_calendars.__version__,
)
_SOURCE_URL = "https://www.nyse.com/trade/hours-calendars"
_SCOPE = "Scheduled NYSE/Nasdaq regular equity session; excludes extended hours and unscheduled trading halts."


@lru_cache(maxsize=1)
def _schedule_for_day(eastern_day):
  """Cache dates, never the clock-dependent open/closed result."""
  schedule = _CALENDAR.schedule(
    start_date=eastern_day,
    end_date=eastern_day + timedelta(days=32),
  )
  return tuple(
    (
      market_open.to_pydatetime().astimezone(timezone.utc),
      market_close.to_pydatetime().astimezone(timezone.utc),
    )
    for market_open, market_close in zip(
      schedule["market_open"].tolist(), schedule["market_close"].tolist(),
    )
  )


def _eastern_time(timestamp):
  eastern = timestamp.astimezone(_NEW_YORK)
  return "%s %d, %d at %s %s" % (
    eastern.strftime("%a, %b"), eastern.day, eastern.year,
    eastern.strftime("%I:%M %p").lstrip("0"), eastern.tzname(),
  )


def _status_at(now):
  """Pure clock parameter for session-boundary and holiday verification."""
  if now.tzinfo is None:
    raise ValueError("Market-status timestamps must be timezone-aware.")
  now = now.astimezone(timezone.utc)
  result = {
    "state": "unavailable",
    "label": "US market status unavailable",
    "detail": "The regular-session calendar is unavailable for the current date.",
    "checked_at": now.isoformat(),
    "next_change": None,
    "schedule_source": _SCHEDULE_SOURCE,
    "source_url": _SOURCE_URL,
    "scope": _SCOPE,
  }
  for market_open, market_close in _schedule_for_day(now.astimezone(_NEW_YORK).date()):
    if market_open <= now < market_close:
      result.update({
        "state": "open",
        "label": "US markets open",
        "detail": "Regular session closes %s." % _eastern_time(market_close),
        "next_change": market_close.isoformat(),
      })
      return result
    if now < market_open:
      result.update({
        "state": "closed",
        "label": "US markets closed",
        "detail": "Next regular session opens %s." % _eastern_time(market_open),
        "next_change": market_open.isoformat(),
      })
      return result
  return result


@anvil.server.callable
def get_us_market_status():
  """Status of the scheduled core US equity session at the server's UTC time."""
  return _status_at(datetime.now(timezone.utc))
