"""Startup work is reused, while account changes and editable drafts stay safe."""
import ast
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import unittest

from test_planning_forms import Harness, analysis
from test_portfolio_workspace import WorkspaceHarness, User


APP = Path(__file__).resolve().parents[1]


def workspace_methods(user, call=None):
    path = APP / 'client_code/Form1/__init__.py'
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    methods = {'prepare_navigation', '_prepare_tool', '_render_readout',
               '_ensure_readout_rendered', '_render_active_readout',
               'open_profile_portfolio', '_restore_last_portfolio', 'analyze_button_click'}
    cls.bases = []
    cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in methods]
    for node in cls.body:
        node.decorator_list = []
    pending = []
    def defer(callback, current=None, priority=False):
        pending.append((callback, current))
    namespace = dict(date=date, defer=defer, anvil=SimpleNamespace(users=SimpleNamespace(get_user=user),
                                                     server=SimpleNamespace(call=call)))
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), 'exec'), namespace)
    form = namespace['Form1']()
    form._busy = False
    form._preparing_navigation = False
    form._background_tool = None
    form._startup_loading = False
    form._navigation_scope = form._navigation_history = form._navigation_job = None
    form._autosaving = False
    form._autosave_generation = 0
    form._current_portfolio = None
    form._history = None
    form.account_id = user().get_id() if user() is not None else None
    form._workspace_matches = lambda account, portfolio: (user().get_id() if user() is not None else None) == account
    form._dirty_readout_pages = set()
    form._plot_sizes = {}
    form._page_charts = lambda page: []
    def run_idle():
        while pending:
            callback, current = pending.pop(0)
            if current is None or current():
                callback()
    form.run_idle = run_idle
    form.pending = pending
    return form


