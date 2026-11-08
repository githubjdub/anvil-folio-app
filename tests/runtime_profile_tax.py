"""Exercise in the disposable agent database: anvil repl - < this file."""
from datetime import date, datetime, timedelta, timezone
import copy
import hashlib
import hmac
import json
import time
import uuid

import anvil
import anvil.users
from anvil.tables import app_tables
import account_profiles
import investor_profiles as profiles
import memberships
import portfolios
import stripe_billing as billing
import tax_estimator as estimator
import tax_rules


settings = dict(tax_year=2026, filing_status='single', state='federal', ordinary_income=100000,
                magi=116100, other_investment_income=0, qualified_dividends=0, other_short=0, other_long=0)
clean_settings = tax_rules.validate_settings(settings)
base = [{'symbol':'AAPL','action':'Buy','trade_date':date(2025,1,1),'shares':10,'trade_price':100,'row':1},
        {'symbol':'AAPL','action':'Buy','trade_date':date(2026,11,1),'shares':20,'trade_price':150,'row':2}]
histories = {'AAPL': {'close':{}, 'splits':{}}}
sell = [{'ticker':'AAPL','action':'Sell','trade_date':'2026-11-09','shares':15,'trade_price':200,'fee':15}]
original_base = copy.deepcopy(base)
result = estimator._calculate(base, histories, date(2026,11,8), sell, clean_settings)
assert result['short_gain'] == 245 and result['long_gain'] == 990, result
assert abs(result['tax']['change']['total'] - 202.4) < .001, result
assert abs(result['after_tax_cash'] - 2782.6) < .001
assert result['remaining_holdings'] == [{'symbol':'AAPL','shares':15.0}]
assert base == original_base, 'Scenario mutated the initial lots'

buy_sell = [{'ticker':'MSFT','action':'Buy','trade_date':'2026-11-08','shares':10,'trade_price':100,'fee':10},
            {'ticker':'MSFT','action':'Sell','trade_date':'2026-12-10','shares':5,'trade_price':120,'fee':5}]
result = estimator.estimate_trade_taxes([], date(2026,11,8), buy_sell, settings)
assert result['ok'] and result['short_gain'] == 90, result
assert abs(result['tax']['change']['federal'] - 19.8) < .001
loss = [dict(sell[0], shares=15, trade_price=80, fee=0)]
result = estimator._calculate(base, histories, date(2026,11,8), loss, clean_settings)
assert result['requires_wash_review'] and result['tax'] is None
reviewed = dict(clean_settings, wash_sale_reviewed=True)
result = estimator._calculate(base, histories, date(2026,11,8), loss, reviewed)
assert result['tax'] is not None and result['tax']['change']['federal'] < 0
unknown = [dict(base[0], trade_price=None)]
try:
  estimator._calculate(unknown, histories, date(2026,11,8), [dict(sell[0], shares=1)], clean_settings)
  raise AssertionError('Unknown basis accepted')
except tax_rules.TaxInputError:
  pass
for invalid in ([dict(buy_sell[0], shares=-1)], [dict(buy_sell[0], ticker='bad<script>')],
                [dict(buy_sell[0], trade_date='2027-01-01')], [dict(buy_sell[1], shares=100)]):
  output = estimator.estimate_trade_taxes([], date(2026,11,8), invalid, settings)
  assert output['ok'] is False, output
print('PASS: live scenario endpoint, FIFO terms/fees, loss review, unknown basis, invalid trades, no input mutation')

original_get_user = anvil.users.get_user
original_stripe = billing._stripe
original_saved_analysis = portfolios._saved_analysis
users, portfolio_rows, event_ids = [], [], []
tag = uuid.uuid4().hex[:10]
snapshot = app_tables.profile_rankings.get(quarter=profiles.previous_quarter()[2])
original_snapshot = (list(snapshot['participants'] or []), snapshot['generated_at']) if snapshot is not None else None
calls = []
fake_subscriptions = []
expiry = int((datetime.now(timezone.utc) + timedelta(days=31)).timestamp())
config = {'STRIPE_APP_URL':'https://example.invalid', 'STRIPE_PLUS_PRICE_ID':'price_fixture_plus'}


