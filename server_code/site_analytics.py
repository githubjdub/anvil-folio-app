import anvil.secrets
"""Coarse browser-visit counts, aggregated only for the platform creator.

There are no user links, IP addresses, stored session identifiers or precise
locations. Anvil's private server session holds the current visit row only.
"""

from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
import math
from typing import Any

import anvil.server
import anvil.users
from anvil.tables import app_tables, order_by, query as q, RowDeleted
# Installed from requirements.txt; verified in the Anvil server runtime.
import pycountry  # pyright: ignore[reportMissingImports]

from . import creator_portfolios

# Anvil's generated cross-module stubs expose callables, not private helpers.
_creator: Any = creator_portfolios


_PAGES = {
  "dashboard": "Portfolio workspace", "overview": "Overview",
  "allocation": "Portfolio structure", "performance": "Performance",
  "simulations": "Simulations (archived)", "tax_estimator": "Tax Estimator", "retrospective": "Retrospective (archived)", "methods": "Methods", "shared": "Shared performance",
  "retirement": "Retirement", "dividends": "Dividends", "passive": "Passive Income",
  "tax": "Tax Harvester", "notifications": "Notifications", "income": "Income (previous navigation)", "disclosures": "Disclosures",
  "subscribe": "Plans", "subscription": "My subscription", "profile": "Profile",
}
_RANGES = {"7d": 7, "30d": 30, "90d": 90, "1y": 365}
_SESSION_KEY = "portfoliora_site_visit"
_SETUP_MESSAGE = "Apply the Site Visits Data Table schema in this app's database, then refresh Site interest."
_METHOD = (
  "Collection begins when this feature is enabled; there is no imported historical traffic. "
  "A visit is an Anvil browser session with less than 30 minutes of inactivity. Visits are not unique people. "
  "Visits and their accumulated pageviews are attributed to the visit's UTC start date. "
  "A page filter counts visits containing that page and only that page's views. "
  "The interest index is relative: 100 is the highest selected-metric bucket in this view. "
  "Geographic shares use the same filters; regional interest compares the selected metric with the leading country. "
  "Country estimates come from Anvil and can be unavailable or inaccurate, including for VPNs. "
  "Only country-level labels are retained. Creator visits are excluded unless requested. "
  "Rapid repeats and bursts are limited; browser automation can still affect counts. "
  "This is site activity, not Google search demand."
)


def _now():
  return datetime.now(timezone.utc)


def _utc(value):
  if not isinstance(value, datetime):
    return None
  return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _table():
  try:
    return getattr(app_tables, "site_visits")
  except AttributeError as error:
    # This exact missing-table failure was confirmed in the current runtime.
    if str(error) != "No such app table: 'site_visits'":
      raise
    return None


def _field(value, name):
  return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _country(value):
  if not isinstance(value, str) or not value.strip() or len(value) > 100:
    return {"country_code": "XX", "country_name": "Unknown", "iso3": None}
  token = value.strip()
  if token.upper() == "UK":
    token = "GB"
  try:
    country = pycountry.countries.lookup(token)
  except LookupError:
    return {"country_code": "XX", "country_name": "Unknown", "iso3": None}
  return {"country_code": country.alpha_2, "country_name": country.name, "iso3": country.alpha_3}


def _remember(state):
  # The documented/runtime session is a dictionary; its generated stub only
  # lists get()/set(), so state the supported mapping type explicitly here.
  session: Any = anvil.server.session
  session[_SESSION_KEY] = state