class StartupPreparationTests(unittest.TestCase):
    def test_tools_prepare_after_first_page_and_stop_on_account_change(self):
        current = ['a']
        form = workspace_methods(lambda: SimpleNamespace(get_id=lambda: current[0]))
        form.account_id = 'a'
        prepared = []
        form.income_form = SimpleNamespace(ensure_loaded=lambda: prepared.append('income'))
        form.tax_estimator_form = SimpleNamespace(redraw=lambda: prepared.append('tax'), _inputs_dirty=False)
        form.retirement_form = SimpleNamespace(redraw=lambda: prepared.append('retirement'))
        form.notifications_form = SimpleNamespace(preload=lambda: prepared.append('notifications'))
        form.prepare_navigation()
        self.assertEqual(prepared, [])
        form.run_idle()
        self.assertEqual(prepared, ['retirement', 'tax', 'notifications', 'income'])
        prepared.clear()
        def switch_account():
            prepared.append('income')
            current[0] = 'b'
        form.retirement_form.redraw = switch_account
        form._history = object()
        form.prepare_navigation()
        form.run_idle()
        self.assertEqual(prepared, ['income'])

    def test_initial_views_render_once_and_clean_tab_switches_do_no_work(self):
        h = WorkspaceHarness()
        form = h.form
        form._history = object()
        form._dirty_readout_pages = {'overview', 'allocation', 'performance'}
        rendered = []
        for method in ('_render_overview', '_render_sectors', '_render_security_rows', '_render_comparison'):
            setattr(form, method, lambda method=method: rendered.append(method))
        form._page_charts = lambda page: []
        # Keep the real ensure/render methods while deferring their execution.
        form.prepare_navigation()
        self.assertEqual(rendered, [])
        h.run_idle()
        self.assertEqual(len(rendered), 4)
        self.assertEqual(form._dirty_readout_pages, set())
        for page in ('performance', 'allocation', 'overview', 'performance'):
            form._ensure_readout_rendered(page)
        self.assertEqual(len(rendered), 4)

    def test_preparation_is_deduplicated_and_failure_releases_background_state(self):
        form = workspace_methods(lambda: SimpleNamespace(get_id=lambda: 'a'))
        form.account_id = 'a'
        prepared = []
        form.income_form = SimpleNamespace(ensure_loaded=lambda: (prepared.append('income'), form.prepare_navigation()))
        form.tax_estimator_form = SimpleNamespace(redraw=lambda: prepared.append('tax'), _inputs_dirty=False)
        form.retirement_form = SimpleNamespace(redraw=lambda: prepared.append('retirement'))
        form.notifications_form = SimpleNamespace(preload=lambda: prepared.append('notifications'))
        form.prepare_navigation()
        form.prepare_navigation()
        form.run_idle()
        self.assertEqual(prepared, ['retirement', 'tax', 'notifications', 'income'])
        def fail():
            raise RuntimeError('unexpected tool error')
        form.income_form.ensure_loaded = fail
        form._history = object()
        form.prepare_navigation()
        with self.assertRaisesRegex(RuntimeError, 'unexpected tool error'):
            form.run_idle()
        self.assertIsNone(form._background_tool)

    def test_constructor_attaches_page_before_reading_account_data(self):
        h = WorkspaceHarness(defer_load=True)
        self.assertEqual(h.account_reads, 0)
        self.assertEqual(h.calls, [])
        self.assertTrue(h.form.startup_region.visible)
        self.assertTrue(h.form._startup_loading)
        h.form.form_show()
        self.assertEqual(h.account_reads, 0)
        h.run_idle()
        self.assertEqual(h.account_reads, 1)
        self.assertFalse(h.form._startup_loading)
        self.assertFalse(h.form._busy)
        self.assertFalse(h.form.startup_region.visible)

    def test_account_change_before_initial_read_cannot_load_old_portfolio(self):
        h = WorkspaceHarness(defer_load=True)
        h.form.form_show()
        h.user = User('b')
        h.run_idle()
        self.assertEqual(h.account_reads, 0)
        self.assertEqual(h.calls, [])

    def test_hidden_tool_busy_events_do_not_disable_navigation(self):
        h = WorkspaceHarness()
        for tool, handler in [('income', h.form.income_busy_change),
                              ('tax', h.form.tax_estimator_busy_change),
                              ('notifications', h.form.notifications_busy_change)]:
            h.form._prepare_tool(tool, lambda: (handler(True), handler(False)))
            self.assertFalse(h.form._busy)
        h.form.income_busy_change(True)
        self.assertTrue(h.form._busy)

    def test_requested_portfolio_restores_without_loading_previous_portfolio(self):
        calls, applied = [], []
        portfolio = dict(name='Linked portfolio')
        analysis = object()
        def call(name, *args):
            calls.append((name, args))
            return dict(ok=True, portfolio=portfolio, analysis=analysis)
        form = workspace_methods(lambda: None, call)
        form._confirm_discard = lambda: True
        form._set_portfolio_status = lambda *a, **kw: None
        form._apply_portfolio = lambda *args: applied.append(args)
        form._restore_last_portfolio('p2')
        self.assertEqual(calls, [('load_portfolio', ('p2',))])
        self.assertEqual(applied, [(portfolio, analysis)])

    def test_opening_portfolio_prepares_tools_once_after_busy_cleanup(self):
        calls, prepared = [], []
        portfolio = SimpleNamespace(get_id=lambda: 'p2')
        def call(name, *args):
            calls.append((name, args))
            return dict(ok=True, portfolio=portfolio, analysis=None)
        form = workspace_methods(lambda: None, call)
        form._current_portfolio = None
        form._confirm_discard = lambda: True
        form._set_busy = lambda busy: setattr(form, '_busy', busy)
        form._apply_portfolio = lambda row, analysis: setattr(form, '_current_portfolio', row)
        form.prepare_navigation = lambda: prepared.append(form._busy)
        self.assertTrue(form.open_profile_portfolio('p2'))
        self.assertTrue(form.open_profile_portfolio('p2'))
        self.assertEqual(calls, [('load_portfolio', ('p2',))])
        self.assertEqual(prepared, [False])

    def test_unavailable_portfolio_keeps_draft_and_skips_tool_preparation(self):
        prepared = []
        form = workspace_methods(lambda: None, lambda *a: dict(ok=False, message='Unavailable'))
        original = form._current_portfolio = SimpleNamespace(get_id=lambda: 'p1')
        form._confirm_discard = lambda: True
        form._set_busy = lambda busy: setattr(form, '_busy', busy)
        form._set_portfolio_status = lambda *a, **kw: None
        form.prepare_navigation = lambda: prepared.append(True)
        self.assertFalse(form.open_profile_portfolio('p2'))
        self.assertIs(form._current_portfolio, original)
        self.assertEqual(prepared, [])
        self.assertFalse(form._busy)

    def test_refresh_prepares_tools_after_success_but_skips_rejected_refresh(self):
        for success in (True, False):
            prepared, calls = [], []
            def call(name, *args):
                calls.append(name)
                return dict(ok=success, message='Check inputs',
                    portfolio=dict(name='Portfolio', as_of_date=date(2026, 6, 30), analysis_updated_at=None),
                    analysis=dict(metadata=dict(moved_transaction_dates=0)))
            form = workspace_methods(lambda: None, call)
            form.account_id = 'a'
            form._user = object()
            form._username = 'owner'
            form._autosaving = False
            form._autosave_generation = form._edit_revision = 1
            form._current_portfolio = SimpleNamespace(get_id=lambda: 'p1')
            form.autosave_timer = SimpleNamespace(interval=0)
            form.portfolio_name_box = SimpleNamespace(text='Portfolio')
            form.risk_free_box = SimpleNamespace(text='0')
            form.as_of_date_picker = SimpleNamespace(date=None)
            form._read_transactions = lambda: [dict(ticker='TEST', trade_date=date(2026, 1, 2),
                action='Buy', shares=1, trade_price=100)]
            form._parse_number = lambda *args: 0
            form._workspace_matches = lambda *args: True
            form._set_busy = lambda busy: setattr(form, '_busy', busy)
            for method in ('_set_status', '_set_portfolio_status', '_adopt_saved_portfolio',
                           '_apply_analysis', '_set_last_refreshed', '_apply_readout_page'):
                setattr(form, method, lambda *a, **kw: None)
            form.prepare_navigation = lambda: prepared.append((form._busy, form._refreshing))
            form.analyze_button_click()
            self.assertEqual(calls, ['refresh_portfolio'])
            self.assertEqual(prepared, [(False, False)] if success else [])

    def test_filter_edits_do_not_rebuild_every_hidden_view(self):
        form = workspace_methods(lambda: None)
        form._history = object()
        form._readout_page = 'overview'
        form._dirty_readout_pages = set()
        rendered = []
        form._render_overview = lambda: rendered.append('overview')
        form._page_charts = lambda page: []
        form._invalidate_allocation = lambda: None
        form._render_portfolio_summary = lambda: None
        form._update_readout_context = lambda: None
        form._render_readout()
        self.assertEqual(rendered, ['overview'])
        self.assertEqual(form._dirty_readout_pages, {'allocation', 'performance'})


