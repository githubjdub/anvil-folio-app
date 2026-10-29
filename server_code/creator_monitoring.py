"""Creator-only cross-account usage and a deduplicated holdings reference view."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import math
from typing import Any

import anvil.server
from anvil.tables import app_tables, query as q, RowDeleted
from . import creator_portfolios, site_analytics, portfolio_analytics

_creator: Any = creator_portfolios
_traffic: Any = site_analytics
_prices: Any = portfolio_analytics


def _duration(value):
  return (float(value) if isinstance(value, (int, float)) and not isinstance(value, bool)
          and math.isfinite(value) and value > 0 else 0)


@anvil.server.callable(require_user=True)
def get_creator_monitoring(days=30, include_own=False):
  creator = _creator._require_creator()
  if isinstance(days, bool) or days not in (7, 30, 90) or not isinstance(include_own, bool):
    return {"ok": False, "message": "Choose a supported monitoring range."}
  now = datetime.now(timezone.utc)
  lower = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
  tabs, accounts, guests = {}, {}, 0
  visits = 0
  for visit in app_tables.site_visits.search(started_at=q.between(lower, now + timedelta(microseconds=1))):
    try:
      owner = visit['owner']
      if not include_own and (visit['is_creator'] is True or owner == creator):
        continue
      owner_id = owner.get_id() if owner is not None else None
      if owner is not None:
        email = owner['email'] or 'Account without email'
      counts = _traffic._page_counts(visit)
      raw = visit['tab_activity'] or {}
      activity = {key: _duration(raw.get(key)) for key in _traffic._PAGES}
      last_seen = _traffic._utc(visit['last_seen_at'])
      visits += 1
      if owner_id is None:
        guests += 1
      else:
        account = accounts.setdefault(owner_id, {
          'account_id': owner_id, 'email': email, 'last_seen': last_seen,
          'visits': 0, 'seconds': 0, '_tabs': {},
        })
        account['visits'] += 1
        account['seconds'] += sum(activity.values())
        if last_seen is not None and (account['last_seen'] is None or last_seen > account['last_seen']):
          account['last_seen'] = last_seen
      for key, label in _traffic._PAGES.items():
        seconds, views = activity[key], counts.get(key, 0)
        if not seconds and not views:
          continue
        entry = tabs.setdefault(key, {'key': key, 'label': label, 'seconds': 0,
                                     'views': 0, 'visits': 0, '_accounts': set()})
        entry['seconds'] += seconds
        entry['views'] += views
        entry['visits'] += 1
        if owner_id is not None:
          entry['_accounts'].add(owner_id)
          account['_tabs'][key] = account['_tabs'].get(key, 0) + seconds
    except RowDeleted:
      continue
  total_seconds = sum(item['seconds'] for item in tabs.values())
  items = []
  for entry in tabs.values():
    entry['account_count'] = len(entry.pop('_accounts'))
    entry['share'] = entry['seconds'] / total_seconds * 100 if total_seconds else 0
    entry['average_seconds'] = entry['seconds'] / entry['visits'] if entry['visits'] else 0
    items.append(entry)
  items.sort(key=lambda item: (-item['seconds'], -item['views'], item['label']))
  recent = []
  for account in accounts.values():
    ranked = account.pop('_tabs')
    top = max(ranked, key=ranked.get) if ranked and max(ranked.values()) > 0 else None
    account['top_tab'] = _traffic._PAGES[top] if top else 'No timed activity yet'
    recent.append(account)
  recent.sort(key=lambda item: -(item['last_seen'].timestamp() if item['last_seen'] else 0))
  return {'ok': True, 'tabs': items, 'accounts': recent[:20], 'account_count': len(accounts),
          'active_today': sum(item['last_seen'] is not None and item['last_seen'].date() == now.date()
                              for item in recent),
          'visits': visits, 'guest_visits': guests, 'seconds': total_seconds,
          'updated_at': now, 'days': days}


def _latest_portfolios(creator, include_own):
  """Use one saved scenario per person so duplicate scenarios cannot inflate value."""
  latest = {}
  for portfolio in app_tables.portfolios.search():
    try:
      owner = portfolio['owner']
      if owner is None or (not include_own and owner == creator):
        continue
      owner['email']
      key = owner.get_id()
      stamp = portfolio['updated_at']
      rank = (stamp.timestamp() if stamp else 0, portfolio.get_id())
      if key not in latest or rank > latest[key][0]:
        latest[key] = (rank, portfolio)
    except RowDeleted:
      continue
  return [(key, value[1]) for key, value in latest.items()]


def _saved_positions(owner_id, portfolio):
  """Validate the full saved document, never silently skip a malformed sell."""
  trades, cutoff, rate = _prices._validate(portfolio['transactions'], portfolio['as_of_date'],
                                          portfolio['risk_free_rate'])
  return {'owner_id': owner_id, 'trades': trades, 'cutoff': cutoff}


def _open_lots(trades, history, cutoff, value_day):
  """Replay FIFO with split adjustments; keep missing entered basis unknown."""
  lots = []
  events = [(trade['trade_date'], 1, index, trade) for index, trade in enumerate(trades)]
  events += [(day, 0, 0, ratio) for day, ratio in history['splits'].items() if day <= value_day]
  for day, kind, index, event in sorted(events, key=lambda item: item[:3]):
    if kind == 0:
      for lot in lots:
        lot['shares'] *= event
        if lot['price'] is not None:
          lot['price'] /= event
      continue
    if day > cutoff:
      continue
    shares = event['shares']
    if event['action'] == 'Buy':
      lots.append({'shares': shares, 'price': event['trade_price']})
    else:
      if shares > sum(lot['shares'] for lot in lots) + max(1, shares) * 1e-10:
        raise ValueError('Sell exceeds recorded shares.')
      for lot in lots:
        sold = min(shares, lot['shares'])
        lot['shares'] -= sold
        shares -= sold
        if shares <= 1e-10:
          break
  return [lot for lot in lots if lot['shares'] > 1e-10]


def _quote(symbol, start, now):
  try:
    history = _prices._fetch_history(symbol, start, now)
  except _prices.AnalysisInputError:
    return symbol, None
  return symbol, history


def _holdings_report(documents, histories, skipped, now):
  symbols, invalid_positions = {}, 0
  covered_accounts = set()
  for document in documents:
    grouped = {}
    for trade in document['trades']:
      grouped.setdefault(trade['symbol'], []).append(trade)
    for symbol, trades in grouped.items():
      history = histories.get(symbol)
      if history is None:
        continue
      quote = history.get('latest_quote')
      if quote is not None:
        price, value_day = quote['price'], quote['date']
      elif history['close']:
        value_day = max(history['close'])
        price = history['close'][value_day]
      else:
        continue
      # Transactions after the newest available mark cannot be fairly valued.
      if any(trade['trade_date'] > value_day for trade in trades):
        invalid_positions += 1
        continue
      try:
        lots = _open_lots(trades, history, document['cutoff'], value_day)
      except ValueError:
        invalid_positions += 1
        continue
      shares = sum(lot['shares'] for lot in lots)
      if shares <= 1e-10:
        continue
      known = [lot for lot in lots if lot['price'] is not None]
      known_shares = sum(lot['shares'] for lot in known)
      basis = sum(lot['shares'] * lot['price'] for lot in known)
      entry = symbols.setdefault(symbol, {'ticker': symbol, 'company': history.get('company_name') or symbol,
            'price': price, 'price_date': value_day, 'shares': 0, 'market_value': 0,
            'entered_basis': 0, 'basis_shares': 0, '_owners': set()})
      entry['shares'] += shares
      entry['market_value'] += shares * price
      entry['entered_basis'] += basis
      entry['basis_shares'] += known_shares
      entry['_owners'].add(document['owner_id'])
      covered_accounts.add(document['owner_id'])
  items = []
  for entry in symbols.values():
    entry['account_count'] = len(entry.pop('_owners'))
    entry['average_cost'] = entry['entered_basis'] / entry['basis_shares'] if entry['basis_shares'] else None
    entry['basis_coverage'] = entry['basis_shares'] / entry['shares'] * 100
    items.append(entry)
  items.sort(key=lambda item: (-item['account_count'], -item['market_value'], item['ticker']))
  return {'ok': True, 'items': items, 'accounts': len(documents), 'covered_accounts': len(covered_accounts),
          'market_value': sum(item['market_value'] for item in items), 'updated_at': now,
          'priced_symbols': sum(value is not None for value in histories.values()),
          'requested_symbols': len(histories), 'skipped_portfolios': skipped,
          'skipped_positions': invalid_positions, 'holdings_cutoff': min((d['cutoff'] for d in documents), default=None)}


@anvil.server.callable(require_user=True)
def get_creator_holdings(include_own=False):
  creator = _creator._require_creator()
  if not isinstance(include_own, bool):
    return {'ok': False, 'message': 'Choose whether to include your creator account.'}
  documents, starts, skipped = [], {}, 0
  for owner_id, portfolio in _latest_portfolios(creator, include_own):
    try:
      document = _saved_positions(owner_id, portfolio)
    except (ValueError, RowDeleted):
      skipped += 1
      continue
    documents.append(document)
    for trade in document['trades']:
      symbol = trade['symbol']
      starts[symbol] = min(starts.get(symbol, trade['trade_date']), trade['trade_date'])
  # Cap work explicitly instead of publishing a silently truncated ranking.
  if len(starts) > 100:
    return {'ok': False, 'message': 'This library contains over 100 securities. Ownership pricing needs a background refresh before this larger library can be ranked.'}
  now = datetime.now(timezone.utc)
  with ThreadPoolExecutor(max_workers=6) as pool:
    histories = dict(pool.map(lambda item: _quote(item[0], item[1], now), starts.items()))
  return _holdings_report(documents, histories, skipped, now)