@anvil.server.callable
def record_site_page(page):
  client = anvil.server.context.client
  caller = anvil.server.context.remote_caller
  if _field(client, "type") != "browser" or _field(caller, "type") != "browser":
    return {"ok": True, "recorded": False, "reason": "browser_only"}
  if not isinstance(page, str) or page not in _PAGES:
    return {"ok": False, "recorded": False, "message": "Choose a supported site page."}
  table = _table()
  if table is None:
    return {"ok": False, "recorded": False, "message": _SETUP_MESSAGE}
  now = _now()
  state = anvil.server.session.get(_SESSION_KEY, {})
  visit = state.get("visit")
  creator = _creator._is_creator(anvil.users.get_user())
  if visit is not None:
    try:
      last_seen = _utc(visit["last_seen_at"])
      if last_seen is None or (now - last_seen).total_seconds() >= 1800:
        visit = None
      elif creator and visit["is_creator"] is not True:
        # Mark a guest-to-creator visit even if the current event is deduped.
        visit.update(is_creator=True)
    except RowDeleted:
      visit = None
  if visit is None:
    state = {}
  accepted = [stamp for stamp in state.get("accepted_at", [])
              if 0 <= (now - stamp).total_seconds() < 60]
  last_event = state.get("last_event_at")
  if (state.get("last_page") == page and last_event is not None
      and 0 <= (now - last_event).total_seconds() < 2):
    return {"ok": True, "recorded": False, "reason": "repeat"}
  if len(accepted) >= 20:
    return {"ok": True, "recorded": False, "reason": "rate_limited"}
  if visit is None:
    location = _field(client, "location")
    country = _country(_field(location, "country"))
    visit = table.add_row(started_at=now, last_seen_at=now, page_views=1,
                          pages={page: 1}, country_code=country["country_code"],
                          country_name=country["country_name"], is_creator=creator)
  else:
    pages = dict(visit["pages"])
    pages[page] = pages.get(page, 0) + 1
    visit.update(last_seen_at=now, page_views=visit["page_views"] + 1, pages=pages)
  _remember({"visit": visit, "accepted_at": accepted + [now],
             "last_page": page, "last_event_at": now})
  return {"ok": True, "recorded": True}


def _filter_date(value):
  if type(value) is date:
    return value
  if isinstance(value, str):
    return date.fromisoformat(value)
  raise ValueError("Choose valid start and end dates.")


def _filters(range_key, country_code, page, include_creator, metric, start_date, end_date, now):
  if not isinstance(range_key, str) or range_key not in set(_RANGES) | {"all", "custom"}:
    raise ValueError("Choose a supported time range.")
  if not isinstance(include_creator, bool):
    raise ValueError("Choose whether to include creator visits.")
  if metric not in ("visits", "pageviews"):
    raise ValueError("Choose visits or pageviews for the interest index.")
  if page is not None and (not isinstance(page, str) or page not in _PAGES):
    raise ValueError("Choose a supported page filter.")
  if country_code is not None:
    if not isinstance(country_code, str) or len(country_code) != 2:
      raise ValueError("Choose a valid country filter.")
    country_code = country_code.upper()
    if country_code != "XX" and pycountry.countries.get(alpha_2=country_code) is None:
      raise ValueError("Choose a valid country filter.")
  if range_key == "custom":
    start, end = _filter_date(start_date), _filter_date(end_date)
    if start > end or end > now.date():
      raise ValueError("Choose a date range ending today or earlier, with the start on or before the end.")
  elif start_date is not None or end_date is not None:
    raise ValueError("Choose Custom dates to set start and end dates.")
  else:
    end = now.date()
    start = end - timedelta(days=_RANGES.get(range_key, 1) - 1)
  return start, end, country_code


def _collection_dates(table, now):
  first = next(iter(table.search(order_by("started_at"), started_at=q.less_than_or_equal_to(now))), None)
  latest = next(iter(table.search(order_by("last_seen_at", ascending=False),
                                 last_seen_at=q.less_than_or_equal_to(now))), None)
  return (_utc(first["started_at"]) if first is not None else None,
          _utc(latest["last_seen_at"]) if latest is not None else None)


def _count(value):
  if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
    return 0
  try:
    return int(value) if math.isfinite(value) and value == int(value) else 0
  except OverflowError:
    return 0


def _page_counts(row):
  raw = row["pages"]
  return {page: _count(raw.get(page)) for page in _PAGES} if isinstance(raw, dict) else {}


def _next_month(value, count):
  months = value.year * 12 + value.month - 1 + count
  year, month = divmod(months, 12)
  return date(year, month + 1, min(value.day, monthrange(year, month + 1)[1]))


def _buckets(start, end, now):
  days = (end - start).days + 1
  months = (end.year - start.year) * 12 + end.month - start.month + 1
  bucket = "day" if days <= 400 else "week" if days <= 2800 else "month"
  size = max(1, math.ceil(months / 400)) if bucket == "month" else 1
  result = []
  current = start
  while current <= end:
    following = (current + timedelta(days=1 if bucket == "day" else 7)
                 if bucket != "month" else _next_month(current, size))
    until = min(end, following - timedelta(days=1))
    result.append({"date": current.isoformat(), "label": current.isoformat(), "end_date": until.isoformat(),
                   "visits": 0, "pageviews": 0, "interest": 0, "complete": until < now.date()})
    current = following
  return bucket, result


