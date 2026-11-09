"""Planner form state/lazy loading behavior with tiny Anvil-boundary fakes."""
import ast
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
import unittest

APP = Path(__file__).resolve().parents[1]


class Names(HTMLParser):
    def __init__(self, path):
        super().__init__()
        self.names = set()
        self.feed(path.read_text())

    def handle_starttag(self, tag, attributes):
        for key, value in attributes:
            if key in ('name', 'anvil:name'):
                self.names.add(value)


class Control:
    def __init__(self):
        self.text = ''
        self.visible = True
        self.enabled = True
        self.checked = False
        self.selected_value = None
        self.items = []
        self.data = []
        self.classes = {}

    def set_event_handler(self, *args):
        pass

    def get_components(self):
        return []

    def raise_event(self, *args, **kwargs):
        pass


class Harness:
    def __init__(self, name):
        self.user = 'a'
        self.calls = []
        self.plans = {}
        self.responses = {}
        self.hook = None
        harness = self
        path = APP / 'client_code/Components' / name
        names = Names(path / 'form_template.html').names

        class Template:
            def __init__(self, **properties):
                for component in names:
                    setattr(self, component, Control())

            def set_event_handler(self, *args):
                pass

            def raise_event(self, *args, **kwargs):
                pass

        def call(method, *args):
            harness.calls.append((method, args))
            if harness.hook:
                hook, harness.hook = harness.hook, None
                hook()
            if method in harness.responses:
                return harness.responses[method]
            if method == 'get_tax_estimator_catalog':
                return dict(tax_years=[2025,2026], filing_statuses=[('Single','single')], states=[('Federal','federal')], presets={})
            if method == 'get_planning_inputs':
                key = (harness.user, *args)
                return {'ok': True, 'plan': {'inputs': harness.plans[key]} if key in harness.plans else None}
            if method == 'save_planning_inputs':
                portfolio, tool, document, *year = args
                key = (harness.user, portfolio, tool, *year)
                harness.plans[key] = document
                return {'ok':True, 'plan':{'inputs':document}}
            raise AssertionError(method)

        namespace = dict(date=date, handle=lambda *names: (lambda fn: fn),
            TaxTradeRow=type('TaxTradeRow', (), {}), chart_theme=SimpleNamespace(MODE='light'),
            anvil=SimpleNamespace(server=SimpleNamespace(call=call), users=SimpleNamespace(get_user=lambda: SimpleNamespace(get_id=lambda: harness.user))))
        namespace[name + 'Template'] = Template
        tree = ast.parse((path / '__init__.py').read_text())
        tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
        exec(compile(tree, str(path), 'exec'), namespace)
        self.row_class = namespace['TaxTradeRow']
        self.form = namespace[name]()


def analysis(year=2026, value=1000):
    position = dict(shares=10, current_price=value/10)
    report = dict(positions={'TEST':position}, lots=[{}], summary=dict(realized_short_term=0, realized_long_term=100))
    return {'planning_context': dict(tax_by_year={str(year):report}, earned_dividends_by_year={str(year):10})}


