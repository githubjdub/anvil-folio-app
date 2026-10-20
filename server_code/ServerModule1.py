"""Billing configuration lives in stripe_billing.

The app uses Stripe's HTTP API, so it does not need the separate Stripe SDK.
Credentials are read only when billing is requested; importing server modules
must not depend on a payment secret or prevent the portfolio from opening.
"""
import anvil.server
from .stripe_billing import stripe_webhook as verified_stripe_webhook


@anvil.server.http_endpoint("/stripe-webhook", methods=["POST"])
def stripe_webhook(**params):
  """Keep the earlier endpoint URL working through verified billing handling."""
  return verified_stripe_webhook(**params)
