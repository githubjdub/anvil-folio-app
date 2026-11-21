"""Portfolio autosave, refresh and navigation with Anvil boundaries replaced."""
import ast
import copy
from datetime import date
from html.parser import HTMLParser
import math
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest

APP = Path(__file__).resolve().parents[1]
GROUPING = runpy.run_path(str(APP / 'client_code/transaction_groups.py'))


class Names(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.names, self.dom_nodes = set(), set()
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        for key, value in attributes:
            if key in ('name', 'anvil:name'):
                self.names.add(value)
            elif key == 'anvil:dom-node':
                self.dom_nodes.add(value)


class Control:
    def __init__(self):
        self.visible = self.enabled = True
        self.classes, self.attrs = {}, {}
        self.text = ''
        self._items, self.children, self.contexts = [], [], []
        self.items_assignments = 0
        self.selected_value = self.date = self.file = None
        self.interval = self.redraw_count = self.reset_count = self.loaded_count = 0
        self.view = None
        self._inputs_dirty = False
        self.handlers = {}

    def redraw(self):
        self.redraw_count += 1

    @property
    def items(self):
        return self._items

    @items.setter
    def items(self, value):
        self.items_assignments += 1
        self._items = value

    def raise_event_on_children(self, name, **args):
        for child in self.children:
            if hasattr(child, 'raise_event'):
                child.raise_event(name, **args)

    def reset_workspace(self):
        self.reset_count += 1

    reset_context = reset_workspace

    def ensure_loaded(self):
        self.loaded_count += 1

    preload = ensure_loaded

    def set_active(self, active):
        self.active = active

    def set_view(self, view):
        self.view = view

    def set_portfolio_context(self, *args, **kwargs):
        self.contexts.append((args, kwargs))

    def set_analysis(self, *args, **kwargs):
        self.contexts.append((args, kwargs))

    def set_account_context(self, *args):
        pass

    def get_components(self):
        return self.children

    def add_component(self, component):
        self.children.append(component)

    def set_event_handler(self, name, handler):
        self.handlers[name] = handler

    def raise_event(self, name, **args):
        if name in self.handlers:
            self.handlers[name](**args)

    def prepare(self):
        pass

    def clear(self):
        self.file = None


class Node:
    def __init__(self):
        self.attrs, self.scrollTop = {}, 0

    def removeAttribute(self, name):
        self.attrs.pop(name, None)


class User:
    def __init__(self, account_id):
        self.account_id = account_id

    def get_id(self):
        return self.account_id


class Portfolio(dict):
    def __init__(self, portfolio_id, name='Long term'):
        super().__init__(name=name, as_of_date=date(2026, 11, 6), analysis_updated_at=None)
        self.portfolio_id = portfolio_id

    def get_id(self):
        return self.portfolio_id


class WorkspaceHarness:
    def __init__(self, saved=True, account_id='a', defer_load=False, transaction_render=False):
        self.user = User(account_id) if account_id else None
        self.calls, self.rendered = [], []
        self.invalidations = 0
        self.analysis_hook = self.save_hook = self.login_hook = None
        self.analysis_error = self.save_error = None
        self.save_ok = True
        self.documents = {}
        self.pending = []
        self.account_reads = 0
        self.tools_created = []
        self.startup_result = {'ok': True, 'portfolio': None, 'overview': None}
        self.startup_hook = self.history_hook = None
        self.history_error = None
        self.history_result = None
        self.result = {
            'ok': True,
            'selection_data': {'securities': [{'symbol': 'TEST'}]},
            'rows': [{'entry_price': 200, 'current_price': 250}],
            'series': [{'date': '2026-01-02'}, {'date': '2026-11-06'}],
            'metrics': {'holding_count': 1, 'market_value': 1250},
            'benchmarks': {},
            'metadata': {'end_date': '2026-11-06', 'moved_transaction_dates': 0},
        }
        path = APP / 'client_code/Form1/__init__.py'
        names = Names(path.with_name('form_template.html').read_text())

        class Template:
            def __init__(form, **properties):
                for name in names.names:
                    setattr(form, name, Control())
                form.dom_nodes = {name: Node() for name in names.dom_nodes}
                form.risk_free_box.text = '4.50'

        namespace = {
            'Form1Template': Template,
            'handle': lambda *args: lambda function: function,
            'date': date, 'math': math,
            'chart_theme': SimpleNamespace(MODE='light', SECTOR_COLORS={}),
            'HoldingRow': type('HoldingRow', (), {}),
            'TransactionYearGroup': type('TransactionYearGroup', (), {}),
            'anvil': SimpleNamespace(users=SimpleNamespace(get_user=lambda: self.user),
                                     server=SimpleNamespace(call=self.server_call)),
            'invalidate_workspace': self.invalidate,
            'startup_workspace': lambda portfolio_id: self.server_call('get_portfolio_startup', portfolio_id),
            'analyze_selection': lambda result, selected: dict(result),
            'record_page': lambda page: None,
            'defer': self.defer,
            '_create_tool': self.create_tool,
            'group_transactions_by_date': GROUPING['group_transactions_by_date'],
            'group_transactions_by_year': GROUPING['group_transactions_by_year'],
            'startup_performance': SimpleNamespace(mark=lambda *a: None,
                after_frame=lambda *a: None, complete=lambda: None),
        }
        tree = ast.parse(path.read_text())
        tree.body = [node for node in tree.body if isinstance(node, (ast.Assign, ast.ClassDef))]
        exec(compile(tree, str(path), 'exec'), namespace)
        self.namespace = namespace
        cls = namespace['Form1']
        # Run the real constructor for state; replace account/DOM/rendering
        # boundaries, leaving navigation, input validation and save logic real.
        cls._refresh_account = lambda form: self.refresh_account(form)
        cls._restore_last_portfolio = lambda form, *args: None
        cls._ensure_signed_in = lambda form: self.ensure_signed_in(form)
        cls._set_status = lambda form, message, is_error=False: self.statuses.append((message, is_error))
        cls._set_portfolio_status = lambda form, message, is_error=False: self.portfolio_statuses.append((message, is_error))
        if not transaction_render:
            cls._refresh_transactions = cls._ensure_transaction_rows = lambda form: None
        for method in ('_update_controls', '_update_welcome',
                       '_render_portfolio_summary', '_resize_readout_charts', '_set_charts_expanded'):
            setattr(cls, method, lambda form, *args, **kwargs: None)
        cls._page_charts = lambda form, page: []
        for method, page in (('_render_overview', 'overview'), ('_render_sectors', 'allocation'),
                             ('_render_security_rows', 'security rows'), ('_render_comparison', 'performance')):
            setattr(cls, method, lambda form, page=page: self.rendered.append(page))
        self.statuses, self.portfolio_statuses = [], []
        self.form = form = cls(defer_load=defer_load)
        if defer_load:
            return
        form._current_portfolio = Portfolio('p1') if saved else None
        form._portfolio_dirty = True
        form._readout_page = form._portfolio_readout_page = 'allocation'
        form._history, form._analysis_source = dict(self.result), self.result
        form._selected_symbols = {'TEST'}
        form._dirty_readout_pages.clear()
        form.portfolio_name_box.text = 'Long term' if saved else ''
        form.as_of_date_picker.date = date(2026, 11, 6)
        form.transactions = [{'ticker': 'TEST', 'trade_date': date(2026, 1, 2),
                              'action': 'Buy', 'shares': 5.0, 'trade_price': 185.0}]
        form._analyzed_transactions = list(form.transactions)
        if not transaction_render:
            form._transaction_rows_dirty = False
        self.statuses.clear()
        self.portfolio_statuses.clear()

    def refresh_account(self, form):
        self.account_reads += 1
        form._user = self.user
        form.account_id = self.user.get_id() if self.user else None
        form._username = 'owner' if self.user else ''

    def create_tool(self, name):
        self.tools_created.append(name)
        return Control()

    def defer(self, callback, is_current=None, priority=False):
        job = (callback, is_current)
        if priority:
            self.pending.insert(0, job)
        else:
            self.pending.append(job)

    def run_idle(self):
        while self.pending:
            callback, current = self.pending.pop(0)
            if current is None or current():
                callback()

    def invalidate(self):
        self.invalidations += 1

    def ensure_signed_in(self, form):
        if self.login_hook:
            self.login_hook()
        self.refresh_account(form)
        return self.user is not None

    def server_call(self, name, *arguments):
        self.calls.append((name, copy.deepcopy(arguments)))
        if name == 'get_portfolio_startup':
            if self.startup_hook:
                self.startup_hook()
            return self.startup_result
        if name == 'get_portfolio_history':
            if self.history_hook:
                self.history_hook()
            if self.history_error:
                raise self.history_error
            return self.history_result
        if name not in ('refresh_portfolio', 'save_portfolio'):
            raise AssertionError('Unexpected server call: ' + name)
        if self.save_error:
            raise self.save_error
        if self.save_hook:
            self.save_hook()
        if not self.save_ok:
            return {'ok': False, 'message': 'Saving limit reached.'}
        portfolio_id, title, document, *settings = arguments
        as_of, rate = (date.today(), settings[0]) if name == 'refresh_portfolio' else settings
        portfolio_id = portfolio_id or 'new-p'
        self.documents[portfolio_id] = copy.deepcopy(document)
        portfolio = Portfolio(portfolio_id, name=title)
        portfolio['as_of_date'] = as_of
        result = dict(ok=True, portfolio=portfolio,
                      directory_entry=dict(portfolio_id=portfolio_id, name=title))
        if name == 'refresh_portfolio':
            if self.analysis_error:
                raise self.analysis_error
            if self.analysis_hook:
                self.analysis_hook()
            result['analysis'] = self.result
        return result


class PortfolioTabsTests(unittest.TestCase):
    def test_notification_polling_is_active_only_on_the_inbox_page(self):
        h = WorkspaceHarness()
        h.form.overview_tab_click()
        self.assertIsNone(h.form.notifications_form)
        h.form.notifications_tab_click()
        self.assertTrue(h.form.notifications_form.active)
        h.form.performance_tab_click()
        self.assertFalse(h.form.notifications_form.active)
        self.assertEqual(h.calls, [])

    def test_transactions_tab_preserves_analysis_without_repricing(self):
        h = WorkspaceHarness()
        analysis, history = h.form._analysis_source, h.form._history
        h.form.transactions_tab_click()
        self.assertTrue(h.form.transactions_column.visible)
        self.assertFalse(h.form.readout_column.visible)
        self.assertEqual(h.form.transactions_tab_button.role, 'readout-tab-active')
        self.assertFalse(h.form.results_section.visible)
        self.assertIs(h.form._analysis_source, analysis)
        self.assertIs(h.form._history, history)
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

    def test_tax_estimator_tab_has_no_market_call(self):
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


class AutosaveTests(unittest.TestCase):
    def test_account_mismatch_before_autosave_makes_no_server_call(self):
        for saved in (True, False):
            h = WorkspaceHarness(saved=saved)
            h.user = User('b')
            h.form.portfolio_name_box.text = 'Private draft A'
            h.form._autosave()
            self.assertEqual(h.calls, [])
            self.assertTrue(h.form._portfolio_dirty)

    def test_account_change_during_save_does_not_apply_the_response(self):
        h = WorkspaceHarness()
        old = h.form._current_portfolio
        h.save_hook = lambda: setattr(h, 'user', User('b'))
        h.form._autosave()
        self.assertIs(h.form._current_portfolio, old)
        self.assertTrue(h.form._portfolio_dirty)
        self.assertEqual(h.invalidations, 0)
        self.assertFalse(h.form._autosaving)

    def test_portfolio_change_during_save_does_not_replace_new_workspace(self):
        h = WorkspaceHarness()
        h.save_hook = lambda: setattr(h.form, '_current_portfolio', Portfolio('p2'))
        h.form._autosave()
        self.assertEqual(h.form._current_portfolio.get_id(), 'p2')
        self.assertTrue(h.form._portfolio_dirty)
        self.assertEqual(h.invalidations, 0)

    def test_portfolio_change_while_committing_inputs_sends_no_old_document(self):
        h = WorkspaceHarness()
        row = h.namespace['HoldingRow']()
        row.commit_inputs = lambda: setattr(h.form, '_current_portfolio', Portfolio('p2'))
        h.form.holdings_panel.children = [row]
        h.form._autosave()
        self.assertEqual(h.calls, [])
        self.assertTrue(h.form._portfolio_dirty)

    def test_server_rejection_keeps_draft_dirty(self):
        h = WorkspaceHarness()
        h.save_ok = False
        h.form._autosave()
        self.assertTrue(h.form._portfolio_dirty)
        self.assertEqual(h.portfolio_statuses[-1], ('Saving limit reached.', True))
        self.assertEqual(h.invalidations, 0)

    def test_input_validation_keeps_draft_and_makes_no_server_call(self):
        for field, invalid in (('shares', '-1'), ('trade_price', 'not a price'), ('ticker', '')):
            h = WorkspaceHarness()
            h.form.transactions[0][field] = invalid
            h.form._autosave()
            self.assertEqual(h.calls, [])
            self.assertTrue(h.form._portfolio_dirty)
            self.assertTrue(h.portfolio_statuses[-1][1])

    def test_pending_editor_inputs_are_committed_before_autosave(self):
        h = WorkspaceHarness()
        row = h.namespace['HoldingRow']()
        row.commit_inputs = lambda: h.form.transactions[0].update(shares='7.5')
        h.form.holdings_panel.children = [row]
        h.form._autosave()
        self.assertEqual(h.calls[0][1][2][0]['shares'], 7.5)
        self.assertFalse(h.form._portfolio_dirty)

    def test_edits_during_save_are_not_marked_saved_and_are_rescheduled(self):
        h = WorkspaceHarness()
        def edit():
            h.form.transactions[0]['shares'] = 8
            h.form._mark_portfolio_dirty()
        h.save_hook = edit
        h.form._autosave()
        self.assertEqual(h.documents['p1'][0]['shares'], 5)
        self.assertTrue(h.form._portfolio_dirty)
        self.assertGreater(h.form.autosave_timer.interval, 0)
        h.save_hook = None
        h.form.autosave_timer_tick()
        self.assertEqual(h.documents['p1'][0]['shares'], 8)
        self.assertFalse(h.form._portfolio_dirty)

    def test_overlapping_autosave_cannot_send_duplicate_requests(self):
        h = WorkspaceHarness()
        h.save_hook = h.form._autosave
        h.form._autosave()
        self.assertEqual([name for name, args in h.calls], ['save_portfolio'])

    def test_guest_autosave_keeps_draft_without_creating_account_or_portfolio(self):
        h = WorkspaceHarness(saved=False, account_id=None)
        h.form._autosave()
        self.assertEqual(h.calls, [])
        self.assertIsNone(h.user)
        self.assertIsNone(h.form._current_portfolio)
        self.assertTrue(h.form._portfolio_dirty)

    def test_unexpected_save_error_propagates_and_releases_autosave(self):
        h = WorkspaceHarness()
        h.save_error = RuntimeError('database unavailable')
        with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
            h.form._autosave()
        self.assertFalse(h.form._autosaving)
        self.assertTrue(h.form._portfolio_dirty)


class RefreshTests(unittest.TestCase):
    def test_stale_account_before_refresh_cannot_send_draft(self):
        h = WorkspaceHarness(saved=False)
        h.user = User('b')
        h.form.analyze_button_click()
        self.assertEqual(h.calls, [])
        self.assertTrue(h.form._portfolio_dirty)

    def test_refresh_saves_exact_editable_inputs_in_one_server_call(self):
        h = WorkspaceHarness()
        h.form.analyze_button_click()
        self.assertEqual([name for name, args in h.calls], ['refresh_portfolio'])
        self.assertEqual(h.calls[0][1], ('p1', 'Long term', [dict(
            ticker='TEST', trade_date='2026-01-02', action='Buy', shares=5.0, trade_price=185.0)], 4.5))
        self.assertIs(h.form._analysis_source, h.result)
        self.assertEqual(h.form.as_of_date_picker.date, date.today())
        self.assertFalse(h.form._portfolio_dirty)
        self.assertEqual(h.invalidations, 1)
        self.assertFalse(h.form._busy)

    def test_new_draft_refresh_creates_saved_portfolio(self):
        h = WorkspaceHarness(saved=False)
        h.form.analyze_button_click()
        self.assertEqual([name for name, args in h.calls], ['refresh_portfolio'])
        self.assertIsNone(h.calls[0][1][0])
        self.assertEqual(h.form._current_portfolio.get_id(), 'new-p')
        self.assertEqual(h.form.portfolio_name_box.text, 'My portfolio')
        self.assertFalse(h.form._portfolio_dirty)

    def test_guest_refresh_can_cancel_signin_and_keep_draft(self):
        h = WorkspaceHarness(saved=False, account_id=None)
        original = list(h.form.transactions)
        h.form.analyze_button_click()
        self.assertEqual(h.calls, [])
        self.assertEqual(h.form.transactions, original)
        self.assertIsNone(h.user)
        self.assertIsNone(h.form._current_portfolio)
        self.assertFalse(h.form._busy)

    def test_guest_refresh_saves_under_account_selected_at_signin(self):
        h = WorkspaceHarness(saved=False, account_id=None)
        h.login_hook = lambda: setattr(h, 'user', User('a'))
        h.form.portfolio_name_box.text = 'My first portfolio'
        h.form.analyze_button_click()
        self.assertEqual([name for name, args in h.calls], ['refresh_portfolio'])
        self.assertEqual(h.form.account_id, 'a')
        self.assertEqual(h.form._current_portfolio.get_id(), 'new-p')
        self.assertFalse(h.form._portfolio_dirty)

    def test_late_analysis_for_changed_account_is_not_applied(self):
        h = WorkspaceHarness()
        original = h.form._analysis_source
        h.analysis_hook = lambda: setattr(h, 'user', User('b'))
        h.form.analyze_button_click()
        self.assertEqual([name for name, args in h.calls], ['refresh_portfolio'])
        self.assertIs(h.form._analysis_source, original)
        self.assertTrue(h.form._portfolio_dirty)
        self.assertFalse(h.form._busy)

    def test_late_analysis_for_changed_portfolio_is_not_applied(self):
        h = WorkspaceHarness()
        original = h.form._analysis_source
        h.analysis_hook = lambda: setattr(h.form, '_current_portfolio', Portfolio('p2'))
        h.form.analyze_button_click()
        self.assertEqual(h.form._current_portfolio.get_id(), 'p2')
        self.assertIs(h.form._analysis_source, original)
        self.assertTrue(h.form._portfolio_dirty)

    def test_failed_save_preserves_existing_analysis_and_dirty_inputs(self):
        h = WorkspaceHarness()
        original = h.form._analysis_source
        h.save_ok = False
        h.form.analyze_button_click()
        self.assertIs(h.form._analysis_source, original)
        self.assertTrue(h.form._portfolio_dirty)
        self.assertEqual(h.statuses[-1], ('Saving limit reached.', True))

    def test_unexpected_market_error_propagates_and_restores_busy_state(self):
        h = WorkspaceHarness()
        h.analysis_error = RuntimeError('market connection failed')
        with self.assertRaisesRegex(RuntimeError, 'market connection failed'):
            h.form.analyze_button_click()
        self.assertFalse(h.form._busy)

    def test_unexpected_save_error_propagates_and_preserves_draft(self):
        h = WorkspaceHarness()
        h.save_error = RuntimeError('database unavailable')
        with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
            h.form.analyze_button_click()
        self.assertFalse(h.form._busy)
        self.assertTrue(h.form._portfolio_dirty)

    def test_account_change_during_signin_cannot_receive_another_accounts_draft(self):
        h = WorkspaceHarness(saved=False)
        h.form._username = ''
        h.login_hook = lambda: setattr(h, 'user', User('b'))
        h.form.portfolio_name_box.text = 'Private draft A'
        h.form.analyze_button_click()
        self.assertEqual(h.calls, [])
        self.assertTrue(h.form._portfolio_dirty)

    def test_portfolio_change_during_signin_cannot_overwrite_another_portfolio(self):
        h = WorkspaceHarness()
        h.form._username = ''
        h.login_hook = lambda: setattr(h.form, '_current_portfolio', Portfolio('p2'))
        h.form.analyze_button_click()
        self.assertEqual(h.calls, [])
        self.assertTrue(h.form._portfolio_dirty)

    def test_refresh_waits_for_inflight_autosave(self):
        h = WorkspaceHarness()
        h.save_hook = h.form.analyze_button_click
        h.form._autosave()
        self.assertEqual([name for name, args in h.calls], ['save_portfolio'])
        self.assertTrue(h.form._refresh_requested)
        h.save_hook = None
        h.form.autosave_timer_tick()
        self.assertEqual([name for name, args in h.calls], ['save_portfolio', 'refresh_portfolio'])
        self.assertFalse(h.form._refresh_requested)
        self.assertFalse(h.form._busy)


if __name__ == '__main__':
    unittest.main()
