import anvil.secrets
"""Free/Plus plans and authenticated Stripe actions."""
from datetime import datetime, timezone
from typing import Any
import anvil.server
import anvil.users
from anvil.tables import app_tables
from . import memberships, stripe_billing
_memberships: Any = memberships
_billing: Any = stripe_billing


@anvil.server.callable
def get_subscription_catalog():
  return _catalog(_billing.configuration() is not None)


def _catalog(ready):
  return {'checkout_available': ready, 'billing_portal_available': ready,
          'premium_price_label': '$19.99/month', 'plus_price_label': '$19.99/month',
          'premium_note': 'USD, billed monthly. Save up to 3 portfolios.',
          'free_portfolio_limit': 1, 'plus_portfolio_limit': 3}


@anvil.server.callable(require_user=True)
def get_my_subscription():
  return _subscription_details(_memberships._signed_in_user())


def _subscription_details(user):
  from investor_profiles import verification_for
  membership = memberships.membership_for(user)
  state = user['stripe_state'] or {}
  recurring_statuses = ('active', 'trialing', 'past_due', 'unpaid', 'paused', 'incomplete')
  badge_state = user['verification_state'] or {}
  if state.get('status') not in recurring_statuses and badge_state.get('subscription_id'):
    state = badge_state
  status = state.get('status')
  end = datetime.fromtimestamp(state['period_end'], timezone.utc) if state.get('period_end') else None
  recurring = status in recurring_statuses
  canceling = state.get('cancel_at_period_end', False)
  return {
    'email': user['email'], 'username': user['username'], 'member_since': user['signed_up'],
    'membership': membership, 'verification': verification_for(user), 'portfolio_count': len(app_tables.portfolios.search(owner=user)),
    'status_label': 'Active ' + membership['label'] if membership['is_premium'] else 'Free',
    'access_type_label': membership['label'] + ' membership',
    'billing_status_label': (status or 'No recurring subscription').replace('_', ' ').capitalize(),
    'billing_detail': ('Your subscription is scheduled to end after the current billing period.' if canceling else
                       'Manage payment methods, invoices, and cancellation securely in Stripe.' if recurring else
                       'No recurring subscription is active on this account.'),
    'renewal_label': ('Ends ' if canceling else 'Renews ') + end.strftime('%b %d, %Y (UTC)') if end and recurring else 'No scheduled renewal',
    'can_manage_billing': bool(user['stripe_customer_id']),
  }


@anvil.server.callable
def get_billing_workspace(include_details=False, refresh=False):
  """One owner-scoped bootstrap; Stripe reconciliation is explicitly requested."""
  if type(include_details) is not bool or type(refresh) is not bool:
    raise ValueError('Choose whether to refresh billing details.')
  user = anvil.users.get_user()
  if user is None and (include_details or refresh):
    raise anvil.users.AuthenticationFailed('Sign in to view your billing details.')
  if user is not None:
    user = _memberships._signed_in_user()
  config = _billing.configuration()
  refreshed = {'ok': True}
  if refresh and user is not None and config and user['stripe_customer_id']:
    account_id = user.get_id()
    try:
      _billing.reconcile_customer(user['stripe_customer_id'], config)
    except _billing.BillingSetupError as error:
      refreshed = {'ok': False, 'message': str(error)}
    # Reconciliation updates another Row instance; return the current values.
    user = app_tables.users.get_by_id(account_id)
    if user is None:
      raise anvil.users.AuthenticationFailed('This billing account is no longer available.')
  return {'account_id': user.get_id() if user is not None else None,
          'catalog': _catalog(config is not None),
          'membership': memberships.membership_for(user) if user is not None else None,
          'has_username': bool(user['username']) if user is not None else False,
          'details': _subscription_details(user) if include_details else None,
          'refresh_result': refreshed}


@anvil.server.callable(require_user=True)
def start_verification_checkout():
  user = _memberships._signed_in_user()
  config = _billing.configuration()
  if config is None:
    return {'ok': False, 'message': 'Payments are being set up. No payment has been taken.'}
  if not user['username']:
    return {'ok': False, 'message': 'Choose your username before subscribing.'}
  try:
    return _billing.verification_checkout(user, config)
  except _billing.BillingSetupError as error:
    return {'ok': False, 'message': str(error)}


@anvil.server.callable(require_user=True)
def start_subscription_checkout():
  user = _memberships._signed_in_user()
  config = _billing.configuration()
  if config is None:
    return {'ok': False, 'message': 'Payments are being set up. Please check back soon.'}
  if not user['username']:
    return {'ok': False, 'message': 'Choose your username before subscribing.'}
  if memberships.membership_for(user)['is_premium']:
    return {'ok': False, 'message': 'You already have Plus access. View your current membership below.'}
  try:
    return _billing.checkout(user, config)
  except _billing.BillingSetupError as error:
    return {'ok': False, 'message': str(error)}


@anvil.server.callable(require_user=True)
def open_subscription_billing_portal():
  user = _memberships._signed_in_user()
  config = _billing.configuration()
  if config is None:
    return {'ok': False, 'message': 'Billing management is being set up. Please check back soon.'}
  try:
    return _billing.portal(user, config)
  except _billing.BillingSetupError as error:
    return {'ok': False, 'message': str(error)}


@anvil.server.callable(require_user=True)
def refresh_my_billing():
  user = _memberships._signed_in_user()
  config = _billing.configuration()
  if config and user['stripe_customer_id']:
    try:
      _billing.reconcile_customer(user['stripe_customer_id'], config)
    except _billing.BillingSetupError as error:
      return {'ok': False, 'message': str(error)}
  return {'ok': True}
