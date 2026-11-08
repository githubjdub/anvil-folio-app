"""Recalculate a selected set of securities from already loaded daily history."""

from datetime import date
import math


def _date_from_iso(value):
  return date(*[int(part) for part in value.split("-")])


def _sector_allocation(source, securities):
  allocation = dict(source)
  holdings = []
  groups = {}
  for security in sorted(securities, key=lambda item: item["symbol"]):
    if security["shares"] <= 0:
      continue
    holding = {key: security[key] for key in (
      "symbol", "company_name", "sector", "shares", "price", "market_value"
    )}
    holdings.append(holding)
    sector = holding["sector"]
    group = groups.setdefault(sector, {
      "sector": sector, "market_value": 0.0, "tickers": []
    })
    group["market_value"] += holding["market_value"]
    group["tickers"].append(holding["symbol"])
  total_value = sum(holding["market_value"] for holding in holdings)
  slices = sorted(groups.values(), key=lambda group: (
    -group["market_value"], group["sector"]
  ))
  for group in slices:
    group["weight"] = group["market_value"] / total_value if total_value else 0.0
  allocation.update({
    "holdings": holdings, "slices": slices, "total_value": total_value,
    "unclassified_tickers": [
      holding["symbol"] for holding in holdings
      if holding["sector"] == "Unclassified"
    ],
  })
  return allocation


def update_sector_classification(source, symbol, sector, override):
  """Update presentation groups without changing prices or return paths."""
  securities = source["selection_data"]["securities"]
  for security in securities:
    if security["symbol"] == symbol:
      security["sector"] = sector
      security["sector_override"] = override
  source["sector_allocation"] = _sector_allocation(source["sector_allocation"], securities)
  source["sector_allocation"]["classification_source"] = "Folio catalog and account choices"
  source["sector_allocation"]["classification_as_of"] = date.today().isoformat()


def analyze_selection(source, selected_symbols):
  """Return an analysis without requesting quotes or altering transactions.

  Per-security NAV includes accumulated dividend cash. Buys contribute capital
  at the beginning of a session and sales withdraw it at the end, matching the
  server's return calculation. The chosen securities' first displayed valuation
  remains the baseline, even when its trade price differs from that valuation.
  A provisional mark affects returns but is not a completed risk observation.
  Treat the result as read-only: an all-security selection reuses the server's
  already computed arrays, and benchmark history is shared in every result.
  """
  selected = set(selected_symbols)
  selection_data = source["selection_data"]
  securities = [security for security in selection_data["securities"]
                if security["symbol"] in selected]
  # The backend has already computed this case. Avoid walking and copying its
  # complete daily history when initially rendering or selecting all again.
  if securities and len(securities) == len(selection_data["securities"]):
    return dict(source)
  result = dict(source)
  metrics = dict(source["metrics"])
  metadata = dict(source["metadata"])
  result["metrics"] = metrics
  result["metadata"] = metadata
  result["sector_allocation"] = _sector_allocation(
    source["sector_allocation"], securities
  )
  result["series"] = []

  if not securities:
    metrics.update({
      "buy_cost": 0.0, "sale_proceeds": 0.0, "market_value": 0.0,
      "dividends": 0.0, "profit": 0.0, "simple_return": None,
      "total_return": None, "annualized_return": None,
      "days": 0, "holding_count": 0,
    })
    metadata.update({
      "start_date": None, "observed_return_sessions": 0,
      "uninvested_sessions": 0,
    })
    return result

  start_date = min(security["start_date"] for security in securities)
  source_points = source["series"]
  paths = []
  buy_cost = sale_proceeds = market_value = dividends = 0.0
  holding_count = 0
  for security in securities:
    paths.append(security["days"])
    buy_cost += security["buy_cost"]
    sale_proceeds += security["sale_proceeds"]
    market_value += security["market_value"]
    dividends += security["dividends"]
    holding_count += security["shares"] > 0
  previous_nav = 0.0
  portfolio_index = 100.0
  series = result["series"]
  observed_sessions = uninvested_sessions = 0
  single_path = paths[0] if len(paths) == 1 else None
  for index, day in enumerate(selection_data["dates"]):
    if day < start_date:
      continue
    if single_path is not None:
      daily = single_path[index]
      nav = daily["nav"]
      contributions = daily["contributions"]
      withdrawals = daily["withdrawals"]
    else:
      nav = contributions = withdrawals = 0.0
      for path in paths:
        daily = path[index]
        nav += daily["nav"]
        contributions += daily["contributions"]
        withdrawals += daily["withdrawals"]
    denominator = previous_nav + contributions
    return_observed = bool(series) and denominator > 0
    session_complete = source_points[index].get("session_complete", True)
    if return_observed:
      portfolio_index *= (nav + withdrawals) / denominator
      observed_sessions += bool(session_complete)
    elif series and session_complete:
      uninvested_sessions += 1
    # selection_data dates and paths use the same positions as source series.
    point = dict(source_points[index])
    point["portfolio_index"] = portfolio_index
    point["return_observed"] = return_observed
    series.append(point)
    previous_nav = nav

  profit = market_value + sale_proceeds + dividends - buy_cost
  days = (_date_from_iso(series[-1]["date"]) - _date_from_iso(series[0]["date"])).days
  annualized_return = None
  provisional = not series[-1].get("session_complete", True)
  if days > 0 and portfolio_index > 0 and not uninvested_sessions and not provisional:
    exponent = math.log(portfolio_index / 100) * 365.25 / days
    if exponent < 700:
      annualized_return = math.exp(exponent) - 1
  metrics.update({
    "buy_cost": buy_cost, "sale_proceeds": sale_proceeds,
    "market_value": market_value, "dividends": dividends, "profit": profit,
    "simple_return": profit / buy_cost if buy_cost else 0.0,
    "total_return": portfolio_index / 100 - 1,
    "annualized_return": annualized_return, "days": days,
    "holding_count": holding_count,
  })
  metadata.update({
    "start_date": series[0]["date"], "end_date": series[-1]["date"],
    "observed_return_sessions": observed_sessions,
    "uninvested_sessions": uninvested_sessions,
    "completed_end_date": next((point["date"] for point in reversed(series)
                                if point.get("session_complete", True)), None),
    "provisional_date": series[-1]["date"] if provisional else None,
  })
  return result
