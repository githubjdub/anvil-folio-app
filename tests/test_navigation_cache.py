"""Retained-page behavior with Anvil client boundaries replaced by small fakes."""
import ast
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
    def __init__(self, account_id='a'):
        self.user = User(account_id) if account_id else None
        self.route = {'page': 'portfolio'}
        self.active = None
        self.now = 100
        self.revision = 0
        self.opened = []
        self.instances = {}
        self.constructor_hook = None
        self.router = None
        harness = self

        class Template:
            def __init__(self, **properties):
                pass

        class Page:
            def __init__(self, **properties):
                self.account_id = harness.account_id()
                self.refresh_count = 1
                self.plan_comparison = SimpleNamespace(refresh_plan=self._refresh)
                harness.instances.setdefault(type(self).__name__, []).append(self)

            def _refresh(self):
                self.refresh_count += 1

            _refresh_details = _refresh

        class Editor(Page):
            def __init__(self, **properties):
                super().__init__(**properties)
                self._busy = False
                self._restore_on_resume = False
                self.resume_count = 0
                self.analysis = object()
                self.draft = ['unsaved trade']
                self.portfolio_id = 'p1'
                self.load_count = 0
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

            def open_profile_portfolio(self, portfolio_id):
                if self.portfolio_id != portfolio_id:
                    self.load_count += 1
                    self.portfolio_id = portfolio_id
                return True

            def open_notifications(self):
                pass

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
            invalidate_workspace=self.invalidate,
        )
        self.namespace = source_namespace(CLIENT / 'Router' / '__init__.py', namespace)
        self.router = self.namespace['Router']()

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

    def editors(self):
        return self.instances.get('Editor', [])


class RetainedNavigationTests(unittest.TestCase):
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
        self.assertEqual(editor.load_count, 1)
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

    def test_quick_visits_do_not_extend_metadata_expiry_forever(self):
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
        def call(name):
            self.assertEqual(name, 'get_portfolio_workspace')
            self.calls += 1
            if self.hook:
                hook, self.hook = self.hook, None
                hook()
            return {'request': self.calls}
        self.cache = source_namespace(CLIENT / 'workspace_cache.py', {
            'time': lambda: self.now,
            'anvil': SimpleNamespace(users=SimpleNamespace(get_user=lambda: self.user),
                                     server=SimpleNamespace(call=call)),
        })

    def test_cached_workspace_has_fixed_sixty_second_expiry(self):
        first = self.cache['account_workspace']()
        self.now += 40
        self.assertIs(self.cache['account_workspace'](), first)
        self.now += 21
        self.assertIsNot(self.cache['account_workspace'](), first)
        self.assertEqual(self.calls, 2)

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


if __name__ == '__main__':
    unittest.main()
