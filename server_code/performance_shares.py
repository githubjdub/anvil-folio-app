import anvil.secrets
"""Revocable, unlisted percentage-only performance snapshots.

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
  benchmark_points = {point["date"]: point for point in benchmark_data["series"]}
  benchmark_by_date = {day: point["index"] for day, point in benchmark_points.items()}
  first_benchmark, last_benchmark = min(benchmark_by_date), max(benchmark_by_date)
  common = [point for point in selected
            if first_benchmark <= point["date"] <= last_benchmark]
  if len(common) < 2:
    raise AnalysisInputError("Choose a period with at least two overlapping trading valuations.")
  if any(point["date"] not in benchmark_by_date for point in common):
    raise AnalysisInputError("This benchmark has missing daily closes. Sync again or choose another benchmark.")
  portfolio = [point["portfolio_index"] for point in common]
  benchmark_indices = [benchmark_by_date[point["date"]] for point in common]
  if portfolio[0] <= 0:
    raise AnalysisInputError("The portfolio has no positive return baseline in this period. Choose another start date.")
  completed = [point.get("session_complete", True) for point in common]
  benchmark_completed = [benchmark_points[point["date"]].get("session_complete", True)
                         for point in common]
  observed = [point["return_observed"] and completed[index]
              for index, point in enumerate(common)]
  portfolio_returns = [value / portfolio[0] - 1 for value in portfolio]
  benchmark_returns = [value / benchmark_indices[0] - 1 for value in benchmark_indices]
  rate = analysis["metadata"]["risk_free_rate"]
  volatility, sharpe = _risk(portfolio, observed, rate)
  benchmark_volatility, benchmark_sharpe = _risk(
    benchmark_indices, benchmark_completed, rate,
  )
  observations = sum(bool(value) for value in observed[1:])
  benchmark_observations = sum(bool(value) for value in benchmark_completed[1:])
  inactive = sum(completed[index] and not common[index]["return_observed"]
                 for index in range(1, len(common)))
  provisional = not (completed[-1] and benchmark_completed[-1])
  days = (_as_date(common[-1]["date"]) - _as_date(common[0]["date"])).days
  annualized = None
  if days > 0 and portfolio[-1] > 0 and not inactive and not provisional:
    exponent = math.log(portfolio[-1] / portfolio[0]) * 365.25 / days
    if exponent < 700:
      annualized = math.expm1(exponent)
  method = _METHOD
  source = "Yahoo Finance daily history"
  quoted_at = analysis["metadata"].get("quoted_at") if provisional else None
  if provisional:
    method = method.replace("historical daily prices", "historical daily prices and regular-session quotes")
    method = method.replace("same completed closing date", "same starting valuation date")
    method = method.replace("The first completed close is the baseline.",
                            "The first displayed valuation is the baseline.")
    method = method.replace("next completed session", "next trading session")
    method += (" The final valuation is provisional. The unfinished session is excluded "
               "from both volatility and Sharpe, and annualized return is unavailable.")
    if quoted_at:
      method += " The oldest included portfolio regular-session quote was recorded at %s." % quoted_at
    source += " and regular-session quotes"
  snapshot = {
    "version": 1, "title": title, "visibility": "private",
    "created_at": created_at.isoformat(),
    "start_date": common[0]["date"], "end_date": common[-1]["date"],
    "provisional_date": common[-1]["date"] if provisional else None,
    "quoted_at": quoted_at,
    "completed_end_date": next((point["date"] for index, point in reversed(list(enumerate(common)))
                                if completed[index] and benchmark_completed[index]), None),
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
      "observations": observations, "benchmark_observations": benchmark_observations,
      "uninvested_sessions": inactive,
    },
    "series": [{
      "date": point["date"], "portfolio_return": portfolio_returns[index],
      "benchmark_return": benchmark_returns[index],
      "session_complete": bool(completed[index] and benchmark_completed[index]),
    } for index, point in enumerate(common)],
    "method": method, "source": source,
  }
  return _public_snapshot(snapshot)


def _public_snapshot(stored):
  """Only percentages and comparison details, including for older full shares."""
  result = {key: stored[key] for key in (
    "version", "title", "created_at", "start_date", "end_date",
    "currency", "risk_free_rate", "method", "source",
  )}
  result["visibility"] = "private"
  for key in ("provisional_date", "quoted_at", "completed_end_date"):
    result[key] = stored.get(key)
  result["benchmark"] = {key: stored["benchmark"][key] for key in (
    "key", "name", "return_mode", "source_url",
  )}
  result["metrics"] = {key: stored["metrics"][key] for key in _METRIC_FIELDS}
  result["series"] = [dict({key: point[key] for key in (
    "date", "portfolio_return", "benchmark_return",
  )}, session_complete=point.get("session_complete", True))
    for point in stored["series"]]
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
      raise AnalysisInputError("Performance shares show percentages only.")
    # Accept the old positional option without creating another full-details
    # snapshot. Older clients therefore receive the same percentage-only result.
    visibility = "private"
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
