"""Portfolio interaction boundaries without a browser or live market requests."""
import ast
import copy
from datetime import date
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


APP = Path(__file__).resolve().parents[1]


class Names(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.names = set()
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        for key, value in attributes:
            if key in ('name', 'anvil:name'):
                self.names.add(value)


class Control:
    def __init__(self):
        self.visible = True
        self.enabled = True
        self.classes = {}
        self.attrs = {}
        self.text = ''
        self.items = []
        self.selected_value = None
        self.date = None
        self.redraw_count = 0
        self.reset_count = 0
        self.loaded_count = 0
        self.view = None

    def redraw(self):
        self.redraw_count += 1

    def reset_workspace(self):
        self.reset_count += 1

    def reset_context(self):
        self.reset_count += 1

    def ensure_loaded(self):
        self.loaded_count += 1

    def set_active(self, active):
        self.active = active

    def set_view(self, view):
        self.view = view


class User:
    def __init__(self, account_id):
        self.account_id = account_id

    def get_id(self):
        return self.account_id


class Portfolio(dict):
    def __init__(self, portfolio_id, name='Long term'):
        super().__init__(name=name)
        self.portfolio_id = portfolio_id

    def get_id(self):
        return self.portfolio_id


def fingerprint_function():
    path = APP / 'server_code' / 'portfolios.py'
    tree = ast.parse(path.read_text())
    tree.body = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == '_analysis_fingerprint']
    namespace = {'json': json, 'hashlib': hashlib}
    exec(compile(tree, str(path), 'exec'), namespace)
    return namespace['_analysis_fingerprint']


class WorkspaceHarness:
    def __init__(self, saved=True, account_id='a'):
        self.user = User(account_id) if account_id else None
        self.calls = []
        self.invalidations = 0
        self.analysis_hook = None
        self.save_hook = None
        self.login_hook = None
        self.analysis_error = None
        self.save_error = None
        self.save_ok = True
        self.staged = None
        self.attached = None
        self.rendered = []
        self.fingerprint = fingerprint_function()
        self.result = {
            'ok': True,
            'selection_data': {'securities': [{'symbol': 'TEST'}]},
            'rows': [{'entry_price': 200, 'current_price': 250}],
            'series': [{'date': '2026-01-02'}, {'date': '2026-11-06'}],
            'metadata': {'end_date': '2026-11-06', 'moved_transaction_dates': 0},
        }
        path = APP / 'client_code' / 'Form1' / '__init__.py'
        tree = ast.parse(path.read_text())
        tree.body = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Form1']
        namespace = {
            'Form1Template': object,
            'handle': lambda *args: lambda function: function,
            'date': date,
            'anvil': SimpleNamespace(
                users=SimpleNamespace(get_user=lambda: self.user),
                server=SimpleNamespace(call=self.server_call)),
            'invalidate_workspace': self.invalidate,
            'analyze_selection': lambda result, selected: {'result': result, 'selected': selected},
            'record_page': lambda page: None,
        }
        exec(compile(tree, str(path), 'exec'), namespace)
        self.namespace = namespace
        self.form = form = namespace['Form1'].__new__(namespace['Form1'])
        for name in Names((APP / 'client_code' / 'Form1' / 'form_template.html').read_text()).names:
            setattr(form, name, Control())
        form.account_id = account_id
        form._user = self.user
        form._current_portfolio = Portfolio('p1') if saved else None
        form._portfolio_dirty = True
        form._busy = False
        form._readout_page = 'allocation'
        form._history = {'cached': True}
        form._analysis_source = self.result
        form._selected_symbols = {'TEST'}
        form._dirty_readout_pages = set()
        form._plot_sizes = {}
        form._allocation_snapshots = []
        form._allocation_snapshot = None
        form._analyzed_transactions = []
        form._transactions_collapsed = True
        form._collapsed_transaction_years = {}
        form.portfolio_name_box.text = 'Long term' if saved else ''
        form.transactions = [{'ticker': 'TEST', 'trade_date': date(2026, 1, 2),
                              'action': 'Buy', 'shares': 5.0, 'trade_price': 185.0}]
        form.dom_nodes = {'readout_scroller': SimpleNamespace(scrollTop=20)}
        form.statuses = []
        form.portfolio_statuses = []
        form._set_status = lambda message, is_error=False: form.statuses.append((message, is_error))
        form._set_portfolio_status = lambda message, is_error=False: form.portfolio_statuses.append((message, is_error))
        form._set_busy = lambda busy: setattr(form, '_busy', busy)
        form._read_transactions = lambda: form.transactions
        form._read_analysis_settings = lambda transactions: (date(2026, 11, 6), 4.5)
        form._ensure_signed_in = self.ensure_signed_in
        form._refresh_portfolios = lambda: None
        form._refresh_transactions = lambda: None
        form._update_benchmark_options = lambda: None
        form._render_readout = lambda: None
        form._update_readout_context = lambda: None
        form._render_portfolio_summary = lambda: None
        form._clear_chart_trades = lambda: None
        form._set_charts_expanded = lambda *args, **kwargs: None
        form._resize_readout_charts = lambda: None
        form._page_charts = lambda page: []
        for method, page in (('_render_overview', 'overview'), ('_render_sectors', 'allocation'),
                             ('_render_security_rows', 'security rows'), ('_render_comparison', 'performance')):
            setattr(form, method, lambda page=page: self.rendered.append(page))

    def invalidate(self):
        self.invalidations += 1

    def ensure_signed_in(self):
        if self.login_hook:
            self.login_hook()
        return self.user is not None

    def server_call(self, name, *arguments):
        self.calls.append((name, copy.deepcopy(arguments)))
        if name == 'analyze_portfolio_history':
            if self.analysis_error:
                raise self.analysis_error
            document, as_of, rate, portfolio_id = arguments
            self.staged = (self.fingerprint(document, as_of, rate * 100), self.result)
            if self.analysis_hook:
                self.analysis_hook()
            return self.result
        if name == 'save_portfolio':
            if self.save_error:
                raise self.save_error
            portfolio_id, name, document, as_of, rate = arguments
            if self.save_hook:
                self.save_hook()
            if not self.save_ok:
                return {'ok': False, 'message': 'Saving limit reached.'}
            if self.staged and self.staged[0] == self.fingerprint(document, as_of, rate):
                self.attached = self.staged[1]
            return {'ok': True, 'portfolio': Portfolio(portfolio_id or 'new-p', name=name)}
        raise AssertionError('Unexpected server call: ' + name)


