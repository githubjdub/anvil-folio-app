import anvil.server
import anvil.users
from ._anvil_designer import MySubscriptionTemplate
from anvil import *
from ..account_profile import ensure_username, sign_in
from ..Components.PlanComparison import open_stripe_url
from ..workspace_cache import invalidate_workspace


class MySubscription(MySubscriptionTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._busy = False
    self._billing_eligible = False
    self._loading_details = False
    self._pending_refresh = False
    self._checkout_return_seen = False
    self._loaded = False
    self._billing_workspace = None
    self.account_id = None
    self.plan_comparison.set_managed()
    self.account_region.visible = False
    self.signed_out_region.visible = anvil.users.get_user() is None
    self.action_status_label.visible = False
    self._refresh_details()

  def _set_busy(self, busy):
    self._busy = busy
    for button in (self.signin_button, self.refresh_button):
      button.enabled = not busy
    self.billing_portal_button.enabled = not busy and self._billing_eligible
    self.plan_comparison.set_parent_busy(busy)

  def _refresh_details(self, refresh=False):
    user = anvil.users.get_user()
    if (user.get_id() if user is not None else None) != self.account_id:
      self.account_region.visible = False
      self._billing_eligible = False
      self._loaded = False
      self.plan_comparison.clear_billing_context()
    self.signed_out_region.visible = user is None
    route = get_url_hash()
    returning = (not self._checkout_return_seen and isinstance(route, dict)
                 and route.get('checkout') == 'success')
    if returning:
      self._checkout_return_seen = True
    self._pending_refresh = self._pending_refresh or refresh or returning
    if self._loading_details:
      return
    self._set_busy(True)
    self.action_status_label.text = 'Checking payment status…' if self._pending_refresh else 'Loading membership…'
    self.action_status_label.visible = True
    self.billing_load_timer.interval = 0.01

  @handle('', 'show')
  def form_show(self, **event_args):
    if not self._loaded and not self._loading_details:
      self._refresh_details()

  @handle('billing_load_timer', 'tick')
  def load_details(self, **event_args):
    self.billing_load_timer.interval = 0
    if self._loading_details:
      return
    user = anvil.users.get_user()
    account_id = user.get_id() if user is not None else None
    if account_id != self.account_id:
      self.account_region.visible = False
    self.signed_out_region.visible = user is None
    self._billing_eligible = False
    refresh = self._pending_refresh and user is not None
    self._pending_refresh = False
    self._loading_details = True
    changed_account = False
    try:
      try:
        workspace = anvil.server.call_s('get_billing_workspace', include_details=user is not None, refresh=refresh)
      except (getattr(anvil.server, 'RuntimeUnavailableError'), anvil.server.AppOfflineError):
        current = anvil.users.get_user()
        if (current.get_id() if current is not None else None) == account_id:
          self.action_status_label.text = 'Billing details could not be loaded. Please try again.'
          self.action_status_label.visible = True
          self.plan_comparison.retry_button.visible = True
        return
      current = anvil.users.get_user()
      if (current.get_id() if current is not None else None) != account_id:
        changed_account = True
        return
      if workspace['account_id'] != account_id:
        return
      self.account_id = account_id
      self._billing_workspace = workspace
      self.plan_comparison.set_billing_context(workspace)
      self._loaded = True
      refreshed = workspace['refresh_result']
      if refresh and refreshed['ok']:
        invalidate_workspace()
      self.action_status_label.text = '' if refreshed['ok'] else refreshed['message']
      self.action_status_label.visible = not refreshed['ok']
      if user is not None:
        self._render_details(workspace['details'], workspace['catalog'])
    finally:
      self._loading_details = False
      self._set_busy(False)
      if changed_account or self._pending_refresh:
        self._refresh_details()

  def _render_details(self, details, catalog):
    self.plan_badge.text = details['membership']['label']
    self.plan_badge.role = 'membership-badge-premium' if details['membership']['is_premium'] else 'membership-badge-free'
    self.usage_label.text = '%s of %s portfolios saved' % (details['portfolio_count'], details['membership']['portfolio_limit'])
    self.email_label.text = details['email']
    self.username_label.text = '@' + (details['username'] or '') + ' · Permanent username'
    self.status_label.text = details['status_label']
    self.billing_status_label.text = details['billing_status_label']
    self.renewal_label.text = details['renewal_label']
    self.billing_detail_label.text = details['billing_detail']
    verification = details['verification']
    self.verification_status_label.text = verification['label']
    self.verification_detail_label.text = ('Included with your plan. A separate Verified subscription, if present, is managed in Stripe.' if verification['included'] else
      ('Ends ' if verification['canceling'] else 'Renews ') + verification['period_end'].strftime('%b %d, %Y (UTC)') if verification['verified'] and verification['period_end'] else
      'Optional $1 USD/month membership badge. Get it from Profile settings.')
    self._billing_eligible = catalog['billing_portal_available'] and details['can_manage_billing']
    self.billing_portal_button.text = 'Manage billing' if catalog['billing_portal_available'] else 'Payments being set up'
    self.account_region.visible = True

  @handle('signin_button', 'click')
  def signin_click(self, **event_args):
    if self._busy:
      return
    self._set_busy(True)
    try:
      if sign_in() is not None and ensure_username() and get_open_form() is self:
        set_url_hash({'page': 'profile'})
    finally:
      self._set_busy(False)

  @handle('refresh_button', 'click')
  def refresh_click(self, **event_args):
    if self._busy:
      return
    self._refresh_details(refresh=True)

  @handle('plan_comparison', 'x-refresh-billing')
  def retry_billing(self, **event_args):
    if not self._busy:
      self._refresh_details(refresh=True)

  @handle('billing_portal_button', 'click')
  def billing_portal_click(self, **event_args):
    if self._busy or not self._billing_eligible:
      return
    account_id = self.account_id
    button_text = self.billing_portal_button.text
    self.billing_portal_button.text = 'Opening billing…'
    self.action_status_label.text = 'Opening secure Stripe billing…'
    self.action_status_label.visible = True
    self._set_busy(True)
    try:
      try:
        result = anvil.server.call_s('open_subscription_billing_portal')
      except (getattr(anvil.server, 'RuntimeUnavailableError'), anvil.server.AppOfflineError):
        user = anvil.users.get_user()
        if get_open_form() is self and user is not None and user.get_id() == account_id:
          self.action_status_label.text = 'Billing could not be opened. Please try again.'
        return
      user = anvil.users.get_user()
      if get_open_form() is not self or user is None or user.get_id() != account_id:
        return
      if result['ok']:
        open_stripe_url(result['url'])
      else:
        self.action_status_label.text = result['message']
        self.action_status_label.visible = True
    finally:
      self.billing_portal_button.text = button_text
      if self.action_status_label.text == 'Opening secure Stripe billing…':
        self.action_status_label.visible = False
      self._set_busy(False)
