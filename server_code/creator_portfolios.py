"""Creator access to saved documents; imports never modify the source."""

from datetime import date, datetime, timezone
import math

import anvil.server
import anvil.users
from anvil.tables import app_tables, RowDeleted

from portfolios import _validate_inputs
from portfolio_analytics import _normalise_ticker, _EXCHANGES


# Pin existing accounts as well as their exact email. Public signup cannot claim
# creator privileges just by registering or changing to an unverified address.
# Entries are set by the app owner, never by a client-callable function.
_CREATOR_ACCOUNTS = {
  "jonathanjonathanwing@gmail.com": ("[1112365,9662690481]",),
}
_PAGE_SIZE = 20


def _is_creator(user):
  if user is None:
    return False
  try:
    email = user["email"]
    return (user["enabled"] is True and isinstance(email, str)
            and user.get_id() in _CREATOR_ACCOUNTS.get(email.strip().lower(), ()))
  except RowDeleted:
    return False


def _require_creator():
  user = anvil.users.get_user()
  if user is None or not _is_creator(user):
    raise anvil.users.AuthenticationFailed("This workspace is available only to the platform creator.")
  return user


@anvil.server.callable(require_user=True)
def get_creator_access():
  user = anvil.users.get_user()
  try:
    enabled = _is_creator(user)
    email = user["email"] if user is not None else None
    reference = None
    if user is not None and not enabled:
      if (user["enabled"] is True and isinstance(email, str)
          and email.strip().lower() in _CREATOR_ACCOUNTS):
        # This is only the signed-in user's own opaque reference. It does not
        # enroll the account, grant table access, or change the creator allowlist.
        reference = user.get_id()
  except RowDeleted:
    return {"enabled": False, "email": None, "setup_reference": None}
  return {"enabled": enabled, "email": email if enabled else None,
          "setup_reference": reference}


def _stock_ticker(value):
  if not isinstance(value, str):
    return None
  symbol, exchange = _normalise_ticker(value)
  return symbol.replace(".", "-") if symbol and exchange in _EXCHANGES else None


def _browse_error(search_text, page, include_own):
  if not isinstance(search_text, str) or len(search_text) > 200:
    return {"ok": False, "message": "Search using up to 200 characters."}
  if isinstance(page, bool) or not isinstance(page, int) or not 0 <= page <= 100000:
    return {"ok": False, "message": "Choose a valid page."}
  if not isinstance(include_own, bool):
    return {"ok": False, "message": "Choose whether to include your own account."}
  return None


