"""Independent 2026 tax examples; run with python -m unittest discover -s tests."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server_code'))
from tax_rules import validate_settings, compare_tax, liability, TaxInputError


def assumptions(**changes):
  inputs = dict(tax_year=2026, filing_status='single', state='federal', ordinary_income=100000,
    magi=116100, other_investment_income=0, qualified_dividends=0, other_short=0, other_long=0)
  inputs.update(changes)
  return validate_settings(inputs)


class TaxRulesTests(unittest.TestCase):
  def test_short_term_gain_crosses_ordinary_bracket(self):
    # $5,700 at 22%, then $4,300 at 24%.
    result = compare_tax(assumptions(), 0, 0, 10000, 0)
    self.assertAlmostEqual(result['change']['federal'], 2286)

  def test_long_term_gain_uses_preferential_rate(self):
    result = compare_tax(assumptions(), 0, 0, 0, 10000)
    self.assertAlmostEqual(result['change']['federal'], 1500)

  def test_long_term_gain_crosses_zero_percent_band(self):
    result = compare_tax(assumptions(ordinary_income=45000), 0, 0, 0, 10000)
    self.assertAlmostEqual(result['change']['federal'], 832.5)

  def test_preferential_computation_does_not_exceed_regular_tax(self):
    result = compare_tax(assumptions(ordinary_income=50000), 0, 0, 0, 100)
    self.assertAlmostEqual(result['change']['federal'], 12)

  def test_joint_gain_crosses_fifteen_percent_band(self):
    result = compare_tax(assumptions(filing_status='joint', ordinary_income=610000, magi=610000), 0, 0, 0, 10000)
    self.assertAlmostEqual(result['change']['federal'], 1815)
    self.assertAlmostEqual(result['change']['niit'], 380)

  def test_qualified_dividends_use_up_zero_rate_band(self):
    result = compare_tax(assumptions(ordinary_income=45000, qualified_dividends=4450,
      other_investment_income=4450), 0, 0, 0, 10000)
    self.assertAlmostEqual(result['change']['federal'], 1500)

  def test_unused_deductions_cover_a_short_term_gain(self):
    result = compare_tax(assumptions(ordinary_income=-10000, magi=0), 0, 0, 10000, 0)
    self.assertAlmostEqual(result['change']['federal'], 0)

  def test_unused_deductions_cover_a_long_term_gain(self):
    result = compare_tax(assumptions(ordinary_income=-10000, magi=0), 0, 0, 0, 65000)
    self.assertAlmostEqual(result['change']['federal'], 832.5)

  def test_opposite_term_losses_net_before_tax(self):
    result = compare_tax(assumptions(), 0, 10000, -4000, 0)
    self.assertAlmostEqual(result['change']['federal'], -600)

  def test_capital_loss_limit_and_carryforward(self):
    result = liability(assumptions(), -10000, 0)
    self.assertAlmostEqual(result['capital_loss_deduction'], 3000)
    self.assertAlmostEqual(result['loss_carryforward'], 7000)
    separate = liability(assumptions(filing_status='separate'), -10000, 0)
    self.assertAlmostEqual(separate['capital_loss_deduction'], 1500)
    self.assertAlmostEqual(separate['loss_carryforward'], 8500)

  def test_unused_loss_deduction_remains_in_carryforward(self):
    result = liability(assumptions(ordinary_income=0, magi=0), -10000, 0)
    self.assertAlmostEqual(result['loss_carryforward'], 10000)

  def test_niit_threshold_uses_lesser_of_income_and_excess(self):
    result = compare_tax(assumptions(magi=195000), 0, 0, 0, 10000)
    self.assertAlmostEqual(result['change']['niit'], 190)

  def test_niit_loss_deduction_is_limited(self):
    result = liability(assumptions(magi=300000, other_investment_income=20000), -10000, 0)
    self.assertAlmostEqual(result['niit'], 646)

  def test_state_planning_rates_apply_to_net_gains(self):
    result = compare_tax(assumptions(state='IL', state_short_rate=4.95, state_long_rate=4.95), 0, 0, 0, 10000)
    self.assertAlmostEqual(result['change']['state'], 495)

  def test_missing_custom_state_rates_are_not_treated_as_zero(self):
    with self.assertRaises(TaxInputError):
      assumptions(state='CA')

  def test_unsupported_year_and_washington_are_explicit(self):
    for changes in ({'tax_year':2027}, {'state':'WA'}):
      with self.assertRaises(TaxInputError):
        assumptions(**changes)

  def test_nonfinite_and_boolean_inputs_are_rejected(self):
    for value in (True, float('nan'), float('inf')):
      with self.assertRaises(TaxInputError):
        assumptions(ordinary_income=value)


if __name__ == '__main__':
  unittest.main()
