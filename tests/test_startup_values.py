"""Saved values appear before history; stale or failed reads cannot enable a partial analysis."""
import ast
import copy
import runpy
import unittest

from test_portfolio_workspace import APP, Control, Names, Portfolio, User, WorkspaceHarness


class StartupValuesTests(unittest.TestCase):
    def ready(self):
        h = WorkspaceHarness(defer_load=True, transaction_render=True)
        portfolio = Portfolio('p1')
        portfolio.update(risk_free_rate=4.5, transactions=[dict(
            ticker='TEST', trade_date='2026-01-02', action='Buy', shares=5, trade_price=185)])
        overview = dict(summary={}, holdings={}, metrics=h.result['metrics'], metadata=h.result['metadata'])
        h.startup_result = dict(ok=True, portfolio=portfolio, overview=overview)
        h.history_result = dict(ok=True, portfolio=portfolio, analysis=h.result, holdings=overview['holdings'])
        return h

    def show_values(self, h):
        h.form.form_show()
        h.pending.pop(0)[0]()

    def test_saved_values_are_visible_before_history_request_or_editor_work(self):
        h = self.ready()
        self.show_values(h)
        self.assertEqual(h.calls, [('get_portfolio_startup', (None,))])
        self.assertTrue(h.form.workspace_region.visible)
        self.assertTrue(h.form.results_section.visible)
        self.assertTrue(h.form.overview_panel.visible)
        self.assertFalse(h.form.startup_region.visible)
        self.assertIs(h.form._startup_overview, h.startup_result['overview'])
        self.assertIsNone(h.form._history)
        self.assertTrue(h.form._busy)
        self.assertTrue(h.form._startup_loading)
        self.assertIn('overview', h.rendered)
        self.assertEqual(h.form.transaction_years_panel.items_assignments, 0)
        self.assertIn('Loading chart history', h.form.readout_tab_context.text)
        resets = h.form.portfolio_holdings_form.reset_count
        # A repeated show event cannot schedule another startup/history load.
        h.form.form_show()
        h.pending.pop(0)[0]()
        self.assertEqual([name for name, args in h.calls], ['get_portfolio_startup', 'get_portfolio_history'])
        self.assertIsNone(h.form._startup_overview)
        self.assertEqual(h.form._history, h.result)
        self.assertFalse(h.form._startup_loading)
        self.assertFalse(h.form._busy)
        self.assertTrue(h.form.results_section.visible)
        self.assertEqual(h.form.portfolio_holdings_form.reset_count, resets)
        self.assertIs(h.form.portfolio_holdings_form.contexts[-1][1]['snapshot'], h.history_result['holdings'])
        h.run_idle()
        self.assertEqual(h.form.transaction_years_panel.items_assignments, 1)

    def test_linked_portfolio_is_requested_directly(self):
        h = self.ready()
        h.form._initial_portfolio_id = 'p1'
        self.show_values(h)
        self.assertEqual(h.calls, [('get_portfolio_startup', ('p1',))])

    def test_empty_account_finishes_without_history_request(self):
        h = WorkspaceHarness(defer_load=True)
        self.show_values(h)
        self.assertEqual(len(h.calls), 1)
        self.assertFalse(h.form._startup_loading)
        self.assertFalse(h.form._busy)
        self.assertEqual(h.form._readout_page, 'transactions')

    def test_saved_inputs_without_analysis_open_the_editor(self):
        h = self.ready()
        h.startup_result['overview'] = None
        self.show_values(h)
        self.assertEqual(len(h.calls), 1)
        self.assertIsNone(h.form._history)
        self.assertFalse(h.form._busy)
        self.assertEqual(h.form._readout_page, 'transactions')
        self.assertEqual(h.form.transactions[0]['ticker'], 'TEST')

    def test_unavailable_link_displays_expected_error_and_releases_controls(self):
        h = self.ready()
        h.startup_result = dict(ok=False, message='This portfolio is no longer available to your account.')
        self.show_values(h)
        self.assertEqual(h.portfolio_statuses[-1], (h.startup_result['message'], True))
        self.assertIsNone(h.form._history)
        self.assertFalse(h.form._busy)
        self.assertEqual(len(h.calls), 1)

    def test_account_change_during_values_request_does_not_apply_values(self):
        h = self.ready()
        h.startup_hook = lambda: setattr(h, 'user', User('b'))
        self.show_values(h)
        self.assertIsNone(h.form._current_portfolio)
        self.assertIsNone(h.form._startup_overview)
        self.assertEqual(h.account_reads, 0)

    def test_account_change_before_history_skips_request_and_clears_snapshot(self):
        h = self.ready()
        self.show_values(h)
        h.user = User('b')
        h.pending.pop(0)[0]()
        self.assertEqual(len(h.calls), 1)
        self.assertIsNone(h.form._history)
        self.assertIsNone(h.form._startup_overview)
        self.assertFalse(h.form.results_section.visible)

    def test_account_change_during_history_discards_response(self):
        h = self.ready()
        self.show_values(h)
        h.history_hook = lambda: setattr(h, 'user', User('b'))
        h.pending.pop(0)[0]()
        self.assertIsNone(h.form._history)
        self.assertIsNone(h.form._startup_overview)
        self.assertFalse(h.form._busy)

    def test_new_portfolio_while_history_is_pending_cannot_be_replaced(self):
        h = self.ready()
        self.show_values(h)
        replacement = Portfolio('p2')
        replacement.update(risk_free_rate=3, transactions=[])
        h.history_hook = lambda: h.form._apply_portfolio(replacement)
        h.pending.pop(0)[0]()
        self.assertIs(h.form._current_portfolio, replacement)
        self.assertIsNone(h.form._history)

    def test_deleted_portfolio_clears_values_and_opens_new_workspace(self):
        h = self.ready()
        self.show_values(h)
        h.history_result = dict(ok=False, message='This portfolio is no longer available to your account.')
        h.pending.pop(0)[0]()
        self.assertIsNone(h.form._current_portfolio)
        self.assertIsNone(h.form._startup_overview)
        self.assertFalse(h.form._busy)
        self.assertTrue(h.portfolio_statuses[-1][1])

    def test_inputs_changed_elsewhere_replace_snapshot_with_current_editor(self):
        h = self.ready()
        self.show_values(h)
        h.history_result['analysis'] = None
        h.history_result['portfolio']['transactions'][0]['shares'] = 7
        h.pending.pop(0)[0]()
        self.assertIsNone(h.form._startup_overview)
        self.assertIsNone(h.form._history)
        self.assertFalse(h.form._busy)
        self.assertEqual(h.form._readout_page, 'transactions')
        self.assertEqual(h.form._read_transactions()[0]['shares'], 7)

    def test_unexpected_history_error_propagates_and_removes_partial_analysis(self):
        h = self.ready()
        self.show_values(h)
        h.history_error = RuntimeError('History transport failure')
        with self.assertRaisesRegex(RuntimeError, 'History transport failure'):
            h.pending.pop(0)[0]()
        self.assertIsNone(h.form._startup_overview)
        self.assertIsNone(h.form._history)
        self.assertFalse(h.form._busy)
        self.assertFalse(h.form._startup_loading)
        self.assertFalse(h.form.results_section.visible)
        self.assertNotIn('Loading chart history', h.statuses[-1][0])


