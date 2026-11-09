"""Compact planning facts captured with a sync; never requests market data."""
from datetime import date
from typing import Any

from .income_tax_lots import build_tax_report


def build_synced_planning_context(transactions, histories, as_of, rows, securities):
  resolved: list[dict[str, Any]] = []
  compact = {}
  for trade in sorted(transactions, key=lambda item: (item['trade_date'], item['action'] != 'Buy')):
    summary = rows[trade['row'] - 1]
    effective = date.fromisoformat(summary['entry_date_used'])
    item = dict(trade, trade_date=effective)
    resolved.append(item)
    symbol = trade['symbol']
    history = compact.setdefault(symbol, {'close': {}, 'splits': {}})
    # A manually entered trade price is not the session's market close. Two
    # trades on one date may use different overrides, while missing prices
    # must still resolve to the independently observed market mark.
    history['close'][effective] = histories[symbol]['close'][effective]
  # Preserve the analyzer's original-date ordering when weekend trades map to
  # one session; stable effective-date sorting must not move Monday buys ahead.
  resolved.sort(key=lambda item: item['trade_date'])
  for index, item in enumerate(resolved, 1):
    item['row'] = index
  for symbol, history in compact.items():
    source = histories[symbol]
    close_days = [day for day in source['close'] if day <= as_of]
    if close_days:
      latest = max(close_days)
      history['close'][latest] = source['close'][latest]
      # Prior-year estimates use a year-end quote, never a newer mark.
      for year in {day.year for day in close_days}:
        year_days = [day for day in close_days if day.year == year]
        year_end = max(year_days)
        history['close'][year_end] = source['close'][year_end]
    history['splits'] = {day: ratio for day, ratio in source.get('splits', {}).items() if day <= as_of}
  report = build_tax_report(resolved, compact, as_of)
  dividends = {}
  dates = [point['date'] for point in securities['series']]
  for security in securities['securities']:
    previous = 0.0
    for day, point in zip(dates, security['days']):
      cumulative = point['nav'] - point['market_value']
      dividends[day[:4]] = dividends.get(day[:4], 0.0) + cumulative - previous
      previous = cumulative
  years = {}
  for year in (2025, 2026):
    through = min(as_of, date(year, 12, 31))
    if through.year == year:
      # Keep known following-January purchases as wash-sale evidence; the lot
      # engine only executes transactions through the selected year end.
      years[str(year)] = build_tax_report(resolved, compact, through)
  return {'version': 1, 'as_of': as_of.isoformat(), 'tax_by_year': years,
          'transactions': [dict(item, trade_date=item['trade_date'].isoformat()) for item in resolved],
          'histories': {symbol: {key: {day.isoformat(): amount for day, amount in values.items()}
                                 for key, values in history.items()} for symbol, history in compact.items()},
          'tax': report, 'earned_dividends_by_year': dividends,
          'dividend_note': 'Synced dividends accrue on ex-date. Payment date and qualified status require confirmation against broker records.'}


def decode_planning_context(context):
  """Only used with a trusted saved analysis, not unvalidated client facts."""
  clean = [dict(item, trade_date=date.fromisoformat(item['trade_date'])) for item in context['transactions']]
  histories = {symbol: {key: {date.fromisoformat(day): amount for day, amount in values.items()}
                        for key, values in history.items()} for symbol, history in context['histories'].items()}
  return clean, histories, date.fromisoformat(context['as_of'])