def _rank_countries(countries, metric):
  entries = sorted(countries.values(), key=lambda entry: (-entry[metric], entry["country_name"]))
  total = sum(entry[metric] for entry in entries)
  peak = max((entry[metric] for entry in entries), default=0)
  return [dict(entry, percent=100 * entry[metric] / total if total else 0,
               interest=100 * entry[metric] / peak if peak else 0) for entry in entries]


def _add_country(target, country, visits, pageviews):
  entry = target.setdefault(country["country_code"], dict(country, visits=0, pageviews=0))
  entry["visits"] += visits
  entry["pageviews"] += pageviews


@anvil.server.callable(require_user=True)
def get_creator_site_analytics(range_key="30d", country_code=None, page=None, include_creator=False,
                               metric="visits", start_date=None, end_date=None):
  _creator._require_creator()
  now = _now()
  try:
    start, end, country_code = _filters(range_key, country_code, page, include_creator,
                                       metric, start_date, end_date, now)
  except ValueError as error:
    return {"ok": False, "message": str(error)}
  table = _table()
  if table is None:
    return {"ok": False, "message": _SETUP_MESSAGE}
  first, last_updated = _collection_dates(table, now)
  if range_key == "all" and first is not None:
    start = first.date()
  series_start = max(start, first.date()) if first is not None else end + timedelta(days=1)
  bucket, series = _buckets(series_start, end, now) if series_start <= end else ("day", [])
  global_countries, selected_countries = {}, {}
  pages: dict[str, dict[str, Any]] = {key: {"key": key, "label": label, "visits": 0, "pageviews": 0} for key, label in _PAGES.items()}
  visits = pageviews = 0
  lower = datetime.combine(start, time.min, timezone.utc)
  upper = datetime.combine(end + timedelta(days=1), time.min, timezone.utc)
  bucket_index = 0
  records = table.search(order_by("started_at"), started_at=q.between(lower, upper))
  for row in records:
    try:
      stamp = _utc(row["started_at"])
      if stamp is None or stamp > now or (not include_creator and row["is_creator"] is True):
        continue
      counts = _page_counts(row)
      views = counts.get(page, 0) if page is not None else _count(row["page_views"])
      if page is not None and views == 0:
        continue
      country = _country(row["country_code"])
      _add_country(global_countries, country, 1, views)
      if country_code is not None and country["country_code"] != country_code:
        continue
      visits += 1
      pageviews += views
      _add_country(selected_countries, country, 1, views)
      for key in _PAGES:
        count = counts.get(key, 0)
        if count and (page is None or page == key):
          pages[key]["visits"] += 1
          pages[key]["pageviews"] += count
      day = stamp.date().isoformat()
      while bucket_index < len(series) and series[bucket_index]["end_date"] < day:
        bucket_index += 1
      if bucket_index < len(series) and series[bucket_index]["date"] <= day:
        series[bucket_index]["visits"] += 1
        series[bucket_index]["pageviews"] += views
    except RowDeleted:
      continue
  peak = max((point[metric] for point in series), default=0)
  for point in series:
    point["interest"] = 100 * point[metric] / peak if peak else 0
  page_items = [dict(entry, percent=100 * entry["pageviews"] / pageviews if pageviews else 0)
                for entry in pages.values() if entry["pageviews"]]
  page_items.sort(key=lambda entry: (-entry["pageviews"], entry["label"]))
  countries = _rank_countries(selected_countries, metric)
  return {"ok": True, "range": {"key": range_key, "start_date": start.isoformat(), "end_date": end.isoformat(),
                                 "bucket": bucket},
          "filters": {"country_code": country_code, "page": page, "include_creator": include_creator},
          "metric": metric, "summary": {"visits": visits, "pageviews": pageviews,
            "countries": sum(country["country_code"] != "XX" for country in countries),
            "interest": series[-1]["interest"] if series else 0, "peak": peak},
          "series": series, "countries": countries, "pages": page_items,
          "available_countries": _rank_countries(global_countries, metric),
          "available_pages": [{"key": key, "label": label} for key, label in _PAGES.items()],
          "first_collected_at": first, "last_updated_at": last_updated, "empty": visits == 0,
          "methodology": _METHOD}
