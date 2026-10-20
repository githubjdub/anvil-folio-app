"""Browser-local display preference; never sends account or portfolio data."""

import anvil.js
from anvil import get_open_form
from . import chart_theme


_STORAGE_KEY = "portfoliora.appearance"
_mode = None


def _storage_unavailable(error):
  return getattr(error.original_error, "name", None) in ("SecurityError", "QuotaExceededError")


def initialize():
  """Restore once before Forms construct their charts."""
  global _mode
  if _mode is not None:
    return _mode
  try:
    saved = anvil.js.window.localStorage.getItem(_STORAGE_KEY)
  except anvil.js.ExternalError as error:
    if not _storage_unavailable(error):
      raise
    saved = None
  _mode = saved if saved in ("light", "dark") else "light"
  _apply()
  return _mode


def _apply():
  # The document root covers dialogs and detached popovers as well as Forms.
  root = anvil.js.window.document.documentElement
  mode = _mode
  if root is None or mode is None:
    raise RuntimeError("Display appearance must initialize after the document loads.")
  root.setAttribute("data-appearance", mode)
  chart_theme.set_mode(mode)


def current_mode():
  return initialize()


def set_mode(mode):
  """Apply immediately; blocked browser storage still permits session changes."""
  global _mode
  if mode not in ("light", "dark"):
    raise ValueError("Choose light or dark appearance.")
  initialize()
  if mode == _mode:
    return
  _mode = mode
  _apply()
  try:
    anvil.js.window.localStorage.setItem(_STORAGE_KEY, mode)
  except anvil.js.ExternalError as error:
    if not _storage_unavailable(error):
      raise
  active = get_open_form()
  refresh = getattr(active, "apply_appearance", None)
  if refresh is not None:
    refresh()