class PortfolioTabsTests(unittest.TestCase):
    def test_notification_polling_is_active_only_on_the_inbox_page(self):
        h = WorkspaceHarness()
        h.form.overview_tab_click()
        self.assertFalse(h.form.notifications_form.active)
        h.form.notifications_tab_click()
        self.assertTrue(h.form.notifications_form.active)
        h.form.performance_tab_click()
        self.assertFalse(h.form.notifications_form.active)
        self.assertEqual(h.calls, [])

    def test_transactions_tab_shows_editor_and_preserves_analysis_without_repricing(self):
        h = WorkspaceHarness()
        analysis = h.form._analysis_source
        history = h.form._history
        h.form.transactions_tab_click()
        self.assertTrue(h.form.transactions_column.visible)
        self.assertFalse(h.form.readout_column.visible)
        self.assertEqual(h.form.transactions_tab_button.role, 'readout-tab-active')
        self.assertFalse(h.form.results_section.visible)
        self.assertIs(h.form._analysis_source, analysis)
        self.assertIs(h.form._history, history)
        self.assertEqual(h.calls, [])
        h.form.overview_tab_click()
        self.assertFalse(h.form.transactions_column.visible)
        self.assertTrue(h.form.readout_column.visible)
        self.assertTrue(h.form.results_section.visible)
        self.assertEqual(h.calls, [])

    def test_returning_to_clean_chart_tabs_does_not_render_again(self):
        h = WorkspaceHarness()
        h.form._dirty_readout_pages = {'performance'}
        h.form.performance_tab_click()
        h.form.transactions_tab_click()
        h.form.performance_tab_click()
        self.assertEqual(h.rendered, ['performance'])
        self.assertEqual(h.calls, [])

    def test_tax_estimator_tab_uses_tax_form_and_has_no_market_call(self):
        h = WorkspaceHarness()
        h.form.tax_estimator_tab_click()
        self.assertEqual(h.form._readout_page, 'tax_estimator')
        self.assertTrue(h.form.tax_estimator_panel.visible)
        self.assertFalse(h.form.overview_panel.visible)
        self.assertEqual(h.form.tax_estimator_form.redraw_count, 1)
        self.assertEqual(h.calls, [])

    def test_restored_analysis_does_not_change_selected_tab(self):
        h = WorkspaceHarness()
        h.form._apply_analysis(h.result, h.form.transactions)
        self.assertEqual(h.form._readout_page, 'allocation')
        self.assertEqual(h.calls, [])
        self.assertEqual(h.form.transactions[0]['trade_price'], 185)
        self.assertEqual(h.form.transactions[0]['entry_price'], 200)

    def test_reopening_active_portfolio_preserves_selected_tab_and_analysis(self):
        h = WorkspaceHarness()
        original = h.form._analysis_source
        self.assertTrue(h.form.open_profile_portfolio('p1'))
        self.assertEqual(h.form._readout_page, 'allocation')
        self.assertIs(h.form._analysis_source, original)
        self.assertEqual(h.calls, [])


