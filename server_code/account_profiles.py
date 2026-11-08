import anvil.secrets
"""Account profile edits with server-enforced, case-insensitive usernames.

Users still sign in with their email address. The Users service exposes a
signed-in user's own username; table-wide access and the normalized lookup key
remain private. Profile payloads include only the fields needed by onboarding.
"""

import re
from datetime import datetime, timedelta, timezone

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
  from investor_profiles import profile_summary_for
  user = _profile_user()
  changed = user["display_name_updated_at"]
  if changed is not None and changed.tzinfo is None:
    changed = changed.replace(tzinfo=timezone.utc)
  next_change = changed + timedelta(days=7) if changed else None
  return {"username": user["username"], "email": user["email"],
          "display_name": user["display_name"] or user["username"] or "",
          "member_since": user["signed_up"], "investor": profile_summary_for(user),
          "next_display_name_change_at": next_change,
          "can_change_display_name": next_change is None or datetime.now(timezone.utc) >= next_change}


@anvil.server.callable(require_user=True)
@in_transaction
def set_my_username(username):
  user = _profile_user()
  if not isinstance(username, str):
    return {"ok": False, "message": _USERNAME_GUIDANCE, "username": user["username"]}
  username = username.strip()
  if user["username"]:
    return {"ok": username == user["username"], "username": user["username"],
            "message": "Username saved." if username == user["username"] else
            "Your username is permanent. You can change your display name instead."}
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


@anvil.server.callable(require_user=True)
@in_transaction
def set_my_display_name(display_name):
  user = _profile_user()
  if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 40 or any(ord(c) < 32 for c in display_name):
    return {"ok": False, "message": "Use a display name of 1–40 characters, without line breaks."}
  display_name = display_name.strip()
  if not user["username"]:
    return {"ok": False, "message": "Choose your permanent username first."}
  profile = get_my_profile()
  if display_name == profile["display_name"]:
    return {"ok": True, "message": "Your display name is already up to date."}
  next_change = profile["next_display_name_change_at"]
  now = datetime.now(timezone.utc)
  if next_change and now < next_change:
    return {"ok": False, "message": "You can change your display name again on %s UTC." % next_change.strftime("%b %d at %H:%M"),
            "next_display_name_change_at": next_change}
  user.update(display_name=display_name, display_name_updated_at=now)
  return {"ok": True, "message": "Display name saved. You can change it again in 7 days.",
          "display_name": display_name, "next_display_name_change_at": now + timedelta(days=7)}


@anvil.server.callable
def sign_in_with_identifier(identifier, password, remember=False):
  failure = {"ok": False, "message": "Couldn't sign in. Check your details, or use email sign-in and account recovery."}
  if not isinstance(identifier, str) or not isinstance(password, str) or not 1 <= len(identifier.strip()) <= 254 or not 1 <= len(password) <= 4096:
    return failure
  identifier = identifier.strip().lower()
  if "@" in identifier:
    email = identifier
  else:
    user = app_tables.users.get(username_key=identifier)
    if user is None:
      return failure
    email = user["email"]
  try:
    user = anvil.users.login_with_email(email, password, remember=remember is True)
  except (anvil.users.MFARequired, anvil.users.PasswordResetRequested):
    return {"ok": False, "standard_login": True, "message": "Continue with email sign-in to complete account verification."}
  except anvil.users.AuthenticationFailed:
    return failure
  return {"ok": user is not None, "message": "Signed in." if user is not None else failure["message"]}
