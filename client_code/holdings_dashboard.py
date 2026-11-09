"""Small, read-only holding snapshots built from the saved analysis payload.

The quarter activity uses logged cash flows, so a stock split never masquerades
as a purchase. Returns remove buy/sell flows from invested market value and
exclude dividend cash. Daily history is borrowed, never copied or repriced.
"""

import math


ACTIVITIES = (
  ("new", "New positions"), ("added", "Bought more"),
  ("trimmed", "Sold some"), ("closed", "Closed positions"),
  ("mixed", "Bought and sold"),
)


def quarter_periods(dates):
  """Return one pair of inclusive indices per available calendar quarter."""
  periods = []
  for index, day in enumerate(dates):
    quarter = (int(day[5:7]) - 1) // 3 + 1
    key = "%s-Q%d" % (day[:4], quarter)
    if not periods or periods[-1]["key"] != key:
      periods.append({"key": key, "label": "Q%d %s" % (quarter, day[:4]),
                      "start": index, "end": index})
    else:
      periods[-1]["end"] = index
  return periods


def _held_return(days, start, end):
  """Compound marked-value returns while capital is invested, without cash.

  The previous close is the quarter baseline when present. An incomplete first
  history day remains the baseline, matching the portfolio's existing charts.
  Reinvesting after a fully closed position restarts exposure, without treating
  the outside cash or the dormant interval as an investment return.
  """
  baseline = max(0, start - 1)
  if any("market_value" not in days[index] for index in range(baseline, end + 1)):
    return None, None, None
  factor = 1.0
  observations = 0
  first = last = None
  for index in range(baseline + 1, end + 1):
    point = days[index]
    if "contributions" not in point or "withdrawals" not in point:
      return None, None, None
    previous = days[index - 1]["market_value"]
    denominator = previous + point["contributions"]
    if denominator <= 0:
      continue
    change = (point["market_value"] + point["withdrawals"]) / denominator
    factor *= change
    if not math.isfinite(factor) or change < 0:
      return None, None, None
    observations += 1
    if first is None:
      first = index - 1 if previous > 0 else index
    last = index
  return (factor - 1 if observations else None), first, last


def _activity(days, start, end):
  """Classify actual buy/sell activity, including same-quarter round trips."""
  before = days[start - 1] if start else {"shares": 0}
  ending = days[end]
  if "shares" not in before or "shares" not in ending or any(
         "contributions" not in days[index] or "withdrawals" not in days[index]
         for index in range(start, end + 1)):
    return None, 0.0, 0.0
  bought = sum(days[index]["contributions"] for index in range(start, end + 1))
  sold = sum(days[index]["withdrawals"] for index in range(start, end + 1))
  previously_open = before.get("shares", 0) > 0
  is_open = ending.get("shares", 0) > 0
  if not bought and not sold:
    return "unchanged", bought, sold
  if previously_open and not is_open and sold:
    return "closed", bought, sold
  if not previously_open and is_open and bought:
    return "new", bought, sold
  if bought and sold:
    return "mixed", bought, sold
  return ("added" if bought else "trimmed"), bought, sold


def build_dashboard(source, selected_symbols=None, quarter_key=None):
  """Read one quarter, bounded by the selected securities' existing paths.

  Legacy caches can still show latest holdings. Historical share counts,
  activity and returns stay unavailable until the user syncs a newer payload.
  """
  source = source or {}
  selection = source.get("selection_data") or {}
  dates = selection.get("dates") or []
  securities = selection.get("securities") or []
  selected = ({row["symbol"] for row in securities} if selected_symbols is None
              else set(selected_symbols))
  periods = quarter_periods(dates)
  period = next((item for item in periods if item["key"] == quarter_key),
                periods[-1] if periods else None)
  result = {"periods": periods, "period": period, "rows": [], "total": 0.0,
            "open_count": 0, "activity_groups": [], "legacy": False,
            "date": None, "baseline_date": None, "provisional": False}
  if not source or not securities:
    return result
  end = period["end"] if period else 0
  latest = period is None or end == len(dates) - 1
  if period:
    result["date"] = dates[end]
    result["baseline_date"] = dates[max(0, period["start"] - 1)]
    series = source.get("series") or []
    result["provisional"] = (end < len(series)
                             and not series[end].get("session_complete", True))
  activity_names = dict(ACTIVITIES)
  activity_names["unchanged"] = "No trades"
  for security in securities:
    if security["symbol"] not in selected:
      continue
    days = security.get("days") or []
    valid_path = (period is not None and len(days) == len(dates)
                  and all(key in days[end] for key in ("shares", "market_value")))
    if not valid_path:
      result["legacy"] = True
      if not latest:
        continue
      point = security
      change = activity = first = last = None
      bought = sold = 0.0
    else:
      point = days[end]
      activity, bought, sold = _activity(days, period["start"], end)
      change, first, last = _held_return(days, period["start"], end)
      if activity is None:
        result["legacy"] = True
    shares = point.get("shares", 0)
    value = point.get("market_value", 0)
    # Future holdings not yet bought at this historical snapshot are omitted.
    if not shares and not bought and not sold and not point.get("buy_cost", 0):
      continue
    result["rows"].append({
      "symbol": security["symbol"],
      "company_name": security.get("company_name") or security["symbol"],
      "sector": security.get("sector") or "Unclassified",
      "shares": shares, "market_value": value,
      "price": point.get("price"), "return": change,
      "activity": activity, "activity_label": activity_names.get(activity, "Sync for activity"),
      "bought": bought, "sold": sold,
      "return_start": dates[first] if first is not None else None,
      "return_end": dates[last] if last is not None else None,
      "is_open": shares > 0,
    })
  result["rows"].sort(key=lambda row: (-row["market_value"], row["symbol"]))
  result["total"] = sum(row["market_value"] for row in result["rows"])
  result["open_count"] = sum(row["is_open"] for row in result["rows"])
  for row in result["rows"]:
    row["weight"] = row["market_value"] / result["total"] if result["total"] else 0.0
  result["activity_groups"] = [
    {"key": key, "title": title,
     "symbols": sorted(row["symbol"] for row in result["rows"] if row["activity"] == key)}
    for key, title in ACTIVITIES
  ]
  return result


def filter_holdings(rows, query="", activity="all", sort="value", position="open"):
  """Filters affect the table alone; market values and the map stay unchanged."""
  query = str(query or "").strip().lower()
  filtered = [row for row in rows
              if (not query or query in (row["symbol"] + " " + row["company_name"]
                                         + " " + row["sector"]).lower())
              and (activity == "all" or row["activity"] == activity)
              and (position == "all" or row["is_open"])]
  if sort == "symbol":
    return sorted(filtered, key=lambda row: row["symbol"])
  if sort == "return":
    return sorted(filtered, key=lambda row: (row["return"] is None,
                  -(row["return"] or 0), row["symbol"]))
  if sort == "shares":
    return sorted(filtered, key=lambda row: (-row["shares"], row["symbol"]))
  return sorted(filtered, key=lambda row: (-row["market_value"], row["symbol"]))