class SaveSnapshotTests(unittest.TestCase):
    def snapshot(self, harness):
        return harness.form._save_snapshot('p1', 'a', 'Long term', [], date(2026, 11, 6), 4.5)

    def test_account_mismatch_before_save_makes_no_server_call(self):
        h = WorkspaceHarness()
        h.user = User('b')
        self.assertIsNone(self.snapshot(h))
        self.assertEqual(h.calls, [])
        self.assertTrue(h.form._portfolio_dirty)

    def test_portfolio_mismatch_before_save_makes_no_server_call(self):
        h = WorkspaceHarness()
        h.form._current_portfolio = Portfolio('p2')
        self.assertIsNone(self.snapshot(h))
        self.assertEqual(h.calls, [])

    def test_account_change_during_save_does_not_apply_the_response(self):
        h = WorkspaceHarness()
        old = h.form._current_portfolio
        h.save_hook = lambda: setattr(h, 'user', User('b'))
        self.assertIsNone(self.snapshot(h))
        self.assertIs(h.form._current_portfolio, old)
        self.assertTrue(h.form._portfolio_dirty)
        self.assertEqual(h.invalidations, 0)

    def test_portfolio_change_during_save_does_not_replace_the_new_workspace(self):
        h = WorkspaceHarness()
        h.save_hook = lambda: setattr(h.form, '_current_portfolio', Portfolio('p2'))
        self.assertIsNone(self.snapshot(h))
        self.assertEqual(h.form._current_portfolio.get_id(), 'p2')
        self.assertTrue(h.form._portfolio_dirty)
        self.assertEqual(h.invalidations, 0)

    def test_validation_failure_keeps_draft_dirty(self):
        h = WorkspaceHarness()
        h.save_ok = False
        result = self.snapshot(h)
        self.assertFalse(result['ok'])
        self.assertTrue(h.form._portfolio_dirty)
        self.assertEqual(h.invalidations, 0)


