"""Browser-local screen privacy; the underlying portfolio data stays intact."""

import anvil.js


_STORAGE_KEY = "folio.hide_amounts"
_enabled = None


def _storage_unavailable(error):
  return getattr(error.original_error, "name", None) in ("SecurityError", "QuotaExceededError")


def _apply():
  root = anvil.js.window.document.documentElement
  if root is None:
    raise RuntimeError("Screen privacy must initialize after the document loads.")
  root.setAttribute("data-privacy", "on" if _enabled else "off")


def initialize():
  """Restore before the dashboard constructs any financial displays."""
  global _enabled
  if _enabled is None:
    try:
      saved = anvil.js.window.localStorage.getItem(_STORAGE_KEY)
    except anvil.js.ExternalError as error:
      if not _storage_unavailable(error):
        raise
      saved = None
    _enabled = saved == "on"
    _apply()
  return _enabled


def is_enabled():
  return initialize()


def set_enabled(enabled):
  """Update every mounted Form immediately, including newly loaded rows."""
  global _enabled
  initialize()
  _enabled = bool(enabled)
  _apply()
  try:
    anvil.js.window.localStorage.setItem(_STORAGE_KEY, "on" if _enabled else "off")
  except anvil.js.ExternalError as error:
    if not _storage_unavailable(error):
      raise
