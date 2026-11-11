"""Payment navigation, single-call loading, account guards and price-cache boundaries."""
import ast
import hashlib
from pathlib import Path
import re
from types import SimpleNamespace
import unittest


APP = Path(__file__).resolve().parents[1]


class Disconnected(Exception):
    pass


class User:
    def __init__(self, key):
        self.key = key

    def get_id(self):
        return self.key


def load_source(path, namespace):
    tree = ast.parse(path.read_text())
    tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
    exec(compile(tree, str(path), 'exec'), namespace)
    return namespace


class Payments:
    def __init__(self, guest=False, returned=False):
        self.user = None if guest else User('account-a')
        self.now = 100
        self.active = None
        self.calls = []
        self.redirects = []
        self.username_checks = 0
        self.invalidations = 0
        self.hook = None
        self.error = None
        self.result = dict(ok=True, url='https://checkout.stripe.com/c/pay/fixture')
        self.route = dict(page='subscription', **({'checkout': 'success'} if returned else {}))
        h = self

        def call(name, **kwargs):
            h.calls.append((name, kwargs))
            if h.hook:
                h.hook()
            if h.error:
                raise h.error
            return h.workspace if name == 'get_billing_workspace' else h.result

        def username():
            h.username_checks += 1
            return True

        class Template:
            def __init__(self, **properties):
                name = type(self).__name__
                folder = APP / 'client_code' / ('Components/PlanComparison' if name == 'PlanComparison' else name)
                for component in re.findall(r'(?:anvil:name|name)="([^"]+)"', (folder / 'form_template.html').read_text()):
                    setattr(self, component, SimpleNamespace(text='', visible=False, enabled=True, interval=0))
                if name == 'MySubscription':
                    self.plan_comparison = h.Plan()

        def invalidate():
            h.invalidations += 1

        ns = dict(anvil=SimpleNamespace(server=SimpleNamespace(call_s=call, call=call,
                      RuntimeUnavailableError=Disconnected, AppOfflineError=Disconnected),
                      users=SimpleNamespace(get_user=lambda: h.user)),
                  handle=lambda *a: lambda f: f, time=lambda: h.now,
                  get_open_form=lambda: h.active, get_url_hash=lambda: h.route,
                  set_url_hash=lambda route: None, sign_in=lambda: h.user, ensure_username=username,
                  invalidate_workspace=invalidate, PlanComparisonTemplate=Template)
        path = APP / 'client_code/Components/PlanComparison/__init__.py'
        self.Plan = load_source(path, ns)['PlanComparison']
        # Use a harmless destination recorder rather than browser navigation.
        ns['open_stripe_url'] = self.redirects.append
        sub_ns = dict(ns, MySubscriptionTemplate=Template)
        self.Subscription = load_source(APP / 'client_code/MySubscription/__init__.py', sub_ns)['MySubscription']
        self.workspace = self.payload(None if guest else 'account-a')

    def payload(self, account_id, ready=True, premium=False):
        membership = dict(label='Plus' if premium else 'Free', is_premium=premium, portfolio_limit=3 if premium else 1)
        details = dict(email='owner@example.invalid', username='owner', membership=membership,
                       portfolio_count=0, status_label=membership['label'], billing_status_label='Active',
                       renewal_label='No scheduled renewal', billing_detail='Fixture billing', can_manage_billing=True,
                       verification=dict(label='Not verified', included=False, canceling=False, verified=False, period_end=None))
        return dict(account_id=account_id, catalog=dict(checkout_available=ready, billing_portal_available=ready),
                    has_username=account_id is not None, membership=membership if account_id else None,
                    details=details if account_id else None, refresh_result=dict(ok=True))

    def plan(self):
        self.active = form = self.Plan()
        return form

    def subscription(self):
        self.active = form = self.Subscription()
        return form

    def verification(self):
        path = APP / 'client_code/ProfileHome/__init__.py'
        tree = ast.parse(path.read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        cls.bases = []
        cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'verify_click']
        namespace = dict(self.Plan.plus_click.__globals__)
        namespace['confirm'] = lambda *args, **kwargs: True
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), 'exec'), namespace)
        self.active = form = namespace['ProfileHome']()
        form.account_id = self.user.get_id()
        form._busy = False
        form._verified = False
        form.verify_button = SimpleNamespace(text='Get Verified · $1/month', enabled=True)
        form.status_label = SimpleNamespace(text='', visible=False)
        def busy(value):
            form._busy = value
            form.verify_button.enabled = not value
        form._set_busy = busy
        return form