class PreparedTaxTests(unittest.TestCase):
    def prepare(self, successful=True):
        h = Harness('TaxEstimator')
        source = analysis()
        source['planning_context']['tax_by_year']['2026']['positions']['TEST']['current_value'] = 1000
        h.responses['estimate_saved_trade_taxes'] = dict(ok=successful, message='Check income',
                                                       logged_realized=[], realized=[])
        h.form._render_result = lambda: None
        h.form.set_portfolio_context([{'ticker': 'TEST'}], '2026-06-30', 'p1', source)
        return h

    def test_startup_prepares_estimate_without_saving_inputs(self):
        h = self.prepare()
        h.plans[('a', 'p1', 'tax', 2026)] = {'input_mode': 'quick', 'annual_income': '70000'}
        h.form.redraw()
        result = h.form._result
        self.assertEqual([name for name, args in h.calls],
                         ['get_tax_estimator_catalog', 'get_planning_inputs', 'estimate_saved_trade_taxes'])
        self.assertEqual(h.form.income_box.text, '70000')
        h.form.holding_dropdown.selected_value = None
        for _ in range(5):
            h.form.redraw()
        self.assertEqual(len(h.calls), 3)
        self.assertIs(h.form._result, result)
        self.assertIsNone(h.form.holding_dropdown.selected_value)

    def test_invalid_saved_inputs_do_not_repeat_estimation_on_each_visit(self):
        h = self.prepare(successful=False)
        h.form.redraw()
        for _ in range(3):
            h.form.redraw()
        self.assertEqual(len(h.calls), 3)
        self.assertIsNone(h.form._result)

    def test_new_analysis_replaces_cached_holdings_context(self):
        h = self.prepare()
        h.form.redraw()
        source = analysis(value=2000)
        source['planning_context']['tax_by_year']['2026']['positions']['TEST']['current_value'] = 2000
        h.form.set_portfolio_context([{'ticker': 'TEST'}], '2026-07-01', 'p1', source)
        self.assertEqual(h.form._positions['TEST']['current_value'], 2000)
        self.assertIn('$2,000.00', h.form.synced_summary_label.text)


if __name__ == '__main__':
    unittest.main()
