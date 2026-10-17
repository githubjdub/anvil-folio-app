import anvil.server
import anvil.users
from ._anvil_designer import MySubscriptionTemplate
from anvil import *
from ..account_profile import ensure_username, sign_in
from ..Components.PlanComparison import open_stripe_url


class MySubscription(MySubscriptionTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._busy = False
    self._billing_eligible = False
    self.account_id = None
    self.action_status_label.visible = False
    self._refresh_details()

  def _set_busy(self, busy):
    self._busy = busy
    for button in (self.profile_button, self.dashboard_button, self.signin_button, self.refresh_button):
      button.enabled = not busy
    self.billing_portal_button.enabled = not busy and self._billing_eligible

  def _refresh_details(self):
    user = anvil.users.get_user()
    self.account_region.visible = False
    self.signed_out_region.visible = user is None
    self._billing_eligible = False
    if user is None:
      self.account_id = None
      self._set_busy(False)
      return
    account_id = user.get_id()
    refreshed = anvil.server.call('refresh_my_billing')
    details = anvil.server.call('get_my_subscription')
    catalog = anvil.server.call('get_subscription_catalog')
    current = anvil.users.get_user()
    if current is None or current.get_id() != account_id:
      self._refresh_details()
      return
    self.account_id = account_id
    self.plan_badge.text = details['membership']['label']
    self.plan_badge.role = 'membership-badge-premium' if details['membership']['is_premium'] else 'membership-badge-free'
    self.usage_label.text = '%s of %s portfolios saved' % (details['portfolio_count'], details['membership']['portfolio_limit'])
    self.email_label.text = details['email']
    self.username_label.text = '@' + (details['username'] or '') + ' · Permanent username'
    self.status_label.text = details['status_label']
    self.billing_status_label.text = details['billing_status_label']
    self.renewal_label.text = details['renewal_label']
    self.billing_detail_label.text = details['billing_detail']
    self._billing_eligible = catalog['billing_portal_available'] and details['can_manage_billing']
    self.billing_portal_button.text = 'Manage billing' if catalog['billing_portal_available'] else 'Payments being set up'
    self.account_region.visible = True
    self.plan_comparison.refresh_plan()
    if not refreshed['ok']:
      self.action_status_label.text = refreshed['message']
      self.action_status_label.visible = True
    self._set_busy(False)

  @handle('dashboard_button', 'click')
  def dashboard_click(self, **event_args):
    if not self._busy:
      set_url_hash({'page': 'portfolio'})

  @handle('profile_button', 'click')
  def profile_click(self, **event_args):
    if not self._busy:
      set_url_hash({'page': 'profile'})

  @handle('signin_button', 'click')
  def signin_click(self, **event_args):
    if self._busy:
      return
    self._set_busy(True)
    try:
      if sign_in() is not None and ensure_username() and get_open_form() is self:
        self._refresh_details()
    finally:
      self._set_busy(False)

  @handle('refresh_button', 'click')
  def refresh_click(self, **event_args):
    if self._busy:
      return
    self._set_busy(True)
    try:
      self._refresh_details()
    finally:
      self._set_busy(False)

  @handle('billing_portal_button', 'click')
  def billing_portal_click(self, **event_args):
    if self._busy or not self._billing_eligible:
      return
    self._set_busy(True)
    try:
      result = anvil.server.call('open_subscription_billing_portal')
      user = anvil.users.get_user()
      if get_open_form() is not self or user is None or user.get_id() != self.account_id:
        return
      if result['ok']:
        open_stripe_url(result['url'])
      else:
        self.action_status_label.text = result['message']
        self.action_status_label.visible = True
    finally:
      self._set_busy(False)
