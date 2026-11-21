import anvil.secrets
"""Private, account-owned portfolio documents. Prices are refreshed on analysis."""

from datetime import date, datetime
import base64
import gzip
import hashlib
import json
import math
from typing import Any

import anvil
import anvil.server
import anvil.users
from anvil.tables import app_tables, RowDeleted, in_transaction
from .memberships import membership_for


def _current_user():
  user = anvil.users.get_user()
  if user is None or user["enabled"] is not True:
    raise anvil.users.AuthenticationFailed("Sign in to access your portfolios.")
  return user


def _finite_number(value, message):
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise ValueError(message)
  try:
    number = float(value)
  except (ValueError, OverflowError):
    raise ValueError(message)
  if not math.isfinite(number):
    raise ValueError(message)
  return number


def _validate_inputs(name, transactions, as_of_date, risk_free_rate):
  if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
    raise ValueError("Give your portfolio a name of 1–80 characters.")
  if type(as_of_date) is not date:
    raise ValueError("Choose an analysis date.")
  rate = _finite_number(risk_free_rate, "Enter a valid risk-free rate.")
  if not isinstance(transactions, list) or len(transactions) > 1000:
    raise ValueError("A portfolio can contain up to 1,000 transactions.")

  clean_transactions = []
  for index, transaction in enumerate(transactions, start=1):
    if not isinstance(transaction, dict):
      raise ValueError("Row %d: enter a valid transaction." % index)
    ticker = transaction.get("ticker")
    if not isinstance(ticker, str) or not 1 <= len(ticker.strip()) <= 40:
      raise ValueError("Row %d: enter a ticker of 1–40 characters." % index)
    action = transaction.get("action")
    if action not in ("Buy", "Sell"):
      raise ValueError("Row %d: choose Buy or Sell." % index)
    trade_date = transaction.get("trade_date")
    if not isinstance(trade_date, str):
      raise ValueError("Row %d: choose a trade date." % index)
    try:
      trade_date = date.fromisoformat(trade_date)
    except ValueError:
      raise ValueError("Row %d: choose a valid trade date." % index)
    if trade_date > as_of_date:
      raise ValueError("Every transaction must be on or before the analysis date.")
    shares = _finite_number(
      transaction.get("shares"), "Row %d: enter a valid share count." % index
    )
    if shares <= 0:
      raise ValueError("Row %d: shares must be greater than zero." % index)
    trade_price = transaction.get("trade_price")
    if trade_price is not None:
      message = "Row %d: enter a price per share greater than zero, or leave it blank." % index
      trade_price = _finite_number(trade_price, message)
      if trade_price <= 0:
        raise ValueError(message)
    # Keep editable inputs, including a user's price override, but discard fetched prices.
    clean_transaction = {
      "ticker": ticker.strip().upper(),
      "trade_date": trade_date.isoformat(),
      "action": action,
      "shares": shares,
    }
    if trade_price is not None:
      clean_transaction["trade_price"] = trade_price
    clean_transactions.append(clean_transaction)

  return {
    "name": name.strip(),
    "transactions": clean_transactions,
    "as_of_date": as_of_date,
    "risk_free_rate": rate,
    "transaction_count": len(clean_transactions),
  }


def _analysis_fingerprint(transactions, as_of_date, risk_free_rate):
  """Match only editable inputs; fetched prices never become part of identity."""
  document = [{
    "ticker": str(row["ticker"]).strip().upper(),
    "trade_date": str(row["trade_date"]), "action": row["action"],
    "shares": float(row["shares"]),
    "trade_price": float(row["trade_price"]) if row.get("trade_price") is not None else None,
  } for row in transactions]
  serialized = json.dumps(["sector-history-v2", document, str(as_of_date), round(float(risk_free_rate), 10)],
                          separators=(",", ":"), sort_keys=True, allow_nan=False)
  return hashlib.sha256(serialized.encode()).hexdigest()


