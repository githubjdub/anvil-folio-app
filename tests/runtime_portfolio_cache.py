"""Persistence contract checks in the disposable DB: anvil repl - < this file."""
from datetime import date, datetime, timedelta, timezone
import copy
import base64
import gzip
import json
import uuid

import anvil.users
from anvil.tables import app_tables
import investor_profiles
import portfolio_analytics
import portfolios
import sectors


original_get_user = anvil.users.get_user
original_staged = portfolios._staged_analysis
original_fetch = portfolio_analytics._fetch_history
users = []
tag = uuid.uuid4().hex[:12]
as_of = date.today()
quarter_start, quarter_end, _ = investor_profiles.previous_quarter(as_of)
transactions = [{'ticker': 'AAPL', 'trade_date': (quarter_start-timedelta(days=1)).isoformat(),
                 'action': 'Buy', 'shares': 1, 'trade_price': 100}]
analysis = {'ok': True,
    'series': [{'date': transactions[0]['trade_date'], 'portfolio_index': 100,
                'session_complete': True, 'return_observed': True},
               {'date': quarter_end.isoformat(), 'portfolio_index': 110,
                'session_complete': True, 'return_observed': True}],
    'selection_data': {'dates': [], 'securities': [{'symbol': 'AAPL'}]},
    'open_holdings': [{'symbol': 'AAPL', 'company_name': 'Apple', 'shares': 1,
                       'price': 150, 'market_value': 150}],
    'metrics': {'market_value': 150, 'holding_count': 1},
    'metadata': {'start_date': transactions[0]['trade_date'], 'end_date': as_of.isoformat()}}


def forbidden_fetch(*args, **kwargs):
  raise AssertionError('Save/load/navigation fetched market history')


