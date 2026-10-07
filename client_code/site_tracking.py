"""Record named page views without URLs, portfolio data, or visitor IDs."""

import anvil.js
import anvil.server


def record_page(page):
  navigator = anvil.js.window.navigator
  if (getattr(navigator, "globalPrivacyControl", False) is True
      or getattr(navigator, "doNotTrack", None) in ("1", "yes")):
    return
  # Server-owned sessions establish visits and limit repeated events. Nothing
  # is added to browser storage, and share tokens never leave the route parser.
  anvil.server.call_s("record_site_page", page)
