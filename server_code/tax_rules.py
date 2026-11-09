"""2025–2026 US investment-tax planning rules, independent of quotes.

Federal brackets: IRS Rev. Proc. 2025-32, section 4.01 and 4.03.
https://www.irs.gov/pub/irs-drop/rp-25-32.pdf
Netting/loss deduction: https://www.irs.gov/taxtopics/tc409
NIIT: https://www.irs.gov/individuals/net-investment-income-tax
State inputs are marginal planning rates, not complete state tax returns.
"""
import math

YEAR = 2026
SUPPORTED_YEARS = (2025, 2026)
ORDINARY = {
  'single': [12400, 50400, 105700, 201775, 256225, 640600],
  'joint': [24800, 100800, 211400, 403550, 512450, 768700],
  'separate': [12400, 50400, 105700, 201775, 256225, 384350],
  'head': [17700, 67450, 105700, 201750, 256200, 640600],
}
CAPITAL = {'single': [49450, 545500], 'joint': [98900, 613700],
           'separate': [49450, 306850], 'head': [66200, 579600]}
# IRS Rev. Proc. 2024-40, sections 2.01 and 2.03. Income is supplied
# after deductions, so this module does not assume a standard deduction.
ORDINARY_BY_YEAR = {2026: ORDINARY, 2025: {
  'single': [11925, 48475, 103350, 197300, 250525, 626350],
  'joint': [23850, 96950, 206700, 394600, 501050, 751600],
  'separate': [11925, 48475, 103350, 197300, 250525, 375800],
  'head': [17000, 64850, 103350, 197300, 250500, 626350],
}}
CAPITAL_BY_YEAR = {2026: CAPITAL, 2025: {
  'single': [48350, 533400], 'joint': [96700, 600050],
  'separate': [48350, 300000], 'head': [64750, 566700],
}}
NIIT = {'single': 200000, 'joint': 250000, 'separate': 125000, 'head': 200000}
STATE_PRESETS = {
  'IL': (4.95, 'https://tax.illinois.gov/research/taxrates/income.html'),
  'PA': (3.07, 'https://www.pa.gov/agencies/revenue/resources/tax-types-and-information/personal-income-tax'),
  'CO': (4.4, 'https://tax.colorado.gov/individual-income-tax-FAQ'),
  'FL': (0.0, 'https://floridarevenue.com/faq/Pages/FAQDetails.aspx?FAQID=1466'),
}
STATES = [('Alabama','AL'),('Alaska','AK'),('Arizona','AZ'),('Arkansas','AR'),('California','CA'),
  ('Colorado','CO'),('Connecticut','CT'),('Delaware','DE'),('District of Columbia','DC'),('Florida','FL'),
  ('Georgia','GA'),('Hawaii','HI'),('Idaho','ID'),('Illinois','IL'),('Indiana','IN'),('Iowa','IA'),
  ('Kansas','KS'),('Kentucky','KY'),('Louisiana','LA'),('Maine','ME'),('Maryland','MD'),('Massachusetts','MA'),
  ('Michigan','MI'),('Minnesota','MN'),('Mississippi','MS'),('Missouri','MO'),('Montana','MT'),('Nebraska','NE'),
  ('Nevada','NV'),('New Hampshire','NH'),('New Jersey','NJ'),('New Mexico','NM'),('New York','NY'),
  ('North Carolina','NC'),('North Dakota','ND'),('Ohio','OH'),('Oklahoma','OK'),('Oregon','OR'),
  ('Pennsylvania','PA'),('Rhode Island','RI'),('South Carolina','SC'),('South Dakota','SD'),
  ('Tennessee','TN'),('Texas','TX'),('Utah','UT'),('Vermont','VT'),('Virginia','VA'),('Washington','WA'),
  ('West Virginia','WV'),('Wisconsin','WI'),('Wyoming','WY')]


class TaxInputError(ValueError):
  pass


def number(value, label, lower=-1e12, upper=1e12):
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise TaxInputError('Enter a number for ' + label + '.')
  if not math.isfinite(value) or not lower <= value <= upper:
    raise TaxInputError('Check ' + label + '; the amount is outside the supported range.')
  return float(value)


