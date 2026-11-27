"""Retained-page behavior with Anvil client boundaries replaced by small fakes."""
import ast
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import unittest


CLIENT = Path(__file__).resolve().parents[1] / 'client_code'


def source_namespace(path, namespace):
    tree = ast.parse(path.read_text())
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    exec(compile(tree, str(path), 'exec'), namespace)
    return namespace


class User:
    def __init__(self, account_id):
        self.account_id = account_id

    def get_id(self):
        return self.account_id


class NavigationHarness:
    def __init__(self, account_id='a', auto_startup=True):
        self.user = User(account_id) if account_id else None
        self.route = {'page': 'portfolio'}
        self.active = None
        self.now = 100
        self.revision = 0
        self.opened = []
        self.instances = {}
        self.constructor_hook = None
        self.router = None
        self.pending = []
        self.startup_jobs = []
        self.startup_requests = []
        self.auto_startup = auto_startup
        harness = self

        class Request:
            def __init__(self, account_id, portfolio_id):
                self.account_id, self.portfolio_id = account_id, portfolio_id
                self.started = self.cancelled = False
                harness.startup_requests.append(self)

            def start(self, on_started):
                def start():
                    self.started = True
                    harness.startup_jobs.append(on_started)
                harness.startup_jobs.append(start)

            def cancel(self):
                self.cancelled = True

        class Template:
            def __init__(self, **properties):
                self.startup_status = SimpleNamespace(text='')

        class Page:
            def __init__(self, **properties):
                self.account_id = harness.account_id()
                self.refresh_count = 1
                self.plan_comparison = SimpleNamespace(refresh_plan=self._refresh,
                                                       set_billing_context=lambda workspace: None)
                self._billing_workspace = None
                harness.instances.setdefault(type(self).__name__, []).append(self)

            def _refresh(self):
                self.refresh_count += 1

            _refresh_details = _refresh

            def load_details(self):
                self._billing_workspace = {'account_id': harness.account_id()}

        class Editor(Page):
            def __init__(self, **properties):
                super().__init__(**properties)
                self.initial_portfolio_id = properties.get('initial_portfolio_id')
                self.startup_request = properties.get('startup_request')
                self._busy = False
                self._startup_loading = False
                self._restore_on_resume = False
                self.resume_count = 0
                self.analysis = object()
                self.draft = ['unsaved trade']
                self.portfolio_id = 'p1'
                if self.initial_portfolio_id is not None:
                    self.portfolio_id = self.initial_portfolio_id
                self.load_count = 0
                self.prepared_count = 0
                self.notifications_opened = 0
                if harness.constructor_hook:
                    hook = harness.constructor_hook
                    harness.constructor_hook = None
                    hook()

            def resume_account(self):
                self.resume_count += 1
                self.account_id = harness.account_id()
                self._restore_on_resume = False

            def apply_appearance(self):
                pass

            def prepare_navigation(self):
                if self.account_id is not None and not self.prepared_count:
                    self.prepared_count += 1

            def open_profile_portfolio(self, portfolio_id):
                if self.portfolio_id != portfolio_id:
                    self.load_count += 1
                    self.portfolio_id = portfolio_id
                return True

            def open_notifications(self):
                if not self._busy:
                    self.notifications_opened += 1

            def open_portfolio_page(self):
                pass

        pages = {name: type(name, (Page,), {}) for name in
                 ('ProfileHome', 'MySubscription', 'Subscribe', 'Disclosures', 'PublicPerformance')}
        namespace = dict(
            RouterTemplate=Template, Form1=Editor, **pages,
            handle=lambda *args: lambda fn: fn,
            time=lambda: self.now,
            anvil=SimpleNamespace(
                users=SimpleNamespace(get_user=lambda: self.user),
                js=SimpleNamespace(report_exceptions=lambda fn: fn)),
            get_url_hash=lambda: self.route,
            get_open_form=lambda: self.active,
            open_form=self.open_form,
            set_url_hash=self.navigate,
            record_page=lambda page: None,
            ensure_username=lambda: True,
            username_prompt_open=lambda: False,
            initialize=lambda: None,
            CACHE_SECONDS=60,
            workspace_revision=lambda: self.revision,
            StartupRequest=Request,
            invalidate_workspace=self.invalidate,
            defer=self.defer,
            startup_performance=SimpleNamespace(mark=lambda *a: None, complete=lambda: None),
        )
        self.namespace = source_namespace(CLIENT / 'Router' / '__init__.py', namespace)
        self.namespace['_create_page'] = lambda page, **props: (
            Editor if page == 'dashboard' else pages[{
                'profile': 'ProfileHome', 'subscription': 'MySubscription',
                'subscribe': 'Subscribe', 'disclosures': 'Disclosures',
                'shared': 'PublicPerformance'}[page]])(**props)
        self.router = self.namespace['Router']()

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

    def run_startup(self):
        while self.startup_jobs:
            self.startup_jobs.pop(0)()

    def account_id(self):
        return self.user.get_id() if self.user else None

    def invalidate(self):
        self.revision += 1

    def open_form(self, form):
        self.active = form
        self.opened.append(form)

    def navigate(self, route):
        self.route = route if isinstance(route, dict) else {'page': route}
        self.router._open_route()
        if self.auto_startup:
            self.run_startup()

    def editors(self):
        return self.instances.get('Editor', [])