class HoldingsSnapshotTests(unittest.TestCase):
    def fixture(self):
        from test_holdings_dashboard import point, source
        functions = runpy.run_path(str(APP / 'client_code/holdings_dashboard.py'))
        path = APP / 'client_code/Components/PortfolioHoldings/__init__.py'
        names = Names(path.with_name('form_template.html').read_text())
        class Template:
            def __init__(self, **properties):
                for name in names.names:
                    setattr(self, name, Control())
        tree = ast.parse(path.read_text())
        tree.body = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        namespace = dict(functions, PortfolioHoldingsTemplate=Template,
                         handle=lambda *args: lambda fn: fn)
        exec(compile(tree, str(path), 'exec'), namespace)
        form = namespace['PortfolioHoldings']()
        analysis = source([point(100, 1), point(100, 2), point(110, 2), point(110, 2)])
        snapshot = functions['build_dashboard'](analysis)
        return form, analysis, snapshot, namespace

    def test_snapshot_filters_work_and_full_history_restores_quarter_navigation(self):
        form, analysis, snapshot, namespace = self.fixture()
        form.set_startup_snapshot(snapshot)
        self.assertFalse(form.quarter_dropdown.enabled)
        self.assertTrue(form.dashboard_region.visible)
        self.assertEqual(form.value_label.text, '$110.00')
        self.assertEqual(len(form.holdings_panel.items), 1)
        form.search_box.text = 'missing'
        form.table_filter_change()
        self.assertEqual(form.holdings_panel.items, [])
        form.search_box.text = ''
        form.table_filter_change()
        preview_rows = form.holdings_panel.items
        form.set_analysis(analysis)
        form.redraw()
        self.assertTrue(form.quarter_dropdown.enabled)
        self.assertEqual(form.holdings_panel.items, preview_rows)

    def test_history_reuses_visible_rows_and_does_not_recalculate_latest_quarter(self):
        form, analysis, snapshot, namespace = self.fixture()
        form.set_startup_snapshot(snapshot)
        rows = form.holdings_panel.items
        assignments = form.holdings_panel.items_assignments
        activity_assignments = form.activity_panel.items_assignments
        def forbidden(*args, **kwargs):
            raise AssertionError('Latest snapshot was recalculated in the browser')
        namespace['build_dashboard'] = forbidden
        namespace['quarter_periods'] = forbidden
        form.set_analysis(analysis, snapshot=copy.deepcopy(snapshot))
        form.redraw()
        self.assertIs(form.holdings_panel.items, rows)
        self.assertEqual(form.holdings_panel.items_assignments, assignments)
        self.assertEqual(form.activity_panel.items_assignments, activity_assignments)
        self.assertTrue(form.quarter_dropdown.enabled)
        self.assertEqual(len(form.quarter_dropdown.items), len(snapshot['periods']))

    def test_changed_history_replaces_rows_without_losing_filters(self):
        form, analysis, snapshot, namespace = self.fixture()
        form.set_startup_snapshot(snapshot)
        form.search_box.text = 'Technology'
        form.table_filter_change()
        updated = copy.deepcopy(snapshot)
        updated['rows'][0]['sector'] = 'Industrials'
        assignments = form.holdings_panel.items_assignments
        form.set_analysis(analysis, snapshot=updated)
        form.redraw()
        self.assertEqual(form.search_box.text, 'Technology')
        self.assertEqual(form.holdings_panel.items, [])
        self.assertEqual(form.holdings_panel.items_assignments, assignments + 1)

    def test_other_quarters_still_calculate_from_complete_history(self):
        form, analysis, snapshot, namespace = self.fixture()
        form.set_startup_snapshot(snapshot)
        form.set_analysis(analysis, snapshot=copy.deepcopy(snapshot))
        form.redraw()
        form.quarter_dropdown.selected_value = snapshot['periods'][0]['key']
        form.quarter_change()
        self.assertEqual(form._snapshot, namespace['build_dashboard'](analysis, quarter_key=snapshot['periods'][0]['key']))
        self.assertEqual(form.value_label.text, '$100.00')


if __name__ == '__main__':
    unittest.main()
