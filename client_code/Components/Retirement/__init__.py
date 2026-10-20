import anvil.server
from anvil import *
from ._anvil_designer import RetirementTemplate
from ... import chart_theme


DEFAULTS = {
  "current_age": "35", "retirement_age": "65", "end_age": "95",
  "starting_balance": "100000", "monthly_savings": "1000",
  "monthly_spending": "4000", "monthly_income": "1800",
  "expected_return": "6", "volatility": "15", "inflation": "2.5",
  "annual_fee": "0.25", "bequest_goal": "0",
}


class Retirement(RetirementTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._busy = False
    self._parent_editable = True
    self._generation = 0
    self._result = None
    self._chart_mode = None
    self._chart_size = None
    self._portfolio_value = None
    self._portfolio_date = None
    self.set_event_handler("x-set-editable", self.set_editable)
    for key in DEFAULTS:
      getattr(self, key + "_box").set_event_handler("change", self.inputs_change)
    self.reset_workspace()

  def reset_workspace(self):
    # A response from the previous portfolio must never populate this workspace.
    self._generation += 1
    self._invalidate_result()
    for key, value in DEFAULTS.items():
      getattr(self, key + "_box").text = value
    self._portfolio_value = None
    self._portfolio_date = None
    self.portfolio_context_label.text = "Sync a portfolio to use its selected value, or enter your retirement savings directly."
    self.balance_source_label.text = "Example values only. Replace them with your own plan before running."
    self._set_status("")
    self._update_controls()

  def set_portfolio_context(self, value, as_of=None):
    """Make the latest balance available without replacing the user's scenario."""
    self._portfolio_value = value
    self._portfolio_date = as_of
    if value is None:
      self.portfolio_context_label.text = "Sync a portfolio to use its selected value, or enter your retirement savings directly."
    else:
      self.portfolio_context_label.text = "Selected portfolio value: %s%s. Importing this value is optional." % (
        self._money(value), " · as of %s" % as_of if as_of else "")
    self._update_controls()

  def set_starting_balance(self, value, as_of=None):
    if value is None or self._busy or not self._parent_editable:
      return
    self.starting_balance_box.text = "%.2f" % value
    self.inputs_change()
    self.balance_source_label.text = "Starting savings copied from your selected portfolio%s. Add other retirement assets here if needed." % (
      " as of %s" % as_of if as_of else "")

  def set_editable(self, editable=True, **event_args):
    self._parent_editable = editable
    self._update_controls()

  def _update_controls(self):
    enabled = self._parent_editable and not self._busy
    for key in DEFAULTS:
      getattr(self, key + "_box").enabled = enabled
    setattr(self.run_button, "enabled", enabled)
    setattr(self.use_portfolio_button, "enabled", enabled and self._portfolio_value is not None)
    self.run_button.text = "Simulating…" if self._busy else "Run retirement projection"

  def _set_status(self, text, error=False):
    self.status_label.text = text
    self.status_region.classes["is-error"] = error

  def _invalidate_result(self):
    self._result = None
    self.results_region.visible = False
    self.projection_plot.data = []
    self._chart_size = None

  def inputs_change(self, **event_args):
    self._generation += 1
    self._invalidate_result()
    self._set_status("Inputs changed. Run the projection to update the results.")
    if event_args.get("sender") is self.starting_balance_box:
      self.balance_source_label.text = "Starting savings entered manually. Portfolio changes will not overwrite this amount."

  @handle("use_portfolio_button", "click")
  def use_portfolio_click(self, **event_args):
    if not self._busy and self._parent_editable and self._portfolio_value is not None:
      self.raise_event("x-use-portfolio-balance")

  @handle("run_button", "click")
  def run_click(self, **event_args):
    if self._busy or not self._parent_editable:
      return
    inputs = {key: str(getattr(self, key + "_box").text or "").strip() for key in DEFAULTS}
    self._generation += 1
    generation = self._generation
    self._invalidate_result()
    self._busy = True
    self._update_controls()
    self.raise_event("x-retirement-busy", busy=True)
    self._set_status("Simulating 2,000 possible retirement paths…")
    try:
      result = anvil.server.call("run_retirement_projection", inputs)
      if generation != self._generation:
        return
      if not result["ok"]:
        self._set_status(result["message"], error=True)
        return
      self._result = result
      self.results_region.visible = True
      self._render_result()
      self._set_status("Projection updated. Change any input to explore another scenario.")
    finally:
      self._busy = False
      self.raise_event("x-retirement-busy", busy=False)
      self._update_controls()

  @staticmethod
  def _money(value):
    return "${:,.0f}".format(value)

  @staticmethod
  def _rgba(color, opacity):
    return "rgba(%d,%d,%d,%s)" % (int(color[1:3], 16), int(color[3:5], 16),
                                int(color[5:7], 16), opacity)

  def _render_result(self):
    result = self._result
    if result is None:
      return
    inputs = result["inputs"]
    self.success_label.text = "%.1f%%" % (result["success_rate"] * 100)
    self.success_detail_label.text = "Share of %s simulations funding monthly spending through age %s%s." % (
      "{:,}".format(result["paths"]), inputs["end_age"],
      " and leaving at least %s" % self._money(inputs["bequest_goal"]) if inputs["bequest_goal"] > 0 else "")
    self.funded_label.text = "Spending alone was funded in %.1f%% of simulations. Results depend on the assumptions below." % (result["funded_rate"] * 100)
    self.retirement_value_label.text = self._money(result["retirement_p50"])
    self.retirement_age_label.text = "Median at retirement · age %s" % inputs["retirement_age"]
    self.ending_value_label.text = self._money(result["ending_p50"])
    self.ending_age_label.text = "Median at plan end · age %s" % inputs["end_age"]
    self.downside_value_label.text = self._money(result["ending_p10"])
    self.range_label.text = "10th–90th percentile at plan end: %s to %s." % (
      self._money(result["ending_p10"]), self._money(result["ending_p90"]))
    surplus = inputs["monthly_income"] - inputs["monthly_spending"]
    if surplus > 0:
      self.gap_label.text = "%s per month of income above spending is invested during retirement." % self._money(surplus)
    else:
      self.gap_label.text = "%s per month is drawn from savings in retirement after your other income." % self._money(result["monthly_gap"])
    self._render_chart()

  def _render_chart(self):
    if self._result is None:
      return
    years = self._result["years"]
    ages = [row["age"] for row in years]
    traces = []
    for lower, upper, label, opacity in (("p10", "p90", "10th–90th percentile", 0.12),
                                          ("p25", "p75", "25th–75th percentile", 0.24)):
      traces.append({"type": "scatter", "mode": "lines", "x": ages,
                     "y": [row[lower] for row in years], "line": {"width": 0},
                     "hoverinfo": "skip", "showlegend": False})
      traces.append({"type": "scatter", "mode": "lines", "x": ages,
                     "y": [row[upper] for row in years], "line": {"width": 0},
                     "fill": "tonexty", "fillcolor": self._rgba(chart_theme.BENCHMARK, opacity),
                     "name": label, "hoverinfo": "skip"})
    traces.append({"type": "scatter", "mode": "lines", "x": ages,
                   "y": [row["p50"] for row in years], "name": "Median balance",
                   "line": {"color": chart_theme.POSITIVE, "width": 3},
                   "customdata": [[row["p10"], row["p90"]] for row in years],
                   "hovertemplate": "Age %{x}<br>Median: $%{y:,.0f}<br>10th: $%{customdata[0]:,.0f}<br>90th: $%{customdata[1]:,.0f}<extra></extra>"})
    retirement_age = self._result["inputs"]["retirement_age"]
    self.projection_plot.data = traces
    self.projection_plot.layout = chart_theme.layout({
      "margin": {"l": 70, "r": 20, "t": 36, "b": 85},
      "xaxis": {"title": "Age", "fixedrange": False},
      "yaxis": {"title": "Today's USD", "tickprefix": "$", "tickformat": ",.2s", "rangemode": "tozero"},
      "legend": {"orientation": "h", "x": 0, "y": -0.22, "font": {"color": chart_theme.MUTED}},
      "hovermode": "x unified", "showlegend": True,
      "shapes": [{"type": "line", "x0": retirement_age, "x1": retirement_age,
                   "y0": 0, "y1": 1, "yref": "paper",
                   "line": {"color": chart_theme.MUTED, "dash": "dot", "width": 1}}],
      "annotations": [{"x": retirement_age, "y": 1, "yref": "paper", "text": "Retirement",
                        "showarrow": False, "yanchor": "bottom",
                        "font": {"color": chart_theme.MUTED, "size": 12}}],
    })
    self._chart_mode = chart_theme.MODE
    self._chart_size = chart_theme.redraw(self.projection_plot, force=True)

  def apply_appearance(self):
    if self._result is not None and self._chart_mode != chart_theme.MODE:
      self._render_chart()

  @handle("", "show")
  def form_show(self, **event_args):
    self.redraw()

  def redraw(self):
    self.apply_appearance()
    if self._result is not None:
      self._chart_size = chart_theme.redraw(self.projection_plot, self._chart_size)
