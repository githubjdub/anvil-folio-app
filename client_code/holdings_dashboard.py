"""Small, read-only holding snapshots built from the saved analysis payload.

The quarter activity uses logged cash flows, so a stock split never masquerades
as a purchase. Returns remove buy/sell flows from invested market value and
exclude dividend cash. Daily history is borrowed, never copied or repriced.
"""

from datetime import date, timedelta
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


def _number(value):
  if value is None or isinstance(value, bool) or not str(value).strip():
    return None
  try:
    number = float(value)
  except (ValueError, TypeError):
    return None
  return number if math.isfinite(number) else None


def _quarter_partial(period, dates):
  """The endpoint has not yet reached this quarter's final weekday."""
  year, quarter = int(period["key"][:4]), int(period["key"][-1])
  last = date(year + 1, 1, 1) if quarter == 4 else date(year, quarter * 3 + 1, 1)
  last -= timedelta(days=1)
  while last.weekday() > 4:
    last -= timedelta(days=1)
  return dates[period["end"]] < last.isoformat()


def quarterly_share_history(days, dates, periods, end):
  """At most five quarter endpoints, never beyond the selected snapshot.

  Quantities are the actual shares on each historical date. Percent changes
  include stock splits; they are deliberately not labelled trade activity.
  """
  available = [period for period in periods if period["end"] <= end]
  history = []
  before = available[-6]["end"] if len(available) > 5 else None
  previous = _number(days[before].get("shares")) if before is not None and before < len(days) else None
  for period in available[-5:]:
    index = period["end"]
    shares = _number(days[index].get("shares")) if index < len(days) else None
    partial = _quarter_partial(period, dates)
    change = shares / previous - 1 if shares is not None and previous and previous > 0 else None
    history.append({"label": period["label"], "date": dates[index], "shares": shares,
                    "change": change, "partial": partial,
                    "latest": index == end})
    previous = shares
  maximum = max([item["shares"] or 0 for item in history] or [0])
  for item in history:
    item["height"] = max(2, 100 * item["shares"] / maximum) if item["shares"] and maximum else 2
  return history


def display_share_history(history):
  """Small presentation rows shared by the bars and their accessible details."""
  return [dict(item,
    shares_text="Unavailable" if item["shares"] is None else format(item["shares"], ",.8f").rstrip("0").rstrip("."),
    change_text="—" if item["change"] is None else "%+.1f%%" % (100 * item["change"]),
    label_text=item["label"] + (" · partial" if item["partial"] else ""),
  ) for item in history]


def _basis_trades(source, transactions, dates):
  """Keep source row order, resolved session dates and entered-price provenance."""
  summaries = source.get("rows") or []
  trades = {}
  for index, transaction in enumerate(transactions or []):
    ticker = str(transaction.get("ticker") or "").strip().upper().split(":", 1)[0].replace(".", "-")
    summary = summaries[index] if index < len(summaries) else {}
    entered = transaction.get("trade_date")
    entered = entered.isoformat() if hasattr(entered, "isoformat") else str(entered or "")
    session = summary.get("entry_date_used") or next((day for day in dates if day >= entered), None)
    shares = _number(transaction.get("shares"))
    price = _number(transaction.get("trade_price"))
    estimated = price is None
    if estimated:
      price = _number(summary.get("entry_price"))
    if not ticker or session is None or shares is None or shares <= 0:
      continue
    trades.setdefault(ticker, {}).setdefault(session, []).append({
      "action": transaction.get("action"), "shares": shares,
      "price": price if price is not None and price > 0 else None,
      "estimated": estimated, "row": index, "entered_date": entered,
    })
  return trades


def remaining_fifo_basis(days, dates, end, trades):
  """Remaining lot basis using synced executions, preserving basis on splits.

  Saved daily quantities identify the pre-trade split factor exactly: ending
  shares = previous shares × factor + today's buys − today's sales. Every
  reconstructed endpoint is reconciled with the saved position. Missing daily
  quantities, trades or execution prices leave basis unavailable.
  """
  if len(days) != len(dates):
    return None, False
  lots = []
  previous = 0.0
  for index in range(end + 1):
    owned = _number(days[index].get("shares"))
    if owned is None or owned < 0:
      return None, False
    daily = sorted(trades.get(dates[index], []), key=lambda item: (
      item["entered_date"], item["action"] != "Buy", item["row"]))
    net = sum(trade["shares"] * (1 if trade["action"] == "Buy" else -1) for trade in daily)
    tolerance = max(1.0, owned, previous) * 1e-10
    split = False
    if previous > 0:
      factor = (owned - net) / previous
      if not math.isfinite(factor) or factor <= 0:
        return None, False
      split = factor != 1
      if split:
        for lot in lots:
          lot["shares"] *= factor
    elif abs(owned - net) > tolerance:
      return None, False
    for trade in daily:
      if trade["action"] == "Buy":
        lots.append({"shares": trade["shares"],
                     "basis": trade["shares"] * trade["price"] if trade["price"] is not None else None,
                     "estimated": trade["estimated"]})
      elif trade["action"] == "Sell":
        remaining = trade["shares"]
        for lot in lots:
          if remaining <= tolerance:
            break
          sold = min(remaining, lot["shares"])
          if lot["basis"] is not None and lot["shares"]:
            lot["basis"] *= max(0, 1 - sold / lot["shares"])
          lot["shares"] -= sold
          remaining -= sold
        if remaining > tolerance:
          return None, False
        lots = [lot for lot in lots if lot["shares"] > tolerance]
      else:
        return None, False
    if (daily or split or index == end) and abs(sum(lot["shares"] for lot in lots) - owned) > tolerance:
      return None, False
    previous = owned
  if any(lot["basis"] is None for lot in lots):
    return None, False
  basis = sum(lot["basis"] for lot in lots)
  return (basis if math.isfinite(basis) else None), any(lot["estimated"] for lot in lots)


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


def build_dashboard(source, selected_symbols=None, quarter_key=None, transactions=None):
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
  basis_trades = _basis_trades(source, transactions, dates)
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
    basis, basis_estimated = remaining_fifo_basis(days, dates, end, basis_trades.get(security["symbol"], {})) if valid_path else (None, False)
    result["rows"].append({
      "symbol": security["symbol"],
      "company_name": security.get("company_name") or security["symbol"],
      "sector": security.get("sector") or "Unclassified",
      "shares": shares, "market_value": value,
      "cost_basis": basis, "basis_estimated": basis_estimated,
      "share_history": quarterly_share_history(days, dates, periods, end) if valid_path else [],
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
  """Filters affect the table alone; total portfolio market value stays fixed."""
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
