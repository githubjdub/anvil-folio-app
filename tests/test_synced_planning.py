"""Saved-sync planning invariants without price requests or a browser."""
import ast
import copy
from datetime import date, timedelta
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
import sys

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'server_code'))
from tax_rules import validate_settings, compare_tax, liability, number, TaxInputError


def functions(path, namespace, selected=None):
    tree = ast.parse(path.read_text())
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom)) and
                 (selected is None or isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in selected)]
    exec(compile(tree, str(path), 'exec'), namespace)
    return namespace


class InputError(ValueError):
    pass


LOT = functions(APP / 'server_code/income_tax_lots.py', dict(date=date, timedelta=timedelta, math=math, AnalysisInputError=InputError))
CONTEXT = functions(APP / 'server_code/planning_context.py', dict(date=date, Any=object, build_tax_report=LOT['build_tax_report']))
ESTIMATE = functions(APP / 'server_code/tax_estimator.py', dict(date=date, YEAR=2026, TaxInputError=TaxInputError,
    number=number, _normalise_ticker=lambda ticker: (ticker, 'US'), _EXCHANGES={'US'},
    build_tax_report=LOT['build_tax_report'], compare_tax=compare_tax), {'_scenario_trades', '_calculate'})


def trade(day, action='Buy', shares=1, price=100, row=1):
    return dict(symbol='TEST', action=action, trade_date=date.fromisoformat(day), shares=shares, trade_price=price, row=row)


def context(transactions, marks, dates=None, splits=None, summaries=None):
    dates = dates or sorted(marks)
    through = dates[-1]
    rows = summaries or [dict(entry_date_used=item['trade_date'].isoformat(), entry_price=item['trade_price'] or marks[item['trade_date']]) for item in transactions]
    history = {'TEST': dict(close=marks, splits=splits or {})}
    securities = dict(series=[dict(date=day.isoformat()) for day in dates], securities=[dict(days=[dict(nav=200, market_value=200) for _ in dates])])
    return CONTEXT['build_synced_planning_context'](transactions, history, through, rows, securities)


def settings(year=2026):
    return validate_settings(dict(tax_year=year, filing_status='single', state='federal', ordinary_income=100000,
                                 magi=116100, other_investment_income=0, qualified_dividends=0, other_short=0, other_long=0))


class SyncedPlanningTests(unittest.TestCase):
    def test_manual_trade_price_does_not_replace_market_price(self):
        day = date(2026, 1, 5)
        txns = [trade('2026-01-05', price=None), trade('2026-01-05', price=20, row=2)]
        value = context(txns, {day: 100})
        self.assertEqual(value['tax']['positions']['TEST']['cost_basis'], 120)
        self.assertTrue(value['tax']['lots'][0]['basis_estimated'])
        self.assertEqual(value['histories']['TEST']['close']['2026-01-05'], 100)

    def test_weekend_execution_preserves_analyzer_fifo_order(self):
        txns = [trade('2026-01-05', price=200), trade('2026-01-03', price=100, row=2), trade('2026-01-05', 'Sell', price=150, row=3)]
        rows = [dict(entry_date_used='2026-01-05', entry_price=item['trade_price']) for item in txns]
        value = context(txns, {date(2026, 1, 5): 150}, summaries=rows)
        self.assertEqual(value['tax']['realized'][0]['gain'], 50)

    def test_prior_year_retains_following_january_wash_evidence(self):
        txns = [trade('2024-01-02'), trade('2025-12-22', 'Sell', price=50, row=2), trade('2026-01-02', price=50, row=3)]
        marks = {item['trade_date']: item['trade_price'] for item in txns}
        value = context(txns, marks)
        self.assertTrue(value['tax_by_year']['2025']['realized'][0]['possible_wash_sale'])
        clean, histories, as_of = CONTEXT['decode_planning_context'](value)
        result = ESTIMATE['_calculate'](clean, histories, as_of, [], settings(2025))
        self.assertEqual(result['base_long'], -50)
        self.assertTrue(result['requires_wash_review'])
        self.assertIsNone(result['tax'])

    def test_zero_scenario_rows_estimate_existing_sales_and_no_mutation(self):
        txns = [trade('2025-01-02'), trade('2026-01-05', 'Sell', price=200, row=2)]
        value = context(txns, {item['trade_date']: item['trade_price'] for item in txns})
        before = copy.deepcopy(value)
        clean, histories, as_of = CONTEXT['decode_planning_context'](value)
        result = ESTIMATE['_calculate'](clean, histories, as_of, [], settings())
        self.assertEqual(result['base_long'], 100)
        self.assertEqual(result['tax']['change']['total'], 0)
        self.assertGreater(result['tax']['after']['total'], 0)
        self.assertEqual(len(result['logged_realized']), 1)
        self.assertEqual(value, before)

    def test_splits_keep_basis_and_adjust_current_shares(self):
        txns = [trade('2025-01-02', shares=10, price=100)]
        value = context(txns, {date(2025, 1, 2): 100, date(2026, 1, 5): 60}, splits={date(2026, 1, 5): 2})
        position = value['tax']['positions']['TEST']
        self.assertEqual(position['shares'], 20)
        self.assertEqual(position['cost_basis'], 1000)
        self.assertEqual(position['current_value'], 1200)
        self.assertEqual(value['tax_by_year']['2025']['positions']['TEST']['shares'], 10)

    def test_dividends_are_cash_accrual_differences_not_sale_proceeds(self):
        txns = [trade('2025-12-31'), trade('2026-01-05', 'Sell', price=200, row=2)]
        securities = dict(series=[dict(date='2025-12-31'), dict(date='2026-01-05')],
                          securities=[dict(days=[dict(nav=110, market_value=100), dict(nav=15, market_value=0)])])
        value = CONTEXT['build_synced_planning_context'](txns,
            {'TEST': dict(close={date(2025,12,31):100, date(2026,1,5):200}, splits={})}, date(2026,1,5),
            [dict(entry_date_used=item['trade_date'].isoformat(), entry_price=item['trade_price']) for item in txns], securities)
        self.assertEqual(value['earned_dividends_by_year'], {'2025':10, '2026':5})

    def test_2025_and_2026_use_distinct_capital_brackets(self):
        a = dict(settings(2025), ordinary_income=45000)
        b = dict(settings(2026), ordinary_income=45000)
        self.assertAlmostEqual(compare_tax(a, 0, 0, 0, 10000)['change']['federal'], 997.5)
        self.assertAlmostEqual(compare_tax(b, 0, 0, 0, 10000)['change']['federal'], 832.5)


if __name__ == '__main__':
    unittest.main()
