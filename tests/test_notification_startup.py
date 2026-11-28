"""Startup and transport recovery for the optional navigation badge."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


APP = Path(__file__).resolve().parents[1]
FORM = APP / "client_code/Form1"


class RuntimeUnavailableError(Exception):
    pass


class AppOfflineError(Exception):
    pass


class User(dict):
    def get_id(self):
        return self["id"]


class BadgeHarness:
    def __init__(self):
        self.calls = []
        self.user = User(id="account-a", email="fixture@example.invalid")
        self.result = 3
        self.hook = None
        harness = self

        def call_s(name):
            harness.calls.append(name)
            if harness.hook:
                harness.hook()
            if isinstance(harness.result, Exception):
                raise harness.result
            return harness.result

        tree = ast.parse((FORM / "__init__.py").read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Form1")
        methods = {"_set_notification_unread_count", "_refresh_notification_unread_count",
                   "notification_badge_timer_tick", "notifications_unread_change", "_refresh_account"}
        cls.bases = []
        cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in methods]
        for method in cls.body:
            method.decorator_list = []
        ns = dict(anvil=SimpleNamespace(
            server=SimpleNamespace(call_s=call_s, RuntimeUnavailableError=RuntimeUnavailableError,
                                   AppOfflineError=AppOfflineError),
            users=SimpleNamespace(get_user=lambda: harness.user)),
            account_workspace=lambda require_profile=True: dict(profile=dict(username="fixture"), membership={},
                                           creator=dict(enabled=False, email=None), portfolios=[]))
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(FORM / "__init__.py"), "exec"), ns)
        self.form = f = ns["Form1"]()
        f.account_id = self.user.get_id()
        f._notification_badge_revision = 0
        f._notification_badge_loading = False
        f._initializing = False
        f._busy = False
        for name in ("notification_badge", "notification_badge_label", "notification_badge_accessible_label",
                     "notifications_tab_button", "notification_badge_timer", "subscription_button",
                     "account_button", "account_label", "creator_setup_button", "creator_setup_region",
                     "creator_reference_box", "creator_tab_button", "creator_access_region"):
            setattr(f, name, SimpleNamespace(visible=False, text="", tooltip="", interval=0))
        f.share_composer = SimpleNamespace(set_account_context=lambda: None)
        f.notifications_form = SimpleNamespace(set_account_context=lambda account: None)
        f.creator_workspace = SimpleNamespace(configure_access=lambda *args: None)
        f.hedge_fund_comparison = SimpleNamespace(set_creator_access=lambda *args: None)
        for name in ("_display_membership", "_refresh_membership", "_set_creator_open",
                     "_apply_readout_page", "_refresh_portfolios"):
            setattr(f, name, lambda *args: None)
        f._set_notification_unread_count(7)


class NotificationStartupTests(unittest.TestCase):
    def test_account_bootstrap_defers_badge_rpc(self):
        h = BadgeHarness()
        h.result = RuntimeUnavailableError("Downlink disconnected")
        h.form._refresh_account()
        self.assertEqual(h.calls, [])
        self.assertEqual(h.form.notification_badge_timer.interval, 0.1)
        h.form.notification_badge_timer_tick()
        self.assertEqual(h.calls, ["get_notification_unread_count"])
        self.assertEqual(h.form.notification_badge_timer.interval, 5)

    def test_timer_is_disabled_before_account_bootstrap(self):
        html = (FORM / "form_template.html").read_text()
        self.assertIn('name="notification_badge_timer" prop:interval="0"', html)

    def test_disconnected_poll_preserves_count_and_backs_off(self):
        h = BadgeHarness()
        h.result = RuntimeUnavailableError("Downlink disconnected")
        for expected in (5, 15, 45, 60, 60):
            h.form.notification_badge_timer_tick()
            self.assertEqual(h.form.notification_badge_timer.interval, expected)
            self.assertEqual(h.form.notification_badge_label.text, "7")
            self.assertTrue(h.form.notification_badge.visible)
            self.assertFalse(h.form._notification_badge_loading)
        self.assertIn("Retrying automatically", h.form.notifications_tab_button.tooltip)

    def test_offline_poll_and_reconnection(self):
        h = BadgeHarness()
        h.result = AppOfflineError("Offline")
        h.form.notification_badge_timer_tick()
        self.assertEqual(h.form.notification_badge_timer.interval, 5)
        h.result = 3
        h.form.notification_badge_timer_tick()
        self.assertEqual(h.form.notification_badge_label.text, "3")
        self.assertEqual(h.form.notifications_tab_button.tooltip, "3 unread notifications")
        self.assertEqual(h.form.notification_badge_timer.interval, 60)
        h.result = RuntimeUnavailableError()
        h.form.notification_badge_timer_tick()
        self.assertEqual(h.form.notification_badge_timer.interval, 5)

    def test_guest_startup_never_polls(self):
        h = BadgeHarness()
        h.user = None
        h.form._refresh_account()
        h.form.notification_badge_timer_tick()
        self.assertEqual(h.calls, [])
        self.assertEqual(h.form.notification_badge_timer.interval, 0)
        self.assertFalse(h.form.notification_badge.visible)
        self.assertEqual(h.form.notification_badge_accessible_label.text, "")

    def test_initialization_and_busy_work_delay_poll(self):
        for attribute in ("_initializing", "_busy"):
            h = BadgeHarness()
            setattr(h.form, attribute, True)
            h.form.notification_badge_timer_tick()
            self.assertEqual(h.calls, [])
            self.assertEqual(h.form.notification_badge_timer.interval, 1)
            setattr(h.form, attribute, False)
            h.form.notification_badge_timer_tick()
            self.assertEqual(len(h.calls), 1)

    def test_overlapping_tick_does_not_duplicate_rpc(self):
        h = BadgeHarness()
        h.hook = h.form.notification_badge_timer_tick
        h.form.notification_badge_timer_tick()
        self.assertEqual(len(h.calls), 1)
        self.assertEqual(h.form.notification_badge_timer.interval, 60)

    def test_inbox_response_wins_over_old_poll(self):
        for result in (10, RuntimeUnavailableError()):
            h = BadgeHarness()
            h.result = result
            h.hook = lambda: h.form.notifications_unread_change(2, "account-a")
            h.form.notification_badge_timer_tick()
            self.assertEqual(h.form.notification_badge_label.text, "2")
            self.assertEqual(h.form.notifications_tab_button.tooltip, "2 unread notifications")
            self.assertEqual(h.form.notification_badge_timer.interval, 60)

    def test_account_switch_during_poll_ignores_old_result(self):
        for result in (10, RuntimeUnavailableError()):
            h = BadgeHarness()
            h.result = result
            def switch_account():
                h.user = User(id="account-b", email="other@example.invalid")
                h.form._refresh_account()
            h.hook = switch_account
            h.form.notification_badge_timer_tick()
            self.assertEqual(h.form.account_id, "account-b")
            self.assertFalse(h.form.notification_badge.visible)
            self.assertEqual(h.form.notification_badge_timer.interval, 0.1)
            self.assertNotIn("unavailable", h.form.notifications_tab_button.tooltip)

    def test_logout_during_failed_poll_stops_retry(self):
        h = BadgeHarness()
        h.result = RuntimeUnavailableError()
        def logout():
            h.user = None
            h.form._refresh_account()
        h.hook = logout
        h.form.notification_badge_timer_tick()
        self.assertEqual(h.form.notification_badge_timer.interval, 0)
        self.assertFalse(h.form.notification_badge.visible)
        self.assertEqual(h.form.notification_badge_accessible_label.text, "")

    def test_zero_after_recovery_clears_badge(self):
        h = BadgeHarness()
        h.result = RuntimeUnavailableError()
        h.form.notification_badge_timer_tick()
        h.result = 0
        h.form.notification_badge_timer_tick()
        self.assertFalse(h.form.notification_badge.visible)
        self.assertEqual(h.form.notifications_tab_button.tooltip, "0 unread notifications")

    def test_unexpected_errors_raise_and_release_loading_guard(self):
        h = BadgeHarness()
        h.result = ValueError("Unexpected notification error")
        with self.assertRaisesRegex(ValueError, "Unexpected notification error"):
            h.form.notification_badge_timer_tick()
        self.assertFalse(h.form._notification_badge_loading)
        self.assertEqual(h.form.notification_badge_timer.interval, 0)
        self.assertEqual(h.form.notification_badge_label.text, "7")


if __name__ == "__main__":
    unittest.main()