class PlanningFormTests(unittest.TestCase):
    def test_tax_setter_uses_latest_sync_without_server_calls_and_zero_rows_enabled(self):
        harness = Harness('TaxEstimator')
        form = harness.form
        form.set_portfolio_context([{'ticker':'TEST'}], '2026-06-30', 'p1', analysis())
        self.assertEqual(harness.calls, [])
        form.redraw()
        self.assertTrue(form.estimate_button.enabled)
        self.assertEqual(form._tax_year, 2026)
        self.assertIn('long-term $100.00', form.synced_summary_label.text)
        count = len(harness.calls)
        form.redraw()
        self.assertEqual(len(harness.calls), count)

    def test_tax_saved_assumptions_restore_then_new_sync_keeps_edits(self):
        harness = Harness('TaxEstimator')
        form = harness.form
        harness.plans[('a','p1','tax',2026)] = {'ordinary_income':'70000', 'magi':'90000'}
        form.set_portfolio_context([{'ticker':'TEST'}], '2026-06-30', 'p1', analysis())
        form.redraw()
        self.assertEqual(form.ordinary_box.text, '70000')
        form.ordinary_box.text = '80000'
        form.inputs_change()
        form.set_portfolio_context([{'ticker':'TEST'}], '2026-07-01', 'p1', analysis(value=2000))
        form.redraw()
        self.assertEqual(form.ordinary_box.text, '80000')
        self.assertEqual(form._positions['TEST']['current_price'], 200)
        form.save_inputs_click()
        self.assertEqual(harness.plans[('a','p1','tax',2026)]['ordinary_income'], '80000')

    def test_tax_context_year_and_year_drafts_are_separate(self):
        harness = Harness('TaxEstimator')
        form = harness.form
        form.set_portfolio_context([{'ticker':'TEST'}], '2025-12-31', 'p1', analysis(2025))
        form.redraw()
        self.assertEqual(form.year_dropdown.selected_value, 2025)
        form.ordinary_box.text = '40000'
        form.inputs_change()
        form.year_dropdown.selected_value = 2026
        form.year_change()
        self.assertEqual(form.ordinary_box.text, '')
        form.year_dropdown.selected_value = 2025
        form.year_change()
        self.assertEqual(form.ordinary_box.text, '40000')
        form.reset_workspace()
        self.assertEqual(form.year_dropdown.selected_value, 2026)

    def test_tax_unsupported_context_year_is_explicit_not_different_rules(self):
        harness = Harness('TaxEstimator')
        form = harness.form
        form.set_portfolio_context([{'ticker':'TEST'}], '2024-12-31', 'p1', analysis(2024))
        form.redraw()
        self.assertEqual(form.year_dropdown.selected_value, 2024)
        self.assertFalse(form.estimate_button.enabled)
        self.assertIn(('2024 · unavailable',2024), form.year_dropdown.items)

    def test_tax_sync_rollover_preserves_an_existing_year_draft(self):
        harness = Harness('TaxEstimator')
        form = harness.form
        form.set_portfolio_context([{'ticker':'TEST'}], '2025-12-31', 'p1', analysis(2025))
        form.redraw()
        form.year_dropdown.selected_value = 2026
        form.year_change()
        form.ordinary_box.text = '90000'
        form.inputs_change()
        form.year_dropdown.selected_value = 2025
        form.year_change()
        harness.plans[('a','p1','tax',2026)] = {'ordinary_income':'70000'}
        form.set_portfolio_context([{'ticker':'TEST'}], '2026-06-30', 'p1', analysis())
        form.redraw()
        self.assertEqual(form.ordinary_box.text, '90000')
        self.assertTrue(form._inputs_dirty)

    def test_tax_incomplete_trade_draft_survives_year_roundtrip(self):
        harness = Harness('TaxEstimator')
        form = harness.form
        form.set_portfolio_context([{'ticker':'TEST'}], '2026-06-30', 'p1', analysis())
        form.redraw()
        row = harness.row_class()
        row.item = {}
        row.date_picker = SimpleNamespace(date=None)
        row.shares_box = SimpleNamespace(text='')
        row.price_box = SimpleNamespace(text='unfinished')
        row.fee_box = SimpleNamespace(text='0')
        row.raise_event = lambda *args, **kwargs: None
        row.payload = lambda: {'ticker':'TEST', 'action':'Sell', 'trade_date':None,
            'shares':'', 'trade_price':'unfinished', 'fee':0}
        form.trades_panel.get_components = lambda: [row]
        form.year_dropdown.selected_value = 2025
        form.year_change()
        form.trades_panel.get_components = lambda: []
        form.year_dropdown.selected_value = 2026
        form.year_change()
        self.assertEqual(form._rows[0]['shares'], '')
        self.assertEqual(form._rows[0]['trade_price'], 'unfinished')
        self.assertIsNone(form._rows[0]['date'])
        self.assertTrue(form._inputs_dirty)

    def test_retirement_auto_balance_and_saved_other_inputs(self):
        harness = Harness('Retirement')
        form = harness.form
        harness.plans[('a','p1','retirement')] = {'current_age':'41','monthly_spending':'3000','balance_source':'portfolio','starting_balance':'9'}
        form.set_portfolio_context(1200, '2026-06-30', 'p1')
        self.assertEqual(harness.calls, [])
        self.assertEqual(form.starting_balance_box.text, '1200.00')
        form.redraw()
        self.assertEqual(form.current_age_box.text, '41')
        self.assertEqual(form.starting_balance_box.text, '1200.00')
        count = len(harness.calls)
        form.set_portfolio_context(2000, '2026-07-01', 'p1')
        form.redraw()
        self.assertEqual(form.starting_balance_box.text, '2000.00')
        self.assertEqual(form.monthly_spending_box.text, '3000')
        self.assertEqual(len(harness.calls), count)

    def test_retirement_manual_override_persists_until_use_synced_value(self):
        harness = Harness('Retirement')
        form = harness.form
        form.set_portfolio_context(1200, '2026-06-30', 'p1')
        form.redraw()
        form.starting_balance_box.text = '5000'
        form.inputs_change(sender=form.starting_balance_box)
        form.save_inputs_click()
        self.assertEqual(harness.plans[('a','p1','retirement')]['balance_source'], 'manual')
        form.reset_workspace()
        form.set_portfolio_context(2000, '2026-07-01', 'p1')
        form.redraw()
        self.assertEqual(form.starting_balance_box.text, '5000')
        form.set_portfolio_context(3000, '2026-07-02', 'p1')
        self.assertEqual(form.starting_balance_box.text, '5000')
        form.use_portfolio_click()
        self.assertEqual(form.starting_balance_box.text, '3000.00')
        self.assertEqual(form._balance_source, 'portfolio')

    def test_retirement_late_account_response_cannot_restore_inputs(self):
        harness = Harness('Retirement')
        form = harness.form
        harness.plans[('b','p1','retirement')] = {'current_age':'99'}
        form.set_portfolio_context(1200, '2026-06-30', 'p1')
        harness.hook = lambda: setattr(harness, 'user', 'b')
        form.redraw()
        self.assertEqual(form.current_age_box.text, '35')
        self.assertIsNone(form._loaded_scope)

    def test_planner_context_change_clears_other_portfolio_or_owner_inputs(self):
        for name in ('TaxEstimator', 'Retirement'):
            for change_owner in (True, False):
                with self.subTest(name=name, change_owner=change_owner):
                    harness = Harness(name)
                    form = harness.form
                    def context(portfolio):
                        if name == 'TaxEstimator':
                            form.set_portfolio_context([{'ticker':'TEST'}], '2026-06-30', portfolio, analysis())
                        else:
                            form.set_portfolio_context(1200, '2026-06-30', portfolio)
                    context('p1')
                    form.redraw()
                    field = form.ordinary_box if name == 'TaxEstimator' else form.current_age_box
                    field.text = '999'
                    form.inputs_change()
                    if change_owner:
                        harness.user = 'b'
                    context('p1' if change_owner else 'p2')
                    self.assertNotEqual(field.text, '999')
                    self.assertFalse(form._inputs_dirty)

    def test_guest_draft_inputs_survive_first_explicit_save_and_signin(self):
        for name in ('TaxEstimator', 'Retirement'):
            with self.subTest(name=name):
                harness = Harness(name)
                harness.user = None
                form = harness.form
                form.reset_workspace()
                def context(portfolio):
                    if name == 'TaxEstimator':
                        form.set_portfolio_context([{'ticker':'TEST'}], '2026-06-30', portfolio, analysis())
                    else:
                        form.set_portfolio_context(1200, '2026-06-30', portfolio)
                context(None)
                form.redraw()
                field = form.ordinary_box if name == 'TaxEstimator' else form.current_age_box
                field.text = '42'
                form.inputs_change()
                harness.user = 'a'
                context('p1')
                form.redraw()
                self.assertEqual(field.text, '42')
                self.assertTrue(form._inputs_dirty)

    def test_retirement_pending_projection_ignores_new_synced_balance_or_account(self):
        for account_change in (True, False):
            with self.subTest(account_change=account_change):
                harness = Harness('Retirement')
                form = harness.form
                form.set_portfolio_context(1200, '2026-06-30', 'p1')
                harness.responses['run_retirement_projection'] = {'ok':False, 'message':'stale projection'}
                harness.hook = (lambda: setattr(harness, 'user', 'b')) if account_change else (
                    lambda: form.set_portfolio_context(2400, '2026-07-01', 'p1'))
                form.run_click()
                self.assertIsNone(form._result)
                self.assertNotEqual(form.status_label.text, 'stale projection')
                self.assertFalse(form._busy)
                self.assertTrue(form.run_button.enabled)

    def test_repeated_identical_retirement_context_does_not_invalidate_projection(self):
        harness = Harness('Retirement')
        form = harness.form
        form.set_portfolio_context(1200, '2026-06-30', 'p1')
        form._result = sentinel = {'cached':True}
        generation = form._generation
        form.set_portfolio_context(1200, '2026-06-30', 'p1')
        self.assertIs(form._result, sentinel)
        self.assertEqual(form._generation, generation)

    def test_unexpected_save_error_propagates_and_restores_controls(self):
        for name in ('TaxEstimator', 'Retirement'):
            with self.subTest(name=name):
                harness = Harness(name)
                form = harness.form
                if name == 'TaxEstimator':
                    form.set_portfolio_context([{'ticker':'TEST'}], '2026-06-30', 'p1', analysis())
                else:
                    form.set_portfolio_context(1200, '2026-06-30', 'p1')
                form.redraw()
                def fail():
                    raise RuntimeError('unexpected save failure')
                harness.hook = fail
                with self.assertRaisesRegex(RuntimeError, 'unexpected save failure'):
                    form.save_inputs_click()
                self.assertFalse(form._busy)
                self.assertTrue(form.save_inputs_button.enabled)


if __name__ == '__main__':
    unittest.main()