def fake_stripe(config, method, path, parameters=None, idempotency_key=None):
  calls.append((method,path,parameters,idempotency_key))
  if path == 'customers':
    return {'id':'cus_fixture_' + tag}
  if path == 'subscriptions':
    return {'data':fake_subscriptions, 'has_more':False}
  if path == 'checkout/sessions':
    return {'id':'cs_fixture_' + tag, 'url':'https://checkout.stripe.com/c/pay/fixture'}
  if path.startswith('checkout/sessions/'):
    return {'status':'open','url':'https://checkout.stripe.com/c/pay/fixture'}
  if path.startswith('subscriptions/') and method == 'POST':
    return {'id':path.split('/')[-1], 'cancel_at_period_end':True}
  raise AssertionError('Unexpected billing request: ' + method + ' ' + path)


def make_analysis(return_value):
  return {'series':[{'date':'2026-06-30','portfolio_index':100,'session_complete':True,'return_observed':True},
                    {'date':'2026-07-01','portfolio_index':101,'session_complete':True,'return_observed':True},
                    {'date':'2026-09-30','portfolio_index':100*(1+return_value),'session_complete':True,'return_observed':True}],
          'selection_data':{'dates':[], 'securities':[{'symbol':'AAPL'}]},
          'open_holdings':[{'symbol':'AAPL','company_name':'Apple','shares':1,'price':150,'market_value':150}],
          'metrics':{'market_value':150,'holding_count':1},
          'metadata':{'start_date':'2026-06-30','end_date':'2026-11-06'}}


