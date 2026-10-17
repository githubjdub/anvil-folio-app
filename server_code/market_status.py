import anvil.secrets
"""Scheduled US equity sessions, including extended hours and exchange holidays."""

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from math import ceil
from zoneinfo import ZoneInfo

import anvil.server


_NEW_YORK = ZoneInfo("America/New_York")
_SCHEDULE_SOURCE = "NYSE published holiday rules and scheduled early closes"
_SOURCE_URL = "https://www.nyse.com/trade/hours-calendars"
_EXTENDED_SOURCE_URL = "https://www.nasdaq.com/market-activity/stock-market-holiday-schedule"
_SCOPE = (
  "Scheduled US equity regular session and Nasdaq/NYSE Arca pre-market and after-hours. "
  "Broker hours and overnight trading vary; unscheduled trading halts are not included."
)


_SPECIAL_CLOSURES = frozenset({
  date(2001, 9, 11), date(2001, 9, 12), date(2001, 9, 13), date(2001, 9, 14),
  date(2004, 6, 11), date(2007, 1, 2),
  date(2012, 10, 29), date(2012, 10, 30),
  date(2018, 12, 5), date(2025, 1, 9),
})


def _nth_weekday(year, month, weekday, occurrence):
  first = date(year, month, 1)
  return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (occurrence - 1))


def _last_weekday(year, month, weekday):
  following_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
  last = following_month - timedelta(days=1)
  return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter_sunday(year):
  """Gregorian Easter, using the integer Meeus/Jones/Butcher algorithm."""
  a = year % 19
  b, c = divmod(year, 100)
  d, e = divmod(b, 4)
  f = (b + 8) // 25
  g = (b - f + 1) // 3
  h = (19 * a + b - d - g + 15) % 30
  i, k = divmod(c, 4)
  l = (32 + 2 * e + 2 * i - h - k) % 7
  m = (a + 11 * h + 22 * l) // 451
  month, day_zero = divmod(h + l - 7 * m + 114, 31)
  return date(year, month, day_zero + 1)


def _observed_holiday(day):
  if day.weekday() == 5:
    return day - timedelta(days=1)
  if day.weekday() == 6:
    return day + timedelta(days=1)
  return day


@lru_cache(maxsize=4)
def _holidays_for_year(year):
  """Modern NYSE equity holidays, including its New Year's Saturday exception."""
  new_year = date(year, 1, 1)
  holidays = {
    _nth_weekday(year, 2, 0, 3),                 # Washington's Birthday
    _easter_sunday(year) - timedelta(days=2),   # Good Friday
    _last_weekday(year, 5, 0),                  # Memorial Day
    _observed_holiday(date(year, 7, 4)),
    _nth_weekday(year, 9, 0, 1),                # Labor Day
    _nth_weekday(year, 11, 3, 4),               # Thanksgiving
    _observed_holiday(date(year, 12, 25)),
  }
  # NYSE does not close the preceding Friday when January 1 is a Saturday.
  # Sunday January 1 is observed on Monday, as with the other fixed holidays.
  if new_year.weekday() != 5:
    holidays.add(_observed_holiday(new_year))
  if year >= 1998:
    holidays.add(_nth_weekday(year, 1, 0, 3))    # Martin Luther King Jr. Day
  if year >= 2022:
    holidays.add(_observed_holiday(date(year, 6, 19)))
  holidays.update(day for day in _SPECIAL_CLOSURES if day.year == year)
  return frozenset(holidays)


def _regular_close_hour(day):
  """A session is shortened only when the named early-close date is open."""
  thanksgiving_friday = _nth_weekday(day.year, 11, 3, 4) + timedelta(days=1)
  if day == thanksgiving_friday or (day.month, day.day) in {(7, 3), (12, 24)}:
    return 13
  return 16


@lru_cache(maxsize=1)
def _schedule_for_day(eastern_day):
  """Cache dates, never the clock-dependent open/closed result."""
  sessions = []
  for offset in range(33):
    day = eastern_day + timedelta(days=offset)
    if day.weekday() >= 5 or day in _holidays_for_year(day.year):
      continue
    market_open = datetime.combine(day, time(9, 30), tzinfo=_NEW_YORK)
    market_close = datetime.combine(day, time(_regular_close_hour(day)), tzinfo=_NEW_YORK)
    sessions.append((
      market_open.astimezone(timezone.utc), market_close.astimezone(timezone.utc),
    ))
  return tuple(sessions)


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
