from ._anvil_designer import AllocationGuideTemplate
from anvil import *
from ..AllocationTargetRow import AllocationTargetRow
from ... import chart_theme
from ...allocation_models import ASSET_CLASSES, MODELS, calculate_allocation, get_model, money


class AllocationGuide(AllocationGuideTemplate):
  def __init__(self, **properties):
    self._updating = True
    super().__init__(**properties)
    self._editable = True
    self._portfolio_value = None
    self._portfolio_as_of = None
    self._plot_size = None
    self._appearance_mode = chart_theme.MODE
    self.model_dropdown.items = [(model["name"], model["key"]) for model in MODELS]
    self.reset_workspace()

  def reset_workspace(self):
    self._updating = True
    self.model_dropdown.selected_value = "moderate"
    self.amount_box.text = "100000"
    self.compare_checkbox.checked = False
    self._current_values = {asset["key"]: "" for asset in ASSET_CLASSES}
    self._amount_source = "Example amount · change it or import your portfolio value."
    self._result = None
    self._updating = False
    self._calculate()

  def set_portfolio_context(self, value=None, as_of=None):
    self._portfolio_value = value
    self._portfolio_as_of = as_of
    self._update_controls()

  def set_editable(self, editable, **event_args):
    self._editable = editable
    self._update_controls()

  def _update_controls(self):
    comparing = bool(self.compare_checkbox.checked)
    self.model_dropdown.enabled = self._editable
    self.compare_checkbox.enabled = self._editable
    self.amount_box.enabled = self._editable and not comparing
    self.import_button.enabled = self._editable and not comparing and bool(self._portfolio_value)
    self.calculate_button.enabled = self._editable
    self.reset_button.enabled = self._editable
    self.amount_field.visible = not comparing
    self.portfolio_import.visible = not comparing
    self.comparison_help.visible = comparing
    self.compare_total.visible = comparing
    self.current_heading.visible = comparing
    self.gap_heading.visible = comparing
    self.table_heading.classes["is-comparing"] = comparing
    self.calculate_button.text = "Update comparison" if comparing else "Update targets"
    if self._portfolio_value:
      self.portfolio_hint.text = "Selected holdings: %s · %s. Cash excluded." % (
        money(int(round(self._portfolio_value * 100))), self._portfolio_as_of or "latest sync")
    else:
      self.portfolio_hint.text = "Sync a portfolio to import its selected holdings value, or enter an amount."
    self.targets_panel.raise_event_on_children("x-set-editable", editable=self._editable)

  def _capture_current(self):
    for row in self.targets_panel.get_components():
      if isinstance(row, AllocationTargetRow):
        self._current_values[row.item["key"]] = row.current_box.text

  def _set_model_copy(self):
    model = get_model(self.model_dropdown.selected_value)
    self.model_title.text = model["name"]
    self.model_goal.text = model["goal"]
    self.horizon_label.text = "Schwab horizon · " + model["horizon"]
    self.stock_label.text = "%d%%" % sum(model["weights"][:3])
    self.bond_label.text = "%d%%" % model["weights"][3]
    self.cash_label.text = "%d%%" % model["weights"][4]

  def _colors(self):
    return [chart_theme.BENCHMARK, chart_theme.COMPARISON, chart_theme.AMBER,
            chart_theme.POSITIVE, chart_theme.MUTED]

  def _render_rows(self):
    model = get_model(self.model_dropdown.selected_value)
    colors = self._colors()
    rows = []
    for index, asset in enumerate(ASSET_CLASSES):
      row: dict = dict(asset)
      row.update(color=colors[index], comparing=bool(self.compare_checkbox.checked),
                 target_percent=model["weights"][index], target_text="—", difference_text="—",
                 difference_hint="Update to compare", current_input=self._current_values[asset["key"]])
      if self._result is not None:
        result = self._result["rows"][index]
        row["target_text"] = money(result["target_cents"])
        if self._result["comparing"]:
          gap = result["difference_cents"]
          row["difference_text"] = (("+" if gap > 0 else "−") if gap else "") + money(abs(gap))
          row["difference_hint"] = "Below target" if gap > 0 else "Above target" if gap < 0 else "On target"
      rows.append(row)
    self.targets_panel.items = rows
    self._update_controls()

  def _render_plot(self):
    model = get_model(self.model_dropdown.selected_value)
    comparing = self._result is not None and self._result["comparing"]
    labels = ["Target", "Current"] if comparing else ["Target"]
    traces = []
    for index, asset in enumerate(ASSET_CLASSES):
      values = [model["weights"][index]]
      if comparing:
        values.append(self._result["rows"][index]["current_percent"])
      traces.append({"type": "bar", "orientation": "h", "name": asset["name"],
                     "y": labels, "x": values, "marker": {"color": self._colors()[index]},
                     "hovertemplate": asset["name"] + "<br>%{y}: %{x:.1f}%<extra></extra>"})
    self.mix_plot.height = "120px" if comparing else "88px"
    self.mix_plot.data = traces
    self.mix_plot.layout = chart_theme.layout({
      "barmode": "stack", "bargap": .45, "showlegend": False,
      "margin": {"l": 65, "r": 12, "t": 6, "b": 28},
      "xaxis": {"range": [0, 100], "ticksuffix": "%", "dtick": 25, "fixedrange": True, "zeroline": False},
      "yaxis": {"autorange": "reversed", "fixedrange": True, "showgrid": False, "zeroline": False},
    })
    self.mix_plot.config = {"displayModeBar": False, "responsive": True}
    self._plot_size = None

  def _calculate(self, show_error=True):
    self._set_model_copy()
    self.error_label.text = ""
    self.error_label.visible = False
    try:
      self._result = calculate_allocation(
        self.model_dropdown.selected_value, self.amount_box.text,
        self._current_values if self.compare_checkbox.checked else None)
    except ValueError as error:
      self._result = None
      if show_error:
        self.error_label.text = str(error)
        self.error_label.visible = True
    self._render_summary()
    self._render_rows()
    self._render_plot()

  def _render_summary(self):
    comparing = bool(self.compare_checkbox.checked)
    self.amount_note.text = self._amount_source
    self.total_label.text = money(self._result["total_cents"]) if self._result else "—"
    if comparing:
      self.calculation_note.text = ("Targets use the sum of your current balances. + is below target; − is above target."
                                    if self._result else "Enter current balances, then update to calculate dollar targets and gaps.")
    else:
      self.calculation_note.text = ("Dollar targets based on %s." % money(self._result["total_cents"])
                                    if self._result else "Update the amount to calculate dollar targets.")

  def _invalidate(self):
    had_result = self._result is not None
    self._result = None
    self.error_label.visible = False
    if had_result:
      self.targets_panel.raise_event_on_children("x-invalidate")
      self._render_plot()
    self._render_summary()

  @handle("model_dropdown", "change")
  def model_change(self, **event_args):
    if self._updating:
      return
    self._capture_current()
    self._calculate(show_error=not self.compare_checkbox.checked or any(self._current_values.values()))

  @handle("compare_checkbox", "change")
  def compare_change(self, **event_args):
    if self._updating:
      return
    self._capture_current()
    self._calculate(show_error=not self.compare_checkbox.checked or any(self._current_values.values()))

  @handle("amount_box", "change")
  def amount_change(self, **event_args):
    if self._updating:
      return
    self._amount_source = "Custom amount · USD."
    self._invalidate()

  @handle("calculate_button", "click")
  @handle("amount_box", "pressed_enter")
  @handle("targets_panel", "x-calculate")
  def calculate_click(self, **event_args):
    if self._updating or not self._editable:
      return
    self._capture_current()
    self._calculate()

  @handle("targets_panel", "x-balance-change")
  def balance_change(self, key, value, **event_args):
    if self._updating:
      return
    self._current_values[key] = value
    self._invalidate()

  @handle("import_button", "click")
  def import_click(self, **event_args):
    if not self._editable or not self._portfolio_value or self.compare_checkbox.checked:
      return
    self._updating = True
    self.amount_box.text = "%.2f" % self._portfolio_value
    self._updating = False
    self._amount_source = "Imported selected holdings · %s · cash excluded." % (self._portfolio_as_of or "latest sync")
    self._calculate()

  @handle("reset_button", "click")
  def reset_click(self, **event_args):
    if self._editable:
      self.reset_workspace()

  def redraw(self):
    if self._appearance_mode != chart_theme.MODE:
      self.apply_appearance()
    self._plot_size = chart_theme.redraw(self.mix_plot, self._plot_size)

  def apply_appearance(self):
    self._appearance_mode = chart_theme.MODE
    self._capture_current()
    self._render_rows()
    self._render_plot()