def _saved_analysis(portfolio):
  if portfolio is None or not portfolio["analysis_cache"]:
    return None
  expected = _analysis_fingerprint(portfolio["transactions"], portfolio["as_of_date"], portfolio["risk_free_rate"])
  if portfolio["analysis_fingerprint"] != expected:
    return None
  from sectors import refresh_analysis_classifications
  return refresh_analysis_classifications(json.loads(gzip.decompress(portfolio["analysis_cache"].get_bytes())))


def _analysis_values(fingerprint, media, computed_at):
  return {"analysis_cache": media, "analysis_fingerprint": fingerprint,
          "analysis_updated_at": computed_at}


def _staged_analysis():
  # REPL and other server-originated calls have no browser session.
  return anvil.server.session.get("latest_portfolio_analysis") if anvil.server.context.client.type == "browser" else None


@in_transaction
def _store_analysis_if_unchanged(portfolio_id, user, fingerprint, media, computed_at):
  portfolio = _owned_portfolio(portfolio_id, user)
  if portfolio is None:
    return
  expected = _analysis_fingerprint(portfolio["transactions"], portfolio["as_of_date"], portfolio["risk_free_rate"])
  if expected == fingerprint:
    portfolio.update(**_analysis_values(fingerprint, media, computed_at))
    from investor_profiles import refresh_investor_metrics
    refresh_investor_metrics(portfolio, user)


def remember_analysis(transactions, as_of_date, risk_free_rate, analysis, portfolio_id=None):
  """Stage one server-computed result so a subsequent Save can reuse it."""
  fingerprint = _analysis_fingerprint(transactions, as_of_date, risk_free_rate)
  compressed = gzip.compress(json.dumps(analysis, separators=(",", ":"), allow_nan=False).encode(), compresslevel=5)
  media = anvil.BlobMedia("application/gzip", compressed, name="portfolio-analysis.json.gz")
  computed_at = datetime.now()
  user = anvil.users.get_user()
  if anvil.server.context.client.type == "browser":
    session: Any = anvil.server.session
    session["latest_portfolio_analysis"] = (
      fingerprint, base64.b64encode(compressed).decode("ascii"), computed_at,
      user.get_id() if user is not None else None,
    )
  if portfolio_id is not None:
    _store_analysis_if_unchanged(portfolio_id, _current_user(), fingerprint, media, computed_at)


def _owned_portfolio(portfolio_id, user):
  if not isinstance(portfolio_id, str) or not portfolio_id:
    return None
  portfolio = app_tables.portfolios.get_by_id(portfolio_id)
  if portfolio is None:
    return None
  try:
    if portfolio["owner"] != user:
      return None
  except RowDeleted:
    return None
  return portfolio


def _portfolio_timestamp(value):
  # Older/imported rows can lack either timestamp. Keep them available while
  # placing them after dated portfolios, including dates before the Unix epoch.
  return value.timestamp() if value is not None else float("-inf")


@in_transaction
def _update_directory(portfolio, user):
  """Small materialized rows keep legacy-table lists free of large documents."""
  count = portfolio["transaction_count"]
  if count is None:
    count = len(portfolio["transactions"] or [])
    portfolio.update(transaction_count=count)
  values: dict[str, Any] = {"name": portfolio["name"], "transaction_count": count,
            "updated_at": portfolio["updated_at"], "last_used_at": portfolio["last_used_at"]}
  entry = app_tables.portfolio_directory.get(owner=user, portfolio_id=portfolio.get_id())
  if entry is None:
    return app_tables.portfolio_directory.add_row(owner=user, portfolio_id=portfolio.get_id(), **values)
  entry.update(**values)
  return entry