class SyncAndSaveTests(unittest.TestCase):
    def test_sync_saved_portfolio_persists_exact_input_document_and_staged_analysis(self):
        h = WorkspaceHarness()
        h.form.analyze_button_click()
        self.assertEqual([call[0] for call in h.calls], ['analyze_portfolio_history', 'save_portfolio'])
        analyze = h.calls[0][1]
        save = h.calls[1][1]
        self.assertEqual(save[0:2], ('p1', 'Long term'))
        self.assertEqual(save[2], [dict(analyze[0][0], trade_date='2026-01-02')])
        self.assertEqual(save[3:], (date(2026, 11, 6), 4.5))
        self.assertEqual(analyze[2], .045)
        self.assertIs(h.attached, h.result)
        self.assertFalse(h.form._portfolio_dirty)
        self.assertEqual(h.invalidations, 1)
        self.assertFalse(h.form._busy)
        self.assertNotIn('entry_price', save[2][0])
        self.assertNotIn('current_price', save[2][0])

    def test_new_draft_sync_stays_unsaved_until_explicit_save(self):
        h = WorkspaceHarness(saved=False)
        h.form.analyze_button_click()
        self.assertEqual([call[0] for call in h.calls], ['analyze_portfolio_history'])
        self.assertIsNone(h.form._current_portfolio)
        self.assertTrue(h.form._portfolio_dirty)
        h.form.portfolio_name_box.text = 'My first portfolio'
        h.form.save_portfolio_button_click()
        self.assertEqual(h.calls[-1][0], 'save_portfolio')
        self.assertEqual(h.calls[-1][1][0], None)
        self.assertIs(h.attached, h.result)
        self.assertFalse(h.form._portfolio_dirty)

    def test_guest_sync_creates_no_account_or_portfolio(self):
        h = WorkspaceHarness(saved=False, account_id=None)
        h.form.analyze_button_click()
        self.assertEqual([call[0] for call in h.calls], ['analyze_portfolio_history'])
        self.assertIsNone(h.form._current_portfolio)
        self.assertIsNone(h.user)

    def test_guest_can_explicitly_save_their_analysis_after_first_login(self):
        h = WorkspaceHarness(saved=False, account_id=None)
        h.form.analyze_button_click()
        def first_login():
            h.user = User('a')
            h.form.account_id = 'a'
        h.login_hook = first_login
        h.form.portfolio_name_box.text = 'My first portfolio'
        h.form.save_portfolio_button_click()
        self.assertEqual([call[0] for call in h.calls], ['analyze_portfolio_history', 'save_portfolio'])
        self.assertIs(h.attached, h.result)
        self.assertFalse(h.form._portfolio_dirty)

    def test_late_analysis_for_changed_account_is_not_applied_or_saved(self):
        h = WorkspaceHarness()
        h.analysis_hook = lambda: setattr(h, 'user', User('b'))
        h.form.analyze_button_click()
        self.assertEqual([call[0] for call in h.calls], ['analyze_portfolio_history'])
        self.assertIsNone(h.form._analysis_source)
        self.assertTrue(h.form._portfolio_dirty)
        self.assertFalse(h.form._busy)

    def test_late_analysis_for_changed_portfolio_is_not_applied_or_saved(self):
        h = WorkspaceHarness()
        h.analysis_hook = lambda: setattr(h.form, '_current_portfolio', Portfolio('p2'))
        h.form.analyze_button_click()
        self.assertEqual([call[0] for call in h.calls], ['analyze_portfolio_history'])
        self.assertIsNone(h.form._analysis_source)
        self.assertTrue(h.form._portfolio_dirty)

    def test_failed_save_preserves_successful_analysis_and_dirty_inputs(self):
        h = WorkspaceHarness()
        h.save_ok = False
        h.form.analyze_button_click()
        self.assertIs(h.form._analysis_source, h.result)
        self.assertTrue(h.form._portfolio_dirty)
        self.assertEqual(h.form.portfolio_statuses[-1], ('Saving limit reached.', True))

    def test_unexpected_market_error_propagates_and_restores_busy_state(self):
        h = WorkspaceHarness()
        h.analysis_error = RuntimeError('market connection failed')
        with self.assertRaisesRegex(RuntimeError, 'market connection failed'):
            h.form.analyze_button_click()
        self.assertFalse(h.form._busy)

    def test_unexpected_save_error_propagates_and_preserves_dirty_inputs(self):
        h = WorkspaceHarness()
        h.save_error = RuntimeError('database unavailable')
        with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
            h.form.save_portfolio_button_click()
        self.assertFalse(h.form._busy)
        self.assertTrue(h.form._portfolio_dirty)

    def test_different_signed_in_account_during_save_setup_cannot_receive_old_draft(self):
        h = WorkspaceHarness(saved=False)
        def switch_account():
            h.user = User('b')
            h.form.account_id = 'b'
        h.login_hook = switch_account
        h.form.portfolio_name_box.text = 'Private draft A'
        h.form.save_portfolio_button_click()
        self.assertEqual(h.calls, [])
        self.assertTrue(h.form._portfolio_dirty)

    def test_portfolio_change_during_save_setup_cannot_overwrite_another_portfolio(self):
        h = WorkspaceHarness()
        h.login_hook = lambda: setattr(h.form, '_current_portfolio', Portfolio('p2'))
        h.form.save_portfolio_button_click()
        self.assertEqual(h.calls, [])
        self.assertTrue(h.form._portfolio_dirty)


if __name__ == '__main__':
    unittest.main()
