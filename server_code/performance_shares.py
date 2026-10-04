"""Revocable, unlisted performance snapshots with server-enforced privacy.

Only the finished public document is stored. Editable transactions, account
details and per-security history never become part of a share snapshot.
"""

from datetime import datetime, timezone
import math
import re
import secrets

import anvil.server
import anvil.users
from anvil.tables import app_tables, RowDeleted

from .portfolio_analytics import (
  AnalysisInputError, _as_date, _validate, analyze_portfolio_history,
)


_BENCHMARKS = {"sp500", "nasdaq", "spy", "qqq", "dia", "iwm", "vti"}
_DEFAULT_TITLE = "My portfolio performance"
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
_METRIC_FIELDS = (
  "portfolio_return", "benchmark_return", "excess_return",
  "portfolio_annualized_return", "portfolio_volatility", "portfolio_sharpe",
  "benchmark_volatility", "benchmark_sharpe", "observations",
  "benchmark_observations", "uninvested_sessions",
)
_AMOUNT_FIELDS = ("market_value", "dividends", "buy_cost", "sale_proceeds", "profit")
_HOLDING_FIELDS = (
  "symbol", "company_name", "sector", "shares", "price", "market_value", "weight",
)
_METHOD = (
  "Self-reported transactions, recalculated using historical daily prices. "
  "Daily time-weighted portfolio returns include dividends retained as cash; "
  "both chart lines start at zero on the same completed closing date. "
  "Buys contribute capital at the beginning of a session and sales withdraw "
  "it at the end. The first completed close is the baseline. Non-trading "
  "transaction dates use the next completed session; splits adjust quantities. "
  "Volatility and Sharpe use sample daily returns, 252 trading days per year, "
  "the stated annual risk-free rate and at least 20 observations. Zero-capital "
  "sessions remain flat and are excluded from portfolio risk estimates. "
  "This is a fixed snapshot, not a live account connection."
)


def _current_user():
  user = anvil.users.get_user()
  if user is None:
    raise anvil.users.AuthenticationFailed("Sign in to share your performance.")
  return user


def _share_date(value, label):
  try:
    return _as_date(value)
  except (ValueError, TypeError):
    raise AnalysisInputError("Choose a valid %s date." % label)


def _selected_transactions(transactions, as_of_date, risk_free_rate, selected_symbols):
  """Validate all inputs, then analyze only the explicitly selected securities."""
  clean, as_of, rate = _validate(transactions, as_of_date, risk_free_rate)
  available = {item["symbol"] for item in clean}
  if selected_symbols is None:
    selected = available
  else:
    if (not isinstance(selected_symbols, (list, tuple))
        or not 1 <= len(selected_symbols) <= 25
        or any(not isinstance(symbol, str) for symbol in selected_symbols)):
      raise AnalysisInputError("Select at least one analyzed security to share.")
    selected = {symbol.strip().upper().replace(".", "-") for symbol in selected_symbols}
    if not selected or not selected.issubset(available):
      raise AnalysisInputError("Choose only securities present in this portfolio.")
  filtered = [{
    "ticker": item["ticker"], "trade_date": item["trade_date"].isoformat(),
    "action": item["action"], "shares": item["shares"],
    "trade_price": item["trade_price"],
  } for item in clean if item["symbol"] in selected]
  return filtered, as_of, rate


def _risk(indices, observed, rate):
  """Match the readout's sample daily volatility and annualized Sharpe."""
  if any(indices[index - 1] <= 0 for index in range(1, len(indices))
         if observed[index]):
    return None, None
  daily = [indices[index] / indices[index - 1] - 1
           for index in range(1, len(indices)) if observed[index]]
  if len(daily) < 20:
    return None, None
  daily_rate = math.expm1(math.log1p(rate) / 252)
  excess = [value - daily_rate for value in daily]
  mean = math.fsum(excess) / len(excess)
  variance = math.fsum((value - mean) ** 2 for value in excess) / (len(excess) - 1)
  daily_volatility = math.sqrt(variance)
  volatility = daily_volatility * math.sqrt(252)
  sharpe = mean / daily_volatility * math.sqrt(252) if daily_volatility > 1e-12 else None
  return volatility, sharpe