class PaymentInteractionTests(unittest.TestCase):
    def test_plans_render_before_one_combined_request(self):
        h = Payments()
        f = h.plan()
        self.assertEqual(h.calls, [])
        self.assertFalse(f.plus_button.enabled)
        f.load_plan()
        self.assertEqual(h.calls, [('get_billing_workspace', {})])
        self.assertEqual(f.plus_button.text, 'Get Plus')
        self.assertTrue(f.plus_button.enabled)
        f.form_show()
        self.assertEqual(f.plan_load_timer.interval, 0)
        h.now += 61
        f.form_show()
        self.assertGreater(f.plan_load_timer.interval, 0)

    def test_managed_cards_do_not_fetch_separately(self):
        h = Payments()
        f = h.subscription()
        self.assertEqual(h.calls, [])
        f.plan_comparison.load_plan()
        self.assertEqual(h.calls, [])
        f.load_details()
        self.assertEqual(h.calls, [('get_billing_workspace', dict(include_details=True, refresh=False))])
        self.assertTrue(f.account_region.visible)
        self.assertTrue(f.plan_comparison.plus_button.enabled)
        self.assertEqual(f.email_label.text, 'owner@example.invalid')

    def test_subscription_refresh_is_explicit(self):
        h = Payments()
        f = h.subscription()
        f.load_details()
        f.refresh_click()
        self.assertTrue(f._busy)
        self.assertFalse(f.plan_comparison.plus_button.enabled)
        f.load_details()
        self.assertEqual(h.calls[-1][1], dict(include_details=True, refresh=True))
        self.assertEqual(h.invalidations, 1)

    def test_success_return_checks_stripe_only_once(self):
        h = Payments(returned=True)
        f = h.subscription()
        f.load_details()
        self.assertTrue(h.calls[-1][1]['refresh'])
        f._refresh_details()
        f.load_details()
        self.assertFalse(h.calls[-1][1]['refresh'])

    def test_guest_sees_prices_without_private_billing_request(self):
        h = Payments(guest=True)
        f = h.subscription()
        f.load_details()
        self.assertEqual(h.calls[-1][1], dict(include_details=False, refresh=False))
        self.assertTrue(f.signed_out_region.visible)
        self.assertFalse(f.account_region.visible)

    def test_checkout_feedback_precedes_request_and_double_click_is_ignored(self):
        h = Payments()
        f = h.plan()
        f.load_plan()
        h.calls.clear()
        def pending():
            self.assertEqual(f.plus_button.text, 'Opening checkout…')
            self.assertFalse(f.plus_button.enabled)
            self.assertTrue(f.status_label.visible)
            f.plus_click()
        h.hook = pending
        f.plus_click()
        self.assertEqual(h.calls, [('start_subscription_checkout', {})])
        self.assertEqual(h.username_checks, 0)
        self.assertEqual(len(h.redirects), 1)
        self.assertFalse(f._busy)

    def test_unknown_username_still_requires_setup(self):
        h = Payments()
        h.workspace['has_username'] = False
        f = h.plan()
        f.load_plan()
        f.plus_click()
        self.assertEqual(h.username_checks, 1)
        self.assertEqual(len(h.redirects), 1)

    def test_rejection_keeps_message_without_extra_profile_requests(self):
        h = Payments()
        f = h.plan()
        f.load_plan()
        h.calls.clear()
        h.result = dict(ok=False, message='Plus is already active.')
        f.plus_click()
        self.assertEqual(h.calls, [('start_subscription_checkout', {})])
        self.assertEqual(f.status_label.text, 'Plus is already active.')
        self.assertEqual(h.redirects, [])

    def test_payment_connection_failure_is_retryable_without_automatic_write_retry(self):
        h = Payments()
        f = h.plan()
        f.load_plan()
        h.calls.clear()
        h.error = Disconnected()
        f.plus_click()
        self.assertEqual(len(h.calls), 1)
        self.assertIn('try again', f.status_label.text)
        self.assertTrue(f.plus_button.enabled)
        self.assertFalse(f._busy)

    def test_unexpected_error_propagates_and_releases_button(self):
        h = Payments()
        f = h.plan()
        f.load_plan()
        h.error = RuntimeError('Unexpected billing bug')
        with self.assertRaisesRegex(RuntimeError, 'Unexpected billing bug'):
            f.plus_click()
        self.assertFalse(f._busy)
        self.assertTrue(f.plus_button.enabled)

    def test_old_checkout_cannot_redirect_after_account_or_page_change(self):
        for switch_account in (True, False):
            h = Payments()
            f = h.plan()
            f.load_plan()
            def change():
                if switch_account:
                    h.user = User('account-b')
                else:
                    h.active = object()
            h.hook = change
            f.plus_click()
            self.assertEqual(h.redirects, [])

    def test_billing_portal_feedback_and_account_guard(self):
        h = Payments()
        f = h.subscription()
        f.load_details()
        h.calls.clear()
        def change():
            self.assertEqual(f.billing_portal_button.text, 'Opening billing…')
            h.user = User('account-b')
            f.account_id = 'account-b'
        h.hook = change
        f.billing_portal_click()
        self.assertEqual(h.calls, [('open_subscription_billing_portal', {})])
        self.assertEqual(h.redirects, [])
        self.assertFalse(f._busy)

    def test_account_change_during_page_load_discards_private_details(self):
        h = Payments()
        f = h.subscription()
        h.hook = lambda: setattr(h, 'user', User('account-b'))
        f.load_details()
        self.assertFalse(f.account_region.visible)
        self.assertEqual(f.email_label.text, '')
        self.assertGreater(f.billing_load_timer.interval, 0)

    def test_availability_can_be_rechecked_after_secret_configuration(self):
        h = Payments()
        h.workspace = h.payload('account-a', ready=False)
        f = h.plan()
        f.load_plan()
        self.assertFalse(f.plus_button.enabled)
        self.assertTrue(f.retry_button.visible)
        h.workspace = h.payload('account-a', ready=True)
        f.retry_click()
        f.load_plan()
        self.assertTrue(f.plus_button.enabled)
        self.assertFalse(f.retry_button.visible)

    def test_verified_checkout_redirects_without_reloading_profile(self):
        h = Payments()
        f = h.verification()
        def pending():
            self.assertEqual(f.verify_button.text, 'Opening checkout…')
            self.assertFalse(f.verify_button.enabled)
            f.verify_click()
        h.hook = pending
        f.verify_click()
        self.assertEqual(h.calls, [('start_verification_checkout', {})])
        self.assertEqual(len(h.redirects), 1)
        self.assertFalse(f._busy)
        self.assertEqual(f.verify_button.text, 'Get Verified · $1/month')

    def test_verified_checkout_discards_old_account_result(self):
        h = Payments()
        f = h.verification()
        h.hook = lambda: setattr(h, 'user', User('account-b'))
        f.verify_click()
        self.assertEqual(h.redirects, [])

    def test_verified_rejection_is_visible_without_reload(self):
        h = Payments()
        f = h.verification()
        h.result = dict(ok=False, message='Verified is included with Plus.')
        f.verify_click()
        self.assertEqual(f.status_label.text, 'Verified is included with Plus.')
        self.assertTrue(f.status_label.visible)
        self.assertEqual(len(h.calls), 1)


class PriceValidationTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.calls = []
        self.price = dict(active=True, currency='usd', unit_amount=1999, billing_scheme='per_unit',
                          recurring=dict(interval='month', interval_count=1, usage_type='licensed'))
        h = self
        def stripe(config, method, path):
            h.calls.append((config['STRIPE_SECRET_KEY'], path))
            return h.price
        tree = ast.parse((APP / 'server_code/stripe_billing.py').read_text())
        tree.body = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))
                     and n.name in ('BillingSetupError', '_validate_price')]
        self.ns = dict(hashlib=hashlib, time=SimpleNamespace(monotonic=lambda: h.now),
                       _stripe=stripe, _validated_price=None, _VALIDATED_PRICE_SECONDS=300, _API_VERSION='fixture')
        exec(compile(tree, 'stripe_billing.py', 'exec'), self.ns)
        self.config = dict(STRIPE_SECRET_KEY='fixture-key', STRIPE_PLUS_PRICE_ID='fixture-price')

    def test_success_reuses_check_and_expires(self):
        validate = self.ns['_validate_price']
        validate(self.config)
        validate(self.config)
        self.assertEqual(len(self.calls), 1)
        self.now += 300
        validate(self.config)
        self.assertEqual(len(self.calls), 2)

    def test_credential_and_price_changes_cannot_reuse_validation(self):
        validate = self.ns['_validate_price']
        validate(self.config)
        validate(dict(self.config, STRIPE_SECRET_KEY='another-mode-key'))
        validate(dict(self.config, STRIPE_PLUS_PRICE_ID='another-price'))
        self.assertEqual(len(self.calls), 3)

    def test_invalid_price_does_not_enter_cache(self):
        self.price['unit_amount'] = 100
        for _ in range(2):
            with self.assertRaises(self.ns['BillingSetupError']):
                self.ns['_validate_price'](self.config)
        self.assertEqual(len(self.calls), 2)
        self.assertIsNone(self.ns['_validated_price'])


if __name__ == '__main__':
    unittest.main()