@in_transaction
def _directory_for(user):
  entries = list(app_tables.portfolio_directory.search(owner=user))
  source = app_tables.portfolios.search(owner=user)
  # Existing and creator-imported portfolios are indexed once on first use.
  if len(entries) != len(source):
    portfolios = list(source)
    actual_ids = {portfolio.get_id() for portfolio in portfolios}
    known_ids = {entry["portfolio_id"] for entry in entries}
    for entry in entries:
      if entry["portfolio_id"] not in actual_ids:
        entry.delete()
    for portfolio in portfolios:
      if portfolio.get_id() not in known_ids:
        _update_directory(portfolio, user)
    entries = list(app_tables.portfolio_directory.search(owner=user))
  return entries


def _portfolios_for(user):
  return sorted(
    _directory_for(user),
    key=lambda portfolio: _portfolio_timestamp(portfolio["updated_at"]),
    reverse=True,
  )


@anvil.server.callable(require_user=True)
def list_portfolios():
  return _portfolios_for(_current_user())


@anvil.server.callable(require_user=True)
def get_last_portfolio():
  user = _current_user()
  portfolio = _last_portfolio_for(user, _directory_for(user))
  return {"ok": True, "portfolio": portfolio, "analysis": _saved_analysis(portfolio)}


def _last_portfolio_for(user, entries):
  entry = max(
    entries,
    key=lambda row: (row["last_used_at"] is not None,
                     _portfolio_timestamp(row["last_used_at"] or row["updated_at"])),
    default=None,
  )
  return _owned_portfolio(entry["portfolio_id"], user) if entry is not None else None


@anvil.server.callable(require_user=True)
def load_portfolio(portfolio_id):
  user = _current_user()
  portfolio = _owned_portfolio(portfolio_id, user)
  if portfolio is None:
    return {"ok": False, "message": "This portfolio is no longer available to your account."}
  portfolio.update(last_used_at=datetime.now())
  _update_directory(portfolio, user)
  return {"ok": True, "portfolio": portfolio, "analysis": _saved_analysis(portfolio)}


@anvil.server.callable(require_user=True)
def get_portfolio_workspace():
  """One small bootstrap response; analyses are fetched only when opened."""
  from account_profiles import _profile_for_user
  from creator_portfolios import get_creator_access
  user = _current_user()
  portfolios = _portfolios_for(user)
  membership = membership_for(user)
  return {"profile": _profile_for_user(user, portfolios), "membership": membership,
          "creator": get_creator_access(), "portfolios": portfolios,
          "usage": {"count": len(portfolios), "limit": membership["portfolio_limit"],
                    "plan": membership["label"], "can_create": len(portfolios) < membership["portfolio_limit"]}}


@anvil.server.callable(require_user=True)
def get_portfolio_startup(portfolio_id=None) -> dict:
  """Send visible saved values before the browser downloads daily history."""
  from .portfolio_summary import summarize_portfolio
  from .holdings_dashboard import build_dashboard
  user = _current_user()
  workspace = get_portfolio_workspace()
  if portfolio_id is None:
    portfolio = _last_portfolio_for(user, workspace["portfolios"])
  else:
    portfolio = _owned_portfolio(portfolio_id, user)
    if portfolio is None:
      return {"ok": False, "workspace": workspace,
              "message": "This portfolio is no longer available to your account."}
    portfolio.update(last_used_at=datetime.now())
    _update_directory(portfolio, user)
  analysis = _saved_analysis(portfolio)
  overview = None
  if analysis is not None:
    overview = {
      "summary": summarize_portfolio(analysis),
      "holdings": build_dashboard(analysis, transactions=portfolio["transactions"]),
      "metrics": analysis["metrics"],
      "metadata": {"end_date": analysis["metadata"]["end_date"],
                   "provisional_date": analysis["metadata"].get("provisional_date")},
    }
  return {"ok": True, "workspace": workspace, "portfolio": portfolio, "overview": overview}


@anvil.server.callable(require_user=True)
def get_portfolio_history(portfolio_id) -> dict:
  """Read history for the displayed portfolio without changing navigation order."""
  portfolio = _owned_portfolio(portfolio_id, _current_user())
  if portfolio is None:
    return {"ok": False, "message": "This portfolio is no longer available to your account."}
  return {"ok": True, "portfolio": portfolio, "analysis": _saved_analysis(portfolio)}


