"""Disposable DB: anvil repl - < tests/runtime_hedge_fund_fallback.py

All SEC, mapping and price requests are fixtures. No external download occurs.
"""
from datetime import date, datetime, timedelta, timezone
import copy
import json
import uuid

import hedge_fund_data as data
import hedge_fund_engine as engine
import hedge_funds as funds
from anvil.tables import app_tables

key = 'Fixture+' + uuid.uuid4().hex[:10]
entry = (key, 'Fixture fund', 'Fixture manager', '1234567', ())
old_catalog, old_request, old_price, old_clock = data._CATALOG, data._request, engine.fetch_history, funds._now
old_series = engine.get_series
clock = [datetime.now(timezone.utc)]
calls = []
accession = '0001234567-26-000001'


def source(url, provider, body=None, timeout=20):
  assert timeout == 6, timeout
  calls.append((provider, url))
  if provider == 'figi':
    jobs = json.loads(body)
    assert len(jobs) <= 5
    return json.dumps([{'data': [dict(marketSector='Equity', exchCode='US', securityType2='Common Stock', ticker='FIX')]} for job in jobs]).encode()
  if 'submissions' in url:
    return json.dumps(dict(name='Fixture', filings=dict(recent=dict(form=['13F-HR'], reportDate=['2026-09-30'], filingDate=['2026-10-15'], accessionNumber=[accession]), files=[]))).encode()
  entries = ''.join('<infoTable><nameOfIssuer>Fixture</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>%09d</cusip><value>100</value><shrsOrPrnAmt><sshPrnamt>1</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>' % number for number in range(1, 7))
  return ('<XML><edgarSubmission><reportCalendarOrQuarter>09-30-2026</reportCalendarOrQuarter><tableEntryTotal>6</tableEntryTotal></edgarSubmission></XML><XML><informationTable>' + entries + '</informationTable></XML>').encode()


def price(symbol, start, now=None, timeout=20):
  assert timeout == 6
  calls.append(('prices', symbol))
  values = {date(2026, 9, 30): 100, date(2026, 10, 1): 101, date(2026, 11, 6): 110}
  return dict(close=values, low=values, high=values, dividends={}, splits={}, instrument_type='EQUITY')


def forbidden(*args, **kwargs):
  raise AssertionError('An ordinary cached read tried to build a full model')


def tick(job):
  clock[0] += timedelta(seconds=5)
  before = len(calls)
  result = funds.get_hedge_fund_update_status(job)
  assert len(calls) - before <= 1, calls[before:]
  assert 'histories' not in result and 'update_cache' not in result
  return result


