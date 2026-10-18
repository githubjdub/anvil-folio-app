"""Billing configuration lives in stripe_billing.

The app uses Stripe's HTTP API, so it does not need the separate Stripe SDK.
Credentials are read only when billing is requested; importing server modules
must not depend on a payment secret or prevent the portfolio from opening.
"""
import anvil.server

@anvil.server.http_endpoint("/stripe-webhook")
def stripe_webhook():
  # Anvil will handle the incoming payload here
  payload = anvil.server.request.body.get_bytes()

  # Return a 200 response to tell Stripe you received it
  return anvil.server.HttpResponse(status=200)
