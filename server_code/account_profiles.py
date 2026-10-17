"""Account profile edits with server-enforced, case-insensitive usernames.

Users still sign in with their email address. The Users service exposes a
signed-in user's own username; table-wide access and the normalized lookup key
remain private. Profile payloads include only the fields needed by onboarding.
"""

import re

import anvil.server
import anvil.users
from anvil.tables import app_tables, in_transaction


_USERNAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,23}")
_USERNAME_GUIDANCE = (
  "Use 3–24 letters, numbers, or underscores, starting with a letter."
)


def _profile_user():
  user = anvil.users.get_user()
  if user is None or user["enabled"] is not True:
    raise anvil.users.AuthenticationFailed("Sign in to an enabled account to update your profile.")
  return user


@anvil.server.callable(require_user=True)
def get_my_profile():
  user = _profile_user()
  return {"username": user["username"], "email": user["email"]}


@anvil.server.callable(require_user=True)
@in_transaction
def set_my_username(username):
  user = _profile_user()
  if not isinstance(username, str):
    return {"ok": False, "message": _USERNAME_GUIDANCE, "username": user["username"]}
  username = username.strip()
  if _USERNAME_PATTERN.fullmatch(username) is None:
    return {"ok": False, "message": _USERNAME_GUIDANCE, "username": user["username"]}

  username_key = username.lower()
  # Default serializable isolation includes this lookup, preventing simultaneous
  # claims of an unused username. The decorator retries a transaction conflict.
  existing = app_tables.users.get(username_key=username_key)
  if existing is not None and existing.get_id() != user.get_id():
    return {"ok": False, "message": "That username is already taken. Try another.",
            "username": user["username"]}

  user.update(username=username, username_key=username_key)
  return {"ok": True, "message": "Username saved.", "username": username}
