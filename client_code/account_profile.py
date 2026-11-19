"""One username setup prompt shared by sign-in and remembered sessions."""

import anvil.server
import anvil.users
from anvil import alert
from .workspace_cache import account_workspace, invalidate_workspace


_checking_profile = False


def sign_in():
  user = anvil.users.get_user()
  if user is not None:
    return user
  from .Components.SignIn import SignIn
  action = alert(SignIn(), title="Welcome to Folio", buttons=[], dismissible=False,
                 role="username-setup-dialog")
  if action == "signup":
    return anvil.users.signup_with_form(allow_cancel=True)
  if action == "standard":
    return anvil.users.login_with_form(allow_cancel=True)
  return anvil.users.get_user() if action == "signed_in" else None


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
    # Usernames are permanent. A remembered, already-named account needs no
    # server round trip or onboarding Form imports to enter the workspace.
    if user["username"]:
      return True
    account_id = user.get_id()
    workspace = account_workspace()
    current_user = anvil.users.get_user()
    if workspace is None or current_user is None or current_user.get_id() != account_id:
      return False
    profile = workspace["profile"]
    if profile["username"]:
      return True
    from .Components.UsernameSetup import UsernameSetup
    saved = alert(
      UsernameSetup(account_id=account_id, email=profile["email"]),
      title="Choose your username", buttons=[], dismissible=False,
      role="username-setup-dialog",
    )
    current_user = anvil.users.get_user()
    if saved:
      invalidate_workspace()
    return bool(saved and current_user is not None and current_user.get_id() == account_id)
  finally:
    _checking_profile = False
