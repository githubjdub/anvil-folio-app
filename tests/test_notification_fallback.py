"""Visible-page polling and account guards with client boundaries replaced."""
import ast
from pathlib import Path
import re
from types import SimpleNamespace
import unittest


APP = Path(__file__).resolve().parents[1]
FORM = APP / "client_code/Components/Notifications"


class Component(SimpleNamespace):
    def __init__(self):
        super().__init__(classes={}, interval=0, selected_value=None, checked=False, items=[])

    def set_event_handler(self, *args):
        pass

    def raise_event_on_children(self, *args, **kwargs):
        pass


class InboxHarness:
    def __init__(self):
        self.now = 1000
        self.calls = []
        self.hook = None
        harness = self
        names = re.findall(r'(?:anvil:name|name)="([^"]+)"', (FORM / "form_template.html").read_text())

        class Template:
            def __init__(self, **properties):
                for name in names:
                    setattr(self, name, Component())

            def set_event_handler(self, *args):
                pass

            def raise_event(self, *args, **kwargs):
                pass

        tree = ast.parse((FORM / "__init__.py").read_text())
        tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
        namespace = dict(NotificationsTemplate=Template, handle=lambda *args: lambda method: method,
                         time=lambda: harness.now, anvil=SimpleNamespace(server=SimpleNamespace(call=self.call)))
        exec(compile(tree, str(FORM / "__init__.py"), "exec"), namespace)
        self.form = namespace["Notifications"]()

    def workspace(self):
        return dict(settings=dict(enabled=True, channel="in_app", delivery_time="18:00", timezone="UTC",
                                  phone="", sms_consent_at=None, delivery_status="", delivery_error=""),
                    capabilities={}, watches=[], messages=[], next_cursor=None, unread_count=0,
                    polling=dict(message="Fixture inbox checked."))

    def call(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if self.hook:
            hook, self.hook = self.hook, None
            hook()
        return self.workspace()


class NotificationPollingTests(unittest.TestCase):
    def test_guest_never_polls(self):
        h = InboxHarness()
        h.form.set_active(True)
        h.form.ensure_loaded()
        h.form.check_timer_tick()
        self.assertEqual(h.calls, [])
        self.assertEqual(h.form.check_timer.interval, 0)

    def test_open_and_timer_poll_only_visible_inbox(self):
        h = InboxHarness()
        h.form.set_account_context("account-a")
        h.form.set_active(True)
        h.form.ensure_loaded()
        self.assertEqual(h.form.check_timer.interval, 60)
        self.assertEqual(h.calls[0][1], dict(before=None, check_for_updates=True))
        h.form.check_timer_tick()
        self.assertEqual(len(h.calls), 1)
        h.now += 61
        h.form.check_timer_tick()
        self.assertEqual(len(h.calls), 2)
        h.form.set_active(False)
        h.now += 61
        h.form.check_timer_tick()
        self.assertEqual(len(h.calls), 2)
        self.assertEqual(h.form.check_timer.interval, 0)

    def test_hide_stops_timer_show_restores_only_active_page(self):
        h = InboxHarness()
        h.form.set_account_context("account-a")
        h.form.set_active(True)
        h.form.form_hide()
        self.assertEqual(h.form.check_timer.interval, 0)
        h.form.form_show()
        self.assertEqual(h.form.check_timer.interval, 60)
        h.form.set_active(False)
        h.form.form_show()
        self.assertEqual(h.form.check_timer.interval, 0)

    def test_page_navigation_does_not_check_sec(self):
        h = InboxHarness()
        h.form.set_account_context("account-a")
        h.form._load_page(1, "cursor")
        self.assertEqual(h.calls[0][1], dict(before="cursor", check_for_updates=False))

    def test_slow_source_waits_a_minute_after_completion(self):
        h = InboxHarness()
        h.form.set_account_context("account-a")
        h.hook = lambda: setattr(h, "now", h.now + 120)
        h.form.refresh_click()
        h.form.ensure_loaded()
        self.assertEqual(len(h.calls), 1)
        h.now += 61
        h.form.ensure_loaded()
        self.assertEqual(len(h.calls), 2)

    def test_account_change_discards_inflight_inbox(self):
        h = InboxHarness()
        h.form.set_account_context("account-a")
        h.form.set_active(True)
        h.hook = lambda: h.form.set_account_context("account-b")
        h.form.refresh_click()
        self.assertFalse(h.form._loaded)
        self.assertEqual(h.form.messages_panel.items, [])
        self.assertEqual(h.form.check_timer.interval, 0)
        h.form.set_active(True)
        h.form.ensure_loaded()
        self.assertTrue(h.form._loaded)
        self.assertEqual(h.form._account_id, "account-b")

    def test_unexpected_failure_propagates_without_busy_loop(self):
        h = InboxHarness()
        h.form.set_account_context("account-a")
        def failure():
            raise RuntimeError("fixture unexpected failure")
        h.hook = failure
        with self.assertRaises(RuntimeError):
            h.form.ensure_loaded()
        self.assertFalse(h.form._busy)
        h.form.ensure_loaded()
        self.assertEqual(len(h.calls), 1)


if __name__ == "__main__":
    unittest.main()
