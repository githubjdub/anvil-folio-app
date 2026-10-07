"""Creator access to saved documents; imports never modify the source."""

from datetime import datetime

import anvil.server
import anvil.users
from anvil.tables import app_tables, RowDeleted

from portfolios import _validate_inputs


# Pin existing accounts as well as their exact email. Public signup cannot claim
# creator privileges just by registering or changing to an unverified address.
# Entries are set by the app owner, never by a client-callable function.
_CREATOR_ACCOUNTS = {}
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
  enabled = _is_creator(user)
  return {"enabled": enabled, "email": user["email"] if enabled and user is not None else None}


def _summary(portfolio):
  owner = portfolio["owner"]
  try:
    owner_email = (owner["email"] or "Account without email") if owner is not None else "Deleted account"
  except RowDeleted:
    owner_email = "Deleted account"
  transactions = portfolio["transactions"] or []
  tickers = sorted({str(item.get("ticker") or "").strip().upper()
                    for item in transactions if isinstance(item, dict)} - {""})
  return {"owner_email": owner_email, "transaction_count": len(transactions),
          "tickers": ", ".join(tickers[:12]) + (" +%d more" % (len(tickers) - 12) if len(tickers) > 12 else ""),
          "_search_tickers": " ".join(tickers)}


@anvil.server.callable(require_user=True)
def list_creator_portfolios(search_text="", page=0, include_own=False):
  user = _require_creator()
  if not isinstance(search_text, str) or len(search_text) > 200:
    return {"ok": False, "message": "Search using up to 200 characters."}
  if isinstance(page, bool) or not isinstance(page, int) or not 0 <= page <= 100000:
    return {"ok": False, "message": "Choose a valid portfolio page."}
  if not isinstance(include_own, bool):
    return {"ok": False, "message": "Choose whether to include your own portfolios."}
  term = search_text.strip().lower()
  matches = []
  for portfolio in app_tables.portfolios.search():
    try:
      if not include_own and portfolio["owner"] == user:
        continue
      summary = _summary(portfolio)
      search = "%s %s %s" % (portfolio["name"] or "", summary["owner_email"], summary["_search_tickers"])
      if term and term not in search.lower():
        continue
      updated = portfolio["updated_at"]
      matches.append((updated.timestamp() if updated else 0, portfolio.get_id(), summary, portfolio))
    except RowDeleted:
      continue
  matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
  total = len(matches)
  page = min(page, max(0, (total - 1) // _PAGE_SIZE))
  items = []
  # As in list_portfolios(), returned rows are read-only with client:none.
  # Account links and last_used_at remain client_hidden in the schema. The app
  # uses legacy tables, which do not support query.only_cols() client views.
  for _, _, summary, portfolio in matches[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE]:
    summary.pop("_search_tickers")
    items.append(dict(summary, portfolio=portfolio))
  return {"ok": True, "items": items, "page": page, "total": total,
          "has_previous": page > 0, "has_next": (page + 1) * _PAGE_SIZE < total}


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
