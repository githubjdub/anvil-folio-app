"""Record named page views without URLs, portfolio data, or visitor IDs."""

import anvil.js
import anvil.server

_pending_pages = []
_scheduled = False


def _tracking_allowed():
  navigator = anvil.js.window.navigator
  return not (getattr(navigator, "globalPrivacyControl", False) is True
              or getattr(navigator, "doNotTrack", None) in ("1", "yes"))


def _flush_pages():
  global _scheduled
  # Server-owned sessions establish visits and limit repeated events. Nothing
  # is added to browser storage, and share tokens never leave the route parser.
  try:
    while _pending_pages:
      if not _tracking_allowed():
        _pending_pages.clear()
        return
      page = _pending_pages.pop(0)
      anvil.server.call_s("record_site_page", page)
  finally:
    _scheduled = False


def record_page(page):
  global _scheduled
  if not _tracking_allowed():
    return
  _pending_pages.append(page)
  if not _scheduled:
    _scheduled = True
    # Let the navigation event finish and the browser paint before analytics
    # makes its silent server call. Only one delivery is in flight at a time.
    anvil.js.window.setTimeout(anvil.js.report_exceptions(_flush_pages), 0)
