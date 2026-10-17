import anvil.server
import anvil.users
from ._anvil_designer import SubscribeTemplate
from anvil import *


class Subscribe(SubscribeTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.return_to_account = False
    self._busy = False
    self._catalog = anvil.server.call("get_subscription_catalog")
    self.premium_price_label.text = self._catalog["premium_price_label"]
    self.premium_note_label.text = self._catalog["premium_note"]
    self._user = anvil.users.get_user()
    if self._user is not None:
      self.free_button.text = "Open my portfolio"
    self.action_status_label.visible = False
    self.premium_button.text = "Subscribe to Premium" if self._catalog["checkout_available"] else "Subscriptions coming soon"
    self._set_busy(False)

  def _set_busy(self, busy):
    self._busy = busy
    setattr(self.free_button, "enabled", not busy)
    setattr(self.dashboard_button, "enabled", not busy)
    setattr(self.subscription_button, "enabled", not busy)
    setattr(self.premium_button, "enabled", not busy and self._catalog["checkout_available"])

  @handle("dashboard_button", "click")
  def dashboard_click(self, **event_args):
    if not self._busy:
      set_url_hash({})

  @handle("subscription_button", "click")
  def subscription_click(self, **event_args):
    if not self._busy:
      set_url_hash({"page": "subscription"})

  @handle("free_button", "click")
  def free_click(self, **event_args):
    if self._busy:
      return
    self._set_busy(True)
    try:
      user = anvil.users.get_user()
      if user is None:
        user = anvil.users.login_with_form(allow_cancel=True)
        if user is None:
          return
        self.return_to_account = True
      if get_open_form() is self:
        set_url_hash({})
    finally:
      self._set_busy(False)

  @handle("premium_button", "click")
  def premium_click(self, **event_args):
    if self._busy or not self._catalog["checkout_available"]:
      return
    self._set_busy(True)
    self.action_status_label.visible = False
    try:
      user = anvil.users.get_user()
      if user is None:
        user = anvil.users.login_with_form(allow_cancel=True)
        if user is None:
          return
        self.return_to_account = True
      if get_open_form() is not self:
        return
      result = anvil.server.call("start_subscription_checkout")
      if result["ok"]:
        raise RuntimeError("Subscription checkout redirect is not configured.")
      if get_open_form() is self:
        self.action_status_label.text = result["message"]
        self.action_status_label.visible = True
    finally:
      self._set_busy(False)