try:
  for i in range(5):
    user = app_tables.users.add_row(email='folio-fixture-%s-%s@example.invalid' % (tag,i), enabled=True,
      username='fixture_' + tag + str(i), username_key='fixture_' + tag + str(i), signed_up=datetime.now(timezone.utc))
    users.append(user)
    anvil.users.get_user = lambda user=user: user
    txns = [{'ticker':'AAPL','trade_date':'2026-06-30','action':'Buy','shares':1,'trade_price':100}]
    saved = portfolios.save_portfolio(None, 'Fixture portfolio %s' % i, txns, date(2026,11,8), 0)
    assert saved['ok'], saved
    portfolio = saved['portfolio']
    portfolio_rows.append(portfolio)
    portfolios.remember_analysis(txns, date(2026,11,8), 0, make_analysis(.1-i*.01), portfolio.get_id())
    if i < 4:
      user.update(membership_tier='pro' if i == 0 else 'premium', membership_source='creator')
    profile = account_profiles.get_my_profile()['investor']
    assert profile['metrics']['holding_count'] == 1 and len(profile['metrics']['holdings']) == 1, profile
    assert abs(profile['metrics']['return'] - (.1-i*.01)) < 1e-8, profile
    assert profile['verification']['verified'] is (i < 4)
    assert profiles.set_investor_profile(portfolio.get_id(), i < 4)['ok']

  anvil.users.get_user = lambda: users[0]
  assert not profiles.set_investor_profile(portfolio_rows[1].get_id(), True)['ok'], 'Cross-account primary accepted'
  assert not profiles.set_investor_profile('not-an-id', True)['ok']
  assert not profiles.set_profile_avatar(anvil.BlobMedia('image/svg+xml', b'<svg/>', name='bad.svg'))['ok']
  assert profiles.set_profile_avatar(None)['ok']
  profiles.refresh_profile_rankings()
  own = profiles.profile_summary_for(users[0])
  assert own['ranking']['quartile'] == 1 and own['ranking']['rank'] == 1, own['ranking']
  assert own['ranking']['percentile'] == 87.5, own['ranking']
  assert profiles._percentile(1, [1,1,1,1])['percentile'] == 50
  assert profiles._percentile(1, [1,1,1,1])['quartile'] == 2
  # Cached profile navigation must not decompress any analysis.
  def forbidden_analysis(*args):
    raise AssertionError('Cached profile reloaded a transaction history')
  portfolios._saved_analysis = forbidden_analysis
  assert profiles.profile_summary_for(users[0])['metrics']['holding_count'] == 1
  portfolios._saved_analysis = original_saved_analysis
  assert profiles.set_investor_profile(portfolio_rows[0].get_id(), False)['ok']
  snapshot = app_tables.profile_rankings.get(quarter=profiles.previous_quarter()[2])
  assert users[0].get_id() not in {p['user_id'] for p in snapshot['participants']}
  anvil.users.get_user = lambda: None
  try:
    account_profiles.get_my_profile()
    raise AssertionError('Anonymous profile access succeeded')
  except anvil.users.AuthenticationFailed:
    pass
  print('PASS: saved profile stats/holdings, private ownership, cached navigation, Pro inclusion, rankings/ties, immediate opt-out, auth and invalid avatar')

  user_id = users[4].get_id()
  anvil.users.get_user = lambda: app_tables.users.get_by_id(user_id)
  billing._stripe = fake_stripe
  user = anvil.users.get_user()
  output = billing.verification_checkout(user, config)
  assert output['ok'] and not profiles.verification_for(anvil.users.get_user())['verified']
  checkout_calls = [c for c in calls if c[1] == 'checkout/sessions']
  assert len(checkout_calls) == 1
  params = checkout_calls[0][2]
  assert params['line_items[0][price_data][unit_amount]'] == 100
  assert params['line_items[0][price_data][recurring][interval]'] == 'month'
  assert params['subscription_data[metadata][folio_product]'] == 'verified'
  assert billing.verification_checkout(anvil.users.get_user(), config)['ok']
  assert len([c for c in calls if c[1] == 'checkout/sessions']) == 1
  badge = {'id':'sub_badge_fixture', 'status':'active','created':1,'metadata':{'folio_product':'verified'},
    'items':{'data':[{'quantity':1,'current_period_end':expiry,
                     'price':{'currency':'usd','unit_amount':100,'recurring':{'interval':'month','interval_count':1,'usage_type':'licensed'}}}]}}
  fake_subscriptions[:] = [badge]
  event_id = 'evt_fixture_' + tag
  event_ids.append(event_id)
  customer_id = anvil.users.get_user()['stripe_customer_id']
  billing.reconcile_customer(customer_id, config, event_id)
  assert profiles.verification_for(anvil.users.get_user())['verified']
  assert memberships.membership_for(anvil.users.get_user())['portfolio_limit'] == 1
  billing.reconcile_customer(customer_id, config, event_id)
  assert len(app_tables.stripe_events.search(event_id=event_id)) == 1
  assert not billing.verification_checkout(anvil.users.get_user(), config)['ok']
  wrong_price = copy.deepcopy(badge)
  wrong_price['items']['data'][0]['price']['unit_amount'] = 999
  fake_subscriptions[:] = [wrong_price]
  billing.reconcile_customer(customer_id, config)
  assert not profiles.verification_for(anvil.users.get_user())['verified']
  plus = {'id':'sub_plus_fixture','status':'active','created':2,'metadata':{},
          'items':{'data':[{'quantity':1,'current_period_end':expiry,'price':{'id':'price_fixture_plus'}}]}}
  fake_subscriptions[:] = [badge, plus]
  billing.reconcile_customer(customer_id, config)
  assert profiles.verification_for(anvil.users.get_user())['included']
  assert any(c[1] == 'subscriptions/sub_badge_fixture' and c[2]['cancel_at_period_end'] == 'true' for c in calls)
  fake_subscriptions[:] = [dict(badge, status='canceled')]
  billing.reconcile_customer(customer_id, config)
  assert not profiles.verification_for(anvil.users.get_user())['verified']
  print('PASS: mocked Stripe $1 recurring checkout/reuse, no redirect grant, signed-state entitlement, price validation, duplicate events, cancellation and Plus inclusion')

  body = json.dumps({'id':'evt_signature_fixture'}).encode()
  timestamp = str(int(time.time()))
  signature = hmac.new(b'fixture_signing_secret', timestamp.encode()+b'.'+body, hashlib.sha256).hexdigest()
  assert billing._verified_event(body, 't='+timestamp+',v1='+signature, 'fixture_signing_secret')['id'] == 'evt_signature_fixture'
  for invalid in ('t='+timestamp+',v1=wrong', 't=1,v1='+signature):
    try:
      billing._verified_event(body, invalid, 'fixture_signing_secret')
      raise AssertionError('Invalid signature accepted')
    except ValueError:
      pass
  print('PASS: Stripe webhook signature and timestamp checks')
finally:
  anvil.users.get_user = original_get_user
  billing._stripe = original_stripe
  portfolios._saved_analysis = original_saved_analysis
  for user in users:
    for profile in app_tables.investor_profiles.search(owner=user):
      profile.delete()
    for entry in app_tables.portfolio_directory.search(owner=user):
      entry.delete()
    for portfolio in app_tables.portfolios.search(owner=user):
      portfolio.delete()
    user.delete()
  for event_id in event_ids:
    event = app_tables.stripe_events.get(event_id=event_id)
    if event is not None:
      event.delete()
  current_snapshot = app_tables.profile_rankings.get(quarter=profiles.previous_quarter()[2])
  if current_snapshot is not None:
    if original_snapshot is None:
      current_snapshot.delete()
    else:
      current_snapshot.update(participants=original_snapshot[0], generated_at=original_snapshot[1])
  print('Temporary fixtures removed; server hooks restored.')
