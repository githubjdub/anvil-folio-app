"""Small dashboard summaries from an already loaded, selected analysis.

``summarize_portfolio(history)`` accepts Form1's ``_history`` (the result of
``analyze_selection``), not a raw transaction list. It never changes the input,
reselects securities, requests quotes, or makes an RPC. Rates are fractions,
ready for the caller's normal money/percentage formatters.

Returns use the dividend-inclusive time-weighted portfolio index, never return
on total contributed capital. Three-year returns require completed, continuous
history through the endpoint; shorter histories show an explicitly labelled
available-period return. A provisional endpoint is labelled in the result.
"""

from datetime import date, timedelta
import math

from .portfolio_periods import quarterly_returns
from .portfolio_display import empty_summary


def _date(value):
  return date(*[int(part) for part in value.split("-")])


def _quarter_start(day):
  return date(day.year, ((day.month - 1) // 3) * 3 + 1, 1)


def _next_quarter(start):
  return date(start.year + 1, 1, 1) if start.month == 10 else date(start.year, start.month + 3, 1)


def _ratio(baseline, ending):
  if (baseline is None or ending is None or not math.isfinite(baseline)
      or not math.isfinite(ending) or baseline <= 0 or ending < 0):
    return None
  return ending / baseline - 1


def _continuous(points):
  return all(point.get("session_complete", True) and point.get("return_observed", True)
             for point in points)


def summarize_portfolio(history):
  """Return numeric metrics and honest labels for the portfolio header.

  Keys: ``value``, ``holding_count``, ``prior_quarter_return``,
  ``prior_quarter_label``, ``long_return``, ``long_return_label``,
  ``top20_weight``, ``as_of``, ``return_is_provisional``, ``return_note`` and
  ``prior_quarter_note``. Missing returns/weights are ``None``. The prior-quarter
  cell uses the latest completed calendar quarter at the analysis endpoint,
  accepting that endpoint's own quarter only on its completed final day.
  Closed markets may place a quarter-end close up to seven days before its
  calendar end. The server's validated session series supplies interior dates.
  """
  summary = empty_summary()
  if history is None:
    return summary

  metrics = history["metrics"]
  allocation = history.get("sector_allocation") or {}
  holdings = allocation.get("holdings", [])
  summary.update(value=metrics["market_value"], holding_count=metrics["holding_count"],
                 as_of=history["metadata"].get("end_date"))
  total = allocation.get("total_value", metrics["market_value"])
  if total > 0:
    summary["top20_weight"] = sum(sorted((holding["market_value"] for holding in holdings), reverse=True)[:20]) / total

  points = history.get("series") or []
  if not points:
    return summary
  ending = points[-1]
  end = _date(ending["date"])
  summary["as_of"] = ending["date"]
  provisional = not ending.get("session_complete", True)
  summary["return_is_provisional"] = provisional

  # A quarter that has merely started must never masquerade as completed.
  start = _quarter_start(end)
  quarter_end = _next_quarter(start) - timedelta(days=1)
  if end < quarter_end or provisional:
    quarter_end = start - timedelta(days=1)
    start = _quarter_start(quarter_end)
  quarter_key = "%s-Q%s" % (start.year, (start.month - 1) // 3 + 1)
  summary["prior_quarter_label"] = "Q%s %s return" % ((start.month - 1) // 3 + 1, start.year)
  quarter = next((row for row in quarterly_returns(points, {}) if row["quarter_key"] == quarter_key), None)
  quarter_points = [point for point in points if start.isoformat() <= point["date"] <= quarter_end.isoformat()]
  baseline = next((point for point in reversed(points) if point["date"] < start.isoformat()), None)
  if (quarter and quarter_points and baseline and end >= quarter_end
      and _date(baseline["date"]) >= start - timedelta(days=7)
      and _date(quarter_points[-1]["date"]) >= quarter_end - timedelta(days=7)
      and _continuous(quarter_points) and baseline.get("session_complete", True)):
    summary["prior_quarter_return"] = quarter["portfolio_return"]
    summary["prior_quarter_note"] = "Dividend-inclusive time-weighted return for the full completed quarter."

  # An anniversary can fall on a leap day or a closed market. Use the latest
  # prior close within seven days, and do not silently shorten a three-year span.
  target = date(end.year - 3, end.month, 28 if end.month == 2 and end.day == 29 else end.day)
  baseline_index = next((index for index in range(len(points) - 1, -1, -1)
                         if points[index]["date"] <= target.isoformat()), None)
  if (baseline_index is not None and not provisional
      and _date(points[baseline_index]["date"]) >= target - timedelta(days=7)
      and _continuous(points[baseline_index + 1:])):
    summary["long_return"] = _ratio(points[baseline_index]["portfolio_index"], ending["portfolio_index"])
    summary["long_return_label"] = "3-year return"
    summary["return_note"] = "Cumulative dividend-inclusive time-weighted return over three years, using the prior available closing baseline."
  elif len(points) > 1:
    summary["long_return"] = _ratio(points[0]["portfolio_index"], ending["portfolio_index"])
    summary["return_note"] = "Dividend-inclusive time-weighted return from %s to %s." % (points[0]["date"], ending["date"])
    if provisional:
      summary["long_return_label"] = "Available-period return · live"
      summary["return_note"] += " The latest session is provisional."
    elif not _continuous(points[1:]):
      summary["return_note"] += " Includes gaps with no invested capital; this is not a full three-year return."
  return summary
