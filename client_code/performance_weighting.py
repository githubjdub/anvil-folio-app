"""Hypothetical equal-dollar performance from already synced security paths."""

import math


def equal_weighted_history(source, selected_symbols):
  """Link equal-weighted daily returns without changing the actual portfolio.

  Each session equally weights securities with invested capital that day:
  prior position value plus beginning-of-session purchases. Sales are treated
  as end-of-session withdrawals. Newly entered positions join on their trade
  session; closed positions leave after it. Dividend income is included in the
  session earned, and the hypothetical basket rebalances daily without costs.
  Accumulated dividend cash is excluded from the next day's invested weights.
  The first displayed valuation is a zero-return baseline, matching the actual
  portfolio chart. Empty invested sessions stay flat and unobserved for risk.
  No quotes, current-only holdings or future position weights are used.
  """
  points = source.get("series") or []
  if not points:
    raise ValueError("Select at least one synced security to compare returns.")
  selected = set(selected_symbols)
  data = source.get("selection_data") or {}
  dates = data.get("dates") or []
  paths = [security.get("days") or [] for security in data.get("securities", [])
           if security["symbol"] in selected]
  if not paths or any(len(path) != len(dates) for path in paths):
    raise ValueError("Refresh your portfolio to load daily history for Equal-Weighted.")
  fields = ("market_value", "nav", "contributions", "withdrawals")
  first = points[0]["date"]
  point_by_date = {point["date"]: point for point in points}
  # Keep hypothetical return data separate from actual portfolio value/P&L.
  result = {"benchmarks": source["benchmarks"]}
  series = []
  level = 100.0
  for index, day in enumerate(dates):
    if day < first or day not in point_by_date:
      continue
    growth = 0.0
    active = 0
    for path in paths:
      current = path[index]
      previous = path[index - 1] if index else None
      if any(field not in current for field in fields):
        raise ValueError("Refresh your portfolio to load position values for Equal-Weighted.")
      if previous is not None and ("market_value" not in previous or "nav" not in previous):
        raise ValueError("Refresh your portfolio to load position values for Equal-Weighted.")
      previous_value = previous["market_value"] if previous else 0.0
      previous_cash = previous["nav"] - previous_value if previous else 0.0
      capital = previous_value + current["contributions"]
      if capital <= 0:
        continue
      dividend = current["nav"] - current["market_value"] - previous_cash
      factor = (current["market_value"] + current["withdrawals"] + dividend) / capital
      if not math.isfinite(factor) or factor < 0:
        raise ValueError("This saved history cannot support Equal-Weighted. Refresh your portfolio.")
      growth += factor
      active += 1
    observed = bool(series) and active > 0 and level > 0
    if observed:
      level *= growth / active
    if not math.isfinite(level):
      raise ValueError("Equal-Weighted exceeds the supported return range for this history.")
    point = dict(point_by_date[day])
    point.update(portfolio_index=level, return_observed=observed)
    series.append(point)
  if len(series) != len(points):
    raise ValueError("Refresh your portfolio to align daily history for Equal-Weighted.")
  result["series"] = series
  result["metadata"] = dict(source["metadata"], performance_weighting="equal")
  return result