def _build_snapshot(analysis, benchmark, benchmark_return, start_date, end_date,
                    visibility, title, created_at):
  """Rebase both series together and project an explicitly public document."""
  benchmark_key = benchmark + "_" + benchmark_return
  benchmark_data = analysis["benchmarks"].get(benchmark_key)
  if not benchmark_data or not benchmark_data.get("series"):
    raise AnalysisInputError("This benchmark is unavailable for sharing. Sync again or choose another benchmark.")
  source_points = analysis["series"]
  requested_start = (_share_date(start_date, "start").isoformat()
                     if start_date is not None else source_points[0]["date"])
  requested_end = (_share_date(end_date, "end").isoformat()
                   if end_date is not None else source_points[-1]["date"])
  if requested_start > requested_end:
    raise AnalysisInputError("Choose a start date on or before the end date.")
  selected = [point for point in source_points
              if requested_start <= point["date"] <= requested_end]
  benchmark_by_date = {point["date"]: point["index"] for point in benchmark_data["series"]}
  first_benchmark, last_benchmark = min(benchmark_by_date), max(benchmark_by_date)
  common = [point for point in selected
            if first_benchmark <= point["date"] <= last_benchmark]
  if len(common) < 2:
    raise AnalysisInputError("Choose a period with at least two overlapping completed trading closes.")
  if any(point["date"] not in benchmark_by_date for point in common):
    raise AnalysisInputError("This benchmark has missing daily closes. Sync again or choose another benchmark.")
  portfolio = [point["portfolio_index"] for point in common]
  benchmark_indices = [benchmark_by_date[point["date"]] for point in common]
  if portfolio[0] <= 0:
    raise AnalysisInputError("The portfolio has no positive return baseline in this period. Choose another start date.")
  observed = [point["return_observed"] for point in common]
  portfolio_returns = [value / portfolio[0] - 1 for value in portfolio]
  benchmark_returns = [value / benchmark_indices[0] - 1 for value in benchmark_indices]
  rate = analysis["metadata"]["risk_free_rate"]
  volatility, sharpe = _risk(portfolio, observed, rate)
  benchmark_volatility, benchmark_sharpe = _risk(
    benchmark_indices, [True] * len(benchmark_indices), rate,
  )
  observations = sum(bool(value) for value in observed[1:])
  inactive = len(common) - 1 - observations
  days = (_as_date(common[-1]["date"]) - _as_date(common[0]["date"])).days
  annualized = None
  if days > 0 and portfolio[-1] > 0 and not inactive:
    exponent = math.log(portfolio[-1] / portfolio[0]) * 365.25 / days
    if exponent < 700:
      annualized = math.expm1(exponent)
  snapshot = {
    "version": 1, "title": title, "visibility": visibility,
    "created_at": created_at.isoformat(),
    "start_date": common[0]["date"], "end_date": common[-1]["date"],
    "currency": "USD", "risk_free_rate": rate,
    "benchmark": {
      "key": benchmark, "name": benchmark_data["name"],
      "return_mode": benchmark_return, "source_url": benchmark_data["source_url"],
    },
    "metrics": {
      "portfolio_return": portfolio_returns[-1],
      "benchmark_return": benchmark_returns[-1],
      "excess_return": portfolio_returns[-1] - benchmark_returns[-1],
      "portfolio_annualized_return": annualized,
      "portfolio_volatility": volatility, "portfolio_sharpe": sharpe,
      "benchmark_volatility": benchmark_volatility, "benchmark_sharpe": benchmark_sharpe,
      "observations": observations, "benchmark_observations": len(common) - 1,
      "uninvested_sessions": inactive,
    },
    "series": [{
      "date": point["date"], "portfolio_return": portfolio_returns[index],
      "benchmark_return": benchmark_returns[index],
    } for index, point in enumerate(common)],
    "method": _METHOD, "source": "Yahoo Finance daily history",
  }
  if visibility == "full":
    # The date label separates the analyzed position values from a shorter
    # performance window. Amounts are never mislabeled as window-only profit.
    snapshot["amounts_as_of_date"] = analysis["metadata"]["end_date"]
    snapshot["amounts"] = {key: analysis["metrics"][key] for key in _AMOUNT_FIELDS}
    holdings = analysis["sector_allocation"]["holdings"]
    market_value = analysis["metrics"]["market_value"]
    snapshot["holdings"] = [dict(
      {key: holding[key] for key in _HOLDING_FIELDS if key != "weight"},
      weight=holding["market_value"] / market_value if market_value else 0.0,
    ) for holding in sorted(holdings, key=lambda item: (-item["market_value"], item["symbol"]))]
  return _public_snapshot(snapshot)


