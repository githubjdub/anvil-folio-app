"""Navigation and planner integration against the real workspace methods."""
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
import unittest

from test_navigation_cache import NavigationHarness
from test_portfolio_workspace import WorkspaceHarness

APP = Path(__file__).resolve().parents[1]


class NavigationDestinations(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.nav = None
        self.destinations = {}
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'nav':
            self.nav = attrs.get('aria-label')
        name = attrs.get('name')
        if name in ('profile_button', 'notifications_tab_button'):
            self.destinations.setdefault(name, []).append(self.nav)

    def handle_endtag(self, tag):
        if tag == 'nav':
            self.nav = None


class PortfolioLandingTests(unittest.TestCase):
    def test_empty_route_opens_portfolio_for_authenticated_and_guest_users(self):
        for account in ('a', None):
            with self.subTest(account=account):
                h = NavigationHarness(account)
                h.route = {}
                h.router._open_route()
                h.run_startup()
                self.assertEqual(h.router._route_key(), ('dashboard', None))
                self.assertIs(h.active, h.editors()[0])

    def test_explicit_profile_link_remains_available(self):
        h = NavigationHarness()
        h.navigate('profile')
        self.assertEqual(type(h.active).__name__, 'ProfileHome')

    def test_profile_and_notifications_exist_once_in_header_navigation(self):
        markup = (APP / 'client_code/Form1/form_template.html').read_text()
        parsed = NavigationDestinations(markup)
        self.assertEqual(parsed.destinations, {
            'profile_button': ['Main navigation'],
            'notifications_tab_button': ['Main navigation'],
        })

    def test_header_notifications_preserves_portfolio_and_uses_header_active_state(self):
        h = WorkspaceHarness()
        source = h.form._analysis_source
        h.form.notifications_tab_click()
        self.assertIs(h.form._analysis_source, source)
        self.assertEqual(h.form.notifications_tab_button.role, 'masthead-tab-active')
        self.assertEqual(h.form.portfolio_header_link.attrs['aria-current'], 'false')
        h.form.overview_tab_click()
        self.assertEqual(h.form.notifications_tab_button.role, 'masthead-tab')
        self.assertEqual(h.form.portfolio_header_link.attrs['aria-current'], 'page')
        self.assertEqual(h.calls, [])

    def test_portfolio_header_restores_previous_portfolio_tool_after_notifications(self):
        h = WorkspaceHarness()
        h.form.performance_tab_click()
        h.form.notifications_tab_click()
        h.form.open_portfolio_page()
        self.assertEqual(h.form._readout_page, 'performance')
        self.assertEqual(h.calls, [])

    def test_signin_already_on_portfolio_restores_saved_work_but_preserves_guest_drafts(self):
        for dirty in (True, False):
            with self.subTest(dirty=dirty):
                h = WorkspaceHarness(saved=False)
                h.form._user = None
                h.form._has_unsaved_work = lambda: dirty
                restored, routes = [], []
                h.form._restore_last_portfolio = lambda: restored.append(True)
                h.namespace['set_url_hash'] = routes.append
                h.form.account_button_click()
                self.assertEqual(restored, [] if dirty else [True])
                self.assertEqual(routes, [{'page': 'portfolio'}])
                self.assertFalse(h.form._restore_on_resume)


class LatestSyncedContextTests(unittest.TestCase):
    def harness(self):
        h = WorkspaceHarness()
        h.source = {'metrics': {'market_value': 76244.12},
                    'metadata': {'end_date': '2026-11-05'}}
        h.form._last_synced_analysis = h.source
        h.form._last_synced_transactions = [dict(h.form.transactions[0])]
        # A table filter or an unsynced draft must not change planner inputs.
        h.form._history = {'metrics': {'market_value': 15},
                           'metadata': {'end_date': '2026-10-01'}}
        h.form._selected_symbols = set()
        h.contexts = []
        h.form.retirement_form = SimpleNamespace(
            set_portfolio_context=lambda *a, **k: h.contexts.append(('retirement', a, k)),
            set_starting_balance=lambda *a: h.contexts.append(('balance', a)),
        )
        h.form.tax_estimator_form = SimpleNamespace(
            set_portfolio_context=lambda *a, **k: h.contexts.append(('tax', a, k)),
        )
        h.form.income_form = SimpleNamespace(set_portfolio_context=lambda *a, **k: None)
        return h

    def test_retirement_uses_whole_latest_synced_value_and_saved_portfolio_scope(self):
        h = self.harness()
        type(h.form)._update_retirement_context(h.form)
        self.assertEqual(h.contexts, [('retirement', (76244.12, '2026-11-05'), {'portfolio_id': 'p1'})])
        self.assertEqual(h.calls, [])

    def test_tax_receives_latest_snapshot_and_copied_transactions_despite_empty_selection(self):
        h = self.harness()
        type(h.form)._update_tax_estimator_context(h.form)
        tool, args, kwargs = h.contexts[0]
        self.assertEqual(tool, 'tax')
        self.assertEqual(args[0], h.form._last_synced_transactions)
        self.assertEqual(args[1], '2026-11-05')
        self.assertIs(kwargs['analysis'], h.source)
        self.assertEqual(kwargs['portfolio_id'], 'p1')
        self.assertEqual(h.calls, [])

    def test_use_portfolio_button_also_uses_whole_latest_sync(self):
        h = self.harness()
        h.form.retirement_use_portfolio()
        self.assertEqual(h.contexts[0], ('balance', (76244.12, '2026-11-05')))

    def test_no_synced_portfolio_sends_empty_context_without_loading_prices(self):
        h = self.harness()
        h.form._last_synced_analysis = None
        h.form._last_synced_transactions = []
        type(h.form)._update_retirement_context(h.form)
        type(h.form)._update_tax_estimator_context(h.form)
        self.assertEqual(h.contexts[0], ('retirement', (None, None), {'portfolio_id': 'p1'}))
        self.assertEqual(h.contexts[1], ('tax', ([], None), {'portfolio_id': 'p1', 'analysis': None}))
        self.assertEqual(h.calls, [])

    def test_analysis_captures_editable_transactions_before_editor_rows_are_enriched(self):
        h = WorkspaceHarness()
        h.form.analyze_button_click()
        synced = h.form._last_synced_transactions
        self.assertEqual(set(synced[0]), {'ticker', 'trade_date', 'action', 'shares', 'trade_price'})
        self.assertIs(h.form._last_synced_analysis, h.result)
        h.form.transactions[0]['ticker'] = 'CHANGED'
        self.assertEqual(synced[0]['ticker'], 'TEST')
        h.form._show_results(False)
        self.assertIs(h.form._last_synced_analysis, h.result)
        self.assertEqual(synced[0]['ticker'], 'TEST')


if __name__ == '__main__':
    unittest.main()
