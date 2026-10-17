import anvil.server
import anvil.users
import anvil.js
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
    self.status_label.visible = False
    self.refresh_plan()

  def refresh_plan(self):
    self._catalog = anvil.server.call('get_subscription_catalog')
    user = anvil.users.get_user()
    self._plus = False
    if user is not None:
      account_id = user.get_id()
      membership = anvil.server.call('get_my_membership')
      current = anvil.users.get_user()
      if current is None or current.get_id() != account_id:
        self.refresh_plan()
        return
      self._plus = membership['is_premium']
    self.free_note.text = 'Your current plan' if user is not None and not self._plus else 'Start with the essentials'
    self.plus_note.text = 'Your current plan' if self._plus else 'More room to invest'
    self.plus_button.text = 'Your current plan' if self._plus else ('Get Plus' if self._catalog['checkout_available'] else 'Payments being set up')
    self.free_button.text = 'Open your portfolio' if user is not None else 'Get started for free'
    self._set_busy(False)

  def _set_busy(self, busy):
    self._busy = busy
    self.free_button.enabled = not busy
    self.plus_button.enabled = not busy and not self._plus and self._catalog['checkout_available']

  @handle('free_button', 'click')
  def free_click(self, **event_args):
    if self._busy:
      return
    self._set_busy(True)
    origin = get_open_form()
    try:
      if sign_in() is not None and ensure_username() and get_open_form() is origin:
        set_url_hash({'page': 'portfolio'})
    finally:
      self._set_busy(False)

  @handle('plus_button', 'click')
  def plus_click(self, **event_args):
    if self._busy or self._plus or not self._catalog['checkout_available']:
      return
    self._set_busy(True)
    origin = get_open_form()
    try:
      if sign_in() is None or not ensure_username():
        return
      user = anvil.users.get_user()
      if get_open_form() is not origin or user is None:
        return
      account_id = user.get_id()
      result = anvil.server.call('start_subscription_checkout')
      current = anvil.users.get_user()
      if get_open_form() is not origin or current is None or current.get_id() != account_id:
        return
      if result['ok']:
        open_stripe_url(result['url'])
      else:
        self.status_label.text = result['message']
        self.status_label.visible = True
        self.refresh_plan()
    finally:
      self._set_busy(False)
