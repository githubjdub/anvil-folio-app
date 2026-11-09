"""Private investor profiles and visitor-refreshed peer-ranking snapshots.

Only server-computed saved analyses supply metrics. Membership verification is
a paid badge, not identity verification or an audit of user-entered portfolios.
No public profile endpoint exposes account names, holdings or balances.
"""
from datetime import date, datetime, timedelta, timezone
import math
import uuid
from typing import Any

import anvil
import anvil.server
from anvil.tables import app_tables, in_transaction, order_by, query as q


_RANKING_BATCH = 25
_RANKING_REFRESH_SECONDS = 3600
_RANKING_STEP_SECONDS = 10
_RANKING_LEASE_SECONDS = 120


def previous_quarter(today=None):
  today = today or datetime.now(timezone.utc).date()
  current = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
  end = current - timedelta(days=1)
  start = date(end.year, ((end.month - 1) // 3) * 3 + 1, 1)
  return start, end, "%s-Q%s" % (start.year, (start.month - 1) // 3 + 1)


def _quarter_return(points, start, end, as_of_date, provisional_date):
  baseline = next((p for p in reversed(points) if p['date'] < start.isoformat()), None)
  quarter_points = [p for p in points if start.isoformat() <= p['date'] <= end.isoformat()]
  provisional = provisional_date and start.isoformat() <= provisional_date <= end.isoformat()
  covered = (as_of_date >= end and baseline is not None and quarter_points and not provisional
             and date.fromisoformat(baseline['date']) >= start - timedelta(days=7)
             and date.fromisoformat(quarter_points[-1]['date']) >= end - timedelta(days=7)
             and all(p.get('return_observed', True) for p in quarter_points))
  if covered and baseline['portfolio_index'] > 0:
    value = quarter_points[-1]['portfolio_index'] / baseline['portfolio_index'] - 1
    if math.isfinite(value):
      return value
  return None


def _return_note(value):
  return ('Dividend-inclusive time-weighted return for the full completed quarter.' if value is not None else
          'Full-quarter history and continuous invested capital are required. Sync a portfolio covering the previous quarter.')


@in_transaction
def _profile_for(user):
  profile = app_tables.investor_profiles.get(owner=user)
  return profile if profile is not None else app_tables.investor_profiles.add_row(
    owner=user, ranking_opt_in=False, ranking_key=uuid.uuid4().hex,
    updated_at=datetime.now(timezone.utc))


def verification_for(user):
  from memberships import membership_for
  membership = membership_for(user)
  state = user['verification_state'] or {}
  expires = state.get('period_end')
  paid = (user['enabled'] is True and state.get('status') in ('active', 'trialing')
          and isinstance(expires, (int, float)) and expires > datetime.now(timezone.utc).timestamp())
  included = membership['is_premium']
  return {'verified': bool(included or paid), 'included': included,
          'label': 'Verified · included with ' + membership['label'] if included else
                   'Verified member' if paid else 'Verified · $1/month',
          'detail': 'Paid membership badge. Identity and investment results are not independently verified.',
          'period_end': datetime.fromtimestamp(expires, timezone.utc) if expires else None,
          'canceling': bool(state.get('cancel_at_period_end')),
          'billing_status': state.get('status')}


def metrics_from_analysis(analysis, as_of_date, portfolio_id, portfolio_name):
  start, end, key = previous_quarter()
  result: dict[str, Any] = {'quarter': key, 'quarter_label': 'Q%s %s' % ((start.month - 1) // 3 + 1, start.year),
            'portfolio_id': portfolio_id, 'portfolio_name': portfolio_name,
            'return': None, 'value': None, 'holding_count': None, 'holdings': [],
            'schema_version': 1, 'quarter_returns': {},
            'note': 'Sync and save this portfolio to calculate your profile statistics.'}
  if not analysis:
    return result
  result.update(value=analysis['metrics']['market_value'],
                holding_count=analysis['metrics']['holding_count'],
                as_of=analysis['metadata']['end_date'])
  total = analysis['metrics']['market_value']
  holdings = analysis.get('open_holdings', (analysis.get('sector_allocation') or {}).get('holdings', []))
  result['holdings'] = [dict(item, weight=item['market_value'] / total if total else 0)
    for item in sorted(holdings, key=lambda h: -h['market_value'])[:8]]
  points = [p for p in analysis['series'] if p.get('session_complete', True)]
  result['return'] = _quarter_return(points, start, end, as_of_date, analysis['metadata'].get('provisional_date'))
  result['note'] = _return_note(result['return'])
  # Also materialize recent quarters, including a completed analysis-quarter.
  # Visitor refreshes can roll quarters without reading 3,000 histories.
  cursor = date(as_of_date.year, ((as_of_date.month-1)//3)*3+1, 1)
  for _ in range(8):
    next_start = date(cursor.year+1, 1, 1) if cursor.month == 10 else date(cursor.year, cursor.month+3, 1)
    period_end = next_start - timedelta(days=1)
    period_key = '%s-Q%s' % (cursor.year, (cursor.month-1)//3+1)
    result['quarter_returns'][period_key] = _quarter_return(points, cursor, period_end, as_of_date,
      analysis['metadata'].get('provisional_date'))
    cursor = previous_quarter(cursor)[0]
  return result


def refresh_investor_metrics(portfolio, user):
  """Called on save/sync; unrelated portfolios never replace a chosen primary."""
  profile = _profile_for(user)
  chosen = profile['primary_portfolio_id']
  if chosen and chosen != portfolio.get_id():
    return
  old = profile['metrics'] or {}
  stamp = str(portfolio['analysis_updated_at'])
  if (old.get('schema_version') == 1 and old.get('quarter') == previous_quarter()[2] and old.get('analysis_stamp') == stamp
      and old.get('fingerprint') == portfolio['analysis_fingerprint']
      and old.get('portfolio_name') == portfolio['name']):
    return
  from portfolios import _saved_analysis
  metrics = metrics_from_analysis(_saved_analysis(portfolio), portfolio['as_of_date'], portfolio.get_id(), portfolio['name'])
  metrics.update(analysis_stamp=stamp, fingerprint=portfolio['analysis_fingerprint'])
  _store_metrics(user, portfolio.get_id(), metrics)


@in_transaction
def _store_metrics(user, portfolio_id, metrics):
  profile = _profile_for(user)
  if not profile['primary_portfolio_id'] or profile['primary_portfolio_id'] == portfolio_id:
    profile.update(primary_portfolio_id=portfolio_id, metrics=metrics, updated_at=datetime.now(timezone.utc))
    invalidate_rankings_for(user)


def _percentile(value, values):
  # Midranks give identical returns identical percentiles and quartiles.
  percentile = 100 * (sum(v < value for v in values) + .5 * sum(v == value for v in values)) / len(values)
  return {'percentile': percentile, 'quartile': min(4, 4 - int(percentile // 25)),
          'rank': 1 + sum(v > value for v in values), 'count': len(values)}


def profile_summary_for(user):
  from portfolios import _owned_portfolio, _portfolios_for
  profile = _profile_for(user)
  # Navigation normally reads only the small profile and directory records.
  # Decompress the selected analysis only on first use or quarter rollover;
  # subsequent save/sync hooks keep the materialized metrics current.
  entries = _portfolios_for(user)
  primary = next((p for p in entries if p['portfolio_id'] == profile['primary_portfolio_id']), None)
  if primary is None:
    primary = entries[0] if entries else None
    profile.update(primary_portfolio_id=primary['portfolio_id'] if primary is not None else None, metrics=None)
  cached = profile['metrics']
  if cached and cached.get('quarter') != previous_quarter()[2] and previous_quarter()[2] in cached.get('quarter_returns', {}):
    start, _, key = previous_quarter()
    value = cached['quarter_returns'][key]
    cached.update({'quarter': key, 'quarter_label': 'Q%s %s' % ((start.month-1)//3+1, start.year),
                   'return': value, 'note': _return_note(value)})
    profile['metrics'] = cached
  if primary is not None and (not cached or cached.get('schema_version') != 1 or cached.get('quarter') != previous_quarter()[2]):
    portfolio = _owned_portfolio(primary['portfolio_id'], user)
    if portfolio is not None:
      refresh_investor_metrics(portfolio, user)
      profile = app_tables.investor_profiles.get(owner=user)
      if profile is None:
        raise RuntimeError('Investor profile disappeared during summary refresh.')
  metrics = profile['metrics'] or metrics_from_analysis(None, date.today(), None, 'Choose a saved portfolio')
  verification = verification_for(user)
  ranking = {'quartile': None, 'percentile': None, 'count': 0, 'rank': None,
             'note': 'Opt in to compare your completed-quarter return with Verified members.'}
  if profile['ranking_opt_in']:
    ranking['note'] = ('Verified membership is required for peer rankings.' if not verification['verified'] else
                       'A full completed-quarter return is required for peer rankings.' if metrics['return'] is None else
                       'Rankings refresh while members visit Profile. At least 4 eligible members are required.')
    snapshot = app_tables.profile_rankings.get(quarter=previous_quarter()[2])
    if snapshot is not None and verification['verified'] and metrics['return'] is not None:
      now = datetime.now(timezone.utc).timestamp()
      peers = [p for p in snapshot['participants'] or [] if p['valid_until'] is None or p['valid_until'] > now]
      mine = next((p for p in peers if p['user_id'] == user.get_id()), None)
      ranking.update(count=len(peers), updated_at=snapshot['generated_at'])
      state = snapshot['refresh_state'] or {}
      ranking['refresh_pending'] = bool(state.get('phase'))
      if len(peers) >= 4 and mine and mine['return'] == metrics['return']:
        ranking.update(_percentile(mine['return'], [p['return'] for p in peers]))
        ranking['note'] = 'Among opted-in Verified members with full-quarter data. Q1 is the top quartile; ties share a rank.'
      if state.get('phase'):
        ranking['note'] += ' A refresh is in progress; the last completed comparison stays visible.'
  return {'avatar': profile['avatar'], 'primary_portfolio_id': profile['primary_portfolio_id'],
          'ranking_opt_in': profile['ranking_opt_in'] is True,
          'metrics': metrics, 'verification': verification, 'ranking': ranking}


@anvil.server.callable(require_user=True)
@in_transaction
def set_investor_profile(primary_portfolio_id, ranking_opt_in):
  from account_profiles import _profile_user
  from portfolios import _owned_portfolio
  user = _profile_user()
  if type(ranking_opt_in) is not bool:
    return {'ok': False, 'message': 'Choose whether to join the rankings.'}
  owned = None
  if primary_portfolio_id is not None:
    try:
      owned = _owned_portfolio(primary_portfolio_id, user)
    except ValueError:
      owned = None
    if owned is None:
      return {'ok': False, 'message': 'Choose one of your own saved portfolios.'}
  profile = _profile_for(user)
  if profile['primary_portfolio_id'] != primary_portfolio_id:
    profile.update(metrics=None)
  profile.update(primary_portfolio_id=primary_portfolio_id, ranking_opt_in=ranking_opt_in,
                 updated_at=datetime.now(timezone.utc))
  if owned is not None:
    refresh_investor_metrics(owned, user)
  # Prevent an already-running refresh from reintroducing old data or consent.
  invalidate_rankings_for(user)
  return {'ok': True, 'message': 'Profile preferences saved. Rankings refresh as members visit Profile.'}


@anvil.server.callable(require_user=True)
def set_profile_avatar(media):
  from account_profiles import _profile_user
  user = _profile_user()
  if media is None:
    _profile_for(user).update(avatar=None)
    return {'ok': True, 'message': 'Profile photo removed.'}
  if not isinstance(media, anvil.Media) or not 1 <= media.length <= 2_000_000:
    return {'ok': False, 'message': 'Choose a PNG or JPEG image under 2 MB.'}
  data = media.get_bytes()
  png = data.startswith(b'\x89PNG\r\n\x1a\n')
  jpeg = data.startswith(b'\xff\xd8\xff')
  if not png and not jpeg:
    return {'ok': False, 'message': 'Choose a PNG or JPEG image. SVG and other file types are not supported.'}
  avatar = anvil.BlobMedia('image/png' if png else 'image/jpeg', data, name='avatar.png' if png else 'avatar.jpg')
  _profile_for(user).update(avatar=avatar, updated_at=datetime.now(timezone.utc))
  return {'ok': True, 'message': 'Profile photo saved.'}


@anvil.server.background_task
def refresh_profile_rankings():
  """Retained for a future paid runner; ordinary visits use one bounded step."""
  # An explicitly enabled runner can finish a cycle. No background task is
  # launched by the free-plan fallback, and no portfolio history is loaded.
  for _ in range(1000):
    progress = advance_profile_rankings(bypass_step_cooldown=True)
    if not progress['pending'] or not progress['advanced']:
      break


@in_transaction
def invalidate_rankings_for(user):
  """Atomically remove revoked consent, changed metrics, or changed access."""
  snapshot = app_tables.profile_rankings.get(quarter=previous_quarter()[2])
  if snapshot is None:
    return
  user_id = user.get_id()
  participants = [p for p in snapshot['participants'] or [] if p['user_id'] != user_id]
  state: dict[str, Any] = dict(snapshot['refresh_state'] or {})
  if state.get('phase'):
    state['pending'] = [p for p in state.get('pending', []) if p['user_id'] != user_id]
    state['invalidated'] = list(set(state.get('invalidated', [])) | {user_id})
  snapshot.update(participants=participants, refresh_state=state)


@in_transaction
def _claim_ranking_batch(quarter, bypass_step_cooldown=False):
  now = datetime.now(timezone.utc)
  stamp = now.timestamp()
  snapshot = app_tables.profile_rankings.get(quarter=quarter)
  if snapshot is None:
    snapshot = app_tables.profile_rankings.add_row(quarter=quarter, participants=[])
  state: dict[str, Any] = dict(snapshot['refresh_state'] or {})
  if state.get('lease_until', 0) > stamp:
    return None
  if not state.get('phase'):
    generated = snapshot['generated_at']
    if generated is not None:
      generated = generated.replace(tzinfo=timezone.utc) if generated.tzinfo is None else generated
      if (now - generated).total_seconds() < _RANKING_REFRESH_SECONDS:
        return None
    state = {'phase': 'keys', 'cursor': '', 'pending': [], 'invalidated': []}
  if not bypass_step_cooldown and state.get('next_step', 0) > stamp:
    return None
  token = uuid.uuid4().hex
  state.update(lease=token, lease_until=stamp + _RANKING_LEASE_SECONDS)
  snapshot['refresh_state'] = state
  return {'quarter': quarter, 'token': token, 'phase': state['phase'], 'cursor': state.get('cursor', '')}


def _ranking_candidate(profile, quarter):
  from memberships import membership_for
  user = profile['owner']
  if profile['ranking_opt_in'] is not True or user is None or user['enabled'] is not True:
    return None
  metrics = profile['metrics'] or {}
  value = (metrics.get('return') if metrics.get('quarter') == quarter else
           metrics.get('quarter_returns', {}).get(quarter))
  if not isinstance(value, (int, float)) or not math.isfinite(value):
    return None
  verification = verification_for(user)
  if not verification['verified']:
    return None
  membership = membership_for(user)
  expiry = membership['expires_at'] if membership['is_premium'] else verification['period_end']
  return {'user_id': user.get_id(), 'return': value,
          'valid_until': expiry.timestamp() if expiry else None}


@in_transaction
def _commit_ranking_batch(claim, rows, candidates):
  snapshot = app_tables.profile_rankings.get(quarter=claim['quarter'])
  if snapshot is None:
    return False
  state: dict[str, Any] = dict(snapshot['refresh_state'] or {})
  if state.get('lease') != claim['token']:
    return False  # An expired worker cannot overwrite its replacement.
  now = datetime.now(timezone.utc)
  if claim['phase'] == 'keys':
    # Existing profiles are migrated a batch at a time before pagination begins.
    for profile in rows:
      if not profile['ranking_key']:
        profile['ranking_key'] = uuid.uuid4().hex
    if len(rows) < _RANKING_BATCH:
      state['phase'] = 'scan'
  else:
    blocked = set(state.get('invalidated', []))
    pending = {p['user_id']: p for p in state.get('pending', []) if p['user_id'] not in blocked}
    for candidate in candidates:
      if candidate['user_id'] not in blocked:
        pending[candidate['user_id']] = candidate
    state['pending'] = list(pending.values())
    if rows:
      state['cursor'] = rows[-1]['ranking_key']
    if len(rows) < _RANKING_BATCH:
      stamp = now.timestamp()
      peers = [p for p in pending.values() if p['valid_until'] is None or p['valid_until'] > stamp]
      snapshot.update(participants=peers, generated_at=now, refresh_state={})
      return True
  state.update(lease=None, lease_until=0, next_step=now.timestamp() + _RANKING_STEP_SECONDS)
  snapshot['refresh_state'] = state
  return True


def advance_profile_rankings(bypass_step_cooldown=False):
  """One server-controlled batch, safe across workers and ordinary free hosting."""
  quarter = previous_quarter()[2]
  claim = _claim_ranking_batch(quarter, bypass_step_cooldown)
  advanced = False
  if claim is not None:
    if claim['phase'] == 'keys':
      rows = list(app_tables.investor_profiles.search(ranking_key=None)[:_RANKING_BATCH])
      candidates = []
    else:
      rows = list(app_tables.investor_profiles.search(
        order_by('ranking_key'), ranking_key=q.greater_than(claim['cursor']))[:_RANKING_BATCH])
      candidates = [candidate for candidate in (_ranking_candidate(p, quarter) for p in rows) if candidate is not None]
    advanced = _commit_ranking_batch(claim, rows, candidates)
  snapshot = app_tables.profile_rankings.get(quarter=quarter)
  return {'pending': bool(snapshot and (snapshot['refresh_state'] or {}).get('phase')),
          'advanced': advanced,
          'updated_at': snapshot['generated_at'] if snapshot is not None else None}


@anvil.server.callable(require_user=True)
def refresh_my_profile_rankings():
  from account_profiles import _profile_user
  user = _profile_user()
  progress = advance_profile_rankings()
  summary = profile_summary_for(user)
  return {'ok': True, 'ranking': summary['ranking'], 'pending': progress['pending'],
          'message': ('Peer comparison refresh is in progress. Previous results stay visible.' if progress['pending'] else
                      'Profile summary refreshed. Peer comparisons refresh while members visit Profile.')}
