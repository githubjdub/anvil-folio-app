"""Transient FIFO buy/sell scenarios. Never saves trades or places orders."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from functools import lru_cache
import time

import anvil.server
from .portfolio_analytics import AnalysisInputError, _validate, _fetch_history, _history_for_session, _normalise_ticker, _EXCHANGES
from .income_tax_lots import build_tax_report
from .tax_rules import (YEAR, SUPPORTED_YEARS, STATES, STATE_PRESETS, TaxInputError, number, validate_settings, compare_tax)


@anvil.server.callable
def get_tax_estimator_catalog():
  return {'tax_year': YEAR, 'tax_years': list(SUPPORTED_YEARS), 'states': [('Federal only — exclude state tax', 'federal')] + STATES,
          'presets': STATE_PRESETS,
          'filing_statuses': [('Single', 'single'), ('Married filing jointly / surviving spouse', 'joint'),
                              ('Married filing separately', 'separate'), ('Head of household', 'head')]}


def _scenario_trades(rows, base_count, as_of, year=YEAR):
  if not isinstance(rows, list) or len(rows) > 100:
    raise TaxInputError('Use up to 100 hypothetical trades, or leave the scenario empty to estimate your logged sales.')
  trades = []
  for index, row in enumerate(rows, 1):
    if not isinstance(row, dict):
      raise TaxInputError('Check hypothetical trade %s.' % index)
    symbol = row.get('ticker')
    action = row.get('action')
    if not isinstance(symbol, str) or not 1 <= len(symbol.strip()) <= 40 or action not in ('Buy','Sell'):
      raise TaxInputError('Trade %s: enter a ticker and choose Buy or Sell.' % index)
    # Reuse the app's security normalization rather than accepting arbitrary symbols.
    parsed_symbol, exchange = _normalise_ticker(symbol)
    if not parsed_symbol or exchange not in _EXCHANGES:
      raise TaxInputError('Trade %s: use a USD stock or ETF on a supported US exchange.' % index)
    parsed_symbol = parsed_symbol.replace('.', '-')
    raw_date = row.get('trade_date')
    try:
      day = date.fromisoformat(raw_date) if isinstance(raw_date, str) else None
    except ValueError:
      day = None
    if day is None or day.year != year or day < as_of:
      raise TaxInputError('Trade %s: choose a date in %s on or after the portfolio snapshot date (%s).' % (index, year, as_of))
    shares = number(row.get('shares'), 'trade %s shares' % index, .00000001, 1e9)
    price = number(row.get('trade_price'), 'trade %s price per share' % index, .00000001, 1e9)
    fee = number(row.get('fee', 0), 'trade %s fees' % index, 0, 1e9)
    if action == 'Sell' and fee >= price * shares:
      raise TaxInputError('Trade %s: selling fees must be less than the proceeds.' % index)
    effective = price + (fee / shares if action == 'Buy' else -fee / shares)
    trades.append({'symbol': parsed_symbol, 'action': action, 'trade_date': day,
                   'shares': shares, 'trade_price': effective, 'row': base_count + index})
  if any(trades[i]['trade_date'] > trades[i+1]['trade_date'] for i in range(len(trades)-1)):
    raise TaxInputError('Put hypothetical trades in date order. On the same day, buys/sells follow row order.')
  return trades


@lru_cache(maxsize=64)
def _cached_history(symbol, start, bucket):
  # Public price histories are shared only within this worker, bounded in size
  # and refreshed every 15 minutes. No account data enters this cache.
  return _fetch_history(symbol, start, datetime.now(timezone.utc))


def _histories(clean, as_of):
  if not clean:
    return {}
  start = min(trade['trade_date'] for trade in clean)
  if (as_of - start).days > 3660:
    raise TaxInputError('The estimator supports up to 10 years of logged transaction history.')
  symbols = sorted({trade['symbol'] for trade in clean})
  start = date(start.year, 1, 1)
  bucket = int(time.time() // 900)
  with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as executor:
    fetched = list(executor.map(lambda symbol: _cached_history(symbol, start, bucket), symbols))
  return {symbol: _history_for_session(history, as_of) for symbol, history in zip(symbols, fetched)}


def _calculate(clean, histories, as_of, rows, settings):
  year = settings['tax_year']
  through_snapshot = min(as_of, date(year, 12, 31))
  if through_snapshot.year < year:
    through_snapshot = date(year, 1, 1)
  # Retain known future purchases for the 30-day wash-sale check. The FIFO
  # engine executes only dates through the chosen scenario endpoint.
  hypothetical = _scenario_trades(rows, len(clean), through_snapshot, year)
  # Newly modelled tickers need no market request: all scenario prices are explicit.
  combined_histories = dict(histories)
  for trade in hypothetical:
    combined_histories.setdefault(trade['symbol'], {'close': {}, 'splits': {}})
  through = hypothetical[-1]['trade_date'] if hypothetical else through_snapshot
  report = build_tax_report(clean + hypothetical, combined_histories, through)
  realized = [sale for sale in report['realized'] if date.fromisoformat(sale['sale_date']).year == year]
  base = [sale for sale in realized if sale['sale_row'] <= len(clean)]
  added = [sale for sale in realized if sale['sale_row'] > len(clean)]
  if any(sale['gain'] is None for sale in realized):
    raise TaxInputError('Some sold lots have unknown cost basis or proceeds. Enter the missing historical trade prices and sync again.')
  def total(items, term):
    return sum(item['gain'] for item in items if item['term'] == term)
  base_short, base_long = total(base, 'Short-term'), total(base, 'Long-term')
  short, long = total(added, 'Short-term'), total(added, 'Long-term')
  wash = any(s['possible_wash_sale'] for s in realized)
  tax = None if wash and not settings['wash_sale_reviewed'] else compare_tax(settings, base_short, base_long, short, long)
  sale_proceeds = sum(s['proceeds'] for s in added)
  purchase_cost = sum(t['shares'] * t['trade_price'] for t in hypothetical if t['action'] == 'Buy')
  return {'ok': True, 'tax': tax, 'realized': added, 'logged_realized': base, 'short_gain': short, 'long_gain': long,
          'base_short': base_short, 'base_long': base_long,
          'sale_proceeds': sale_proceeds, 'purchase_cost': purchase_cost,
          'cash_change': sale_proceeds - purchase_cost,
          'after_tax_cash': sale_proceeds - purchase_cost - tax['change']['total'] if tax else None,
          'wash_sale_flag': wash, 'requires_wash_review': wash and not settings['wash_sale_reviewed'],
          'estimated_prices': any(s['basis_estimated'] or s['proceeds_estimated'] for s in realized),
          'remaining_holdings': [{'symbol': symbol, 'shares': pos['shares']} for symbol, pos in report['positions'].items() if pos['shares'] > 0],
          'metadata': {'tax_year': year, 'as_of': through_snapshot.isoformat(), 'scenario_through': through.isoformat(),
                       'state': settings['state'], 'currency': 'USD'}}


@anvil.server.callable
def estimate_trade_taxes(transactions, as_of_date, scenario_trades, settings):
  """No saved portfolio IDs or client-provided tax metrics are trusted."""
  try:
    settings = validate_settings(settings)
    if type(as_of_date) is not date or as_of_date > date.today():
      raise TaxInputError('Choose a portfolio snapshot date on or before today.')
    clean = []
    if transactions:
      clean, _, _ = _validate(transactions, as_of_date, 0)
    elif not isinstance(transactions, list):
      raise TaxInputError('Choose a portfolio snapshot or start with an empty scenario.')
    _scenario_trades(scenario_trades, len(clean), min(as_of_date, date(settings['tax_year'], 12, 31)), settings['tax_year'])
    histories = _histories(clean, as_of_date)
    return _calculate(clean, histories, as_of_date, scenario_trades, settings)
  except (TaxInputError, AnalysisInputError) as error:
    return {'ok': False, 'message': str(error)}


@anvil.server.callable
def estimate_saved_trade_taxes(portfolio_id, scenario_trades, settings):
  """Use server-owned sync facts, with no quote requests or browser tax metrics."""
  from portfolios import _current_user, _owned_portfolio, _saved_analysis
  from planning_context import decode_planning_context
  try:
    portfolio = _owned_portfolio(portfolio_id, _current_user())
    if portfolio is None:
      raise TaxInputError('Open one of your saved portfolios.')
    analysis = _saved_analysis(portfolio)
    context = analysis.get('planning_context') if analysis else None
    if context is None:
      raise TaxInputError('Sync prices once to prepare tax lots from the latest saved portfolio. Opening this tab does not fetch market data.')
    raw = dict(settings) if isinstance(settings, dict) else settings
    if not isinstance(raw, dict):
      raise TaxInputError('Enter your tax assumptions.')
    year = raw.get('tax_year')
    if type(year) is not int or year not in SUPPORTED_YEARS:
      raise TaxInputError('Choose a supported US tax year: 2025 or 2026.')
    earned = max(0.0, context.get('earned_dividends_by_year', {}).get(str(year), 0.0))
    qualified = number(raw.get('portfolio_qualified', 0), 'qualified dividends from this portfolio', 0, earned)
    included = raw.get('include_portfolio_dividends') is True
    if included:
      # The user confirms payment timing; qualified status is an explicit input.
      raw['ordinary_income'] = number(raw.get('ordinary_income'), 'ordinary income after deductions') + earned - qualified
      raw['magi'] = number(raw.get('magi'), 'modified AGI', 0) + earned
      raw['other_investment_income'] = number(raw.get('other_investment_income'), 'other investment income', 0) + earned
      raw['qualified_dividends'] = number(raw.get('qualified_dividends', 0), 'other qualified dividends', 0) + qualified
    validated = validate_settings(raw)
    clean, histories, as_of = decode_planning_context(context)
    output = _calculate(clean, histories, as_of, scenario_trades, validated)
    output['portfolio_dividends'] = earned if included else 0.0
    return output
  except (TaxInputError, AnalysisInputError) as error:
    return {'ok': False, 'message': str(error)}