@anvil.server.callable(require_user=True)
@in_transaction
def save_portfolio(portfolio_id, name, transactions, as_of_date, risk_free_rate):
  user = _current_user()
  portfolio = None
  if portfolio_id is not None:
    portfolio = _owned_portfolio(portfolio_id, user)
    if portfolio is None:
      return {"ok": False, "message": "This portfolio is no longer available to your account."}

  try:
    values = _validate_inputs(name, transactions, as_of_date, risk_free_rate)
  except ValueError as error:
    return {"ok": False, "message": str(error)}

  now = datetime.now()
  fingerprint = _analysis_fingerprint(values["transactions"], values["as_of_date"], values["risk_free_rate"])
  staged = _staged_analysis()
  if (staged and len(staged) == 4 and staged[0] == fingerprint
      and isinstance(staged[1], str) and staged[3] in (None, user.get_id())):
    compressed = base64.b64decode(staged[1], validate=True)
    media = anvil.BlobMedia("application/gzip", compressed, name="portfolio-analysis.json.gz")
    values.update(**_analysis_values(staged[0], media, staged[2]))
  elif portfolio is not None and portfolio["analysis_fingerprint"] != fingerprint:
    values.update(analysis_cache=None, analysis_fingerprint=None, analysis_updated_at=None)
  values.update(updated_at=now, last_used_at=now)
  if portfolio is None:
    membership = membership_for(user)
    if len(app_tables.portfolios.search(owner=user)) >= membership["portfolio_limit"]:
      return {"ok": False, "message": "%s includes up to %s saved portfolio%s. Edit an existing portfolio or manage your plan in My subscription." %
              (membership["label"], membership["portfolio_limit"], "s" if membership["portfolio_limit"] != 1 else ""), "limit_reached": True}
    portfolio = app_tables.portfolios.add_row(owner=user, **values)
  else:
    portfolio.update(**values)
  directory_entry = _update_directory(portfolio, user)
  from investor_profiles import refresh_investor_metrics
  refresh_investor_metrics(portfolio, user)
  return {"ok": True, "portfolio": portfolio, "directory_entry": directory_entry}


@anvil.server.callable(require_user=True)
def refresh_portfolio(portfolio_id, name, transactions, risk_free_rate):
  """Save inputs, refresh through today, and persist the server-owned analysis."""
  from portfolio_analytics import analyze_portfolio_history
  user = _current_user()
  today = date.today()
  saved = save_portfolio(portfolio_id, name, transactions, today, risk_free_rate)
  if not saved["ok"]:
    return saved
  portfolio = saved["portfolio"]
  if not portfolio["transactions"]:
    return dict(saved, ok=False, message="Add at least one transaction before refreshing prices.")
  document = portfolio["transactions"]
  rate = portfolio["risk_free_rate"]
  expected = _analysis_fingerprint(document, today, rate)
  analysis = analyze_portfolio_history(document, today, rate / 100, portfolio.get_id())
  current = _owned_portfolio(portfolio.get_id(), user)
  if current is None:
    return {"ok": False, "message": "This portfolio is no longer available to your account."}
  if _analysis_fingerprint(current["transactions"], current["as_of_date"], current["risk_free_rate"]) != expected:
    return {"ok": False, "message": "This portfolio changed while prices refreshed. Refresh again to use its latest transactions."}
  saved["portfolio"] = current
  if not analysis["ok"]:
    return dict(saved, ok=False, message=analysis["message"])
  return dict(saved, analysis=analysis)


@anvil.server.callable(require_user=True)
def get_portfolio_usage():
  user = _current_user()
  membership = membership_for(user)
  count = len(app_tables.portfolios.search(owner=user))
  return {"count": count, "limit": membership["portfolio_limit"], "plan": membership["label"],
          "can_create": count < membership["portfolio_limit"]}
