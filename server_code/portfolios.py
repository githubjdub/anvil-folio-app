"""Private, account-owned portfolio documents. Prices are refreshed on analysis."""

from datetime import date, datetime
import math

import anvil.server
import anvil.users
from anvil.tables import app_tables, RowDeleted


def _current_user():
  user = anvil.users.get_user()
  if user is None:
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
  if not isinstance(transactions, list) or not 1 <= len(transactions) <= 1000:
    raise ValueError("A portfolio must contain 1–1,000 transactions.")

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
  }


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


def _portfolios_for(user):
  return sorted(
    app_tables.portfolios.search(owner=user),
    key=lambda portfolio: portfolio["updated_at"],
    reverse=True,
  )


@anvil.server.callable(require_user=True)
def list_portfolios():
  return _portfolios_for(_current_user())


@anvil.server.callable(require_user=True)
def get_last_portfolio():
  user = _current_user()
  portfolio = max(
    app_tables.portfolios.search(owner=user),
    key=lambda row: (row["last_used_at"] is not None,
                     (row["last_used_at"] or row["updated_at"]).timestamp()),
    default=None,
  )
  return {"ok": True, "portfolio": portfolio}


@anvil.server.callable(require_user=True)
def load_portfolio(portfolio_id):
  user = _current_user()
  portfolio = _owned_portfolio(portfolio_id, user)
  if portfolio is None:
    return {"ok": False, "message": "This portfolio is no longer available to your account."}
  portfolio.update(last_used_at=datetime.now())
  return {"ok": True, "portfolio": portfolio}


@anvil.server.callable(require_user=True)
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
  values.update(updated_at=now, last_used_at=now)
  if portfolio is None:
    portfolio = app_tables.portfolios.add_row(owner=user, **values)
  else:
    portfolio.update(**values)
  return {"ok": True, "portfolio": portfolio}
