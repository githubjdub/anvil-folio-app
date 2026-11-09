"""Account metadata shared by retained pages; analysis stays in its owning Form."""

from time import time
import anvil.server
import anvil.users

_account_id = None
_workspace = None
_loaded_at = 0
_revision = 0

CACHE_SECONDS = 60


def invalidate_workspace():
  global _workspace, _loaded_at, _revision
  _workspace = None
  _loaded_at = 0
  _revision += 1


def workspace_revision():
  """A cheap change token for retained pages, scoped to the current account."""
  global _account_id
  user = anvil.users.get_user()
  account_id = user.get_id() if user is not None else None
  if account_id != _account_id:
    invalidate_workspace()
    _account_id = account_id
  return _revision


def account_workspace(force=False):
  global _workspace, _loaded_at
  workspace_revision()
  account_id = _account_id
  if force:
    invalidate_workspace()
  if account_id is None:
    return None
  if _workspace is not None and time() - _loaded_at < CACHE_SECONDS:
    return _workspace
  revision = _revision
  result = anvil.server.call("get_portfolio_workspace")
  current = anvil.users.get_user()
  if current is None or current.get_id() != account_id or revision != _revision:
    return None
  _workspace = result
  _loaded_at = time()
  return result
