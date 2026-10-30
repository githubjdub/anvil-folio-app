from datetime import date, timedelta

import anvil.server
from anvil import *
from ._anvil_designer import NetWorthTemplate
from ... import chart_theme
from ...net_worth_display import ASSET_CATEGORIES, DEBT_CATEGORIES, category_label, money


class NetWorth(NetWorthTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._account_id = None
    self._generation = 0
    self._busy = False
    self._parent_editable = True
    self._portfolio_value = None
    self._portfolio_as_of = None
    self._appearance_mode = chart_theme.MODE
    self.category_dropdown.items = ASSET_CATEGORIES + DEBT_CATEGORIES
    self.range_dropdown.items = [("All snapshots", 0), ("Last 30 days", 30),
                                 ("Last 90 days", 90), ("Last year", 365)]
    self.range_dropdown.selected_value = 0
    for panel in (self.assets_panel, self.debts_panel):
      panel.set_event_handler("x-edit-entry", self.edit_entry)
      panel.set_event_handler("x-remove-entry", self.remove_entry)
    self.snapshots_panel.set_event_handler("x-remove-snapshot", self.remove_snapshot)
    self._reset()
    self.set_portfolio_context(None, None)

  def _reset(self):
    self._generation += 1
    self._loaded = False
    self._auto_load_attempted = False
    self._entries = []
    self._snapshots = []
    self._totals = {"assets": 0, "debts": 0, "net_worth": 0, "liquid": 0}
    self._editing = False
    self._editing_id = None
    self.name_box.text = ""
    self.amount_box.text = ""
    self.assets_panel.items = []
    self.debts_panel.items = []
    self.snapshots_panel.items = []
    self.history_plot.data = []
    self.mix_plot.data = []
    for label in (self.net_label, self.assets_label, self.debts_label, self.liquid_label,
                  self.change_label, self.updated_label, self.ratio_label,
                  self.ratio_detail_label, self.largest_label, self.largest_detail_label):
      label.text = ""
    self.editor_region.visible = False
    self._set_status("")
    self._update_controls()

  def set_account_context(self, account_id):
    if account_id != self._account_id:
      self._account_id = account_id
      self._reset()

  def set_portfolio_context(self, value, as_of):
    self._portfolio_value = value
    self._portfolio_as_of = str(as_of) if as_of is not None else None
    self.portfolio_label.text = (
      "%s · Selected holdings as of %s" % (money(value), as_of)
      if value is not None else "Sync portfolio history to import the selected holdings' market value."
    )
    self._update_controls()

  def set_editable(self, editable=True, **event_args):
    self._parent_editable = editable
    self._update_controls()

  def _can_act(self):
    return bool(self._account_id) and self._parent_editable and not self._busy

  def _update_controls(self):
    signed_in = bool(self._account_id)
    enabled = self._can_act()
    self.sign_in_region.visible = not signed_in
    self.workspace_region.visible = signed_in and self._loaded
    self.refresh_button.visible = signed_in
    self.refresh_button.enabled = enabled and not self._editing
    self.refresh_button.text = "Working…" if self._busy else "Refresh"
    self.sign_in_button.enabled = self._parent_editable and not self._busy
    for component in (self.add_asset_button, self.add_debt_button):
      component.enabled = enabled and not self._editing
    self.import_button.enabled = enabled and not self._editing and self._portfolio_value is not None
    self.snapshot_button.enabled = enabled and not self._editing and bool(self._entries)
    for component in (self.name_box, self.category_dropdown, self.amount_box,
                      self.save_button, self.cancel_button):
      component.enabled = enabled
    self.range_dropdown.enabled = enabled
    self.show_breakdown_checkbox.enabled = enabled
    for panel in (self.assets_panel, self.debts_panel, self.snapshots_panel):
      panel.raise_event_on_children("x-set-editable", editable=enabled and not self._editing)

  def _set_status(self, text, error=False):
    self.status_label.text = text
    self.status_region.visible = bool(text)
    self.status_region.classes["is-error"] = error

  def ensure_loaded(self):
    if not self._loaded and not self._auto_load_attempted and self._can_act():
      self.refresh_click()

  def _run(self, function, args=(), message=""):
    if not self._can_act():
      return
    generation = self._generation
    handled = False
    rendered = False
    self._busy = True
    self._set_status("Loading your balance sheet…" if not self._loaded else "Saving…")
    self._update_controls()
    self.raise_event("x-net-worth-busy", busy=True)
    try:
      result = anvil.server.call(function, *args)
      if generation != self._generation:
        return
      if not result["ok"]:
        self._set_status(result["message"], error=True)
        if self._editing:
          self.editor_error_label.text = result["message"]
          self.editor_error_label.visible = True
        handled = True
        return
      self._editing = False
      self._editing_id = None
      self.editor_region.visible = False
      self._loaded = True
      self._entries = result["entries"]
      self._snapshots = result["snapshots"]
      self._totals = result["totals"]
      self._render()
      rendered = True
      self._set_status(message)
      handled = True
    finally:
      if not handled and generation == self._generation:
        self._set_status("")
      self._busy = False
      self.raise_event("x-net-worth-busy", busy=False)
      self._update_controls()
      if rendered and generation == self._generation:
        self.redraw()

  @handle("refresh_button", "click")
  def refresh_click(self, **event_args):
    if self._editing or not self._can_act():
      return
    self._auto_load_attempted = True
    self._run("get_net_worth", message="")

  @handle("sign_in_button", "click")
  def sign_in_click(self, **event_args):
    self.raise_event("x-sign-in")

  def _open_editor(self, category, entry=None):
    if not self._can_act() or self._editing:
      return
    self._editing = True
    self._editing_id = entry.get_id() if entry is not None else None
    self.editor_title_label.text = "Edit balance" if entry is not None else "Add a balance"
    self.name_box.text = entry["name"] if entry is not None else ""
    imported = entry is not None and entry["source_key"] == "selected_portfolio"
    self.category_dropdown.items = ASSET_CATEGORIES if imported else ASSET_CATEGORIES + DEBT_CATEGORIES
    self.category_dropdown.selected_value = category
    self.editor_source_label.visible = imported
    self.amount_box.text = str(entry["amount"]) if entry is not None else ""
    self.save_button.text = "Save changes" if entry is not None else "Add balance"
    self.editor_error_label.visible = False
    self.editor_region.visible = True
    self._set_status("")
    self._update_controls()
    self.name_box.focus()

  @handle("add_asset_button", "click")
  def add_asset_click(self, **event_args):
    self._open_editor("cash")

  @handle("add_debt_button", "click")
  def add_debt_click(self, **event_args):
    self._open_editor("credit_card")

  def edit_entry(self, entry, **event_args):
    self._open_editor(entry["category"], entry)

  @handle("save_button", "click")
  def save_click(self, **event_args):
    if self._editing:
      self._run("save_net_worth_entry", (self._editing_id, self.name_box.text,
                self.category_dropdown.selected_value, self.amount_box.text), "Balance saved.")

  @handle("cancel_button", "click")
  def cancel_click(self, **event_args):
    if not self._can_act():
      return
    self._editing = False
    self._editing_id = None
    self.editor_region.visible = False
    self._set_status("")
    self._update_controls()

  def remove_entry(self, entry, **event_args):
    if self._can_act() and not self._editing and confirm(
      'Remove "%s" from your balance sheet? Saved snapshots will stay unchanged.' % entry["name"],
      title="Remove balance", buttons=[("Cancel", False), ("Remove", True)]
    ):
      self._run("remove_net_worth_entry", (entry.get_id(),), "Balance removed.")

  @handle("import_button", "click")
  def import_click(self, **event_args):
    if not self._editing and self._portfolio_value is not None:
      self._run("import_net_worth_portfolio", (self._portfolio_value, self._portfolio_as_of),
                "Portfolio value saved. Future imports refresh this one entry's balance.")

  @handle("snapshot_button", "click")
  def snapshot_click(self, **event_args):
    if not self._editing:
      self._run("save_net_worth_snapshot", message="Today's snapshot saved. Saving again updates the same UTC day.")

  def remove_snapshot(self, snapshot, **event_args):
    if self._can_act() and not self._editing and confirm(
      "Remove the snapshot for %s? Your current balances will stay unchanged." % snapshot["snapshot_date"],
      title="Remove snapshot", buttons=[("Cancel", False), ("Remove", True)]
    ):
      self._run("remove_net_worth_snapshot", (snapshot.get_id(),), "Snapshot removed.")

  def _render(self):
    assets = [row for row in self._entries if row["kind"] == "asset"]
    debts = [row for row in self._entries if row["kind"] == "debt"]
    self.assets_panel.items = assets
    self.debts_panel.items = debts
    self.snapshots_panel.items = list(reversed(self._snapshots))
    self.empty_assets_label.visible = not assets
    self.empty_debts_label.visible = not debts
    self.snapshot_history.visible = bool(self._snapshots)
    total = self._totals
    self.net_label.text = money(total["net_worth"])
    self.net_label.role = ["nw-net-negative" if total["net_worth"] < 0 else "nw-net-value", "privacy-value"]
    self.assets_label.text = money(total["assets"])
    self.debts_label.text = money(total["debts"])
    self.liquid_label.text = money(total["liquid"])
    self.asset_count_label.text = "%d %s" % (len(assets), "asset" if len(assets) == 1 else "assets")
    self.debt_count_label.text = "%d %s" % (len(debts), "debt" if len(debts) == 1 else "debts")
    self.ratio_label.text = ("{:.1f}%".format(100 * total["debts"] / total["assets"])
                             if total["assets"] else "—")
    self.ratio_detail_label.text = "Debt as a share of recorded assets" if total["assets"] else "Add an asset balance to calculate this ratio"
    if assets and total["assets"]:
      largest = assets[0]
      self.largest_label.text = largest["name"]
      self.largest_detail_label.text = "{:.1f}% of assets · {}".format(100 * largest["amount"] / total["assets"], money(largest["amount"]))
    else:
      self.largest_label.text = "—"
      self.largest_detail_label.text = "Your largest asset will appear here"
    self.change_label.text = (
      "%s vs snapshot on %s" % (money(total["net_worth"] - self._snapshots[-1]["net_worth"], signed=True), self._snapshots[-1]["snapshot_date"])
      if self._snapshots else "Save your first snapshot to start tracking change"
    )
    self.updated_label.text = (
      "Latest balance update · %s" % max(row["updated_at"] for row in self._entries).date()
      if self._entries else "Start by adding what you own and what you owe"
    )
    self._render_charts()

  def _render_charts(self):
    self._appearance_mode = chart_theme.MODE
    amounts = {}
    for row in self._entries:
      if row["kind"] == "asset" and row["amount"] > 0:
        amounts[row["category"]] = amounts.get(row["category"], 0) + row["amount"]
    categories = [key for _, key in ASSET_CATEGORIES if key in amounts]
    palette = {"cash": chart_theme.POSITIVE, "investments": chart_theme.BENCHMARK,
               "retirement": chart_theme.COMPARISON, "property": chart_theme.AMBER,
               "other_asset": chart_theme.MUTED}
    self.mix_plot.visible = bool(categories)
    self.mix_empty_label.visible = not categories
    self.mix_plot.data = [{"type": "pie", "labels": [category_label(key) for key in categories],
                          "values": [amounts[key] for key in categories], "hole": 0.72,
                          "sort": False, "textinfo": "none", "direction": "clockwise",
                          "marker": {"colors": [palette[key] for key in categories],
                                     "line": {"color": chart_theme.PANEL, "width": 3}},
                          "hovertemplate": "%{label}<br>$%{value:,.2f} · %{percent}<extra></extra>"}] if categories else []
    self.mix_plot.layout = chart_theme.layout({"margin": {"l": 4, "r": 4, "t": 10, "b": 12},
      "showlegend": True, "legend": {"orientation": "h", "x": 0.5, "xanchor": "center", "y": -0.08},
      "annotations": [{"text": "ASSET<br>MIX", "x": 0.5, "y": 0.5,
                       "showarrow": False, "font": {"size": 12, "color": chart_theme.MUTED}}]})
    self._render_history()

  @handle("range_dropdown", "change")
  @handle("show_breakdown_checkbox", "change")
  def history_options_change(self, **event_args):
    if self._loaded:
      self._render_history()

  def _render_history(self):
    days = self.range_dropdown.selected_value or 0
    cutoff = date.today() - timedelta(days=days)
    rows = [row for row in self._snapshots if not days or row["snapshot_date"] >= cutoff]
    self.history_plot.visible = bool(rows)
    self.history_empty_label.visible = not rows
    self.history_empty_label.text = ("No snapshots in this range. Try All snapshots, or save one today."
                                   if self._snapshots else "Your story starts here. Save a snapshot, then return as your balances change.")
    traces = []
    series = [("net_worth", "Net worth", chart_theme.POSITIVE)]
    if self.show_breakdown_checkbox.checked:
      series += [("assets", "Assets", chart_theme.BENCHMARK), ("debts", "Debts", chart_theme.NEGATIVE)]
    for key, name, color in series:
      traces.append({"type": "scatter", "mode": "lines+markers",
                     "x": [str(row["snapshot_date"]) for row in rows], "y": [row[key] for row in rows],
                     "name": name, "line": {"color": color, "width": 3 if key == "net_worth" else 1.5},
                     "marker": {"size": 6},
                     "hovertemplate": name + ": $%{y:,.2f}<extra></extra>"})
    self.history_plot.data = traces
    self.history_plot.layout = chart_theme.layout({"margin": {"l": 64, "r": 16, "t": 16, "b": 52},
      "xaxis": {"type": "date", "tickformat": "%b %d\n%Y"},
      "yaxis": {"tickprefix": "$", "tickformat": ",.3s", "zeroline": True},
      "hovermode": "x unified", "showlegend": len(series) > 1,
      "legend": {"orientation": "h", "x": 0, "y": -0.25}})

  def apply_appearance(self):
    if self._loaded and self._appearance_mode != chart_theme.MODE:
      self._render_charts()

  def redraw(self):
    self.apply_appearance()
    if self._loaded:
      chart_theme.redraw(self.mix_plot, force=True)
      chart_theme.redraw(self.history_plot, force=True)