try:
  data._CATALOG = old_catalog + (entry,)
  data._FUNDS[key] = entry
  data._request = source
  engine.fetch_history = price
  engine.get_series = forbidden
  funds._now = lambda: clock[0]

  assert not funds.update_hedge_fund_filings('https://untrusted.example')['ok']
  started = funds.update_hedge_fund_filings(key)
  assert started['state'] == 'running' and not calls
  job = started['job_id']
  assert funds.update_hedge_fund_filings(key)['job_id'] == job
  row, lease, result = funds._claim_step(job)
  assert row is not None and result is None
  duplicate = funds.get_hedge_fund_update_status(job)
  assert duplicate['state'] == 'running' and not calls
  funds._release_step(key, job, lease)
  print('PASS: trusted catalog restriction, shared job, persisted lease prevents duplicate provider request')

  # Unexpected infrastructure failures raise and release the lease for retry.
  data._request = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('fixture unexpected'))
  try:
    tick(job)
    raise AssertionError('Unexpected error was swallowed')
  except RuntimeError as error:
    assert str(error) == 'fixture unexpected'
  row = app_tables.hedge_funds.get(fund_key=key)
  assert row['update_started_at'] is None and row['update_cache'] is not None
  data._request = source
  print('PASS: unexpected error propagates, lease released, persistent work retained')

  for count in range(40):
    result = tick(job)
    if result['state'] == 'completed':
      break
  assert result['ok'], result
  assert result['state'] == 'completed', result
  assert len(data.get_snapshots(key)) == 1
  row = app_tables.hedge_funds.get(fund_key=key)
  assert row['update_cache'] is None and row['update_started_at'] is None
  model_row = app_tables.hedge_fund_models.get(fund_key=key)
  assert model_row is not None and model_row['model']['ok']
  assert len(model_row['model']['series']) == 3
  assert len([call for call in calls if call[0] == 'figi']) == 2
  assert len([call for call in calls if call[0] == 'prices']) == 3
  print('PASS: staged SEC discovery, filing, multiple mapping batches, one-symbol prices, pure model build and cache cleanup')

  count = len(calls)
  profile = funds.get_hedge_fund_profile(key)
  assert profile['update']['state'] == 'completed'
  assert funds._model(key)['ok']
  assert len(calls) == count
  assert funds.update_hedge_fund_filings(key)['state'] == 'completed'
  print('PASS: cache-only model/profile reads and shared cooldown use no external provider requests')

  # Migrate abandoned running task handles, including corrupt page checkpoints.
  # A cached model must not make cooldown handling strand those running rows.
  for legacy_job, include_media in [('old-background-handle', False), ('old-background-with-media', True), ('legacy-no-progress', False), ('page-missing-checkpoint', False)]:
    previous = copy.deepcopy(model_row['model'])
    media = funds._pack_work(funds._refresh.new_progress(key)) if include_media else None
    row.update(update_job_id=legacy_job, update_started_at=clock[0], update_cache=media,
               update_progress=None if legacy_job == 'legacy-no-progress' else {'state': 'running', 'phase': 'prices', 'message': 'Abandoned fixture'})
    status = funds.get_hedge_fund_update_status(legacy_job)
    assert not status['ok']
    migrated = funds.update_hedge_fund_filings(key)
    assert migrated['ok'] and migrated['state'] == 'running', migrated
    assert migrated['job_id'].startswith('page-') and migrated['job_id'] != legacy_job
    assert row['update_progress']['phase'] == 'prepare_model'
    assert funds._model(key) == previous
    before = len([call for call in calls if call[0] == 'sec'])
    for count in range(10):
      result = tick(migrated['job_id'])
      if result['state'] == 'completed':
        break
    assert result['ok'] and result['state'] == 'completed', result
    assert len([call for call in calls if call[0] == 'sec']) == before
    model_row = app_tables.hedge_fund_models.get(fund_key=key)
  print('PASS: abandoned running legacy handles and page jobs without checkpoint migrate, preserve cached model, and resume saved filings')

  # An interrupted old background job may already have checked SEC but not
  # built any model: restart only the model stages during the filing cooldown.
  model_row.delete()
  job = funds.update_hedge_fund_filings(key)['job_id']
  row = app_tables.hedge_funds.get(fund_key=key)
  assert row['update_progress']['phase'] == 'prepare_model'
  before = len([call for call in calls if call[0] == 'sec'])
  for count in range(10):
    result = tick(job)
    if result['state'] == 'completed':
      break
  assert result['ok'] and result['state'] == 'completed', result
  assert len([call for call in calls if call[0] == 'sec']) == before
  model_row = app_tables.hedge_fund_models.get(fund_key=key)
  print('PASS: legacy interrupted worker recovers saved filings without repeating SEC requests')

  # Stale data remains readable during resumable updates and source outages.
  old_model = copy.deepcopy(model_row['model'])
  clock[0] += timedelta(days=1)
  job = funds.update_hedge_fund_filings(key)['job_id']
  assert funds._model(key) == old_model
  data._request = lambda *args, **kwargs: (_ for _ in ()).throw(data._SourceError('fixture SEC unavailable'))
  result = tick(job)
  assert not result['ok'] and 'retry in 15 minutes' in result['message']
  assert funds._model(key) == old_model
  assert not funds.update_hedge_fund_filings(key)['ok']
  assert row['update_cache'] is None
  clock[0] += timedelta(minutes=16)
  retry = funds.update_hedge_fund_filings(key)
  assert retry['state'] == 'running' and retry['job_id'] != job
  assert not funds.get_hedge_fund_update_status(job)['ok']
  print('PASS: provider failure preserves model, persisted retry backoff, expired job rejected, later retry permitted')

  try:
    funds._pack_work({'oversized': 'x' * (8 * 1024 * 1024)})
    raise AssertionError('Unbounded work was stored')
  except data._SourceError:
    pass
  print('PASS: bounded temporary work storage')
finally:
  data._CATALOG, data._request, engine.fetch_history, funds._now = old_catalog, old_request, old_price, old_clock
  engine.get_series = old_series
  data._FUNDS.pop(key, None)
  for name in ('hedge_fund_snapshots', 'hedge_fund_models', 'hedge_funds'):
    for row in list(getattr(app_tables, name).search(fund_key=key)):
      row.delete()
