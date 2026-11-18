"""Run with anvil repl - < tests/runtime_navigation_speed.py in the temporary DB."""
from datetime import date, datetime, timezone
from time import perf_counter
import uuid

import anvil.users
from anvil.tables import app_tables
import account_profiles
import portfolios
import investor_profiles
import portfolio_analytics
import planning_inputs
import notifications
import tax_estimator
import subscriptions


original_user = anvil.users.get_user
original_directory = portfolios._directory_for
original_fetch = portfolio_analytics._fetch_history
original_poll = notifications._poll_for_visit
users = []
directory_calls = []
tag = uuid.uuid4().hex[:12]


def counted_directory(user):
    directory_calls.append(user.get_id())
    return original_directory(user)


def forbidden_external(*args, **kwargs):
    raise AssertionError('Metadata/preloaded inbox unexpectedly contacted a market/SEC source')


try:
    for number in range(2):
        users.append(app_tables.users.add_row(
            email='navigation-%s-%s@example.invalid' % (tag, number), enabled=True,
            signed_up=datetime.now(timezone.utc), username='nav_' + tag + str(number),
            username_key='nav_' + tag + str(number), membership_tier='premium', membership_source='creator'))
    user = users[0]
    anvil.users.get_user = lambda: user
    portfolio_analytics._fetch_history = forbidden_external
    notifications._poll_for_visit = forbidden_external
    saved = portfolios.save_portfolio(None, 'Navigation fixture', [dict(
        ticker='AAPL', trade_date='2026-01-02', action='Buy', shares=1, trade_price=100)], date.today(), 2.5)
    assert saved['ok'], saved
    identifier = saved['portfolio'].get_id()
    portfolios._directory_for = counted_directory
    started = perf_counter()
    workspace = portfolios.get_portfolio_workspace()
    elapsed = perf_counter() - started
    assert len(directory_calls) == 1, directory_calls
    assert workspace['portfolios'][0]['portfolio_id'] == identifier
    assert workspace['profile']['username'] == user['username']
    assert workspace['usage']['count'] == 1
    assert 'analysis' not in workspace
    print('PASS: bootstrap queries the directory once and returns owner-scoped live rows (%.3fs in temporary runtime)' % elapsed)

    directory_calls.clear()
    profile = account_profiles.get_my_profile()
    assert len(directory_calls) == 1
    assert profile['username'] == workspace['profile']['username']
    assert profile['investor']['primary_portfolio_id'] == identifier
    assert portfolios.get_last_portfolio()['portfolio'].get_id() == identifier
    for tool, year in (('tax', 2026), ('retirement', None)):
        result = planning_inputs.get_planning_inputs(identifier, tool, year)
        assert result['ok'] and result['plan'] is None
    assert 2026 in tax_estimator.get_tax_estimator_catalog()['tax_years']
    inbox = notifications.get_notification_workspace(check_for_updates=False)
    assert inbox['unread_count'] == 0 and not inbox['messages']
    assert len(app_tables.portfolio_plans.search(owner=user)) == 0
    billing = subscriptions.get_billing_workspace(include_details=True, refresh=False)
    assert billing['account_id'] == user.get_id() and billing['details']['portfolio_count'] == 1
    print('PASS: profile, portfolio restore, planner inputs, inbox and billing preload; no market/SEC checks or planning writes')

    user = users[1]
    other = portfolios.get_portfolio_workspace()
    assert not other['portfolios'] and other['usage']['count'] == 0
    assert other['profile']['username'] == user['username']
    assert not portfolios.load_portfolio(identifier)['ok']
    assert not planning_inputs.get_planning_inputs(identifier, 'tax', 2026)['ok']
    anvil.users.get_user = lambda: None
    try:
        portfolios.get_portfolio_workspace()
        raise AssertionError('Anonymous workspace access succeeded')
    except anvil.users.AuthenticationFailed:
        pass
    print('PASS: other accounts and anonymous callers cannot access the prepared workspace')
finally:
    anvil.users.get_user = original_user
    portfolios._directory_for = original_directory
    portfolio_analytics._fetch_history = original_fetch
    notifications._poll_for_visit = original_poll
    for user in users:
        for name in ('notification_settings', 'portfolio_plans', 'investor_profiles', 'portfolio_directory', 'portfolios'):
            for row in getattr(app_tables, name).search(owner=user):
                row.delete()
        user.delete()
    print('Temporary navigation fixtures removed; server hooks restored.')