def _page_slice(items, page):
  total = len(items)
  page = min(page, max(0, (total - 1) // _PAGE_SIZE))
  return items[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE], page, total


def _saved_scope(user, include_own):
  portfolios = []
  for portfolio in app_tables.portfolios.search():
    try:
      if not include_own and portfolio["owner"] == user:
        continue
      portfolios.append(portfolio)
    except RowDeleted:
      continue
  return portfolios


def _account_totals(portfolios):
  totals = {}
  for portfolio in portfolios:
    try:
      owner = portfolio["owner"]
      if owner is None:
        continue
      owner_id = owner.get_id()
      owner["email"]
      transactions = portfolio["transactions"]
      updated_at = portfolio["updated_at"]
    except RowDeleted:
      continue
    entry = totals.setdefault(owner_id, {"portfolio_count": 0, "transaction_count": 0,
                                         "latest_saved_at": None})
    entry["portfolio_count"] += 1
    entry["transaction_count"] += len(transactions) if isinstance(transactions, list) else 0
    previous = entry["latest_saved_at"]
    if updated_at is not None and (previous is None or updated_at.timestamp() > previous.timestamp()):
      entry["latest_saved_at"] = updated_at
  return totals


def _account_summary(account, totals):
  account_id = account.get_id()
  counts = totals.get(account_id, {})
  # Legacy tables cannot make column-restricted views. Explicitly project the
  # approved directory fields instead of returning a Users row or its columns.
  return {"account_id": account_id, "email": account["email"],
          "enabled": account["enabled"] is True, "signed_up": account["signed_up"],
          "last_login": account["last_login"],
          "portfolio_count": counts.get("portfolio_count", 0),
          "transaction_count": counts.get("transaction_count", 0),
          "latest_saved_at": counts.get("latest_saved_at")}


@anvil.server.callable(require_user=True)
def list_creator_accounts(search_text="", page=0, include_own=False):
  user = _require_creator()
  error = _browse_error(search_text, page, include_own)
  if error is not None:
    return error
  totals = _account_totals(_saved_scope(user, include_own))
  accounts = []
  for account in app_tables.users.search():
    try:
      if not include_own and account == user:
        continue
      accounts.append(_account_summary(account, totals))
    except RowDeleted:
      continue
  accounts.sort(key=lambda item: (
    -(item["latest_saved_at"].timestamp() if item["latest_saved_at"] is not None else 0),
    (item["email"] or "").lower(), item["account_id"]))
  account_count = len(accounts)
  portfolio_count = sum(item["portfolio_count"] for item in accounts)
  term = search_text.strip().lower()
  matches = [account for account in accounts
             if not term or term in ("%s %s" % (account["email"] or "", account["account_id"])).lower()]
  items, page, total = _page_slice(matches, page)
  return {"ok": True, "items": items, "page": page, "total": total,
          "has_previous": page > 0, "has_next": (page + 1) * _PAGE_SIZE < total,
          "account_count": account_count, "portfolio_count": portfolio_count}


def _recorded_trades(portfolio):
  transactions = portfolio["transactions"]
  if not isinstance(transactions, list):
    return []
  as_of_date = portfolio["as_of_date"]
  trades = []
  for transaction in transactions:
    if not isinstance(transaction, dict) or transaction.get("action") not in ("Buy", "Sell"):
      continue
    shares = transaction.get("shares")
    if isinstance(shares, bool) or not isinstance(shares, (int, float)) or shares <= 0:
      continue
    try:
      if not math.isfinite(shares):
        continue
    except OverflowError:
      continue
    ticker = _stock_ticker(transaction.get("ticker"))
    raw_date = transaction.get("trade_date")
    if ticker is None or not isinstance(raw_date, str):
      continue
    try:
      trade_date = date.fromisoformat(raw_date)
    except ValueError:
      continue
    if type(as_of_date) is date and trade_date > as_of_date:
      continue
    trades.append({"ticker": ticker, "action": transaction["action"], "trade_date": trade_date})
  return trades


def _stock_activity(portfolios):
  rankings = {"Buy": {}, "Sell": {}}
  accounts = set()
  portfolio_count = 0
  for portfolio in portfolios:
    try:
      owner = portfolio["owner"]
      if owner is None:
        continue
      owner_id = owner.get_id()
      owner["email"]
      trades = _recorded_trades(portfolio)
    except RowDeleted:
      continue
    accounts.add(owner_id)
    portfolio_count += 1
    present = set()
    for trade in trades:
      ticker, action = trade["ticker"], trade["action"]
      entry = rankings[action].setdefault(ticker, {"ticker": ticker, "portfolio_count": 0,
                                                  "accounts": set(), "transaction_count": 0})
      entry["transaction_count"] += 1
      entry["accounts"].add(owner_id)
      if (ticker, action) not in present:
        entry["portfolio_count"] += 1
        present.add((ticker, action))
  now = datetime.now(timezone.utc)
  result = {}
  for action, key in (("Buy", "bought"), ("Sell", "sold")):
    ranked = sorted(rankings[action].values(), key=lambda entry: (
      -entry["transaction_count"], -len(entry["accounts"]), -entry["portfolio_count"], entry["ticker"]))
    items = [{"rank": index, "ticker": entry["ticker"], "action": action,
              "portfolio_count": entry["portfolio_count"], "account_count": len(entry["accounts"]),
              "transaction_count": entry["transaction_count"],
              "portfolio_percentage": 100 * entry["portfolio_count"] / portfolio_count}
             for index, entry in enumerate(ranked[:20], start=1)]
    result[key] = {"items": items, "portfolio_count": portfolio_count, "account_count": len(accounts),
                   "ticker_count": len(ranked), "updated_at": now}
  return result


def _portfolio_preview(portfolio):
  try:
    values = _validate_inputs(portfolio["name"], portfolio["transactions"],
                              portfolio["as_of_date"], portfolio["risk_free_rate"])
  except ValueError as error:
    return {"preview_available": False, "preview_transactions": [],
            "preview_message": "Saved transaction preview unavailable: %s" % error}
  return {"preview_available": True, "preview_transactions": values["transactions"],
          "preview_message": None}


def _top_added_stocks(portfolios):
  """Count recorded buys once per portfolio; lots never inflate popularity."""
  symbols = {}
  account_ids = set()
  portfolio_count = 0
  for portfolio in portfolios:
    try:
      owner = portfolio["owner"]
      if owner is None:
        continue
      owner_id = owner.get_id()
      # Deleted linked accounts are not people contributing to this ranking.
      owner["email"]
      transactions = portfolio["transactions"]
    except RowDeleted:
      continue
    account_ids.add(owner_id)
    portfolio_count += 1
    if not isinstance(transactions, list):
      continue
    buys = {}
    for transaction in transactions:
      if not isinstance(transaction, dict) or transaction.get("action") != "Buy":
        continue
      shares = transaction.get("shares")
      if isinstance(shares, bool) or not isinstance(shares, (int, float)) or shares <= 0:
        continue
      try:
        finite = math.isfinite(shares)
      except OverflowError:
        continue
      if not finite:
        continue
      ticker = _stock_ticker(transaction.get("ticker"))
      if ticker is None:
        continue
      buys[ticker] = buys.get(ticker, 0) + 1
    for ticker, count in buys.items():
      entry = symbols.setdefault(ticker, {"ticker": ticker, "portfolio_count": 0,
                                         "buy_transactions": 0, "accounts": set()})
      entry["portfolio_count"] += 1
      entry["buy_transactions"] += count
      entry["accounts"].add(owner_id)
  ranked = sorted(symbols.values(), key=lambda entry: (-entry["portfolio_count"],
                  -len(entry["accounts"]), entry["ticker"]))
  items = [{"rank": index, "ticker": entry["ticker"],
            "portfolio_count": entry["portfolio_count"], "account_count": len(entry["accounts"]),
            "buy_transactions": entry["buy_transactions"],
            "portfolio_percentage": 100 * entry["portfolio_count"] / portfolio_count}
           for index, entry in enumerate(ranked[:20], start=1)]
  return {"items": items, "portfolio_count": portfolio_count, "account_count": len(account_ids),
          "ticker_count": len(ranked), "updated_at": datetime.now(timezone.utc)}


def _summary(portfolio):
  owner = portfolio["owner"]
  try:
    owner_email = (owner["email"] or "Account without email") if owner is not None else "Deleted account"
  except RowDeleted:
    owner_email = "Deleted account"
  transactions = portfolio["transactions"]
  if not isinstance(transactions, list):
    transactions = []
  tickers = sorted({str(item.get("ticker") or "").strip().upper()
                    for item in transactions if isinstance(item, dict)} - {""})
  aliases = set(tickers)
  for item in transactions:
    if isinstance(item, dict):
      alias = _stock_ticker(item.get("ticker"))
      if alias is not None:
        aliases.add(alias)
  trades = _recorded_trades(portfolio)
  return {"owner_email": owner_email, "transaction_count": len(transactions),
          "buy_transaction_count": sum(trade["action"] == "Buy" for trade in trades),
          "sell_transaction_count": sum(trade["action"] == "Sell" for trade in trades),
          "tickers": ", ".join(tickers[:12]) + (" +%d more" % (len(tickers) - 12) if len(tickers) > 12 else ""),
          "_search_tickers": " ".join(sorted(aliases))}


@anvil.server.callable(require_user=True)
def list_creator_portfolios(search_text="", page=0, include_own=False, account_id=None, trade_action=None):
  user = _require_creator()
  error = _browse_error(search_text, page, include_own)
  if error is not None:
    return error
  if trade_action is not None and trade_action not in ("Buy", "Sell"):
    return {"ok": False, "message": "Choose Buy or Sell to filter recorded trades."}
  account = None
  if account_id is not None:
    if not isinstance(account_id, str) or not 1 <= len(account_id) <= 200:
      return {"ok": False, "message": "Choose a valid account."}
    try:
      account = app_tables.users.get_by_id(account_id)
      if account is None:
        return {"ok": False, "message": "This account is no longer available."}
      if not include_own and account == user:
        return {"ok": False, "message": "Include your own account to view its portfolios."}
      account.get_id()
      account["email"]
    except (ValueError, RowDeleted):
      return {"ok": False, "message": "This account is no longer available."}
  term = search_text.strip().lower()
  ticker_filter = _stock_ticker(search_text) if trade_action is not None and term else None
  matches = []
  scope = _saved_scope(user, include_own)
  selected_account = None
  if account is not None:
    try:
      selected_account = _account_summary(account, _account_totals(scope))
    except RowDeleted:
      return {"ok": False, "message": "This account is no longer available."}
  for portfolio in scope:
    try:
      if account_id is not None:
        owner = portfolio["owner"]
        if owner is None or owner.get_id() != account_id:
          continue
      summary = _summary(portfolio)
      if trade_action is not None:
        trades = [trade for trade in _recorded_trades(portfolio) if trade["action"] == trade_action]
        if not trades or (ticker_filter is not None
                          and not any(trade["ticker"] == ticker_filter for trade in trades)):
          continue
      search = "%s %s %s" % (portfolio["name"] or "", summary["owner_email"], summary["_search_tickers"])
      if term and ticker_filter is None and term not in search.lower():
        continue
      updated = portfolio["updated_at"]
      matches.append((updated.timestamp() if updated else 0, portfolio.get_id(), summary, portfolio))
    except RowDeleted:
      continue
  matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
  visible, page, total = _page_slice(matches, page)
  items = []
  # As in list_portfolios(), returned rows are read-only with client:none.
  # Account links and last_used_at remain client_hidden in the schema. The app
  # uses legacy tables, which do not support query.only_cols() client views.
  for _, _, summary, portfolio in visible:
    try:
      summary.pop("_search_tickers")
      items.append(dict(summary, portfolio=portfolio, **_portfolio_preview(portfolio)))
    except RowDeleted:
      continue
  return {"ok": True, "items": items, "page": page, "total": total,
          "has_previous": page > 0, "has_next": (page + 1) * _PAGE_SIZE < total,
          "stock_popularity": _top_added_stocks(scope), "stock_activity": _stock_activity(scope),
          "selected_account": selected_account}


@anvil.server.callable(require_user=True)
def import_creator_portfolio(portfolio_id):
  user = _require_creator()
  if not isinstance(portfolio_id, str) or not 1 <= len(portfolio_id) <= 200:
    return {"ok": False, "message": "Choose a saved portfolio to import."}
  try:
    source = app_tables.portfolios.get_by_id(portfolio_id)
  except ValueError:
    return {"ok": False, "message": "This saved portfolio is no longer available."}
  if source is None:
    return {"ok": False, "message": "This saved portfolio is no longer available."}
  try:
    name = str(source["name"] or "Imported portfolio")[:73] + " · copy"
    values = _validate_inputs(name, source["transactions"], source["as_of_date"], source["risk_free_rate"])
  except RowDeleted:
    return {"ok": False, "message": "This saved portfolio is no longer available."}
  except ValueError as error:
    return {"ok": False, "message": "This saved portfolio cannot be imported: %s" % error}
  now = datetime.now()
  imported = app_tables.portfolios.add_row(owner=user, updated_at=now, last_used_at=now, **values)
  return {"ok": True, "portfolio": imported}
