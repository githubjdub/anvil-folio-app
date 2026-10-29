from ._anvil_designer import ForexTemplate
from anvil import *
import anvil.server
from datetime import date, timedelta
from ... import chart_theme
from ...forex_math import CURRENCIES, cross_rate, convert, calculate_scenario, cash, rate_text


class Forex(ForexTemplate):
  def __init__(self, **properties):
    self._updating = True
    super().__init__(**properties)
    self._editable = True
    self._loading = False
    self._rates: dict = {}
    self._plot_size = None
    self.base_dropdown.items = CURRENCIES
    self.quote_dropdown.items = CURRENCIES
    self.range_dropdown.items = [("1 month", 30), ("3 months", 90)]
    self.direction_dropdown.items = [("Long · buy base currency", "long"), ("Short · sell base currency", "short")]
    self.reset_workspace()

  def reset_workspace(self):
    self._updating = True
    self.base_dropdown.selected_value = "EUR"
    self.quote_dropdown.selected_value = "USD"
    self._scenario_quote = "USD"
    self.range_dropdown.selected_value = 90
    self.convert_box.text = "1000"
    self.direction_dropdown.selected_value = "long"
    self.units_box.text = "10000"
    self.budget_box.text = "100"
    self.costs_box.text = "0"
    for box in (self.entry_box, self.exit_box, self.stop_box, self.target_box):
      box.text = ""
    self._updating = False
    self.scenario_error.visible = False
    self.conversion_error.visible = False
    self.scenario_results.visible = False
    self.scenario_status.text = "Enter your own rates, or start with the reference example."
    self._render_market()
    self._update_controls()

  def set_editable(self, editable, **event_args):
    self._editable = editable
    self._update_controls()

  def _update_controls(self):
    enabled = self._editable and not self._loading
    for component in (self.base_dropdown, self.quote_dropdown, self.range_dropdown, self.swap_button,
                      self.refresh_button, self.convert_box, self.direction_dropdown, self.units_box,
                      self.entry_box, self.exit_box, self.stop_box, self.target_box, self.costs_box, self.budget_box):
      component.enabled = enabled
    different = self.base_dropdown.selected_value != self.quote_dropdown.selected_value
    self.use_rate_button.enabled = enabled and bool(self._rates) and different
    self.calculate_button.enabled = enabled and different
    self.refresh_button.text = "Loading…" if self._loading else "Refresh rates"

  def ensure_loaded(self):
    if not self._rates and not self._loading:
      self._load_rates()

  def _load_rates(self):
    if self._loading:
      return
    self._loading = True
    self.rate_status.text = "Loading daily reference history…"
    self.rate_status.visible = True
    self._update_controls()
    try:
      result = anvil.server.call("get_forex_reference_rates")
      if result["ok"]:
        self._rates = result
        self._render_market()
      else:
        self.rate_status.text = result["message"] + (" Showing the previously loaded observations." if self._rates else " You can still enter rates in the scenario below.")
        self.rate_status.visible = True
    finally:
      self._loading = False
      self._update_controls()
    self.redraw()

  def _pair(self):
    return self.base_dropdown.selected_value, self.quote_dropdown.selected_value

  def _latest(self):
    if not self._rates:
      return None
    base, quote = self._pair()
    return cross_rate(self._rates["history"][-1]["rates"], base, quote)

  def _render_market(self):
    base, quote = self._pair()
    self.pair_label.text = "%s / %s · quote per base unit" % (base, quote)
    self.conversion_input_label.text = "Amount to convert · " + base
    self.scenario_currency_label.text = ("Choose two different currencies to enable position scenarios. A currency converts to itself at 1:1."
                                         if base == quote else "Units in %s · all rates, costs, risk budget and results in %s" % (base, quote))
    latest = self._latest()
    if latest is None:
      self.rate_label.text = "—"
      self.reference_label.text = "Daily observations load when you open this tab."
      self.change_label.text = "—"
      self.rate_status.visible = False
    else:
      self.rate_label.text = rate_text(latest, quote) + " " + quote
      self.reference_label.text = "1 %s · published %s · ECB reference" % (base, self._rates["as_of"])
      history = self._rates["history"]
      change = ((latest / cross_rate(history[-2]["rates"], base, quote) - 1) * 100) if len(history) > 1 else None
      self.change_label.text = ("{:+.2f}%".format(change)) if change is not None else "—"
      self.change_label.foreground = chart_theme.POSITIVE if change is not None and change >= 0 else chart_theme.NEGATIVE
      self.rate_status.visible = self._rates["age_days"] > 4
      self.rate_status.text = "The latest available reference is %s days old. Check its date before using it." % self._rates["age_days"]
    self._render_plot()
    self._convert()

  def _render_plot(self):
    base, quote = self._pair()
    points = []
    if self._rates:
      # The window is relative to the latest observation, so an outage never creates fake data.
      year, month, day = [int(part) for part in self._rates["as_of"].split("-")]
      last = date(year, month, day)
      first = (last - timedelta(days=self.range_dropdown.selected_value)).isoformat()
      points = [(row["date"], cross_rate(row["rates"], base, quote))
                for row in self._rates["history"] if row["date"] >= first]
    self.chart_caption.text = "%s per %s · daily published observations" % (quote, base)
    self.history_plot.data = [{"type": "scatter", "mode": "lines", "name": base + "/" + quote,
                               "x": [point[0] for point in points], "y": [point[1] for point in points],
                               "line": {"color": chart_theme.BENCHMARK, "width": 2.5},
                               "hovertemplate": "%{x}<br>1 " + base + " = %{y:.5f} " + quote + "<extra></extra>"}]
    self.history_plot.layout = chart_theme.layout({
      "margin": {"l": 65, "r": 12, "t": 12, "b": 36}, "showlegend": False,
      "xaxis": {"type": "date", "fixedrange": True, "showgrid": False},
      "yaxis": {"fixedrange": True, "zeroline": False, "tickformat": ".3f" if quote == "JPY" else ".4f"},
      "annotations": [] if points else [{"text": "Reference history will appear here", "xref": "paper", "yref": "paper", "x": .5, "y": .5, "showarrow": False}],
    })
    self.history_plot.config = {"displayModeBar": False, "responsive": True}
    self._plot_size = None

  def _convert(self):
    self.conversion_error.visible = False
    self.conversion_label.text = "—"
    if self._latest() is None:
      return
    try:
      value = convert(self.convert_box.text, self._latest())
    except ValueError as error:
      self.conversion_error.text = str(error)
      self.conversion_error.visible = True
      return
    self.conversion_label.text = cash(value, self.quote_dropdown.selected_value)

  def _invalidate(self):
    self.scenario_results.visible = False
    self.scenario_error.visible = False
    self.scenario_status.text = "Inputs changed. Calculate to update the scenario."

  def _calculate(self):
    self.scenario_error.visible = False
    self.scenario_results.visible = False
    base, quote = self._pair()
    try:
      result = calculate_scenario(base, quote, self.direction_dropdown.selected_value,
        self.entry_box.text, self.exit_box.text, self.units_box.text, self.stop_box.text,
        self.target_box.text, self.costs_box.text, self.budget_box.text)
    except ValueError as error:
      self.scenario_error.text = str(error)
      self.scenario_error.visible = True
      self.scenario_status.text = "Check the highlighted scenario message."
      return
    self.net_label.text = cash(result["net"], quote)
    self.profit_card.classes["is-positive"] = result["net"] >= 0
    self.profit_card.classes["is-negative"] = result["net"] < 0
    self.gross_label.text = "Gross %s · costs %s" % (cash(result["gross"], quote), cash(result["costs"], quote))
    self.stop_label.text = cash(result["stop_loss"], quote)
    self.budget_label.text = "Budget: " + cash(result["budget"], quote)
    self.target_label.text = cash(result["target_net"], quote)
    self.reward_label.text = "Net reward / risk: {:.2f}×".format(result["reward_risk"])
    self.sizing_label.text = "{:,.0f} {}".format(result["max_units"], base)
    self.pip_label.text = "1 pip = %s · pip value %s\nScenario move: %+.1f pips · entry notional %s" % (
      rate_text(result["pip_size"], quote), cash(result["pip_value"], quote), result["pips"], cash(result["notional"], quote))
    self.risk_notice.visible = result["over_budget"] or result["max_units"] == 0
    self.risk_notice.text = ("Fixed costs consume the risk budget; no whole units fit this scenario." if result["max_units"] == 0
                            else "The estimated loss at your stop exceeds your risk budget.")
    self.scenario_status.text = "Calculated from your inputs. Stop and target prices are assumptions."
    self.scenario_results.visible = True

  @handle("base_dropdown", "change")
  @handle("quote_dropdown", "change")
  def pair_change(self, **event_args):
    if self._updating:
      return
    self._updating = True
    for box in (self.entry_box, self.exit_box, self.stop_box, self.target_box):
      box.text = ""
    self.costs_box.text = "0"
    quote_changed = self._scenario_quote != self.quote_dropdown.selected_value
    if quote_changed:
      self.budget_box.text = ""
    self._scenario_quote = self.quote_dropdown.selected_value
    self._updating = False
    self._invalidate()
    self.scenario_status.text = ("Quote currency changed: risk budget cleared. Enter a new budget in " + self._scenario_quote + "."
                                 if quote_changed else "Pair changed. Enter rates or start with the reference example.")
    self._render_market()
    self._update_controls()

  @handle("swap_button", "click")
  def swap_click(self, **event_args):
    base, quote = self._pair()
    self._updating = True
    self.base_dropdown.selected_value = quote
    self.quote_dropdown.selected_value = base
    self._updating = False
    self.pair_change()

  @handle("refresh_button", "click")
  def refresh_click(self, **event_args):
    self._load_rates()

  @handle("range_dropdown", "change")
  def range_change(self, **event_args):
    if not self._updating:
      self._render_plot()
      self.redraw()

  @handle("convert_box", "change")
  def convert_change(self, **event_args):
    if not self._updating:
      self._convert()

  @handle("use_rate_button", "click")
  def example_click(self, **event_args):
    latest = self._latest()
    if latest is None:
      return
    sign = 1 if self.direction_dropdown.selected_value == "long" else -1
    quote = self.quote_dropdown.selected_value
    self._updating = True
    self.entry_box.text = rate_text(latest, quote)
    self.exit_box.text = rate_text(latest * (1 + sign * .01), quote)
    self.stop_box.text = rate_text(latest * (1 - sign * .005), quote)
    self.target_box.text = rate_text(latest * (1 + sign * .01), quote)
    self._updating = False
    if not str(self.budget_box.text or "").strip():
      self._invalidate()
      self.scenario_status.text = "Example rates applied. Enter a risk budget in " + quote + ", then calculate."
      return
    self._calculate()
    self.scenario_status.text = "Illustration: entry at reference, exit/target 1% favorable, stop 0.5% adverse. Edit every assumption."

  @handle("calculate_button", "click")
  def calculate_click(self, **event_args):
    self._calculate()

  @handle("direction_dropdown", "change")
  @handle("units_box", "change")
  @handle("entry_box", "change")
  @handle("exit_box", "change")
  @handle("stop_box", "change")
  @handle("target_box", "change")
  @handle("costs_box", "change")
  @handle("budget_box", "change")
  def scenario_change(self, **event_args):
    if not self._updating:
      self._invalidate()

  def redraw(self):
    self._plot_size = chart_theme.redraw(self.history_plot, self._plot_size)

  def apply_appearance(self, mode=None):
    self._render_market()
    self.redraw()
