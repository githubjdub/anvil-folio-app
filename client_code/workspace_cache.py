"""Account metadata shared by retained pages; analysis stays in its owning Form."""

from time import time
import anvil.js
import anvil.server
import anvil.users

_account_id = None
_workspace = None
_loaded_at = 0
_revision = 0
_refresh_pending = None

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
  if _workspace is not None and time() - _loaded_at >= CACHE_SECONDS:
    _schedule_refresh()
  return _revision


def _schedule_refresh():
  """Keep the last account snapshot usable while one silent read is in flight."""
  global _refresh_pending
  scope = (_account_id, _revision)
  if _refresh_pending == scope:
    return
  _refresh_pending = scope

  def refresh():
    global _workspace, _loaded_at, _revision, _refresh_pending
    try:
      user = anvil.users.get_user()
      if (user is None or user.get_id() != scope[0]
          or (_account_id, _revision) != scope):
        return
      result = anvil.server.call_s("get_portfolio_workspace")
      user = anvil.users.get_user()
      if (user is not None and user.get_id() == scope[0]
          and (_account_id, _revision) == scope):
        _workspace = result
        _loaded_at = time()
        _revision += 1
    finally:
      if _refresh_pending == scope:
        _refresh_pending = None

  # Navigation completes before this callback runs. Unexpected failures still
  # reach Anvil's error handler; a failed read never replaces good cached data.
  anvil.js.window.setTimeout(anvil.js.report_exceptions(refresh), 0)


def account_workspace(force=False):
  global _workspace, _loaded_at
  workspace_revision()
  account_id = _account_id
  if force:
    invalidate_workspace()
  if account_id is None:
    return None
  if _workspace is not None:
    return _workspace
  revision = _revision
  result = anvil.server.call("get_portfolio_workspace")
  current = anvil.users.get_user()
  if current is None or current.get_id() != account_id or revision != _revision:
    return None
  _workspace = result
  _loaded_at = time()
  return result
