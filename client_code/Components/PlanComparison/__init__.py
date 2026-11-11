import anvil.server
import anvil.users
import anvil.js
from time import time
from ._anvil_designer import PlanComparisonTemplate
from anvil import *
from ...account_profile import sign_in, ensure_username


def open_stripe_url(url):
  if not isinstance(url, str) or not url.startswith(('https://checkout.stripe.com/', 'https://billing.stripe.com/')):
    raise ValueError('The billing provider returned an invalid destination.')
  anvil.js.window.location.href = url


class PlanComparison(PlanComparisonTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._busy = False
    self._parent_busy = False
    self._checkout_pending = False
    self._loading = False
    self._ready = False
    self._managed = False
    self._loaded_at = 0
    self._account_id = None
    self._has_username = False
    self._plus = False
    self._catalog = {'checkout_available': False}
    self.status_label.visible = False
    self.refresh_plan()

  def _current_account_id(self):
    user = anvil.users.get_user()
    return user.get_id() if user is not None else None

  def set_managed(self):
    self._managed = True
    self.plan_load_timer.interval = 0

  def set_parent_busy(self, busy):
    self._parent_busy = busy
    self._set_busy(self._busy)

  def clear_billing_context(self):
    self._ready = False
    self._plus = False
    self._has_username = False
    self._account_id = None
    self.status_label.visible = False
    self._set_busy(self._busy)

  def set_billing_context(self, workspace):
    if workspace['account_id'] != self._current_account_id():
      return
    self.plan_load_timer.interval = 0
    self._account_id = workspace['account_id']
    self._catalog = workspace['catalog']
    self._plus = bool(workspace['membership'] and workspace['membership']['is_premium'])
    self._has_username = workspace['has_username']
    self._ready = True
    self._loaded_at = time()
    self.retry_button.visible = not self._catalog['checkout_available']
    self._set_busy(self._busy)

  def refresh_plan(self, force=False):
    if self._managed or self._busy:
      return
    account_id = self._current_account_id()
    if account_id != self._account_id:
      self._ready = False
      self._plus = False
      self._has_username = False
      self._account_id = account_id
      self.status_label.visible = False
    if force or not self._ready or time() - self._loaded_at >= 60:
      self.plan_load_timer.interval = 0.01
    self._set_busy(False)

  @handle('', 'show')
  def form_show(self, **event_args):
    self.refresh_plan()

  @handle('plan_load_timer', 'tick')
  def load_plan(self, **event_args):
    self.plan_load_timer.interval = 0
    if self._managed or self._loading:
      return
    if self._busy:
      self.plan_load_timer.interval = 0.1
      return
    self._loading = True
    account_id = self._current_account_id()
    self._set_busy(False)
    try:
      try:
        workspace = anvil.server.call_s('get_billing_workspace')
      except (getattr(anvil.server, 'RuntimeUnavailableError'), anvil.server.AppOfflineError):
        if account_id == self._current_account_id():
          self.status_label.text = 'Payment availability could not be checked. Please try again.'
          self.status_label.visible = True
          self.retry_button.visible = True
        return
      if account_id != self._current_account_id():
        self.refresh_plan(force=True)
        return
      self.set_billing_context(workspace)
    finally:
      self._loading = False
      self._set_busy(self._busy)

  @handle('retry_button', 'click')
  def retry_click(self, **event_args):
    if self._managed:
      self.raise_event('x-refresh-billing')
    else:
      self.refresh_plan(force=True)

  def _set_busy(self, busy):
    self._busy = busy
    blocked = busy or self._parent_busy
    self.free_note.text = 'Your current plan' if self._account_id is not None and self._ready and not self._plus else 'Start with the essentials'
    self.plus_note.text = 'Your current plan' if self._plus else 'More room to invest'
    self.plus_button.text = ('Opening checkout…' if self._checkout_pending else
      'Your current plan' if self._plus else 'Checking payments…' if not self._ready or self._loading else
      'Get Plus' if self._catalog['checkout_available'] else 'Payments being set up')
    self.free_button.text = 'Open your portfolio' if self._current_account_id() is not None else 'Get started for free'
    self.free_button.enabled = not blocked
    self.plus_button.enabled = not blocked and not self._loading and self._ready and not self._plus and self._catalog['checkout_available']
    self.retry_button.enabled = not blocked and not self._loading

  @handle('free_button', 'click')
  def free_click(self, **event_args):
    if self._busy or self._parent_busy:
      return
    new_login = anvil.users.get_user() is None
    self._set_busy(True)
    origin = get_open_form()
    try:
      if sign_in() is not None and ensure_username() and get_open_form() is origin:
        set_url_hash({'page': 'profile' if new_login else 'portfolio'})
    finally:
      self._set_busy(False)

  @handle('plus_button', 'click')
  def plus_click(self, **event_args):
    if self._busy or self._parent_busy or not self._ready or self._plus or not self._catalog['checkout_available']:
      return
    self._checkout_pending = True
    self._set_busy(True)
    self.status_label.text = 'Opening secure Stripe checkout…'
    self.status_label.visible = True
    origin = get_open_form()
    try:
      user = sign_in()
      if user is None:
        return
      account_id = user.get_id()
      # The bootstrap already checked existing usernames. New sign-ins still
      # complete setup; the checkout callable always enforces it on the server.
      if (account_id != self._account_id or not self._has_username) and not ensure_username():
        return
      current = anvil.users.get_user()
      if get_open_form() is not origin or current is None or current.get_id() != account_id:
        return
      try:
        result = anvil.server.call_s('start_subscription_checkout')
      except (getattr(anvil.server, 'RuntimeUnavailableError'), anvil.server.AppOfflineError):
        if get_open_form() is origin and self._current_account_id() == account_id:
          self.status_label.text = 'Checkout could not be opened. Please try again.'
        return
      current = anvil.users.get_user()
      if get_open_form() is not origin or current is None or current.get_id() != account_id:
        return
      if result['ok']:
        open_stripe_url(result['url'])
      else:
        self.status_label.text = result['message']
        self.status_label.visible = True
    finally:
      self._checkout_pending = False
      if self.status_label.text == 'Opening secure Stripe checkout…':
        self.status_label.visible = False
      self._set_busy(False)
