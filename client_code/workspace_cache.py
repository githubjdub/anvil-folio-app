"""Account metadata shared by retained pages; analysis stays in its owning Form."""

from time import time
import anvil.js
import anvil.server
import anvil.users
from . import startup_performance

_account_id = None
_workspace = None
_loaded_at = 0
_revision = 0
_refresh_pending = None

CACHE_SECONDS = 60


class StartupRequest:
  """One account-scoped read that can run while the dashboard is constructed."""

  def __init__(self, account_id, portfolio_id):
    self.account_id = account_id
    self.portfolio_id = portfolio_id
    self.started = False
    self.done = False
    self.succeeded = False
    self.result = None
    self._cancelled = False
    self._ready = None

  def start(self, on_started):
    def load():
      self.started = True
      # A server call yields to the browser. Build the Form in that interval,
      # including when the request fails or the account has just changed.
      anvil.js.window.setTimeout(anvil.js.report_exceptions(on_started), 0)
      try:
        user = anvil.users.get_user()
        if self._cancelled or user is None or user.get_id() != self.account_id:
          return
        with anvil.server.no_loading_indicator:
          startup_performance.mark("values_load_start")
          result = startup_workspace(self.portfolio_id)
          startup_performance.mark("values_load_end")
        if not self._cancelled:
          self.result = result
          self.succeeded = True
      finally:
        self.done = True
        ready, self._ready = self._ready, None
        if ready is not None:
          self.when_ready(ready)

    anvil.js.window.setTimeout(anvil.js.report_exceptions(load), 0)

  def when_ready(self, callback):
    if self.done:
      # Deliver both fast and slow responses after the Form attaches. Errors
      # still propagate from load(); this callback also releases busy controls.
      anvil.js.window.setTimeout(anvil.js.report_exceptions(callback), 0)
    else:
      self._ready = callback

  def cancel(self):
    self._cancelled = True
    self.result = None


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


def startup_workspace(portfolio_id=None):
  """Load account metadata and the visible saved values in one request."""
  global _workspace, _loaded_at, _revision
  workspace_revision()
  account_id, revision = _account_id, _revision
  if account_id is None:
    return None
  result = anvil.server.call("get_portfolio_startup", portfolio_id)
  current = anvil.users.get_user()
  if current is None or current.get_id() != account_id:
    return None
  if revision == _revision:
    _workspace = result["workspace"]
    _loaded_at = time()
    _revision += 1
  return result
