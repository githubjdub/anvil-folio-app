"""One username setup prompt shared by sign-in and remembered sessions."""

import anvil.server
import anvil.users
from anvil import alert
from .Components.UsernameSetup import UsernameSetup


_checking_profile = False


def username_prompt_open():
  return _checking_profile


def ensure_username():
  global _checking_profile
  if _checking_profile:
    return False
  _checking_profile = True
  try:
    user = anvil.users.get_user()
    if user is None:
      return False
    account_id = user.get_id()
    profile = anvil.server.call("get_my_profile")
    current_user = anvil.users.get_user()
    if current_user is None or current_user.get_id() != account_id:
      return False
    if profile["username"]:
      return True
    saved = alert(
      UsernameSetup(account_id=account_id, email=profile["email"]),
      title="Choose your username", buttons=[], dismissible=False,
      role="username-setup-dialog",
    )
    current_user = anvil.users.get_user()
    return bool(saved and current_user is not None and current_user.get_id() == account_id)
  finally:
    _checking_profile = False
