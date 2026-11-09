"""Private saved planning drafts scoped to an owned portfolio and tax year."""
from datetime import datetime
import json

import anvil.server
from anvil.tables import app_tables, in_transaction
from .portfolios import _current_user, _owned_portfolio
from .tax_rules import SUPPORTED_YEARS

_RETIREMENT = {'current_age', 'retirement_age', 'end_age', 'starting_balance', 'monthly_savings',
  'monthly_spending', 'monthly_income', 'expected_return', 'volatility', 'inflation', 'annual_fee',
  'bequest_goal', 'balance_source'}
_TAX = {'filing_status', 'state', 'ordinary_income', 'magi', 'other_investment_income',
  'qualified_dividends', 'other_short', 'other_long', 'state_short_rate', 'state_long_rate',
  'portfolio_qualified', 'include_portfolio_dividends', 'wash_sale_reviewed', 'scenario_trades'}


def _scope(portfolio_id, tool, year):
  user = _current_user()
  portfolio = _owned_portfolio(portfolio_id, user)
  if portfolio is None:
    return None, 'Open one of your saved portfolios to save planning inputs.'
  if tool not in ('tax', 'retirement'):
    return None, 'Choose a planning tool.'
  if tool == 'tax' and (type(year) is not int or year not in SUPPORTED_YEARS):
    return None, 'Choose a supported tax year.'
  return (user, portfolio.get_id(), tool, year if tool == 'tax' else 0), None


@anvil.server.callable
def get_planning_inputs(portfolio_id, tool, year=None):
  scope, message = _scope(portfolio_id, tool, year)
  if scope is None:
    return {'ok': False, 'message': message}
  owner, identifier, kind, tax_year = scope
  row = app_tables.portfolio_plans.get(owner=owner, portfolio_id=identifier, tool=kind, year=tax_year)
  return {'ok': True, 'plan': row}


def _validate_draft(inputs, tool):
  if not isinstance(inputs, dict) or set(inputs) - (_TAX if tool == 'tax' else _RETIREMENT):
    return 'Check the planning inputs before saving.'
  # Incomplete fields can be saved as drafts. Numerical/tax validation occurs
  # when the user runs the projection, rather than losing unfinished work.
  for key, value in inputs.items():
    if key == 'scenario_trades':
      if not isinstance(value, list) or len(value) > 100:
        return 'A tax draft can contain up to 100 hypothetical trades.'
      for trade in value:
        if not isinstance(trade, dict) or set(trade) - {'ticker', 'action', 'trade_date', 'shares', 'trade_price', 'fee'}:
          return 'Check the hypothetical trades before saving.'
        if any(type(v) not in (str, int, float, bool, type(None)) or (isinstance(v, str) and len(v) > 128) for v in trade.values()):
          return 'A hypothetical trade contains an invalid value.'
    elif type(value) not in (str, int, float, bool) or (isinstance(value, str) and len(value) > 128):
      return 'A planning input contains an invalid value.'
  try:
    serialized = json.dumps(inputs, allow_nan=False)
  except (ValueError, TypeError):
    return 'Save finite numbers or text in planning inputs.'
  if len(serialized) > 80000:
    return 'The planning draft is too large.'
  return None


@anvil.server.callable
@in_transaction
def save_planning_inputs(portfolio_id, tool, inputs, year=None):
  scope, message = _scope(portfolio_id, tool, year)
  if scope is None:
    return {'ok': False, 'message': message}
  message = _validate_draft(inputs, tool)
  if message:
    return {'ok': False, 'message': message}
  owner, identifier, kind, tax_year = scope
  row = app_tables.portfolio_plans.get(owner=owner, portfolio_id=identifier, tool=kind, year=tax_year)
  updated_at = datetime.now()
  if row is None:
    row = app_tables.portfolio_plans.add_row(owner=owner, portfolio_id=identifier, tool=kind, year=tax_year, inputs=inputs, updated_at=updated_at)
  else:
    row.update(inputs=inputs, updated_at=updated_at)
  return {'ok': True, 'plan': row}
