"""Local browser startup measurements, with no account data or telemetry RPC."""

import anvil.js

_marks = {}
_frames = set()
_finishing = False
_finished = False


def mark(name):
  if _finished or name in _marks:
    return
  _marks[name] = round(anvil.js.window.performance.now(), 1)
  anvil.js.window.performance.mark("folio:" + name)


def after_frame(name):
  if name in _frames:
    return
  _frames.add(name)
  # Two frames record a paint opportunity after the Form was attached. Actual
  # browser FCP is reported separately; it can include the runtime's loader.
  def first_frame(timestamp):
    anvil.js.window.requestAnimationFrame(
      anvil.js.report_exceptions(lambda timestamp: mark(name)))
  anvil.js.window.requestAnimationFrame(anvil.js.report_exceptions(first_frame))


def _report():
  phases = {}
  for label, start, end in (
    ("portfolio_import", "portfolio_import_start", "portfolio_import_end"),
    ("template_construction", "portfolio_build_start", "portfolio_template_ready"),
    ("form_setup", "portfolio_template_ready", "portfolio_shell_ready"),
    ("account_load", "account_load_start", "account_load_end"),
    ("saved_values_load", "values_load_start", "values_load_end"),
    ("saved_portfolio_restore", "portfolio_restore_start", "portfolio_restore_end"),
    ("analysis_apply", "analysis_apply_start", "analysis_apply_end"),
  ):
    if start in _marks and end in _marks:
      phases[label] = round(_marks[end] - _marks[start], 1)
  if all(name in _marks for name in (
      "values_load_start", "values_load_end", "portfolio_import_start", "portfolio_shell_ready")):
    phases["values_request_during_page_build"] = round(max(0,
      min(_marks["values_load_end"], _marks["portfolio_shell_ready"])
      - max(_marks["values_load_start"], _marks["portfolio_import_start"])), 1)
    phases["values_wait_after_page_build"] = round(max(0,
      _marks["values_load_end"] - _marks["portfolio_shell_ready"]), 1)
  paints = {entry.name: round(entry.startTime, 1)
            for entry in anvil.js.window.performance.getEntriesByType("paint")}
  result = {"navigation_ms": dict(_marks), "phases_ms": phases,
            "browser_paint_ms": paints,
            "note": "All times are milliseconds. Phase durations can overlap. analysis_apply is included in saved_portfolio_restore."}
  navigation = anvil.js.window.performance.getEntriesByType("navigation")
  if navigation:
    entry = navigation[0]
    result["document_ms"] = round(entry.responseEnd - entry.requestStart, 1)
    result["document_transfer_bytes"] = entry.transferSize
  return result


def complete():
  global _finishing
  if _finishing or _finished:
    return
  _finishing = True
  mark("page_ready")
  def first_frame(timestamp):
    anvil.js.window.requestAnimationFrame(anvil.js.report_exceptions(finish))
  def finish(timestamp):
    global _finished
    mark("ready_frame")
    report = _report()
    _finished = True
    anvil.js.window.folioStartupTimings = report
    anvil.js.window.console.info("Folio startup timings (milliseconds)", report)
    anvil.js.window.console.table(report["phases_ms"])
  anvil.js.window.requestAnimationFrame(anvil.js.report_exceptions(first_frame))
