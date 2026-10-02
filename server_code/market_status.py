"""Scheduled US equity sessions, including extended hours and exchange holidays."""

from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from math import ceil
from zoneinfo import ZoneInfo

import anvil.server
import pandas_market_calendars as market_calendars


_NEW_YORK = ZoneInfo("America/New_York")
_CALENDAR = market_calendars.get_calendar("NYSE")
_SCHEDULE_SOURCE = "pandas_market_calendars %s · NYSE regular trading calendar" % (
  market_calendars.__version__,
)
_SOURCE_URL = "https://www.nyse.com/trade/hours-calendars"
_EXTENDED_SOURCE_URL = "https://www.nasdaq.com/market-activity/stock-market-holiday-schedule"
_SCOPE = (
  "Scheduled US equity regular session and Nasdaq/NYSE Arca pre-market and after-hours. "
  "Broker hours and overnight trading vary; unscheduled trading halts are not included."
)


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


def _seconds_until(now, timestamp):
  return max(0, ceil((timestamp - now).total_seconds()))


def _duration(seconds):
  """Round a clock countdown upward to readable minutes."""
  if seconds < 60:
    return "less than 1m"
  minutes = (seconds + 59) // 60
  days, minutes = divmod(minutes, 24 * 60)
  hours, minutes = divmod(minutes, 60)
  parts = []
  if days:
    parts.append("%dd" % days)
  if hours:
    parts.append("%dh" % hours)
  if minutes:
    parts.append("%dm" % minutes)
  return " ".join(parts)


def _pre_market_open(market_open):
  eastern_day = market_open.astimezone(_NEW_YORK).date()
  return datetime.combine(eastern_day, time(4), tzinfo=_NEW_YORK).astimezone(timezone.utc)


def _status_at(now):
  """Pure clock parameter for session-boundary and holiday verification."""
  if now.tzinfo is None:
    raise ValueError("Market-status timestamps must be timezone-aware.")
  now = now.astimezone(timezone.utc)
  result = {
    "state": "unavailable",
    "label": "US market status unavailable",
    "detail": "The trading calendar is unavailable for the current date.",
    "checked_at": now.isoformat(),
    "next_change": None,
    "next_regular_open": None,
    "session_end": None,
    "countdown_seconds": None,
    "countdown_label": "",
    "is_early_close": False,
    "schedule_source": _SCHEDULE_SOURCE,
    "source_url": _SOURCE_URL,
    "extended_source_url": _EXTENDED_SOURCE_URL,
    "scope": _SCOPE,
  }
  eastern_day = now.astimezone(_NEW_YORK).date()
  sessions = _schedule_for_day(eastern_day)
  next_regular_open = next((market_open for market_open, _ in sessions if market_open > now), None)
  if next_regular_open is None:
    return result
  opens_in = _seconds_until(now, next_regular_open)
  result.update({
    "state": "closed",
    "label": "US markets closed",
    "detail": "Regular session opens in %s · %s." % (
      _duration(opens_in), _eastern_time(next_regular_open),
    ),
    "next_regular_open": next_regular_open.isoformat(),
    "next_change": _pre_market_open(next_regular_open).isoformat(),
    "countdown_seconds": opens_in,
    "countdown_label": "Regular session opens in %s" % _duration(opens_in),
  })
  for market_open, market_close in sessions:
    if market_open.astimezone(_NEW_YORK).date() != eastern_day:
      continue
    pre_market_open = _pre_market_open(market_open)
    # Nasdaq/Arca late sessions last four hours, including 13:00–17:00
    # on scheduled early-close days. Do not apply weekday-only assumptions:
    # the NYSE calendar above determines holidays and early regular closes.
    after_hours_end = market_close + timedelta(hours=4)
    early_close = market_close.astimezone(_NEW_YORK).time() < time(16)
    result["is_early_close"] = early_close
    if market_open <= now < market_close:
      closes_in = _seconds_until(now, market_close)
      result.update({
        "state": "open",
        "label": "US markets live · Regular session",
        "detail": "%s in %s · %s." % (
          "Early close" if early_close else "Regular session closes",
          _duration(closes_in), _eastern_time(market_close),
        ),
        "next_change": market_close.isoformat(),
        "session_end": market_close.isoformat(),
        "countdown_seconds": closes_in,
        "countdown_label": "%s in %s" % (
          "Early close" if early_close else "Regular session closes", _duration(closes_in),
        ),
      })
      return result
    if pre_market_open <= now < market_open:
      result.update({
        "state": "pre_market",
        "label": "US markets · Pre-market trading",
        "next_change": market_open.isoformat(),
        "session_end": market_open.isoformat(),
      })
      return result
    if market_close <= now < after_hours_end:
      result.update({
        "state": "after_hours",
        "label": "US markets · After-hours trading",
        "detail": "After-hours ends in %s · %s. Regular session reopens in %s · %s." % (
          _duration(_seconds_until(now, after_hours_end)), _eastern_time(after_hours_end),
          _duration(opens_in), _eastern_time(next_regular_open),
        ),
        "next_change": after_hours_end.isoformat(),
        "session_end": after_hours_end.isoformat(),
      })
      return result
  return result


@anvil.server.callable
def get_us_market_status():
  """Status of scheduled regular and extended equity sessions at server UTC time."""
  return _status_at(datetime.now(timezone.utc))
