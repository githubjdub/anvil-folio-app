"""Deferred preparation yields between jobs and never hides unexpected errors."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

APP = Path(__file__).resolve().parents[1]


class QuietCalls:
    def __init__(self):
        self.depth = 0

    @property
    def active(self):
        return self.depth > 0

    def __enter__(self):
        self.depth += 1

    def __exit__(self, *args):
        self.depth -= 1


class DeferredWorkTests(unittest.TestCase):
    def setUp(self):
        self.timers = []
        self.quiet = QuietCalls()
        namespace = dict(anvil=SimpleNamespace(
            js=SimpleNamespace(report_exceptions=lambda fn: fn,
                window=SimpleNamespace(setTimeout=lambda fn, delay: self.timers.append((delay, fn)))),
            server=SimpleNamespace(no_loading_indicator=self.quiet)))
        path = APP / 'client_code/deferred_work.py'
        tree = ast.parse(path.read_text())
        tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
        exec(compile(tree, str(path), 'exec'), namespace)
        self.defer = namespace['defer']

    def tick(self):
        index = min(range(len(self.timers)), key=lambda i: self.timers[i][0])
        self.timers.pop(index)[1]()

    def test_first_page_can_open_before_any_background_work(self):
        events = []
        self.defer(lambda: events.append('warm page'))
        events.append('page visible')
        self.assertEqual(events, ['page visible'])
        self.tick()
        self.assertEqual(events, ['page visible', 'warm page'])

    def test_each_job_gets_a_separate_turn_and_can_queue_more_work(self):
        events = []
        def first():
            events.append('first')
            self.assertTrue(self.quiet.active)
            self.defer(lambda: events.append('third'))
            self.assertEqual(self.timers, [])
        self.defer(first)
        self.defer(lambda: events.append('second'))
        self.assertEqual(len(self.timers), 1)
        self.tick()
        self.assertEqual(events, ['first'])
        self.assertFalse(self.quiet.active)
        self.tick()
        self.assertEqual(events, ['first', 'second'])
        self.tick()
        self.assertEqual(events, ['first', 'second', 'third'])
        self.assertEqual(self.timers, [])

    def test_core_portfolio_load_takes_priority_over_secondary_pages(self):
        events = []
        self.defer(lambda: events.append('secondary'))
        self.defer(lambda: events.append('portfolio'), priority=True)
        self.tick()
        self.assertEqual(events, ['portfolio'])

    def test_obsolete_account_or_snapshot_is_not_prepared(self):
        events = []
        current = [True]
        self.defer(lambda: events.append('old snapshot'), lambda: current[0])
        current[0] = False
        self.tick()
        self.assertEqual(events, [])

    def test_core_loading_can_run_while_optional_request_is_in_flight(self):
        events = []
        def background_request():
            events.append('background started')
            self.defer(lambda: events.append('core ready'), priority=True)
            # Simulate the browser delivering this callback during a yielding
            # server request. It must not wait for the optional job's finally.
            self.tick()
            self.assertEqual(events, ['background started', 'core ready'])
            self.assertTrue(self.quiet.active)
            events.append('background finished')
        self.defer(background_request)
        self.tick()
        self.assertEqual(events[-1], 'background finished')
        self.assertFalse(self.quiet.active)

    def test_failure_propagates_releases_queue_and_does_not_retry(self):
        events = []
        def fail():
            raise RuntimeError('unexpected background failure')
        self.defer(fail)
        self.defer(lambda: events.append('next'))
        with self.assertRaisesRegex(RuntimeError, 'unexpected background failure'):
            self.tick()
        self.assertFalse(self.quiet.active)
        self.tick()
        self.assertEqual(events, ['next'])
        self.assertEqual(self.timers, [])


class CachedUsernameTests(unittest.TestCase):
    def setUp(self):
        self.user = {'username': 'owner'}
        self.calls = []
        def workspace():
            self.calls.append('workspace')
            return {'profile': {'username': 'owner'}}
        path = APP / 'client_code/account_profile.py'
        tree = ast.parse(path.read_text())
        tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
        self.namespace = dict(account_workspace=workspace,
            anvil=SimpleNamespace(users=SimpleNamespace(get_user=lambda: self.user)))
        exec(compile(tree, str(path), 'exec'), self.namespace)

    def test_remembered_username_needs_no_bootstrap_request(self):
        self.assertTrue(self.namespace['ensure_username']())
        self.assertEqual(self.calls, [])

    def test_missing_cached_username_still_checks_server_profile(self):
        class User(dict):
            def get_id(self):
                return 'account-a'
        self.user = User(username=None)
        self.assertTrue(self.namespace['ensure_username']())
        self.assertEqual(self.calls, ['workspace'])

    def test_guest_cannot_pass_username_setup(self):
        self.user = None
        self.assertFalse(self.namespace['ensure_username']())
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
