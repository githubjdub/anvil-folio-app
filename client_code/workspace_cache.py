"""Small account metadata cache; private analysis stays in its owning Form."""

from time import time
import anvil.server
import anvil.users

_account_id = None
_workspace = None
_loaded_at = 0
_revision = 0


def invalidate_workspace():
  global _workspace, _loaded_at, _revision
  _workspace = None
  _loaded_at = 0
  _revision += 1


def account_workspace(force=False):
  global _account_id, _workspace, _loaded_at
  user = anvil.users.get_user()
  account_id = user.get_id() if user is not None else None
  if account_id != _account_id:
    invalidate_workspace()
    _account_id = account_id
  if account_id is None:
    return None
  if not force and _workspace is not None and time() - _loaded_at < 60:
    return _workspace
  revision = _revision
  result = anvil.server.call("get_portfolio_workspace")
  current = anvil.users.get_user()
  if current is None or current.get_id() != account_id or revision != _revision:
    return None
  _workspace = result
  _loaded_at = time()
  return result
