"""Server-owned membership access; all existing accounts default to Free.

This module does not collect payments. A future billing integration must verify
provider events on the server and store the paid-through expiry before granting
subscription access. Never grant access from a checkout redirect or client claim.
Only creator grants may have no expiry. Membership columns stay client-hidden;
feature endpoints should call require_premium() when premium features are added.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import anvil.server
import anvil.users
from anvil.tables import app_tables, RowDeleted


def membership_for(user):
  """Project safe, current access details without exposing the Users row."""
  configured_tier = "premium" if user["membership_tier"] == "premium" else "free"
  raw_source = user["membership_source"]
  source = raw_source if raw_source in ("creator", "subscription") else None
  expires_at = user["membership_expires_at"]
  if expires_at is not None:
    # Old server timestamps can be naive; server-created values are UTC.
    expires_at = (expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None
                  else expires_at.astimezone(timezone.utc))
  now = datetime.now(timezone.utc)
  enabled = user["enabled"] is True
  expired = expires_at is not None and expires_at <= now
  eligible = (source in ("creator", "subscription")
              and (expires_at is not None or source == "creator"))
  is_premium = enabled and configured_tier == "premium" and eligible and not expired
  expires_on = (expires_at - timedelta(microseconds=1)).date() if expires_at is not None else None
  if not enabled:
    detail = "This account is disabled."
  elif is_premium:
    detail = ("Premium access through %s (UTC)." % expires_on.strftime("%b %d, %Y")
              if expires_on is not None else "Premium membership is active.")
  elif configured_tier == "premium" and expired:
    detail = "Premium access expired. Your account is now Free."
  else:
    detail = "Free membership."
  return {"tier": "premium" if is_premium else "free",
          "label": "Premium" if is_premium else "Free", "is_premium": is_premium,
          "source": source, "expires_at": expires_at, "configured_tier": configured_tier,
          "expires_on": expires_on, "detail": detail}


def _signed_in_user():
  user = anvil.users.get_user()
  if user is None or user["enabled"] is not True:
    raise anvil.users.AuthenticationFailed("Sign in to an enabled account to access your membership.")
  return user


@anvil.server.callable(require_user=True)
def get_my_membership():
  return membership_for(_signed_in_user())


def require_premium():
  """Server-side guard for future Premium endpoints; never trust client badges."""
  user = _signed_in_user()
  if not membership_for(user)["is_premium"]:
    raise anvil.users.AuthenticationFailed("Premium membership is required for this feature.")
  return user


@anvil.server.callable(require_user=True)
def creator_set_membership(account_id, tier, expires_on=None):
  # Late import keeps creator account summaries free to use membership_for().
  from . import creator_portfolios
  creator_module: Any = creator_portfolios
  creator = creator_module._require_creator()
  if not isinstance(account_id, str) or not 1 <= len(account_id) <= 200:
    return {"ok": False, "message": "Choose a valid account."}
  if not isinstance(tier, str) or tier not in ("free", "premium"):
    return {"ok": False, "message": "Choose Free or Premium membership."}
  if expires_on is not None and type(expires_on) is not date:
    return {"ok": False, "message": "Choose an expiry date, or leave it blank."}
  if tier == "free" and expires_on is not None:
    return {"ok": False, "message": "Free membership does not have an expiry date."}
  now = datetime.now(timezone.utc)
  if expires_on is not None and not now.date() <= expires_on < date.max:
    return {"ok": False, "message": "Choose today or a future expiry date before year 10000."}
  expires_at = (datetime.combine(expires_on + timedelta(days=1), time.min, timezone.utc)
                if expires_on is not None else None)
  try:
    account = app_tables.users.get_by_id(account_id)
  except ValueError:
    return {"ok": False, "message": "This account is no longer available."}
  try:
    if account is None:
      return {"ok": False, "message": "This account is no longer available."}
    if tier == "premium" and account["enabled"] is not True:
      return {"ok": False, "message": "Enable this account before setting Premium membership."}
    values = {"membership_tier": tier, "membership_source": "creator",
              "membership_expires_at": expires_at, "membership_updated_at": now,
              "membership_updated_by": creator}
    account.update(**values)
    membership = membership_for(account)
  except RowDeleted:
    return {"ok": False, "message": "This account is no longer available."}
  return {"ok": True, "membership": membership,
          "message": "Premium access saved." if tier == "premium" else "Account set to Free."}
