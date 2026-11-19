"""Small, serialized preparation jobs that start after the page can paint."""

import anvil.js
import anvil.server

_jobs = []
_scheduled = False
_running = False


def defer(callback, is_current=None, priority=False):
  if priority:
    # Core data must not wait for an optional request already in flight. A
    # separate callback also leaves time to paint the attached loading state.
    def run_priority():
      if is_current is None or is_current():
        with anvil.server.no_loading_indicator:
          callback()
    anvil.js.window.setTimeout(anvil.js.report_exceptions(run_priority), 32)
    return
  job = (callback, is_current)
  _jobs.append(job)
  _schedule_next()


def _schedule_next():
  global _scheduled
  if _scheduled or _running or not _jobs:
    return
  _scheduled = True
  # Leave a frame for the page and user input before each preparation step.
  anvil.js.window.setTimeout(anvil.js.report_exceptions(_run_next), 100)


def _run_next():
  global _scheduled, _running
  _scheduled = False
  callback, is_current = _jobs.pop(0)
  _running = True
  try:
    if is_current is None or is_current():
      with anvil.server.no_loading_indicator:
        callback()
  finally:
    _running = False
    _schedule_next()
