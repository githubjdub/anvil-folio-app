import anvil.server
from ._anvil_designer import SignInTemplate
from anvil import *


class SignIn(SignInTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._busy = False
    self.status_label.visible = False

  def _set_busy(self, value):
    self._busy = value
    for component in (self.login_button, self.signup_button, self.recovery_button,
                      self.cancel_button, self.identifier_box, self.password_box, self.remember_box):
      component.enabled = not value

  @handle("login_button", "click")
  @handle("password_box", "pressed_enter")
  def login_click(self, **event_args):
    if self._busy:
      return
    self._set_busy(True)
    try:
      result = anvil.server.call("sign_in_with_identifier", self.identifier_box.text,
                                 self.password_box.text, self.remember_box.checked)
      self.password_box.text = ""
      if result["ok"]:
        self.raise_event("x-close-alert", value="signed_in")
      elif result.get("standard_login"):
        self.raise_event("x-close-alert", value="standard")
      else:
        self.status_label.text = result["message"]
        self.status_label.visible = True
    finally:
      self.password_box.text = ""
      self._set_busy(False)

  @handle("signup_button", "click")
  def signup_click(self, **event_args):
    if not self._busy:
      self.raise_event("x-close-alert", value="signup")

  @handle("recovery_button", "click")
  def recovery_click(self, **event_args):
    if not self._busy:
      self.raise_event("x-close-alert", value="standard")

  @handle("cancel_button", "click")
  def cancel_click(self, **event_args):
    if not self._busy:
      self.raise_event("x-close-alert", value=None)
