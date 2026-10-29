"""Account-owned balance sheets using the app's existing legacy Data Tables."""

from datetime import date, datetime, timezone
import math

import anvil.server
import anvil.users
from anvil.tables import app_tables, in_transaction, RowDeleted


ASSET_CATEGORIES = ("cash", "investments", "retirement", "property", "other_asset")
DEBT_CATEGORIES = ("mortgage", "loan", "credit_card", "other_debt")


def _current_user():
  user = anvil.users.get_user()
  if user is None or user["enabled"] is not True:
    raise anvil.users.AuthenticationFailed("Sign in to access your net worth.")
  return user


def _entry_values(name, category, amount):
  if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
    raise ValueError("Enter a name between 1 and 80 characters.")
  if category not in ASSET_CATEGORIES + DEBT_CATEGORIES:
    raise ValueError("Choose an asset or debt category.")
  if isinstance(amount, bool) or not isinstance(amount, (str, int, float)):
    raise ValueError("Enter a valid balance in US dollars.")
  try:
    amount = float(amount)
  except (ValueError, OverflowError):
    raise ValueError("Enter a valid balance in US dollars.")
  if not math.isfinite(amount) or amount < 0 or amount > 1_000_000_000_000:
    raise ValueError("Enter a balance from $0 to $1 trillion. Enter debts as positive amounts.")
  return {"name": name.strip(), "category": category,
          "kind": "asset" if category in ASSET_CATEGORIES else "debt",
          "amount": round(amount, 2)}


def _owned_row(table, row_id, user):
  if not isinstance(row_id, str) or not row_id:
    return None
  row = table.get_by_id(row_id)
  if row is None:
    return None
  try:
    return row if row["owner"] == user else None
  except RowDeleted:
    return None


def _totals(entries):
  # Sum cents so displayed assets minus displayed debts equals net worth.
  assets = sum(round(row["amount"] * 100) for row in entries if row["kind"] == "asset")
  debts = sum(round(row["amount"] * 100) for row in entries if row["kind"] == "debt")
  liquid = sum(round(row["amount"] * 100) for row in entries if row["category"] in ("cash", "investments"))
  return {"assets": assets / 100, "debts": debts / 100,
          "net_worth": (assets - debts) / 100, "liquid": liquid / 100}


def _sheet(user):
  # Return real rows; access to the table and its ownership column stays server-only.
  entries = sorted(app_tables.net_worth_entries.search(owner=user),
                   key=lambda row: (-row["amount"], row["name"].lower()))
  snapshots = sorted(app_tables.net_worth_snapshots.search(owner=user),
                     key=lambda row: row["snapshot_date"])
  return {"ok": True, "entries": entries, "snapshots": snapshots,
          "totals": _totals(entries)}


@anvil.server.callable(require_user=True)
def get_net_worth():
  return _sheet(_current_user())


@anvil.server.callable(require_user=True)
@in_transaction
def save_net_worth_entry(entry_id, name, category, amount):
  user = _current_user()
  row = _owned_row(app_tables.net_worth_entries, entry_id, user) if entry_id is not None else None
  if entry_id is not None and row is None:
    return {"ok": False, "message": "This entry is no longer available. Refresh your balance sheet."}
  try:
    values = _entry_values(name, category, amount)
  except ValueError as error:
    return {"ok": False, "message": str(error)}
  if row is not None and row["source_key"] == "selected_portfolio" and values["kind"] != "asset":
    return {"ok": False, "message": "The portfolio import entry must remain an asset. Add debts separately."}
  values.update(updated_at=datetime.now(timezone.utc), as_of=None)
  if row is None:
    app_tables.net_worth_entries.add_row(owner=user, **values)
  else:
    row.update(**values)
  return _sheet(user)


@anvil.server.callable(require_user=True)
@in_transaction
def remove_net_worth_entry(entry_id):
  user = _current_user()
  row = _owned_row(app_tables.net_worth_entries, entry_id, user)
  if row is None:
    return {"ok": False, "message": "This entry is no longer available. Refresh your balance sheet."}
  row.delete()
  return _sheet(user)


@anvil.server.callable(require_user=True)
@in_transaction
def import_net_worth_portfolio(amount, as_of):
  user = _current_user()
  try:
    values = _entry_values("Selected portfolio", "investments", amount)
    if not isinstance(as_of, str):
      raise ValueError("Sync portfolio history before importing its value.")
    as_of = date.fromisoformat(as_of)
  except ValueError as error:
    return {"ok": False, "message": str(error)}
  # A single explicit import slot avoids counting repeated imports as new assets.
  row = app_tables.net_worth_entries.get(owner=user, source_key="selected_portfolio")
  values.update(as_of=as_of, updated_at=datetime.now(timezone.utc))
  if row is None:
    app_tables.net_worth_entries.add_row(owner=user, source_key="selected_portfolio", **values)
  else:
    # User labels and asset classifications survive explicit value refreshes.
    values.update(name=row["name"], category=row["category"])
    row.update(**values)
  return _sheet(user)


@anvil.server.callable(require_user=True)
@in_transaction
def save_net_worth_snapshot():
  user = _current_user()
  entries = list(app_tables.net_worth_entries.search(owner=user))
  if not entries:
    return {"ok": False, "message": "Add an asset or debt before saving a snapshot."}
  totals = _totals(entries)
  now = datetime.now(timezone.utc)
  row = app_tables.net_worth_snapshots.get(owner=user, snapshot_date=now.date())
  values = {key: totals[key] for key in ("assets", "debts", "net_worth")}
  values["updated_at"] = now
  if row is None:
    app_tables.net_worth_snapshots.add_row(owner=user, snapshot_date=now.date(), **values)
  else:
    row.update(**values)
  return _sheet(user)


@anvil.server.callable(require_user=True)
@in_transaction
def remove_net_worth_snapshot(snapshot_id):
  user = _current_user()
  row = _owned_row(app_tables.net_worth_snapshots, snapshot_id, user)
  if row is None:
    return {"ok": False, "message": "This snapshot is no longer available. Refresh your balance sheet."}
  row.delete()
  return _sheet(user)