try:
  portfolio_analytics._fetch_history = forbidden_fetch
  for index in range(2):
    users.append(app_tables.users.add_row(email='cache-%s-%s@example.invalid' % (tag, index),
      enabled=True, signed_up=datetime.now(timezone.utc), username='cache_' + tag + str(index),
      username_key='cache_' + tag + str(index), membership_tier='premium', membership_source='creator'))
  user = users[0]
  anvil.users.get_user = lambda: user
  portfolios._staged_analysis = lambda: None
  saved = portfolios.save_portfolio(None, 'Cache fixture', transactions, as_of, 2.5)
  assert saved['ok'], saved
  row = saved['portfolio']
  portfolio_id = row.get_id()
  assert portfolios.load_portfolio(portfolio_id)['analysis'] is None
  portfolios.remember_analysis(transactions, as_of, 2.5, copy.deepcopy(analysis), portfolio_id)
  row = app_tables.portfolios.get_by_id(portfolio_id)
  first_media = row['analysis_cache'].get_bytes()
  first_overview = copy.deepcopy(row['analysis_overview'])
  assert first_overview['version'] == portfolios._OVERVIEW_VERSION
  assert first_overview['fingerprint'] == row['analysis_fingerprint']
  assert portfolios.get_portfolio_startup(portfolio_id)['overview']['summary']['value'] == 150
  assert json.loads(gzip.decompress(first_media))['metrics'] == analysis['metrics']
  opened = portfolios.load_portfolio(portfolio_id)
  assert opened['ok'] and opened['analysis']['metrics'] == analysis['metrics']
  assert portfolios.get_last_portfolio()['portfolio'].get_id() == portfolio_id
  assert portfolios.get_last_portfolio()['analysis']['metrics']['market_value'] == 150
  renamed = portfolios.save_portfolio(portfolio_id, 'Renamed fixture', transactions, as_of, 2.5)
  row = app_tables.portfolios.get_by_id(portfolio_id)
  assert renamed['ok'] and row['analysis_cache'].get_bytes() == first_media
  assert row['analysis_overview'] == first_overview
  print('PASS: save, gzip persistence, cached reopening, most-recent restore, rename preserves analysis; no market calls')

  entries = portfolios.list_portfolios()
  assert len(entries) == 1 and entries[0]['portfolio_id'] == portfolio_id
  column_names = {column['name'] for column in app_tables.portfolio_directory.list_columns()}
  assert not {'transactions', 'analysis_cache', 'analysis_fingerprint'} & column_names
  workspace = portfolios.get_portfolio_workspace()
  assert len(workspace['portfolios']) == 1 and 'analysis' not in workspace
  print('PASS: navigation metadata uses a separate directory without transaction or analysis columns')

  sectors.set_security_sector('AAPL', 'Industrials')
  reopened = portfolios.load_portfolio(portfolio_id)['analysis']
  assert reopened['selection_data']['securities'][0]['sector'] == 'Industrials'
  assert reopened['metrics']['market_value'] == 150
  assert row['analysis_cache'].get_bytes() == first_media
  print('PASS: local classification changes refresh cached display without repricing or rewriting market history')

  user = users[1]
  assert not portfolios.load_portfolio(portfolio_id)['ok']
  assert not portfolios.save_portfolio(portfolio_id, 'Forbidden', transactions, as_of, 2.5)['ok']
  assert portfolios.get_last_portfolio()['portfolio'] is None
  assert portfolios.list_portfolios() == []
  assert not portfolios.load_portfolio('not-a-portfolio-id')['ok']
  print('PASS: cross-account load/save/list/restore and malformed portfolio IDs are rejected')

  user = users[0]
  changed = [dict(transactions[0], shares=2)]
  assert portfolios.save_portfolio(portfolio_id, 'Changed fixture', changed, as_of, 2.5)['ok']
  row = app_tables.portfolios.get_by_id(portfolio_id)
  assert row['analysis_cache'] is None and portfolios.load_portfolio(portfolio_id)['analysis'] is None
  assert row['analysis_overview'] is None
  portfolios.remember_analysis(transactions, as_of, 2.5, copy.deepcopy(analysis), portfolio_id)
  row = app_tables.portfolios.get_by_id(portfolio_id)
  assert row['analysis_cache'] is None, 'Slow old analysis replaced a changed portfolio'
  assert row['analysis_overview'] is None
  fingerprint = portfolios._analysis_fingerprint(changed, as_of, 2.5)
  compressed = gzip.compress(json.dumps(analysis).encode())
  staged = base64.b64encode(compressed).decode('ascii')
  portfolios._staged_analysis = lambda: (fingerprint, staged, datetime.now(), users[1].get_id())
  assert portfolios.save_portfolio(portfolio_id, 'Changed fixture', changed, as_of, 2.5)['ok']
  row = app_tables.portfolios.get_by_id(portfolio_id)
  assert row['analysis_cache'] is None, 'Other account staged data leaked into save'
  assert row['analysis_overview'] is None
  portfolios._staged_analysis = lambda: (fingerprint, staged, datetime.now(), user.get_id())
  assert portfolios.save_portfolio(portfolio_id, 'Changed fixture', changed, as_of, 2.5)['ok']
  assert portfolios.load_portfolio(portfolio_id)['analysis']['metrics']['market_value'] == 150
  row = app_tables.portfolios.get_by_id(portfolio_id)
  assert row['analysis_overview']['fingerprint'] == fingerprint
  assert portfolios.get_portfolio_startup(portfolio_id)['overview']['metrics']['market_value'] == 150
  print('PASS: edit invalidation, stale-analysis race guard, account-bound staging, matching staged analysis reused')

  portfolios._staged_analysis = lambda: None
  assert portfolios.save_portfolio(portfolio_id, 'Rate edit', changed, as_of, 3)['ok']
  row = app_tables.portfolios.get_by_id(portfolio_id)
  assert row['analysis_cache'] is None
  assert row['analysis_overview'] is None
  portfolios.remember_analysis(changed, as_of, 3, copy.deepcopy(analysis), portfolio_id)
  assert portfolios.save_portfolio(portfolio_id, 'Date edit', changed, as_of-timedelta(days=1), 3)['ok']
  row = app_tables.portfolios.get_by_id(portfolio_id)
  assert row['analysis_cache'] is None
  assert row['analysis_overview'] is None
  invalid = portfolios.save_portfolio(portfolio_id, 'Invalid', [dict(changed[0], shares=-1)], as_of, 3)
  row = app_tables.portfolios.get_by_id(portfolio_id)
  assert not invalid['ok'] and row['name'] == 'Date edit'
  # Autosave deliberately supports a portfolio whose last trade was removed.
  empty = portfolios.save_portfolio(portfolio_id, 'Empty', [], as_of, 3)
  row = app_tables.portfolios.get_by_id(portfolio_id)
  assert empty['ok'] and row['transactions'] == [] and row['analysis_cache'] is None
  assert row['analysis_overview'] is None
  anvil.users.get_user = lambda: None
  try:
    portfolios.list_portfolios()
    raise AssertionError('Anonymous portfolio access succeeded')
  except anvil.users.AuthenticationFailed:
    pass
  print('PASS: risk/date changes invalidate cache, invalid saves preserve prior state, empty drafts autosave, anonymous access rejected')
finally:
  anvil.users.get_user = original_get_user
  portfolios._staged_analysis = original_staged
  portfolio_analytics._fetch_history = original_fetch
  for user in users:
    for table_name in ('security_sectors', 'investor_profiles', 'portfolio_directory', 'portfolios'):
      for row in getattr(app_tables, table_name).search(owner=user):
        row.delete()
    user.delete()
  print('Temporary cache fixtures removed; server hooks restored.')