class RetainedNavigationTests(unittest.TestCase):
    def test_values_request_starts_before_editor_and_optional_pages(self):
        h = NavigationHarness(auto_startup=False)
        h.navigate('portfolio')
        self.assertEqual(h.editors(), [])
        self.assertEqual(h.pending, [])
        self.assertEqual(len(h.startup_requests), 1)
        request = h.startup_requests[0]
        # Hash changes before the timer runs must not construct or refetch.
        h.navigate('portfolio')
        self.assertEqual(len(h.startup_requests), 1)
        self.assertEqual(h.editors(), [])
        h.run_startup()
        self.assertIs(h.active.startup_request, request)
        self.assertTrue(request.started)
        self.assertIsNone(h.router._startup_request)

    def test_latest_portfolio_replaces_queued_request_before_construction(self):
        h = NavigationHarness(auto_startup=False)
        h.navigate({'page': 'portfolio', 'portfolio': 'p1'})
        h.navigate({'page': 'portfolio', 'portfolio': 'p2'})
        self.assertTrue(h.startup_requests[0].cancelled)
        h.run_startup()
        self.assertEqual(len(h.editors()), 1)
        self.assertEqual(h.active.initial_portfolio_id, 'p2')
        self.assertIs(h.active.startup_request, h.startup_requests[1])

    def test_switching_to_profile_cancels_queued_editor(self):
        h = NavigationHarness(auto_startup=False)
        h.navigate('portfolio')
        h.navigate('profile')
        self.assertTrue(h.startup_requests[0].cancelled)
        h.run_startup()
        self.assertEqual(h.editors(), [])
        self.assertEqual(type(h.active).__name__, 'ProfileHome')

    def test_account_change_replaces_pending_private_request(self):
        h = NavigationHarness(auto_startup=False)
        h.navigate('portfolio')
        h.user = User('b')
        h.run_startup()
        self.assertTrue(h.startup_requests[0].cancelled)
        self.assertEqual(len(h.editors()), 1)
        self.assertEqual(h.active.account_id, 'b')
        self.assertEqual(h.active.startup_request.account_id, 'b')

    def test_startup_opens_editor_before_preparing_secondary_pages(self):
        h = NavigationHarness()
        h.navigate('portfolio')
        self.assertEqual(h.active.prepared_count, 1)
        self.assertEqual(h.router._page_forms, {})
        self.assertEqual(h.opened, [h.active])
        h.run_idle()
        self.assertEqual(set(h.router._page_forms), {'profile', 'subscription', 'subscribe', 'disclosures'})
        for page in ('profile', 'subscription', 'subscribe', 'disclosures', 'portfolio'):
            h.navigate(page)
        self.assertEqual(h.active.prepared_count, 1)
        self.assertEqual(h.active.resume_count, 0)

    def test_profile_first_route_does_not_construct_portfolio_editor(self):
        h = NavigationHarness()
        h.navigate('profile')
        self.assertEqual(h.editors(), [])
        h.run_idle()
        self.assertEqual(h.editors(), [])
        self.assertEqual(type(h.active).__name__, 'ProfileHome')

    def test_queued_private_pages_are_discarded_after_logout(self):
        h = NavigationHarness()
        h.navigate('portfolio')
        h.user = None
        h.navigate('disclosures')
        h.run_idle()
        self.assertEqual(set(h.router._page_forms), {'disclosures'})

    def test_background_preparation_does_not_duplicate_a_page_opened_early(self):
        h = NavigationHarness()
        h.navigate('portfolio')
        h.navigate('profile')
        profile = h.active
        h.run_idle()
        self.assertIs(h.active, profile)
        self.assertEqual(len(h.instances['ProfileHome']), 1)

    def test_route_changed_during_startup_is_applied_when_data_is_ready(self):
        h = NavigationHarness()
        h.navigate('portfolio')
        editor = h.active
        editor._startup_loading = editor._busy = True
        h.navigate({'page': 'portfolio', 'portfolio': 'p2'})
        self.assertEqual(editor.load_count, 0)
        editor._startup_loading = editor._busy = False
        h.router._editor_ready(editor)
        self.assertIs(h.active, editor)
        self.assertEqual(editor.portfolio_id, 'p2')
        self.assertEqual(editor.load_count, 1)

    def test_notification_link_waits_for_core_data_then_opens_inbox(self):
        h = NavigationHarness()
        h.navigate('portfolio')
        editor = h.active
        editor._startup_loading = editor._busy = True
        h.navigate('notifications')
        self.assertEqual(editor.notifications_opened, 0)
        editor._startup_loading = editor._busy = False
        h.router._editor_ready(editor)
        self.assertEqual(editor.notifications_opened, 1)

    def test_public_and_guest_landing_pages_do_not_preload_private_workspace(self):
        for route in ({'share': 'public-token'}, {'page': 'subscribe'}, {'page': 'disclosures'}):
            h = NavigationHarness()
            h.navigate(route)
            self.assertEqual(h.editors(), [])
        h = NavigationHarness(account_id=None)
        h.navigate('portfolio')
        self.assertEqual(h.active.prepared_count, 0)
        self.assertEqual(h.router._page_forms, {})

    def test_portfolio_analysis_and_draft_survive_all_account_pages(self):
        h = NavigationHarness()
        h.navigate('portfolio')
        editor = h.active
        analysis = editor.analysis
        for page in ('profile', 'subscription', 'disclosures', 'subscribe', 'portfolio'):
            h.navigate(page)
        self.assertIs(h.active, editor)
        self.assertIs(editor.analysis, analysis)
        self.assertEqual(editor.draft, ['unsaved trade'])
        self.assertEqual(len(h.editors()), 1)

    def test_account_pages_reuse_instances_without_repeating_refresh(self):
        h = NavigationHarness()
        for page in ('profile', 'subscription', 'subscribe', 'disclosures'):
            h.navigate(page)
            original = h.active
            h.navigate('portfolio')
            h.navigate(page)
            self.assertIs(h.active, original)
            self.assertEqual(original.refresh_count, 1)

    def test_same_portfolio_id_does_not_reload(self):
        h = NavigationHarness()
        h.navigate({'page': 'portfolio', 'portfolio': 'p2'})
        editor = h.active
        h.navigate('profile')
        h.navigate({'page': 'portfolio', 'portfolio': 'p2'})
        self.assertEqual(editor.initial_portfolio_id, 'p2')
        self.assertEqual(editor.load_count, 0)
        self.assertEqual(len(h.editors()), 1)

    def test_invalidation_refreshes_metadata_without_resetting_portfolio(self):
        h = NavigationHarness()
        h.navigate('portfolio')
        editor = h.active
        h.navigate('profile')
        h.invalidate()
        h.navigate('portfolio')
        self.assertEqual(editor.resume_count, 1)
        self.assertEqual(editor.draft, ['unsaved trade'])
        self.assertIs(h.active, editor)

    def test_profile_mutation_invalidates_other_retained_pages(self):
        h = NavigationHarness()
        h.navigate('profile')
        profile = h.active
        h.navigate('portfolio')
        h.invalidate()
        h.navigate('profile')
        self.assertIs(h.active, profile)
        self.assertEqual(profile.refresh_count, 2)

    def test_route_cache_is_bounded_to_one_editor_and_four_account_pages(self):
        h = NavigationHarness()
        for _ in range(3):
            for page in ('portfolio', 'profile', 'subscription', 'subscribe', 'disclosures'):
                h.navigate(page)
        self.assertEqual(len(h.editors()), 1)
        self.assertEqual(len(h.router._page_forms), 4)
        self.assertEqual(sum(len(forms) for forms in h.instances.values()), 5)

    def test_expired_pages_wait_for_new_metadata_without_blocking_navigation(self):
        h = NavigationHarness()
        h.navigate('profile')
        profile = h.active
        h.now += 40
        h.navigate('disclosures')
        h.navigate('profile')
        self.assertEqual(profile.refresh_count, 1)
        h.now += 25
        h.navigate('portfolio')
        h.navigate('profile')
        self.assertEqual(profile.refresh_count, 1)
        # The asynchronous cache refresh publishes a new revision on completion.
        h.invalidate()
        h.navigate('portfolio')
        h.navigate('profile')
        self.assertEqual(profile.refresh_count, 2)

    def test_signed_in_account_change_discards_every_private_form(self):
        h = NavigationHarness()
        h.navigate('portfolio')
        editor_a = h.active
        h.navigate('profile')
        profile_a = h.active
        h.user = User('b')
        h.navigate('portfolio')
        self.assertIsNot(h.active, editor_a)
        self.assertEqual(h.active.account_id, 'b')
        h.navigate('profile')
        self.assertIsNot(h.active, profile_a)

    def test_logout_then_other_login_cannot_inherit_old_draft(self):
        h = NavigationHarness()
        h.navigate('portfolio')
        editor_a = h.active
        h.user = None
        h.navigate('profile')
        h.user = User('b')
        h.navigate('portfolio')
        self.assertIsNot(h.active, editor_a)
        self.assertEqual(h.active.account_id, 'b')

    def test_guest_can_keep_their_draft_after_signing_in(self):
        h = NavigationHarness(account_id=None)
        h.navigate('portfolio')
        guest = h.active
        h.user = User('a')
        h.navigate('profile')
        h.navigate('portfolio')
        self.assertIs(h.active, guest)
        self.assertEqual(guest.account_id, 'a')
        self.assertEqual(guest.draft, ['unsaved trade'])

    def test_rapid_route_changes_do_not_construct_duplicate_editors(self):
        h = NavigationHarness()
        h.constructor_hook = lambda: (h.navigate('profile'), h.navigate('portfolio'))
        h.navigate('portfolio')
        self.assertEqual(len(h.editors()), 1)
        self.assertIs(h.active, h.editors()[0])
        self.assertEqual(h.instances.get('ProfileHome', []), [])

    def test_account_change_during_constructor_never_opens_stale_editor(self):
        h = NavigationHarness()
        def switch_account():
            h.user = User('b')
            h.navigate('portfolio')
        h.constructor_hook = switch_account
        h.navigate('portfolio')
        self.assertEqual(len(h.editors()), 2)
        self.assertEqual(h.active.account_id, 'b')
        self.assertNotIn(h.editors()[0], h.opened)


