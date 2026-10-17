"""Subscription page data; payment collection is not configured yet.

Membership access is distinct from a paid subscription. These endpoints expose
only the signed-in account's display details and deliberately leave checkout and
billing management unavailable until a verified payment integration is added.
"""

from datetime import datetime, timezone
from typing import Any

import anvil.server

from . import memberships

# Generated cross-module stubs omit private helpers; reuse the existing guard.
_memberships: Any = memberships


@anvil.server.callable
def get_subscription_catalog():
  return {
    "checkout_available": False,
    "premium_price_label": "Pricing coming soon",
    "premium_note": (
      "Premium subscriptions are coming soon. Pricing and included benefits "
      "will be announced before checkout opens."
    ),
    "billing_portal_available": False,
  }


@anvil.server.callable(require_user=True)
def get_my_subscription():
  user = _memberships._signed_in_user()
  membership = _memberships.membership_for(user)
  configured_premium = membership["configured_tier"] == "premium"
  expired = (membership["expires_at"] is not None
             and membership["expires_at"] <= datetime.now(timezone.utc))
  if membership["is_premium"]:
    status_label = "Active Premium"
  elif configured_premium and expired:
    status_label = "Expired Premium"
  elif configured_premium:
    status_label = "Inactive Premium"
  else:
    status_label = "Free"

  if membership["source"] == "subscription":
    access_type_label = "Subscription access"
    billing_status_label = "Billing details unavailable"
    billing_detail = (
      "Your account has subscription access. Payment, renewal, and cancellation "
      "details are not available here yet."
    )
    renewal_label = "Renewal details unavailable"
  elif membership["source"] == "creator" and configured_premium:
    access_type_label = "Creator-granted Premium"
    billing_status_label = "No recurring subscription"
    billing_detail = (
      "This Premium access was granted by the creator. The grant does not "
      "create a charge or a recurring subscription."
    )
    renewal_label = "No automatic renewal"
  else:
    access_type_label = "Free membership" if not configured_premium else "Inactive membership"
    billing_status_label = "No recurring subscription"
    billing_detail = "No recurring subscription is set up for this account."
    renewal_label = "No automatic renewal"

  return {
    "email": user["email"],
    "member_since": user["signed_up"],
    "membership": membership,
    "status_label": status_label,
    "access_type_label": access_type_label,
    "billing_status_label": billing_status_label,
    "billing_detail": billing_detail,
    "renewal_label": renewal_label,
  }


@anvil.server.callable(require_user=True)
def start_subscription_checkout():
  _memberships._signed_in_user()
  return {
    "ok": False,
    "message": "Subscriptions are coming soon. Checkout is not available yet.",
  }


@anvil.server.callable(require_user=True)
def open_subscription_billing_portal():
  _memberships._signed_in_user()
  return {
    "ok": False,
    "message": "Subscription billing management is not available in the app yet.",
  }
