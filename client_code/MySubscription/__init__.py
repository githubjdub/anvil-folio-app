import anvil.server
import anvil.users
from ._anvil_designer import MySubscriptionTemplate
from anvil import *


class MySubscription(MySubscriptionTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.return_to_account = False
    self._busy = False
    self._billing_eligible = False
    self._catalog = anvil.server.call("get_subscription_catalog")
    self.account_region.visible = False
    self.signed_out_region.visible = False
    self.refresh_status_label.visible = False
    self.billing_action_status_label.visible = False
    self.billing_portal_button.enabled = False
    self.billing_portal_button.text = "Manage billing" if self._catalog["billing_portal_available"] else "Manage billing · Coming soon"
    self._refresh_details()
    self._set_busy(False)

  def _set_busy(self, busy):
    self._busy = busy
    self.signin_button.enabled = not busy
    self.refresh_button.enabled = not busy
    self.dashboard_button.enabled = not busy
    self.plans_button.enabled = not busy
    self.account_plans_button.enabled = not busy
    self.billing_portal_button.enabled = not busy and self._billing_eligible and self._catalog["billing_portal_available"]

  def _refresh_details(self):
    user = anvil.users.get_user()
    self.signed_out_region.visible = user is None
    self.account_region.visible = False
    self._billing_eligible = False
    if user is None:
      return
    details = anvil.server.call("get_my_subscription")
    membership = details["membership"]
    self._billing_eligible = membership["source"] == "subscription"
    self.email_label.text = details["email"]
    member_since = details["member_since"]
    self.member_since_label.text = member_since.strftime("%b %d, %Y") if member_since else "Not recorded"
    self.plan_badge.text = membership["label"]
    self.plan_badge.role = "membership-badge-premium" if membership["is_premium"] else "membership-badge-free"
    self.membership_detail_label.text = membership["detail"]
    self.status_label.text = details["status_label"]
    self.access_type_label.text = details["access_type_label"]
    expires_at = membership["expires_at"]
    if expires_at is not None:
      self.access_until_label.text = expires_at.strftime("%b %d, %Y at %H:%M UTC")
    elif membership["is_premium"]:
      self.access_until_label.text = "No expiry set"
    else:
      self.access_until_label.text = "Free membership has no expiry"
    self.billing_status_label.text = details["billing_status_label"]
    self.billing_detail_label.text = details["billing_detail"]
    self.renewal_label.text = details["renewal_label"]
    self.account_region.visible = True

  @handle("dashboard_button", "click")
  def dashboard_click(self, **event_args):
    if not self._busy:
      set_url_hash({})

  @handle("plans_button", "click")
  def plans_click(self, **event_args):
    if not self._busy:
      set_url_hash({"page": "subscribe"})

  @handle("account_plans_button", "click")
  def account_plans_click(self, **event_args):
    self.plans_click(**event_args)

  @handle("signin_button", "click")
  def signin_click(self, **event_args):
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
      self._refresh_details()
    finally:
      self._set_busy(False)

  @handle("refresh_button", "click")
  def refresh_click(self, **event_args):
    if self._busy:
      return
    self._set_busy(True)
    self.refresh_status_label.visible = False
    try:
      self._refresh_details()
      self.refresh_status_label.text = "Membership details are up to date."
      self.refresh_status_label.visible = self.account_region.visible
    finally:
      self._set_busy(False)

  @handle("billing_portal_button", "click")
  def billing_portal_click(self, **event_args):
    if self._busy or not self._catalog["billing_portal_available"] or not self._billing_eligible:
      return
    self._set_busy(True)
    self.billing_action_status_label.visible = False
    try:
      user = anvil.users.get_user()
      if user is None:
        user = anvil.users.login_with_form(allow_cancel=True)
        if user is None:
          return
        self.return_to_account = True
        self._refresh_details()
      if get_open_form() is not self or not self._billing_eligible:
        return
      result = anvil.server.call("open_subscription_billing_portal")
      if result["ok"]:
        raise RuntimeError("Subscription billing portal redirect is not configured.")
      if get_open_form() is self:
        self.billing_action_status_label.text = result["message"]
        self.billing_action_status_label.visible = True
    finally:
      self._set_busy(False)
