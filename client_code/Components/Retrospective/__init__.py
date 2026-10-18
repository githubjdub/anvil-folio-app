from datetime import date, timedelta
import math

import anvil.server
from ._anvil_designer import RetrospectiveTemplate
from anvil import *
from ... import chart_theme


EXAMPLES = [
  ("Buy yearly lows, sell half at yearly highs", "Buy at a 1-year low in the past year, including dividends, and sell half at the yearly high."),
  ("Buy yearly highs, sell at yearly lows", "Buy at a 52-week high in the past year, including dividends, and sell all at a 52-week low."),
  ("Buy and hold", "Buy and hold in the past year, including dividends."),
]
SECTORS = ["Technology", "Financial", "Healthcare", "Consumer Cyclical", "Consumer Defensive",
           "Communication Services", "Industrials", "Energy", "Basic Materials", "Real Estate",
           "Utilities", "Funds / ETFs", "Unclassified"]


class Retrospective(RetrospectiveTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._busy = False
    self._parent_editable = True
    self._rules_ready = False
    self._last_seed = ""
    self._result = None
    self._chart_mode = None
    self._chart_sizes = {}
    self.set_event_handler("x-set-editable", self.set_editable)
    self.example_dropdown.items = [(title, prompt) for title, prompt in EXAMPLES]
    self.example_dropdown.selected_value = EXAMPLES[0][1]
    self.prompt_box.text = EXAMPLES[0][1]
    self.sector_dropdown.items = [("All sectors", None)] + [(sector, sector) for sector in SECTORS]
    self.sector_dropdown.selected_value = None
    self.entry_dropdown.items = [("Close at a rolling low", "low"), ("Close at a rolling high", "high"),
                                 ("Buy at the first test session's open", "buy_hold")]
    self.exit_dropdown.items = [("Close at a rolling high", "high"), ("Close at a rolling low", "low"),
                                ("Hold through the end", "none")]
    self.entry_dropdown.selected_value = "low"
    self.exit_dropdown.selected_value = "high"
    self.start_picker.date = date.today() - timedelta(days=365)
    self.end_picker.date = date.today()
    self.rules_region.visible = False
    self.results_region.visible = False
    self.include_dividends_checkbox.checked = True
    self._update_controls()

  def reset_workspace(self):
    self._rules_ready = False
    self._last_seed = ""
    self.prompt_box.text = EXAMPLES[0][1]
    self.example_dropdown.selected_value = EXAMPLES[0][1]
    self.tickers_box.text = ""
    self.sector_dropdown.selected_value = None
    self.entry_dropdown.selected_value = "low"
    self.exit_dropdown.selected_value = "high"
    self.lookback_box.text = "365"
    self.exit_percent_box.text = "50"
    self.capital_box.text = "10000"
    self.include_dividends_checkbox.checked = True
    self.start_picker.date = date.today() - timedelta(days=365)
    self.end_picker.date = date.today()
    self.rules_region.visible = False
    self.prompt_warnings_label.visible = False
    self._invalidate_result()
    self.result_plot.data = []
    self.security_results_panel.items = []
    self.trade_results_panel.items = []
    for component in (self.prompt_interpretation_label, self.prompt_warnings_label,
                      self.result_period_label, self.result_warnings_label,
                      self.capital_label, self.equity_label, self.profit_label,
                      self.return_label, self.buy_hold_label, self.benchmark_label,
                      self.dividend_label, self.cash_label, self.drawdown_label):
      component.text = ""
    self._set_status("")
    self._update_controls()

  def set_universe(self, tickers, force=False):
    text = ", ".join(tickers)
    if force or not str(self.tickers_box.text or "").strip() or self.tickers_box.text == self._last_seed:
      if self.tickers_box.text != text:
        self.tickers_box.text = text
        self._invalidate_result()
      self._last_seed = text

  def set_editable(self, editable=True, **event_args):
    self._parent_editable = editable
    self._update_controls()

  def _update_controls(self):
    enabled = self._parent_editable and not self._busy
    for component in (self.prompt_box, self.example_dropdown, self.use_example_button,
                      self.preview_button, self.manual_rules_button, self.tickers_box,
                      self.use_portfolio_button, self.sector_dropdown, self.entry_dropdown,
                      self.exit_dropdown, self.lookback_box, self.exit_percent_box,
                      self.include_dividends_checkbox, self.start_picker, self.end_picker,
                      self.capital_box):
      setattr(component, "enabled", enabled)
    setattr(self.run_button, "enabled", enabled and self._rules_ready)

  def _set_status(self, text, error=False):
    self.status_label.text = text
    self.status_region.classes["is-error"] = error

  def _invalidate_result(self):
    self._result = None
    self.results_region.visible = False

  def _begin_request(self):
    self._busy = True
    self._update_controls()
    self.raise_event("x-retrospective-busy", busy=True)

  def _end_request(self):
    self._busy = False
    self.raise_event("x-retrospective-busy", busy=False)
    self._update_controls()

  @handle("use_portfolio_button", "click")
  def use_portfolio_click(self, **event_args):
    if not self._busy:
      self.raise_event("x-use-portfolio-tickers")

  @handle("use_example_button", "click")
  def use_example_click(self, **event_args):
    self.prompt_box.text = self.example_dropdown.selected_value
    self.prompt_change()

  @handle("prompt_box", "change")
  def prompt_change(self, **event_args):
    self._rules_ready = False
    self.rules_region.visible = False
    self._invalidate_result()
    self._set_status("Interpret the edited prompt to review its rules, or set rules manually.")
    self._update_controls()

  @handle("preview_button", "click")
  def preview_click(self, **event_args):
    if self._busy or not self._parent_editable:
      return
    self._begin_request()
    try:
      self._invalidate_result()
      self._set_status("Interpreting the supported strategy rules…")
      result = anvil.server.call("preview_retrospective_prompt", self.prompt_box.text,
                                 self.sector_dropdown.selected_value)
      if not result["ok"]:
        self._rules_ready = False
        self.rules_region.visible = False
        self._set_status(result["message"], True)
        return
      rules = result["rules"]
      self.entry_dropdown.selected_value = rules["entry_kind"]
      self.exit_dropdown.selected_value = rules["exit_kind"]
      self.lookback_box.text = str(rules["lookback_days"])
      self.exit_percent_box.text = str(rules["exit_fraction"] * 100)
      self.include_dividends_checkbox.checked = rules["include_dividends"]
      self.sector_dropdown.selected_value = rules.get("sector")
      end = self.end_picker.date or date.today()
      self.start_picker.date = self._months_before(end, rules.get("test_months") or 12)
      if rules.get("start_date"):
        self.start_picker.date = self._iso_date(rules["start_date"])
        self.end_picker.date = self._iso_date(rules["end_date"])
      self._rules_ready = True
      self.rules_region.visible = True
      self.prompt_interpretation_label.text = result["description"]
      self.prompt_warnings_label.text = "\n".join(result["warnings"])
      self.prompt_warnings_label.visible = bool(result["warnings"])
      self._set_status("Rules interpreted. Review the dates, tickers, sector, and settings before running.")
    finally:
      self._end_request()

  @handle("manual_rules_button", "click")
  def manual_rules_click(self, **event_args):
    if not self._busy:
      self._rules_ready = True
      self.rules_region.visible = True
      self._invalidate_result()
      self.prompt_interpretation_label.text = "Manual rules — the settings below define the test."
      self.prompt_warnings_label.visible = False
      self._set_status("Set the supported strategy rules below, then run the test.")
      self._update_controls()

  @handle("tickers_box", "change")
  @handle("sector_dropdown", "change")
  @handle("entry_dropdown", "change")
  @handle("exit_dropdown", "change")
  @handle("lookback_box", "change")
  @handle("exit_percent_box", "change")
  @handle("include_dividends_checkbox", "change")
  @handle("start_picker", "change")
  @handle("end_picker", "change")
  @handle("capital_box", "change")
  def settings_change(self, **event_args):
    if not self._busy:
      self._invalidate_result()
      if self._rules_ready:
        self.prompt_interpretation_label.text = "Use the reviewed settings below for this test."
        self.prompt_warnings_label.visible = False
        self._set_status("Settings changed. Run again to update the results.")

  def _number(self, text, name):
    try:
      value = float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
      raise ValueError("Enter a valid %s." % name)
    if math.isnan(value) or math.isinf(value):
      raise ValueError("Enter a finite %s." % name)
    return value

  def _iso_date(self, text):
    year, month, day = [int(part) for part in text.split("-")]
    return date(year, month, day)

  def _months_before(self, end, months):
    target = end.year * 12 + end.month - 1 - months
    year, month = target // 12, target % 12 + 1
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    day = min(end.day, (next_month - timedelta(days=1)).day)
    return date(year, month, day)

  @handle("run_button", "click")
  def run_click(self, **event_args):
    if self._busy or not self._parent_editable or not self._rules_ready:
      return
    try:
      capital = self._number(self.capital_box.text, "starting capital")
      days = self._number(self.lookback_box.text, "lookback")
      fraction = self._number(self.exit_percent_box.text, "sale percentage") / 100
      if days != int(days):
        raise ValueError("Use a whole number of calendar days for the lookback.")
    except ValueError as error:
      self._set_status(str(error), True)
      return
    rules = {"entry_kind": self.entry_dropdown.selected_value, "exit_kind": self.exit_dropdown.selected_value,
             "lookback_days": int(days), "exit_fraction": fraction,
             "include_dividends": bool(self.include_dividends_checkbox.checked)}
    self._begin_request()
    try:
      self._invalidate_result()
      self._set_status("Loading daily prices and dividends, then testing each session in order…")
      result = anvil.server.call("run_retrospective_analysis", self.tickers_box.text, rules,
                                 self.start_picker.date, self.end_picker.date, capital,
                                 self.sector_dropdown.selected_value)
      if not result["ok"]:
        self._set_status(result["message"], True)
        return
      self._result = result
      self._render_result(result)
      self._set_status("Test complete · %d simulated trades. Your real transactions are unchanged." % result["metrics"]["trade_count"])
    finally:
      self._end_request()

  def _money(self, value):
    return "$%s" % format(value, ",.2f")

  def _percent(self, value):
    return "%.2f%%" % (value * 100)

  def _render_result(self, result):
    self.results_region.visible = True
    metrics, metadata = result["metrics"], result["metadata"]
    for component, text in (
      (self.capital_label, self._money(metrics["capital"])),
      (self.equity_label, self._money(metrics["ending_equity"])),
      (self.profit_label, self._money(metrics["profit"])),
      (self.return_label, self._percent(metrics["total_return"])),
      (self.buy_hold_label, self._percent(metrics["buy_hold_return"])),
      (self.benchmark_label, self._percent(metrics["benchmark_return"])),
      (self.dividend_label, self._money(metrics["dividends"])),
      (self.cash_label, self._money(metrics["cash"])),
      (self.drawdown_label, self._percent(metrics["max_drawdown"])),
    ):
      component.text = text
    self.result_period_label.text = "%s – %s · %s · %d executed trades" % (
      metadata["start_date"], metadata["end_date"], ", ".join(metadata["tested_tickers"]), metrics["trade_count"])
    notes = list(result["warnings"])
    for pending in metadata["pending_signals"]:
      notes.append("Pending %s · %s · signal %s: the next session's open falls beyond this test." % (
        pending["action"], pending["ticker"], pending["signal_date"]))
    self.result_warnings_label.text = "\n".join(notes)
    self.security_results_panel.items = [{
      "title": "%s · %s" % (row["ticker"], row["sector"]),
      "detail": "%s · %s allocated · %s dividends" % (row["company_name"], self._money(row["allocation"]), self._money(row["dividends"])),
      "status": row["status"] + (" · " + row["status_detail"] if row["status_detail"] else ""), "value": self._percent(row["total_return"]),
      "subtitle": "%s ending value" % self._money(row["ending_equity"]),
    } for row in result["securities"]]
    self.trade_results_panel.items = [{
      "title": "%s · %s · %s shares" % (row["ticker"], row["action"], format(row["shares"], ",.6f").rstrip("0").rstrip(".")),
      "detail": "Signal: %s · Executed: %s open · %s / share" % (self._signal_label(row), row["execution_date"], self._money(row["price"])),
      "status": row["reason"], "value": self._money(row["value"]), "subtitle": "Simulated trade value",
    } for row in result["trades"]]
    self.no_trades_label.visible = not bool(result["trades"])
    self._render_chart()

  def _render_chart(self):
    result = self._result
    if result is None:
      return
    self._chart_mode = chart_theme.MODE
    self._chart_sizes.clear()
    series = result["series"]
    dates = [point["date"] for point in series]
    returns = {point["date"]: point["return"] for point in series}
    traces = [{"type": "scatter", "mode": "lines", "name": "Your strategy", "x": dates,
               "y": [point["return"] for point in series], "line": {"color": chart_theme.POSITIVE, "width": 2.5},
               "hovertemplate": "%{x}<br>%{y:.2%}<extra>Strategy</extra>"},
              {"type": "scatter", "mode": "lines", "name": "Buy and hold the same stocks", "x": dates,
               "y": [point["buy_hold_return"] for point in series], "line": {"color": chart_theme.AMBER, "width": 2},
               "hovertemplate": "%{x}<br>%{y:.2%}<extra>Buy and hold</extra>"}]
    traces.append({"type": "scatter", "mode": "lines", "name": "SPY · buy and hold", "x": dates,
                   "y": [point["benchmark_return"] for point in series], "line": {"color": chart_theme.BENCHMARK, "width": 1.8},
                   "hovertemplate": "%{x}<br>%{y:.2%}<extra>SPY</extra>"})
    for action, symbol, color in [("Buy", "arrow-up", chart_theme.POSITIVE), ("Sell", "arrow-down", chart_theme.NEGATIVE)]:
      trades = [row for row in result["trades"] if row["action"] == action]
      traces.append({"type": "scatter", "mode": "markers", "name": action, "x": [row["execution_date"] for row in trades],
                     "y": [returns[row["execution_date"]] for row in trades], "marker": {"symbol": symbol, "color": color, "size": 13},
                     "customdata": [[row["ticker"], row["shares"], row["price"], self._signal_label(row)] for row in trades],
                     "hovertemplate": "%{customdata[0]}<br>%{customdata[1]:.4f} shares @ $%{customdata[2]:.2f}<br>Signal: %{customdata[3]}<br>Execution: %{x}<extra>" + action + "</extra>"})
    self.result_plot.data = traces
    self.result_plot.layout = chart_theme.layout({"margin": {"l": 48, "r": 18, "t": 35, "b": 85},
                               "paper_bgcolor": chart_theme.PANEL, "plot_bgcolor": chart_theme.PANEL,
                               "font": {"family": "Inter, sans-serif", "size": 11, "color": chart_theme.MUTED},
                               "hovermode": "closest", "xaxis": {"type": "date"},
                               "yaxis": {"tickformat": ".0%", "gridcolor": chart_theme.GRID},
                               "legend": {"orientation": "h", "x": 0, "y": -0.25}})
    self.result_plot.config = {"displayModeBar": True, "displaylogo": False, "responsive": True, "scrollZoom": False,
                               "modeBarButtonsToRemove": ["select2d", "lasso2d"]}
    self._chart_sizes["result_plot"] = chart_theme.redraw(self.result_plot, self._chart_sizes.get("result_plot"))

  def apply_appearance(self):
    if self._result is not None and self._chart_mode != chart_theme.MODE:
      self._render_chart()
      return True
    return False

  def redraw(self):
    if self.apply_appearance():
      return
    if self._result is not None:
      self._chart_sizes["result_plot"] = chart_theme.redraw(self.result_plot, self._chart_sizes.get("result_plot"))

  def _signal_label(self, trade):
    return trade["signal_date"] if trade["signal_close"] is not None else "Preset before test"
