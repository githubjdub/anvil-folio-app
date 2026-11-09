"""Temporary-database planner checks: anvil repl - < tests/runtime_synced_planning.py."""
from datetime import date, datetime
import uuid
import copy

import anvil.users
from anvil.tables import app_tables
import portfolios
import portfolio_analytics
import planning_inputs
import planning_context
import tax_estimator

original_user = anvil.users.get_user
original_histories = tax_estimator._histories
original_fetch = tax_estimator._fetch_history
users = []
rows = []
tag = uuid.uuid4().hex[:12]

def forbidden(*args, **kwargs):
    raise AssertionError('Planning used a market request')

try:
    for number in range(2):
        users.append(app_tables.users.add_row(email='planner-fixture-%s-%s@example.invalid' % (tag,number), enabled=True,
            username='planner_' + tag + str(number), username_key='planner_' + tag + str(number), signed_up=datetime.now()))
    anvil.users.get_user = lambda: users[0]
    transactions = [dict(ticker='AAPL', trade_date='2025-01-02', action='Buy', shares=10, trade_price=100),
                    dict(ticker='AAPL', trade_date='2026-06-30', action='Sell', shares=2, trade_price=200)]
    as_of = date(2026,6,30)
    saved = portfolios.save_portfolio(None, 'Planner fixture', transactions, as_of, 0)
    assert saved['ok'], saved
    row = saved['portfolio']
    rows.append(row)
    identifier = row.get_id()
    clean, _, _ = portfolio_analytics._validate(transactions, as_of, 0)
    days = [date(2025,1,2), date(2025,12,31), date(2026,1,2), as_of]
    stock = dict(company_name='Apple Inc.', close=dict(zip(days, [100,120,130,200])), splits={}, dividends={date(2026,1,2):1})
    market = dict(company_name='Index', close=dict(zip(days, [100,110,111,120])), splits={}, dividends={})
    analysis = portfolio_analytics._build_analysis(clean, as_of, 0, {'AAPL':stock, '^GSPC':market, '^SP500TR':market})
    assert analysis['planning_context']['earned_dividends_by_year']['2026'] == 10
    assert analysis['planning_context']['tax']['summary']['realized_long_term'] == 200
    portfolios.remember_analysis(transactions, as_of, 0, analysis, identifier)
    assert planning_inputs.get_planning_inputs(identifier, 'tax', 2026)['plan'] is None
    retirement = dict(current_age='41', retirement_age='65', end_age='95', starting_balance='100', monthly_savings='500',
      monthly_spending='3000', monthly_income='1500', expected_return='5', volatility='15', inflation='2.5', annual_fee='.2', bequest_goal='0', balance_source='portfolio')
    output = planning_inputs.save_planning_inputs(identifier, 'retirement', retirement)
    assert output['ok'] and output['plan']['inputs'] == retirement
    tax = dict(filing_status='single', state='federal', ordinary_income='100000', magi='116100', other_investment_income='0',
      qualified_dividends='0', other_short='0', other_long='0', state_short_rate='0', state_long_rate='0', portfolio_qualified='0',
      include_portfolio_dividends=False, wash_sale_reviewed=False, scenario_trades=[])
    output = planning_inputs.save_planning_inputs(identifier, 'tax', tax, 2026)
    assert output['ok']
    tax_2025 = dict(tax, ordinary_income='90000')
    assert planning_inputs.save_planning_inputs(identifier, 'tax', tax_2025, 2025)['ok']
    assert planning_inputs.get_planning_inputs(identifier, 'tax', 2026)['plan']['inputs']['ordinary_income'] == '100000'
    assert planning_inputs.get_planning_inputs(identifier, 'tax', 2025)['plan']['inputs']['ordinary_income'] == '90000'
    assert len(app_tables.portfolio_plans.search(owner=users[0])) == 3
    print('PASS: retirement and tax drafts persist separately per portfolio/year; incomplete draft fields preserved')

    # Isolate fresh-account access and trust checks without changing login state.
    anvil.users.get_user = lambda: users[1]
    assert not planning_inputs.get_planning_inputs(identifier, 'tax', 2026)['ok']
    assert not planning_inputs.save_planning_inputs(identifier, 'retirement', retirement)['ok']
    assert not tax_estimator.estimate_saved_trade_taxes(identifier, [], {})['ok']
    anvil.users.get_user = lambda: users[0]
    assert not planning_inputs.save_planning_inputs(identifier, 'tax', dict(tax, hidden_payload='bad'), 2026)['ok']
    assert not planning_inputs.save_planning_inputs(identifier, 'tax', tax, 2027)['ok']
    assert not planning_inputs.save_planning_inputs(identifier, 'retirement', dict(retirement, monthly_income=float('nan')))['ok']
    print('PASS: cross-account reads/writes/estimates denied, unsupported years and malformed inputs rejected')

    tax_estimator._histories = forbidden
    tax_estimator._fetch_history = forbidden
    settings = dict(tax_year=2026, filing_status='single', state='federal', ordinary_income=100000, magi=116100,
      other_investment_income=0, qualified_dividends=0, other_short=0, other_long=0, portfolio_qualified=0)
    base = tax_estimator.estimate_saved_trade_taxes(identifier, [], settings)
    assert base['ok'] and base['base_long'] == 200, base
    assert base['tax']['change']['total'] == 0
    assert len(base['logged_realized']) == 1
    hypothetical = [dict(ticker='AAPL', action='Sell', trade_date='2026-07-01', shares=1, trade_price=250, fee=0)]
    result = tax_estimator.estimate_saved_trade_taxes(identifier, hypothetical, settings)
    assert result['ok'] and result['long_gain'] == 150, result
    assert result['remaining_holdings'] == [{'symbol':'AAPL','shares':7.0}]
    dividend = tax_estimator.estimate_saved_trade_taxes(identifier, [], dict(settings, include_portfolio_dividends=True))
    assert dividend['ok'] and dividend['portfolio_dividends'] == 10
    assert dividend['tax']['after']['federal'] > base['tax']['after']['federal']
    qualified = tax_estimator.estimate_saved_trade_taxes(identifier, [], dict(settings, include_portfolio_dividends=True, portfolio_qualified=10))
    assert qualified['ok'] and qualified['tax']['after']['federal'] < dividend['tax']['after']['federal']
    unknown = tax_estimator.estimate_saved_trade_taxes(identifier, [], dict(settings, include_portfolio_dividends=True, portfolio_qualified=11))
    assert not unknown['ok']
    prior = tax_estimator.estimate_saved_trade_taxes(identifier, [], dict(settings, tax_year=2025))
    assert prior['ok'] and prior['base_long'] == 0, prior
    assert planning_inputs.get_planning_inputs(identifier, 'retirement')['plan']['inputs'] == retirement
    assert portfolios.load_portfolio(identifier)['analysis']['planning_context']['tax']['positions']['AAPL']['shares'] == 8
    print('PASS: zero-row annual estimate, FIFO scenario, dividend assumptions and prior-year tax reuse saved sync with zero market requests and no mutations')

    # Cache invalidation must request a real Sync, never pretend an empty tax year.
    transactions[0]['shares'] = 11
    assert portfolios.save_portfolio(identifier, 'Planner fixture', transactions, as_of, 0)['ok']
    missing = tax_estimator.estimate_saved_trade_taxes(identifier, [], settings)
    assert not missing['ok'] and 'Sync' in missing['message'], missing
    assert planning_inputs.get_planning_inputs(identifier, 'tax', 2026)['plan']['inputs'] == tax
    print('PASS: stale or legacy snapshots require Sync while saved user assumptions remain available')
finally:
    anvil.users.get_user = original_user
    tax_estimator._histories = original_histories
    tax_estimator._fetch_history = original_fetch
    for user in users:
        for plan in list(app_tables.portfolio_plans.search(owner=user)):
            plan.delete()
        for directory in list(app_tables.portfolio_directory.search(owner=user)):
            directory.delete()
        for profile in list(app_tables.investor_profiles.search(owner=user)):
            profile.delete()
    for row in rows:
        row.delete()
    for user in users:
        user.delete()
    print('Planner fixtures cleaned up')
