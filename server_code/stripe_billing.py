"""Stripe-hosted Checkout/Portal. Access follows verified server-side billing state.

SETUP (begin in Stripe test mode):
1. Create Portfoliora Plus, a recurring USD 19.99 price every month, quantity 1.
2. In Anvil App Secrets add STRIPE_SECRET_KEY, STRIPE_PLUS_PRICE_ID,
   STRIPE_APP_URL (canonical HTTPS published app URL, no hash/query).
3. Add a Stripe snapshot webhook at <STRIPE_APP_URL>/stripe/webhook for the
   events in _EVENTS below. Add its signing secret as STRIPE_WEBHOOK_SECRET.
4. Enable Stripe Customer Portal payment-method/invoice management and
   cancellation at period end. Keep plan switching/quantity changes disabled.
5. Test checkout, failed payment, renewal, cancellation, and resubscription.
   Use separate matching live-mode keys, price, and webhook when ready to launch.
   Never paste secret values into app source or chat.

Checkout verifies the configured price is exactly USD 19.99/month. No real
payments can start until all four App Secrets are available to the app.
Docs: https://docs.stripe.com/checkout/quickstart?client=html&lang=python
https://docs.stripe.com/customer-management/integrate-customer-portal
https://docs.stripe.com/webhooks
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import time
import uuid
from typing import Any
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import anvil.server
import anvil.secrets
from anvil.tables import app_tables, in_transaction


_SECRET_NAMES = ('STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET', 'STRIPE_PLUS_PRICE_ID', 'STRIPE_APP_URL')
_API_VERSION = '2025-06-30.basil'
_EVENTS = {'checkout.session.completed', 'checkout.session.async_payment_succeeded',
           'customer.subscription.created', 'customer.subscription.updated',
           'customer.subscription.deleted', 'invoice.paid', 'invoice.payment_failed'}


class BillingSetupError(Exception):
  pass


def _account(user_id):
  user = app_tables.users.get_by_id(user_id)
  if user is None:
    raise RuntimeError('Billing account is no longer available.')
  return user


def configuration():
  values = {}
  for name in _SECRET_NAMES:
    try:
      values[name] = anvil.secrets.get_secret(name).strip()
    except anvil.secrets.SecretError:
      return None
    if not values[name]:
      return None
  try:
    url = urlsplit(values['STRIPE_APP_URL'])
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
        or url.query or url.fragment or url.port == 0):
      return None
  except ValueError:
    # A malformed configured URL should disable billing, not the plans page.
    return None
  values['STRIPE_APP_URL'] = values['STRIPE_APP_URL'].rstrip('/')
  return values


def _stripe(config, method, path, parameters=None, idempotency_key=None):
  encoded = urlencode(parameters or {}).encode()
  url = 'https://api.stripe.com/v1/' + path
  if method == 'GET' and encoded:
    url += '?' + encoded.decode()
  headers = {'Authorization': 'Bearer ' + config['STRIPE_SECRET_KEY'],
             'Stripe-Version': _API_VERSION, 'Content-Type': 'application/x-www-form-urlencoded'}
  if idempotency_key:
    headers['Idempotency-Key'] = idempotency_key
  request = Request(url, data=encoded if method == 'POST' else None, headers=headers, method=method)
  try:
    with urlopen(request, timeout=20) as response:
      return json.loads(response.read())
  except HTTPError as error:
    # Never include Stripe response bodies, keys, or customer data in client errors.
    if error.code in (400, 401, 403, 404):
      raise BillingSetupError('Billing is temporarily unavailable. Please try again later.') from None
    raise


def _validate_price(config):
  price = _stripe(config, 'GET', 'prices/' + config['STRIPE_PLUS_PRICE_ID'])
  recurring = price.get('recurring') or {}
  if not (price.get('active') and price.get('currency') == 'usd' and price.get('unit_amount') == 1999
          and recurring.get('interval') == 'month' and recurring.get('interval_count') == 1
          and price.get('billing_scheme') == 'per_unit' and recurring.get('usage_type') == 'licensed'):
    raise BillingSetupError('Plus checkout is being configured. Please try again later.')


@in_transaction
def _reserve_attempt(user_id):
  user = _account(user_id)
  state = dict(user['stripe_state'] or {})
  if not state.get('attempt'):
    state['attempt'] = uuid.uuid4().hex
    user['stripe_state'] = state
  return user['stripe_customer_id'], state['attempt']


@in_transaction
def _store_customer(user_id, customer_id):
  user = _account(user_id)
  if user['stripe_customer_id'] and user['stripe_customer_id'] != customer_id:
    raise RuntimeError('Billing customer mapping conflict.')
  user['stripe_customer_id'] = customer_id


@in_transaction
def _store_checkout(user_id, attempt, session):
  user = _account(user_id)
  state = dict(user['stripe_state'] or {})
  if state.get('attempt') == attempt:
    state.update(checkout_id=session['id'], checkout_url=session.get('url'))
    user['stripe_state'] = state


@in_transaction
def _rotate_attempt(user_id, attempt):
  user = _account(user_id)
  state = dict(user['stripe_state'] or {})
  if state.get('attempt') == attempt:
    state.update(attempt=uuid.uuid4().hex, checkout_id=None, checkout_url=None)
    user['stripe_state'] = state


def _subscriptions(config, customer_id):
  parameters = {'customer': customer_id, 'status': 'all', 'limit': 100}
  subscriptions = []
  while True:
    page = _stripe(config, 'GET', 'subscriptions', parameters)
    subscriptions.extend(page['data'])
    if not page.get('has_more'):
      return subscriptions
    parameters['starting_after'] = page['data'][-1]['id']


def _plus_item(subscription, price_id):
  return next((item for item in subscription.get('items', {}).get('data', [])
               if item.get('price', {}).get('id') == price_id and item.get('quantity') == 1), None)


@in_transaction
def reconcile_customer(customer_id, config, event_id=None):
  if event_id and app_tables.stripe_events.get(event_id=event_id):
    return
  user = app_tables.users.get(stripe_customer_id=customer_id)
  if user is not None:
    # Read Stripe INSIDE the serializable transaction. A concurrent update
    # retries this read, so delayed/out-of-order events cannot restore stale access.
    subscriptions = _subscriptions(config, customer_id)
    candidates = [s for s in subscriptions if _plus_item(s, config['STRIPE_PLUS_PRICE_ID'])]
    active = [s for s in candidates if s['status'] in ('active', 'trialing')]
    selected = max(active or candidates, key=lambda s: s.get('created', 0), default=None)
    state = dict(user['stripe_state'] or {})
    state.update(subscription_id=selected['id'] if selected else None,
                 status=selected['status'] if selected else None,
                 cancel_at_period_end=bool(selected and selected.get('cancel_at_period_end')))
    item = _plus_item(selected, config['STRIPE_PLUS_PRICE_ID']) if selected else None
    end = item.get('current_period_end') if item else None
    state['period_end'] = end
    values: dict[str, Any] = {'stripe_state': state}
    if active and end:
      values.update(membership_tier='premium', membership_source='subscription',
                    membership_expires_at=datetime.fromtimestamp(end, timezone.utc),
                    membership_updated_at=datetime.now(timezone.utc), membership_updated_by=None)
    elif user['membership_source'] == 'subscription':
      values.update(membership_tier='free', membership_expires_at=None,
                    membership_updated_at=datetime.now(timezone.utc))
    user.update(**values)
  if event_id:
    app_tables.stripe_events.add_row(event_id=event_id, processed_at=datetime.now(timezone.utc))


def checkout(user, config):
  _validate_price(config)
  user_id = user.get_id()
  customer_id, attempt = _reserve_attempt(user_id)
  if not customer_id:
    key = hashlib.sha256((config['STRIPE_APP_URL'] + user_id).encode()).hexdigest()
    customer = _stripe(config, 'POST', 'customers', {'email': user['email'], 'metadata[account_id]': user_id}, 'customer-' + key)
    customer_id = customer['id']
    _store_customer(user_id, customer_id)
  existing = _subscriptions(config, customer_id)
  if any(_plus_item(s, config['STRIPE_PLUS_PRICE_ID']) and s['status'] not in ('canceled', 'incomplete_expired') for s in existing):
    return {'ok': False, 'message': 'A Plus subscription already exists. Use Manage billing to review it.'}
  # Reload after server writes: Users rows can cache old column values.
  state = _account(user_id)['stripe_state'] or {}
  if state.get('checkout_id'):
    previous = _stripe(config, 'GET', 'checkout/sessions/' + state['checkout_id'])
    if previous['status'] == 'open' and previous.get('url'):
      return {'ok': True, 'url': previous['url']}
    if previous['status'] == 'complete':
      subscription_id = previous.get('subscription')
      subscription = _stripe(config, 'GET', 'subscriptions/' + subscription_id) if subscription_id else None
      if subscription is None or subscription['status'] not in ('canceled', 'incomplete_expired'):
        reconcile_customer(customer_id, config)
        return {'ok': False, 'message': 'Your checkout is being confirmed. Refresh your subscription details shortly.'}
    _rotate_attempt(user_id, attempt)
    _, attempt = _reserve_attempt(user_id)
  root = config['STRIPE_APP_URL']
  session = _stripe(config, 'POST', 'checkout/sessions', {
    'customer': customer_id, 'mode': 'subscription', 'line_items[0][price]': config['STRIPE_PLUS_PRICE_ID'],
    'line_items[0][quantity]': 1, 'client_reference_id': user_id,
    'subscription_data[metadata][account_id]': user_id,
    'success_url': root + '/#?page=subscription&checkout=success',
    'cancel_url': root + '/#?page=subscription&checkout=canceled',
  }, 'checkout-' + attempt)
  _store_checkout(user_id, attempt, session)
  return {'ok': True, 'url': session['url']}


def portal(user, config):
  if not user['stripe_customer_id']:
    return {'ok': False, 'message': 'This account has no Stripe billing profile yet.'}
  session = _stripe(config, 'POST', 'billing_portal/sessions', {
    'customer': user['stripe_customer_id'], 'return_url': config['STRIPE_APP_URL'] + '/#?page=subscription'})
  return {'ok': True, 'url': session['url']}


def _verified_event(body, signature, secret):
  parts = [piece.split('=', 1) for piece in signature.split(',') if '=' in piece]
  timestamp = next((value for key, value in parts if key == 't'), '')
  if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > 300:
    raise ValueError('Invalid webhook timestamp.')
  expected = hmac.new(secret.encode(), timestamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
  if not any(hmac.compare_digest(expected, value) for key, value in parts
             if key == 'v1' and value.isascii()):
    raise ValueError('Invalid webhook signature.')
  event = json.loads(body)
  if not isinstance(event, dict) or not isinstance(event.get('id'), str):
    raise ValueError('Invalid webhook event.')
  return event


@anvil.server.route('/stripe/webhook', methods=['POST'])
def stripe_webhook(**params):
  config = configuration()
  if config is None:
    return anvil.server.HttpResponse(503, 'Billing is not configured.')
  body = anvil.server.request.body
  if len(body) > 2_000_000:
    return anvil.server.HttpResponse(413, 'Payload too large.')
  signature = next((value for name, value in anvil.server.request.headers.items()
                    if name.lower() == 'stripe-signature'), '')
  try:
    event = _verified_event(body, signature, config['STRIPE_WEBHOOK_SECRET'])
  except (ValueError, UnicodeError):
    return anvil.server.HttpResponse(400, 'Invalid webhook.')
  if event.get('type') in _EVENTS:
    obj = event.get('data', {}).get('object', {})
    customer_id = obj.get('customer')
    if isinstance(customer_id, str):
      reconcile_customer(customer_id, config, event['id'])
  return anvil.server.HttpResponse(200, 'Received.')