def validate_settings(settings):
  if not isinstance(settings, dict) or type(settings.get('tax_year')) is not int or settings.get('tax_year') not in SUPPORTED_YEARS:
    raise TaxInputError('Choose a supported US tax year: 2025 or 2026. Other years are not estimated using a different year’s rules.')
  status = settings.get('filing_status')
  state = settings.get('state')
  if status not in ORDINARY:
    raise TaxInputError('Choose a filing status.')
  if state not in {'federal', *(code for _, code in STATES)}:
    raise TaxInputError('Choose a US state or Federal only.')
  clean = {'tax_year': settings['tax_year'], 'filing_status': status, 'state': state,
           'wash_sale_reviewed': settings.get('wash_sale_reviewed') is True}
  clean['ordinary_income'] = number(settings.get('ordinary_income'), 'ordinary income after deductions')
  for key, label in [('magi', 'modified adjusted gross income'),
                     ('other_investment_income', 'other net investment income')]:
    clean[key] = number(settings.get(key), label, 0)
  clean['qualified_dividends'] = number(settings.get('qualified_dividends', 0), 'qualified dividends', 0)
  if clean['qualified_dividends'] > clean['other_investment_income']:
    raise TaxInputError('Include qualified dividends in other net investment income as well as the qualified-dividend field.')
  if state == 'WA':
    raise TaxInputError('Washington capital-gains excise tax is not modelled. Choose Federal only for a federal estimate, and calculate Washington tax separately.')
  for key, label in [('other_short', 'other short-term gains/losses'), ('other_long', 'other long-term gains/losses')]:
    clean[key] = number(settings.get(key, 0), label)
  for key, label in [('state_short_rate', 'the state/local short-term rate'),
                     ('state_long_rate', 'the state/local long-term rate')]:
    clean[key] = 0.0 if state == 'federal' else number(settings.get(key), label, 0, 25) / 100
  return clean


def _bracket_tax(income, limits, rates):
  total, previous = 0.0, 0.0
  for upper, rate in zip(limits + [math.inf], rates):
    total += max(0.0, min(income, upper) - previous) * rate
    previous = upper
  return total


def net_capital(short, long, status):
  if short > 0 > long:
    offset = min(short, -long)
    short, long = short - offset, long + offset
  elif long > 0 > short:
    offset = min(long, -short)
    short, long = short + offset, long - offset
  loss = max(0.0, -(short + long))
  deduction = min(loss, 1500 if status == 'separate' else 3000)
  return max(short, 0.0), max(long, 0.0), deduction, max(0.0, loss - deduction)


def liability(settings, short, long):
  status = settings['filing_status']
  ordinary_limits = ORDINARY_BY_YEAR[settings['tax_year']][status]
  capital_limits = CAPITAL_BY_YEAR[settings['tax_year']][status]
  st, lt, deduction, carry = net_capital(short, long, status)
  ordinary_before_floor = settings['ordinary_income'] + st - deduction
  ordinary = max(0.0, ordinary_before_floor)
  preferred = max(0.0, lt + settings['qualified_dividends'] + min(0.0, ordinary_before_floor))
  ordinary_tax = _bracket_tax(ordinary, ordinary_limits, [.1,.12,.22,.24,.32,.35,.37])
  capital_tax = (_bracket_tax(ordinary + preferred, capital_limits, [0,.15,.2])
                 - _bracket_tax(ordinary, capital_limits, [0,.15,.2]))
  # The Qualified Dividends/Capital Gain Tax Worksheet uses the smaller of
  # its preferential computation and regular tax on all taxable income.
  regular_tax = _bracket_tax(ordinary + preferred, ordinary_limits, [.1,.12,.22,.24,.32,.35,.37])
  capital_tax = min(capital_tax, max(0.0, regular_tax - ordinary_tax))
  magi = settings['magi'] + st + lt - deduction
  investment = max(0.0, settings['other_investment_income'] + st + lt - deduction)
  niit = .038 * min(investment, max(0.0, magi - NIIT[status]))
  # Marginal state planning model: no state ordinary-income loss deduction.
  state = st * settings['state_short_rate'] + lt * settings['state_long_rate']
  return {'ordinary': ordinary_tax, 'capital': capital_tax, 'federal': ordinary_tax + capital_tax,
          'niit': niit, 'state': state, 'total': ordinary_tax + capital_tax + niit + state,
          'capital_loss_deduction': deduction,
          'loss_carryforward': carry + max(0.0, deduction - max(0.0, settings['ordinary_income'] + settings['qualified_dividends'])),
          'net_short': st, 'net_long': lt}


def compare_tax(settings, base_short, base_long, added_short, added_long):
  before = liability(settings, base_short + settings['other_short'], base_long + settings['other_long'])
  after = liability(settings, base_short + settings['other_short'] + added_short,
                    base_long + settings['other_long'] + added_long)
  return {'before': before, 'after': after,
          'change': {key: after[key] - before[key] for key in ('ordinary','capital','federal','niit','state','total')}}
