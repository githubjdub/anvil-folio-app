"""Local date windows for the already-loaded dividend review.

Payment-date views model cash receipts; ex-date views show entitlement. Never
infer a payment date for an unresolved distribution or include a future receipt.
"""
from datetime import date, timedelta


RANGES = (("1M", "1M"), ("3M", "3M"), ("6M", "6M"), ("YTD", "YTD"),
          ("1Y", "1Y"), ("3Y", "3 Years"), ("5Y", "5 Years"),
          ("ALL", "All"), ("CUSTOM", "Custom"))


def _day(value):
  if isinstance(value, date):
    return date(value.year, value.month, value.day)
  year, month, day = str(value).split("-")
  return date(int(year), int(month), int(day))


def _months_before(day, months):
  index = day.year * 12 + day.month - 1 - months
  year, month = index // 12, index % 12 + 1
  next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
  return date(year, month, min(day.day, (next_month - timedelta(days=1)).day))


def dividend_window(ledger, as_of, timeframe="YTD", basis="payment",
                    custom_start=None, custom_end=None, history_start=None):
  end = _day(as_of)
  if basis not in ("payment", "ex"):
    raise ValueError("Choose payment dates or ex-dividend dates.")
  events = [row for row in ledger if row["ex_date"] <= end.isoformat()]
  first = min([_day(row["ex_date"]) for row in events] +
              ([_day(history_start)] if history_start else [end]))
  if timeframe == "CUSTOM":
    if custom_start is None or custom_end is None:
      raise ValueError("Choose both a start date and an end date.")
    start, end = _day(custom_start), _day(custom_end)
    if start > end:
      raise ValueError("Choose a start date on or before the end date.")
    if end > _day(as_of):
      raise ValueError("The end date cannot be after the portfolio snapshot (%s)." % as_of)
  elif timeframe == "ALL":
    start = first
  elif timeframe == "YTD":
    start = date(end.year, 1, 1)
  elif timeframe in ("1M", "3M", "6M", "1Y", "3Y", "5Y"):
    start = _months_before(end, {"1M": 1, "3M": 3, "6M": 6,
                                "1Y": 12, "3Y": 36, "5Y": 60}[timeframe])
  else:
    raise ValueError("Choose a dividend timeframe.")

  low, high = start.isoformat(), end.isoformat()
  field = "payment_date" if basis == "payment" else "ex_date"
  rows = [row for row in events if row.get(field) and low <= row[field] <= high
          and (basis != "payment" or not row.get("unsupported"))]
  rows = sorted(rows, key=lambda row: (row[field], row["symbol"]), reverse=True)
  unresolved = [row for row in events if low <= row["ex_date"] <= high
                and (not row.get("payment_date") or row.get("unsupported"))]
  unavailable = sum(row.get("earned") is None for row in rows)
  values = [row["earned"] for row in rows if row.get("earned") is not None]

  # Keep zero-payment months visible, but don't invent pre-portfolio coverage.
  chart_start = max(start, first)
  months = (end.year - chart_start.year) * 12 + end.month - chart_start.month + 1
  step = 1 if months <= 36 else 3 if months <= 120 else 12
  buckets = {}
  if chart_start <= end:
    cursor = date(chart_start.year, ((chart_start.month - 1) // step) * step + 1, 1)
    while cursor <= end:
      buckets[cursor.isoformat()] = 0
      index = cursor.year * 12 + cursor.month - 1 + step
      cursor = date(index // 12, index % 12 + 1, 1)
  for row in rows:
    if row.get("earned") is not None:
      day = _day(row[field])
      bucket = date(day.year, ((day.month - 1) // step) * step + 1, 1).isoformat()
      buckets[bucket] = buckets.get(bucket, 0) + row["earned"]
  return {"start": low, "end": high, "rows": rows,
          "total": None if rows and not values else sum(values),
          "unavailable_count": unavailable, "unconfirmed_count": len(unresolved),
          "security_count": len(set(row["symbol"] for row in rows)),
          "chart": [{"date": key, "amount": buckets[key]} for key in sorted(buckets)],
          "grouping": {1: "month", 3: "quarter", 12: "year"}[step],
          "limited_history": start < first, "history_start": first.isoformat()}