class WorkspaceCacheTests(unittest.TestCase):
    def setUp(self):
        self.user = User('a')
        self.now = 100
        self.calls = 0
        self.hook = None
        self.pending = []
        def call(name, *args):
            self.assertIn(name, ('get_portfolio_workspace', 'get_portfolio_startup'))
            self.calls += 1
            response = {'request': self.calls}
            if self.hook:
                hook, self.hook = self.hook, None
                hook()
            return {'ok': True, 'workspace': response, 'portfolio': args[0], 'overview': None} if name == 'get_portfolio_startup' else response
        self.cache = source_namespace(CLIENT / 'workspace_cache.py', {
            'time': lambda: self.now,
            'startup_performance': SimpleNamespace(mark=lambda name: None),
            'anvil': SimpleNamespace(users=SimpleNamespace(get_user=lambda: self.user),
                                     server=SimpleNamespace(call=call, call_s=call,
                                                            no_loading_indicator=nullcontext()),
                                     js=SimpleNamespace(report_exceptions=lambda fn: fn,
                                        window=SimpleNamespace(setTimeout=lambda fn, ms: self.pending.append(fn)))),
        })

    def test_slow_startup_allows_page_construction_while_request_is_pending(self):
        request = self.cache['StartupRequest']('a', 'p1')
        events = []
        def build():
            self.assertTrue(request.started)
            self.assertFalse(request.done)
            events.append('page built during request')
            request.when_ready(lambda: events.append(request.result['portfolio']))
        request.start(build)
        self.hook = lambda: self.pending.pop(0)()
        self.pending.pop(0)()
        self.assertEqual(events, ['page built during request'])
        self.assertTrue(request.done)
        self.pending.pop(0)()
        self.assertEqual(events, ['page built during request', 'p1'])
        self.assertIs(self.cache['account_workspace'](), request.result['workspace'])
        self.assertEqual(self.calls, 1)

    def test_fast_startup_keeps_result_until_page_is_ready(self):
        request = self.cache['StartupRequest']('a', 'p1')
        delivered = []
        request.start(lambda: request.when_ready(lambda: delivered.append(request.result)))
        self.pending.pop(0)()
        self.assertTrue(request.done)
        self.assertEqual(delivered, [])
        self.pending.pop(0)()
        self.assertEqual(delivered, [])
        self.pending.pop(0)()
        self.assertEqual(delivered, [request.result])
        self.assertEqual(self.calls, 1)

    def test_cancelled_queued_startup_does_not_make_a_request(self):
        request = self.cache['StartupRequest']('a', 'p1')
        request.start(lambda: None)
        request.cancel()
        self.pending.pop(0)()
        self.assertEqual(self.calls, 0)
        self.assertIsNone(request.result)

    def test_account_change_before_prefetch_does_not_request_other_accounts_values(self):
        request = self.cache['StartupRequest']('a', 'p1')
        request.start(lambda: None)
        self.user = User('b')
        self.pending.pop(0)()
        self.assertEqual(self.calls, 0)
        self.assertIsNone(request.result)

    def test_cancelled_inflight_startup_discards_response(self):
        request = self.cache['StartupRequest']('a', 'p1')
        request.start(lambda: None)
        self.hook = request.cancel
        self.pending.pop(0)()
        self.assertEqual(self.calls, 1)
        self.assertIsNone(request.result)

    def test_prefetch_errors_propagate_and_still_notify_waiting_form(self):
        request = self.cache['StartupRequest']('a', 'p1')
        settled = []
        request.start(lambda: None)
        request.when_ready(lambda: settled.append(request.succeeded))
        def fail():
            raise RuntimeError('Unexpected startup transport failure')
        self.hook = fail
        with self.assertRaisesRegex(RuntimeError, 'Unexpected startup transport failure'):
            self.pending.pop(0)()
        self.assertTrue(request.done)
        self.assertFalse(request.succeeded)
        while self.pending:
            self.pending.pop(0)()
        self.assertEqual(settled, [False])

    def test_expired_workspace_returns_immediately_and_refreshes_once(self):
        first = self.cache['account_workspace']()
        self.now += 40
        self.assertIs(self.cache['account_workspace'](), first)
        self.now += 21
        for _ in range(5):
            self.assertIs(self.cache['account_workspace'](), first)
        self.assertEqual(self.calls, 1)
        self.assertEqual(len(self.pending), 1)
        revision = self.cache['workspace_revision']()
        self.pending.pop(0)()
        self.assertIsNot(self.cache['account_workspace'](), first)
        self.assertEqual(self.calls, 2)
        self.assertGreater(self.cache['workspace_revision'](), revision)

    def test_expired_account_response_cannot_replace_explicit_refresh(self):
        self.cache['account_workspace']()
        self.now += 61
        self.cache['account_workspace']()
        self.hook = lambda: self.cache['account_workspace'](force=True)
        self.pending.pop(0)()
        self.assertEqual(self.calls, 3)
        self.assertEqual(self.cache['_workspace']['request'], 3)

    def test_scheduled_refresh_is_discarded_after_logout(self):
        self.cache['account_workspace']()
        self.now += 61
        self.cache['account_workspace']()
        self.user = None
        self.pending.pop(0)()
        self.assertEqual(self.calls, 1)
        self.assertIsNone(self.cache['account_workspace']())

    def test_background_error_keeps_previous_snapshot_and_propagates(self):
        first = self.cache['account_workspace']()
        self.now += 61
        self.cache['account_workspace']()
        def fail():
            raise RuntimeError('unexpected read failure')
        self.hook = fail
        with self.assertRaisesRegex(RuntimeError, 'unexpected read failure'):
            self.pending.pop(0)()
        self.assertIs(self.cache['_workspace'], first)
        self.assertIsNone(self.cache['_refresh_pending'])

    def test_invalidated_inflight_response_is_not_cached(self):
        self.hook = self.cache['invalidate_workspace']
        self.assertIsNone(self.cache['account_workspace']())
        self.assertIsNone(self.cache['_workspace'])
        self.assertEqual(self.cache['account_workspace']()['request'], 2)

    def test_response_for_previous_account_is_rejected(self):
        self.hook = lambda: setattr(self, 'user', User('b'))
        self.assertIsNone(self.cache['account_workspace']())
        self.assertIsNone(self.cache['_workspace'])
        self.assertEqual(self.cache['account_workspace']()['request'], 2)

    def test_force_refresh_invalidates_an_older_inflight_response(self):
        newest = []
        self.hook = lambda: newest.append(self.cache['account_workspace'](force=True))
        self.assertIsNone(self.cache['account_workspace']())
        self.assertIs(self.cache['_workspace'], newest[0])
        self.assertEqual(self.calls, 2)

    def test_guest_cannot_read_previous_account_workspace(self):
        self.cache['account_workspace']()
        revision = self.cache['workspace_revision']()
        self.user = None
        self.assertIsNone(self.cache['account_workspace']())
        self.assertGreater(self.cache['workspace_revision'](), revision)
        self.assertIsNone(self.cache['_workspace'])

    def test_startup_populates_metadata_cache_without_a_second_request(self):
        result = self.cache['startup_workspace']('p1')
        self.assertEqual(result['portfolio'], 'p1')
        self.assertIs(self.cache['account_workspace'](), result['workspace'])
        self.assertEqual(self.calls, 1)

    def test_startup_does_not_replace_newer_account_metadata(self):
        newest = []
        self.hook = lambda: newest.append(self.cache['account_workspace'](force=True))
        result = self.cache['startup_workspace']('p1')
        self.assertEqual(result['workspace']['request'], 1)
        self.assertIs(self.cache['account_workspace'](), newest[0])
        self.assertEqual(self.calls, 2)

    def test_startup_rejects_values_after_account_change(self):
        self.hook = lambda: setattr(self, 'user', User('b'))
        self.assertIsNone(self.cache['startup_workspace']('p1'))
        self.assertIsNone(self.cache['_workspace'])

    def test_guest_startup_never_requests_private_values(self):
        self.user = None
        self.assertIsNone(self.cache['startup_workspace']())
        self.assertEqual(self.calls, 0)


if __name__ == '__main__':
    unittest.main()
