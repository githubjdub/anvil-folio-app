"""Private investor profiles and small, hourly peer-ranking snapshots.

Only server-computed saved analyses supply metrics. Membership verification is
a paid badge, not identity verification or an audit of user-entered portfolios.
No public profile endpoint exposes account names, holdings or balances.
"""
from datetime import date, datetime, timedelta, timezone
import math
from typing import Any

import anvil
import anvil.server
from anvil.tables import app_tables, in_transaction


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
    owner=user, ranking_opt_in=False, updated_at=datetime.now(timezone.utc))


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
  # The hourly job can roll to a new quarter without reading 3,000 histories.
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
                       'Your ranking will appear after the next hourly update, once at least 4 eligible members participate.')
    snapshot = app_tables.profile_rankings.get(quarter=previous_quarter()[2])
    if snapshot is not None and verification['verified'] and metrics['return'] is not None:
      now = datetime.now(timezone.utc).timestamp()
      peers = [p for p in snapshot['participants'] or [] if p['valid_until'] is None or p['valid_until'] > now]
      mine = next((p for p in peers if p['user_id'] == user.get_id()), None)
      ranking.update(count=len(peers), updated_at=snapshot['generated_at'])
      if len(peers) >= 4 and mine and mine['return'] == metrics['return']:
        ranking.update(_percentile(mine['return'], [p['return'] for p in peers]))
        ranking['note'] = 'Among opted-in Verified members with full-quarter data. Q1 is the top quartile. Updated hourly; ties share a rank.'
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
  # Withdrawing consent removes this account from the current snapshot immediately.
  if not ranking_opt_in:
    snapshot = app_tables.profile_rankings.get(quarter=previous_quarter()[2])
    if snapshot is not None:
      snapshot['participants'] = [p for p in snapshot['participants'] or [] if p['user_id'] != user.get_id()]
  return {'ok': True, 'message': 'Profile preferences saved. Rankings update hourly.'}


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
  """Read small materialized summaries once an hour, never portfolio histories."""
  from memberships import membership_for
  quarter = previous_quarter()[2]
  participants = []
  for profile in app_tables.investor_profiles.search(ranking_opt_in=True):
    user = profile['owner']
    metrics = profile['metrics'] or {}
    value = (metrics.get('return') if metrics.get('quarter') == quarter else
             metrics.get('quarter_returns', {}).get(quarter))
    if user is None or value is None:
      continue
    verification = verification_for(user)
    if not verification['verified']:
      continue
    membership = membership_for(user)
    expiry = membership['expires_at'] if membership['is_premium'] else verification['period_end']
    participants.append({'user_id': user.get_id(), 'return': value,
                         'valid_until': expiry.timestamp() if expiry else None})
  _write_rankings(quarter, participants)


@in_transaction
def _write_rankings(quarter, participants):
  # Recheck consent inside the final transaction, including a concurrent opt-out.
  allowed = {p['owner'].get_id() for p in app_tables.investor_profiles.search(ranking_opt_in=True) if p['owner']}
  participants = [p for p in participants if p['user_id'] in allowed]
  snapshot = app_tables.profile_rankings.get(quarter=quarter)
  values: dict[str, Any] = {'participants': participants, 'generated_at': datetime.now(timezone.utc)}
  if snapshot is None:
    app_tables.profile_rankings.add_row(quarter=quarter, **values)
  else:
    snapshot.update(**values)
