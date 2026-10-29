"""Named page opens and approximate focused time; never capture input contents."""

import anvil.js
import anvil.server
import anvil.users

_page = None
_account_id = None
_last_tick = None
_last_interaction = None
_listeners = []
_sending = False
_generation = 0
_timer_id = None
# Both are documented anvil.server transport exceptions. Older generated
# stubs omit RuntimeUnavailableError, so resolve that class by name.
_transport_errors = (anvil.server.AppOfflineError,
                     getattr(anvil.server, 'RuntimeUnavailableError'))


def _clock():
  return anvil.js.window.performance.now() / 1000


def _allowed():
  navigator = anvil.js.window.navigator
  return not (getattr(navigator, "globalPrivacyControl", False) is True
              or getattr(navigator, "doNotTrack", None) in ("1", "yes"))


def _focused():
  document = anvil.js.window.document
  return document.visibilityState == "visible" and document.hasFocus()


def _interaction(event):
  global _last_interaction, _last_tick
  now = _clock()
  if _last_interaction is None or now - _last_interaction > 120:
    _last_tick = now
  _last_interaction = now


def _visibility(event):
  global _last_tick, _last_interaction
  _last_tick = _clock()
  if _focused():
    _last_interaction = _last_tick


def _install_listeners():
  global _timer_id
  if _listeners:
    return
  document = anvil.js.window.document
  activity = anvil.js.report_exceptions(_interaction)
  visibility = anvil.js.report_exceptions(_visibility)
  tick = anvil.js.report_exceptions(heartbeat)
  _listeners.extend([activity, visibility, tick])
  for event in ("pointerdown", "keydown", "scroll"):
    document.addEventListener(event, activity, {"passive": True, "capture": True})
  document.addEventListener("visibilitychange", visibility)
  anvil.js.window.addEventListener("focus", visibility)
  anvil.js.window.addEventListener("blur", visibility)
  # One app-wide timer also measures Profile and Shared routes. Its callback
  # result is intentionally unused; _sending prevents overlapping requests.
  _timer_id = anvil.js.window.setInterval(tick, 30000)


def heartbeat():
  global _last_tick, _sending
  now = _clock()
  previous = _last_tick
  _last_tick = now
  if (_sending or _page is None or previous is None or not _allowed() or not _focused()
      or _last_interaction is None or now - previous > 45):
    return
  seconds = min(now, _last_interaction + 120) - previous
  if seconds < 1:
    return
  page = _page
  _sending = True
  try:
    anvil.server.call_s("record_site_activity", page, seconds)
  except _transport_errors:
    # Telemetry is best effort: discard this interval, retry at the next tick.
    # Navigation and the user's actual app work must not depend on telemetry.
    return
  finally:
    _sending = False


def pause_tracking():
  global _page, _last_tick, _generation
  _generation += 1
  generation = _generation
  heartbeat()
  if generation == _generation:
    _page = None
    _last_tick = None


def record_page(page):
  global _page, _account_id, _last_tick, _last_interaction, _generation
  _generation += 1
  generation = _generation
  try:
    user = anvil.users.get_user()
  except _transport_errors:
    if generation == _generation:
      _page = None
      _last_tick = None
    return
  if generation != _generation:
    return
  account_id = user.get_id() if user is not None else None
  if page == _page and account_id == _account_id:
    return
  if account_id == _account_id:
    heartbeat()
  if generation != _generation:
    return
  _page = None
  _account_id = account_id
  if not _allowed():
    return
  _install_listeners()
  try:
    result = anvil.server.call_s("record_site_page", page)
  except _transport_errors:
    return
  if generation == _generation and result.get("ok"):
    _page = page
    _last_tick = _last_interaction = _clock()
