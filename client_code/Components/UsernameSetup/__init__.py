import anvil.server
import anvil.users
from ._anvil_designer import UsernameSetupTemplate
from anvil import *


class UsernameSetup(UsernameSetupTemplate):
  def __init__(self, account_id=None, email=None, **properties):
    super().__init__(**properties)
    self._account_id = account_id
    self._busy = False
    self.account_label.text = email or "Your account"
    self.status_label.visible = False

  @handle("", "show")
  def form_show(self, **event_args):
    self.username_box.focus()

  def _same_account(self):
    user = anvil.users.get_user()
    return user is not None and user.get_id() == self._account_id

  def _set_busy(self, busy):
    self._busy = busy
    self.username_box.enabled = not busy
    self.save_button.enabled = not busy
    self.signout_button.enabled = not busy
    self.save_button.text = "Saving…" if busy else "Save & continue"

  @handle("save_button", "click")
  @handle("username_box", "pressed_enter")
  def save_click(self, **event_args):
    if self._busy:
      return
    if not self._same_account():
      self.raise_event("x-close-alert", value=False)
      return
    self._set_busy(True)
    self.status_label.visible = False
    try:
      result = anvil.server.call("set_my_username", self.username_box.text)
      if not self._same_account():
        self.raise_event("x-close-alert", value=False)
      elif result["ok"]:
        self.raise_event("x-close-alert", value=True)
      else:
        self.status_label.text = result["message"]
        self.status_label.visible = True
    finally:
      self._set_busy(False)

  @handle("signout_button", "click")
  def signout_click(self, **event_args):
    if self._busy:
      return
    self._set_busy(True)
    try:
      if self._same_account():
        anvil.users.logout(invalidate_client_objects=True)
      self.raise_event("x-close-alert", value=False)
      set_url_hash({})
    finally:
      self._set_busy(False)