def _public_snapshot(stored):
  """Allowlist again on read so extra stored fields can never leak publicly."""
  result = {key: stored[key] for key in (
    "version", "title", "visibility", "created_at", "start_date", "end_date",
    "currency", "risk_free_rate", "method", "source",
  )}
  result["benchmark"] = {key: stored["benchmark"][key] for key in (
    "key", "name", "return_mode", "source_url",
  )}
  result["metrics"] = {key: stored["metrics"][key] for key in _METRIC_FIELDS}
  result["series"] = [{key: point[key] for key in (
    "date", "portfolio_return", "benchmark_return",
  )} for point in stored["series"]]
  if stored["visibility"] == "full":
    result["amounts_as_of_date"] = stored["amounts_as_of_date"]
    result["amounts"] = {key: stored["amounts"][key] for key in _AMOUNT_FIELDS}
    result["holdings"] = [{key: holding[key] for key in _HOLDING_FIELDS}
                          for holding in stored["holdings"]]
  return result


def _owned_share(share_id, user):
  if not isinstance(share_id, str) or not share_id:
    return None
  row = app_tables.performance_shares.get_by_id(share_id)
  if row is None:
    return None
  try:
    return row if row["owner"] == user else None
  except RowDeleted:
    return None


@anvil.server.callable(require_user=True)
def create_performance_share(transactions, as_of_date, risk_free_rate,
                             selected_symbols=None, benchmark="sp500",
                             benchmark_return="total", start_date=None,
                             end_date=None, visibility="private", title=_DEFAULT_TITLE):
  user = _current_user()
  origin = get_performance_share_origin()
  if not origin["ok"]:
    return {"ok": False, "message": origin["message"]}
  try:
    if visibility not in ("private", "full"):
      raise AnalysisInputError("Choose percentages only or full details for your share.")
    if not isinstance(benchmark, str) or benchmark not in _BENCHMARKS:
      raise AnalysisInputError("Choose a supported comparison benchmark.")
    if benchmark_return not in ("total", "price"):
      raise AnalysisInputError("Choose total return or price return for the benchmark.")
    if benchmark == "nasdaq" and benchmark_return != "price":
      raise AnalysisInputError("Nasdaq Composite supports price returns only.")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
      raise AnalysisInputError("Give your share a title of 1–80 characters.")
    title = title.strip()
    filtered, as_of, rate = _selected_transactions(
      transactions, as_of_date, risk_free_rate, selected_symbols,
    )
    # Check the chosen dates before incurring an external history request.
    if start_date is not None:
      _share_date(start_date, "start")
    if end_date is not None:
      _share_date(end_date, "end")
    if (start_date is not None and end_date is not None
        and _share_date(start_date, "start") > _share_date(end_date, "end")):
      raise AnalysisInputError("Choose a start date on or before the end date.")
    analysis = analyze_portfolio_history(filtered, as_of, rate)
    if not analysis["ok"]:
      return analysis
    now = datetime.now(timezone.utc)
    snapshot = _build_snapshot(
      analysis, benchmark, benchmark_return, start_date, end_date,
      visibility, title, now,
    )
  except AnalysisInputError as error:
    return {"ok": False, "message": str(error)}
  token = secrets.token_urlsafe(32)
  while app_tables.performance_shares.get(token=token) is not None:
    token = secrets.token_urlsafe(32)
  row = app_tables.performance_shares.add_row(
    owner=user, token=token, title=title, visibility=visibility,
    created_at=now, revoked=False, snapshot=snapshot,
  )
  return {"ok": True, "share": row, "snapshot": snapshot}


@anvil.server.callable(require_user=True)
def list_performance_shares():
  return sorted(
    app_tables.performance_shares.search(owner=_current_user()),
    key=lambda row: row["created_at"], reverse=True,
  )


@anvil.server.callable(require_user=True)
def revoke_performance_share(share_id):
  row = _owned_share(share_id, _current_user())
  if row is None:
    return {"ok": False, "message": "This share is no longer available to your account."}
  row.update(revoked=True)
  return {"ok": True}


@anvil.server.callable
def get_performance_share(token):
  unavailable = {"ok": False, "message": "This share is unavailable or its owner has revoked the link."}
  if not isinstance(token, str) or _TOKEN_PATTERN.fullmatch(token) is None:
    return unavailable
  row = app_tables.performance_shares.get(token=token)
  if row is None or row["revoked"]:
    return unavailable
  return {"ok": True, "snapshot": _public_snapshot(row["snapshot"])}


@anvil.server.callable
def get_performance_share_origin():
  # A published root has no temporary preview authentication token. Never
  # substitute get_app_origin() or a browser's current private preview URL.
  published_origin = anvil.server.get_app_origin("published").rstrip("/")
  current_origin = anvil.server.get_app_origin().rstrip("/")
  if current_origin != published_origin:
    return {
      "ok": False, "base_url": published_origin,
      "message": "Open the published app to create share links. Preview links cannot be shared.",
    }
  return {"ok": True, "base_url": published_origin}
