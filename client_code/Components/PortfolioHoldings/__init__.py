from anvil import *
from ._anvil_designer import PortfolioHoldingsTemplate
from ...holdings_dashboard import build_dashboard, filter_holdings, quarter_periods, ACTIVITIES


class PortfolioHoldings(PortfolioHoldingsTemplate):
  """Saved-analysis holdings view; rendering never fetches or reprices data."""

  def __init__(self, **properties):
    super().__init__(**properties)
    self._source = None
    self._selected = ()
    self._classification = ()
    self._transactions = []
    self._transaction_key = ()
    self._snapshots = {}
    self._snapshot = None
    self._dirty = True
    self.position_dropdown.items = [("Open holdings", "open"), ("All traded securities", "all")]
    self.position_dropdown.selected_value = "open"
    self.activity_dropdown.items = [("Any activity", "all")] + [(title, key) for key, title in ACTIVITIES]
    self.activity_dropdown.selected_value = "all"
    self.sort_dropdown.items = [("Portfolio weight ↓", "value"), ("Symbol A–Z", "symbol"),
                                ("Price return ↓", "return"), ("Shares ↓", "shares")]
    self.sort_dropdown.selected_value = "value"
    self.reset_workspace()

  def reset_workspace(self):
    self._source = None
    self._selected = ()
    self._classification = ()
    self._transactions = []
    self._transaction_key = ()
    self._snapshots = {}
    self._snapshot = None
    self._dirty = True
    self.quarter_dropdown.items = []
    self.quarter_dropdown.selected_value = None
    self.quarter_dropdown.enabled = True
    self.holdings_panel.items = []
    self.activity_panel.items = []
    self.dashboard_region.visible = False
    self.empty_region.visible = True
    self.empty_label.text = "Add a security and sync your portfolio to see your holdings here."

  def set_analysis(self, source_analysis, selected_symbols=None, transactions=None):
    securities = ((source_analysis or {}).get("selection_data") or {}).get("securities") or []
    selected = tuple(sorted(row["symbol"] for row in securities)) if selected_symbols is None else tuple(sorted(selected_symbols))
    classification = tuple((row["symbol"], row.get("sector"), row.get("company_name")) for row in securities)
    transaction_key = tuple((row.get("ticker"), str(row.get("trade_date")), row.get("action"),
                             row.get("shares"), row.get("trade_price")) for row in transactions or [])
    if (source_analysis is self._source and selected == self._selected
        and classification == self._classification and transaction_key == self._transaction_key):
      return
    if source_analysis is None:
      self.reset_workspace()
      return
    previous_quarter = self.quarter_dropdown.selected_value
    self._source = source_analysis
    self._selected = selected
    self._classification = classification
    self._transactions = transactions or []
    self._transaction_key = transaction_key
    self._snapshots = {}
    self._dirty = True
    dates = (source_analysis.get("selection_data") or {}).get("dates") or []
    periods = quarter_periods(dates)
    self.quarter_dropdown.items = [(item["label"] + (" · latest" if index == len(periods) - 1 else ""), item["key"])
                                  for index, item in reversed(list(enumerate(periods)))]
    keys = [item["key"] for item in periods]
    self.quarter_dropdown.selected_value = previous_quarter if previous_quarter in keys else keys[-1] if keys else None
    self.quarter_dropdown.enabled = True

  def set_startup_snapshot(self, snapshot):
    """Display the server-computed current holdings while history loads."""
    if self._snapshot is snapshot:
      return
    self._selected = tuple(row["symbol"] for row in snapshot["rows"])
    period = snapshot["period"]
    self.quarter_dropdown.items = [(period["label"] + " · latest", period["key"])] if period else []
    self.quarter_dropdown.selected_value = period["key"] if period else None
    self.quarter_dropdown.enabled = False
    self._display_snapshot(snapshot)

  def redraw(self):
    """The parent calls this only after making this tab visible."""
    if self._source is None:
      return
    if self._dirty:
      self._render_snapshot()

  def apply_appearance(self):
    pass

  def _render_snapshot(self):
    key = self.quarter_dropdown.selected_value
    if key not in self._snapshots:
      self._snapshots[key] = build_dashboard(self._source, self._selected, key, self._transactions)
    self._display_snapshot(self._snapshots[key])

  def _display_snapshot(self, snapshot):
    self._snapshot = snapshot
    self._dirty = False
    self.dashboard_region.visible = bool(snapshot["rows"])
    self.empty_region.visible = not snapshot["rows"]
    self.empty_label.text = ("Choose a security in Portfolio Structure to include it here." if not self._selected
                            else "Sync this portfolio to build daily position history for this quarter."
                            if snapshot["legacy"] else "No positions or trades in this quarter.")
    period = snapshot["period"]
    label = period["label"] if period else "Latest holdings"
    self.period_label.text = "%s · %s%s" % (label, snapshot["date"] or "latest saved valuation",
                                            " · provisional prices" if snapshot["provisional"] else "")
    self.map_title_label.text = "%d open holding%s" % (snapshot["open_count"], "" if snapshot["open_count"] == 1 else "s")
    self.table_title_label.text = "Portfolio holdings · %s" % label
    self.value_label.text = "$%s" % format(snapshot["total"], ",.2f")
    self.legacy_label.visible = snapshot["legacy"]
    groups = [dict(group, count_text=str(len(group["symbols"])),
                   chips=[{"symbol": symbol, "kind": group["key"]} for symbol in group["symbols"]])
              for group in snapshot["activity_groups"] if group["symbols"]]
    self.activity_panel.items = groups
    self.no_activity_label.visible = not groups
    self._render_table()

  def _render_table(self):
    if self._snapshot is None:
      return
    rows = filter_holdings(self._snapshot["rows"], self.search_box.text,
                           self.activity_dropdown.selected_value or "all",
                           self.sort_dropdown.selected_value or "value",
                           self.position_dropdown.selected_value or "open")
    self.holdings_panel.items = [dict(row,
      shares_text=format(row["shares"], ",.8f").rstrip("0").rstrip("."),
      value_text="$%s" % format(row["market_value"], ",.2f"),
      weight_text="%.2f%%" % (row["weight"] * 100),
      basis_text="—" if row["cost_basis"] is None else "$%s" % format(row["cost_basis"], ",.2f"),
      basis_note=("Sync complete trades and prices for basis." if row["cost_basis"] is None
                  else "Estimated · synced closing prices" if row["basis_estimated"] else "Remaining shares · FIFO"),
      return_text="—" if row["return"] is None else "%+.2f%%" % (row["return"] * 100),
      return_detail=("%s – %s" % (row["return_start"], row["return_end"])) if row["return_start"] else "No measured holding interval",
    ) for row in rows]
    self.filter_empty_label.visible = not rows
    self.filter_count_label.text = "%d securities shown · filters affect this table only" % len(rows)

  @handle("quarter_dropdown", "change")
  def quarter_change(self, **event_args):
    self._dirty = True
    self.redraw()

  @handle("search_box", "change")
  @handle("activity_dropdown", "change")
  @handle("position_dropdown", "change")
  @handle("sort_dropdown", "change")
  def table_filter_change(self, **event_args):
    if self.activity_dropdown.selected_value in ("closed", "mixed"):
      self.position_dropdown.selected_value = "all"
    self._render_table()
