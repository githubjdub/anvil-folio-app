import anvil.server
from anvil import *
from ._anvil_designer import IncomeTrackerTemplate
from ... import chart_theme


class IncomeTracker(IncomeTrackerTemplate):
  PAGE_SIZE = 20

  def __init__(self, **properties):
    super().__init__(**properties)
    self._busy = False
    self._parent_editable = True
    self._generation = 0
    self._fingerprint = None
    self._transactions = []
    self._as_of = None
    self._report = None
    self._attempted = False
    self._view = "dividends"
    self._ledger_page = 0
    self._loss_page = 0
    self._lots = []
    self._plot_size = None
    self._chart_mode = None
    self.account_type_dropdown.items = [("US taxable account", "taxable"), ("IRA / tax-advantaged account", "retirement")]
    self.account_type_dropdown.selected_value = "taxable"
    self.loss_term_dropdown.items = [("All losing lots", "all"), ("Short-term only", "Short-term"), ("Long-term only", "Long-term")]
    self.loss_term_dropdown.selected_value = "all"
    self.set_event_handler("x-set-editable", self.set_editable)
    self.losses_panel.set_event_handler("x-loss-selection", self.loss_selection_change)
    self.positions_panel.set_event_handler("x-simulate-position", self.simulate_position)
    self.winning_positions_panel.set_event_handler("x-simulate-position", self.simulate_position)
    self.reset_workspace()
    self._apply_view()

  def reset_workspace(self):
    self._generation += 1
    self._fingerprint = None
    self._transactions = []
    self._as_of = None
    self._clear_report()
    self.context_label.text = "Sync a portfolio to track income and review its losing tax lots."
    self._set_status("")
    self._update_controls()

  def _clear_report(self):
    self._report = None
    self._attempted = False
    self._ledger_page = 0
    self._loss_page = 0
    self._lots = []
    self._positions_expanded = False
    self._plot_size = None
    self.income_plot.data = []
    for panel in (self.securities_panel, self.quarters_panel, self.upcoming_panel, self.ledger_panel, self.losses_panel, self.terms_panel, self.positions_panel, self.winning_positions_panel):
      panel.items = []
    self.sale_planner.set_report(None)
    self.report_region.visible = False

  def set_portfolio_context(self, transactions, as_of=None):
    docs = [{key: row.get(key) for key in ("ticker", "trade_date", "action", "shares", "trade_price")} for row in (transactions or [])]
    fingerprint = (str(as_of), tuple(tuple(str(row[key]) for key in ("ticker", "trade_date", "action", "shares", "trade_price")) for row in docs))
    if fingerprint == self._fingerprint:
      return
    self._generation += 1
    self._fingerprint = fingerprint
    self._transactions = docs
    self._as_of = as_of
    self._clear_report()
    self.context_label.text = ("Selected securities · %s logged transactions · positions as of %s" % (len(docs), as_of)) if docs and as_of else "Sync a portfolio and select securities to track their income."
    self._set_status("Open Dividends, Passive Income or Tax Harvester to load this snapshot." if docs else "")
    self._update_controls()

  def set_editable(self, editable=True, **event_args):
    self._parent_editable = editable
    self._update_controls()

  def ensure_loaded(self):
    if not self._attempted and not self._busy and self._parent_editable and self._transactions and self._as_of:
      self.refresh_click()

  def _set_status(self, message, error=False):
    self.status_label.text = message
    self.status_region.classes["is-error"] = error

  def _update_controls(self):
    enabled = not self._busy and self._parent_editable
    setattr(self.refresh_button, "enabled", enabled and bool(self._transactions) and bool(self._as_of))
    self.refresh_button.text = "Loading review…" if self._busy else "Refresh review"
    self.loss_term_dropdown.enabled = enabled
    self.account_type_dropdown.enabled = enabled
    setattr(self.ledger_previous_button, "enabled", enabled and self._ledger_page > 0)
    ledger_count = len(self._report["dividends"]["ledger"]) if self._report else 0
    setattr(self.ledger_next_button, "enabled", enabled and (self._ledger_page + 1) * self.PAGE_SIZE < ledger_count)
    setattr(self.loss_previous_button, "enabled", enabled and self._loss_page > 0)
    setattr(self.loss_next_button, "enabled", enabled and (self._loss_page + 1) * self.PAGE_SIZE < len(self._filtered_lots()))
    self.losses_panel.raise_event_on_children("x-set-editable", editable=enabled)
    self.positions_panel.raise_event_on_children("x-set-editable", editable=enabled)
    self.winning_positions_panel.raise_event_on_children("x-set-editable", editable=enabled)
    self.positions_toggle_button.enabled = enabled
    self.sale_planner.set_editable(enabled)

  @handle("refresh_button", "click")
  def refresh_click(self, **event_args):
    if self._busy or not self._parent_editable or not self._transactions or not self._as_of:
      return
    self._generation += 1
    generation = self._generation
    self._clear_report()
    self._attempted = True
    self._busy = True
    self._update_controls()
    self.raise_event("x-income-busy", busy=True)
    self._set_status("Checking dividend history, payment dates, announced ex-dates and your logged tax lots…")
    try:
      report = anvil.server.call("get_income_review", self._transactions, self._as_of)
      if generation != self._generation:
        return
      if not report["ok"]:
        self._set_status(report["message"], error=True)
        return
      self._report = report
      self._lots = [dict(lot, selected=False) for lot in report["tax"]["candidates"]]
      self._render_report()
      self.report_region.visible = True
      self.redraw()
      self._set_status("Review ready. Estimates use only the selected securities and logged transactions.")
    finally:
      self._busy = False
      self._update_controls()
      self.raise_event("x-income-busy", busy=False)

  @staticmethod
  def money(value):
    return "Unavailable" if value is None else "{}${:,.2f}".format("−" if value < 0 else "", abs(value))

  @staticmethod
  def percent(value):
    return "Unavailable" if value is None else "{:,.2f}%".format(value)

  def _render_report(self):
    report = self._report
    assert report is not None
    dividends = report["dividends"]
    totals = dividends["totals"]
    self.annual_label.text = self.money(totals["annual_estimate"])
    self.quarter_label.text = self.money(totals["quarterly_average"])
    self.monthly_label.text = self.money(totals["annual_estimate"] / 12 if totals["annual_estimate"] is not None else None)
    self.yield_label.text = self.percent(totals["yield_on_cost"])
    self.paid_label.text = self.money(totals["paid_ytd"])
    self.coverage_label.text = dividends["metadata"].get("coverage_note", "")
    self.unresolved_label.text = "%s dividend records have unconfirmed payment dates. Received-cash totals exclude them." % totals["unresolved_count"] if totals["unresolved_count"] else "Payment dates are matched automatically. Received-cash totals are modeled from those dates."
    self.dividend_unresolved_label.text = self.unresolved_label.text
    ledger_count = len(dividends["ledger"])
    self.payment_coverage_label.text = "%s / %s records" % (ledger_count - totals["unresolved_count"], ledger_count)
    self.securities_panel.items = dividends["securities"]
    self.quarters_panel.items = dividends["quarterly_history"]
    upcoming_as_of = dividends["metadata"].get("upcoming_as_of", "today")
    self.upcoming_context_label.text = "Published dates checked as of %s. Estimated amounts use your selected holdings snapshot." % upcoming_as_of
    self.upcoming_panel.items = [self._dividend_item(row, True) for row in dividends["upcoming"]]
    self.upcoming_empty_label.visible = not bool(dividends["upcoming"])
    self._render_ledger()
    tax = report["tax"]
    summary = tax["summary"]
    self.tax_context_label.text = "%s · FIFO estimate through %s" % (summary["tax_year"], self._as_of)
    self.realized_short_label.text = self.money(summary["realized_short_term"])
    self.realized_long_label.text = self.money(summary["realized_long_term"])
    self.realized_net_label.text = self.money(summary["realized_net"])
    self.unrealized_short_label.text = self.money(summary["unrealized_short_term"])
    self.unrealized_long_label.text = self.money(summary["unrealized_long_term"])
    self.unrealized_net_label.text = self.money(summary["unrealized_net"])
    self.terms_panel.items = tax["term_breakdown"]
    missing_basis = summary["unknown_basis_count"]
    missing_prices = summary["unknown_price_count"]
    missing_sales = sum(row["unknown_realized_count"] for row in tax["term_breakdown"])
    self.tax_coverage_label.visible = bool(missing_basis or missing_prices or missing_sales or summary["estimated_basis_count"])
    coverage = []
    if missing_basis or missing_prices or missing_sales:
      coverage.append("Incomplete coverage: %s open lots lack basis, %s lack market prices, and %s sold lots lack complete trade prices. Affected totals are unavailable; incomplete positions are excluded from the ranking." % (missing_basis, missing_prices, missing_sales))
    if summary["estimated_basis_count"]:
      coverage.append("%s open lots use estimated purchase prices. Check broker cost basis before acting." % summary["estimated_basis_count"])
    self.tax_coverage_label.text = " ".join(coverage)
    self._render_positions()
    self.sale_planner.set_report(tax, self._as_of)

    self.tax_notes_label.text = "\n".join(tax.get("notes", []))
    self._render_losses()
    self.loss_selection_change()
    self._apply_view()
    self._draw_chart()

  def _dividend_item(self, row, upcoming=False):
    amount = row.get("estimated_cash") if upcoming else row.get("earned")
    source = row.get("source") or "Unconfirmed source"
    per_share = "Amount not confirmed" if row.get("amount") is None else "$%.6f per share" % row["amount"]
    return {"headline": "%s · ex-dividend %s" % (row["symbol"], row["ex_date"]),
      "amount": "Cash amount unavailable" if amount is None else self.money(amount) + (" estimated" if upcoming else " earned"),
      "detail": "%s · payment %s" % (per_share, row.get("payment_date") or "not confirmed"),
      "status": ("Published upcoming ex-date" if upcoming else row["status"]) + " · " + source,
      "source_url": row.get("source_url") or "", "note": row.get("note", "")}

  def _render_positions(self):
    if self._report is None:
      return
    tax = self._report["tax"]
    losing, winning = tax["losing_positions"], tax["winning_positions"]
    for positions, panel, empty, context in (
        (losing, self.positions_panel, self.positions_empty_label, self.positions_context_label),
        (winning, self.winning_positions_panel, self.winning_positions_empty_label, self.winning_positions_context_label)):
      shown = positions if self._positions_expanded else positions[:5]
      panel.items = shown
      empty.visible = not bool(positions)
      context.text = "%s of %s positions · ranked by dollar amount" % (len(shown), len(positions)) if positions else "No positions to rank"
    self.positions_toggle_button.visible = max(len(losing), len(winning)) > 5
    self.positions_toggle_button.text = "Show top 5" if self._positions_expanded else "Show all positions"
    self._update_controls()

  @handle("positions_toggle_button", "click")
  def positions_toggle(self, **event_args):
    if self._busy or not self._parent_editable:
      return
    self._positions_expanded = not self._positions_expanded
    self._render_positions()

  def simulate_position(self, symbol, **event_args):
    if not self._busy and self._parent_editable:
      self.sale_planner.choose_position(symbol)

  def _render_ledger(self):
    report = self._report
    assert report is not None
    rows = report["dividends"]["ledger"]
    start = self._ledger_page * self.PAGE_SIZE
    self.ledger_panel.items = [self._dividend_item(row) for row in rows[start:start + self.PAGE_SIZE]]
    self.ledger_page_label.text = "%s–%s of %s dividends" % (start + 1, min(start + self.PAGE_SIZE, len(rows)), len(rows)) if rows else "No earned dividends in this transaction history."
    self.ledger_navigation.visible = len(rows) > self.PAGE_SIZE
    self._update_controls()

  def _filtered_lots(self):
    term = self.loss_term_dropdown.selected_value
    return [lot for lot in self._lots if term == "all" or lot["term"] == term]

  def _render_losses(self):
    lots = self._filtered_lots()
    start = self._loss_page * self.PAGE_SIZE
    self.losses_panel.items = lots[start:start + self.PAGE_SIZE]
    self.loss_empty_label.visible = not bool(lots)
    self.loss_empty_label.text = "No priced losing lots match this holding-period filter." if self._lots else "No priced losing lots were identified. Missing basis or prices can prevent a lot from being assessed."
    self.loss_page_label.text = "%s–%s of %s losing lots" % (start + 1, min(start + self.PAGE_SIZE, len(lots)), len(lots)) if lots else ""
    self.loss_navigation.visible = len(lots) > self.PAGE_SIZE
    self._update_controls()

  def loss_selection_change(self, **event_args):
    if self._report is None:
      return
    selected = [lot for lot in self._lots if lot.get("selected")]
    flagged = sum(lot["potential_loss"] for lot in selected if lot["possible_wash_sale"])
    eligible = sum(lot["potential_loss"] for lot in selected if not lot["possible_wash_sale"])
    gain = self._report["tax"]["summary"]["realized_net"]
    self.selected_loss_label.text = self.money(eligible)
    self.offset_label.text = self.money(min(eligible, max(0, gain))) if gain is not None else "Unavailable"
    self.flagged_loss_label.text = self.money(flagged)
    self.offset_note_label.text = "Based on %s selected lots across all pages and holding-period filters. Flagged purchases are excluded from this estimate. Other accounts, future purchases and broker basis adjustments can change the result." % len(selected)

  def set_view(self, view):
    if view not in ("dividends", "passive", "tax"):
      raise ValueError("Unknown income view")
    self._view = view
    self._apply_view()

  def _apply_view(self):
    self.view_title_label.text = {"dividends": "Dividends", "passive": "Passive Income", "tax": "Tax Harvester"}[self._view]
    self.view_intro_label.text = {
      "dividends": "Follow upcoming ex-dividend dates and review the payments earned by your selected holdings.",
      "passive": "See what your holdings could pay each year, quarter and month, with income trends and security-level detail.",
      "tax": "Find opportunities across winning and losing positions, then test how a sale would change your realized gains.",
    }[self._view]
    self.dividends_region.visible = self._view == "dividends"
    self.passive_region.visible = self._view == "passive"
    self.tax_region.visible = self._view == "tax"
    self.income_coverage_region.visible = self._view != "tax"
    taxable = self.account_type_dropdown.selected_value == "taxable"
    self.taxable_region.visible = taxable
    self.retirement_note.visible = not taxable

  @handle("loss_term_dropdown", "change")
  def loss_term_change(self, **event_args):
    self._loss_page = 0
    self._render_losses()

  @handle("account_type_dropdown", "change")
  def account_type_change(self, **event_args):
    self._apply_view()

  @handle("ledger_previous_button", "click")
  def ledger_previous(self, **event_args):
    self._move_page("ledger", -1)

  @handle("ledger_next_button", "click")
  def ledger_next(self, **event_args):
    self._move_page("ledger", 1)

  @handle("loss_previous_button", "click")
  def loss_previous(self, **event_args):
    self._move_page("loss", -1)

  @handle("loss_next_button", "click")
  def loss_next(self, **event_args):
    self._move_page("loss", 1)

  def _move_page(self, kind, direction):
    if self._busy or not self._parent_editable or self._report is None:
      return
    if kind == "ledger":
      maximum = max(0, (len(self._report["dividends"]["ledger"]) - 1) // self.PAGE_SIZE)
      self._ledger_page = max(0, min(maximum, self._ledger_page + direction))
      self._render_ledger()
    else:
      maximum = max(0, (len(self._filtered_lots()) - 1) // self.PAGE_SIZE)
      self._loss_page = max(0, min(maximum, self._loss_page + direction))
      self._render_losses()

  def _draw_chart(self):
    if self._report is None:
      return
    rows = self._report["dividends"]["quarterly_history"][-8:]
    average = self._report["dividends"]["totals"]["quarterly_average"]
    labels = [row["label"] + ("*" if row["is_partial"] else "") for row in rows]
    self.income_plot.data = [{"type":"bar", "x":labels, "y":[row["amount"] for row in rows], "name":"Matched dividend cash", "marker":{"color":chart_theme.POSITIVE}, "hovertemplate":"%{x}<br>$%{y:,.2f}<extra>Matched cash</extra>"},
      {"type":"scatter", "mode":"lines", "x":labels, "y":[average for row in rows], "name":"Estimated average quarter", "line":{"color":chart_theme.BENCHMARK,"dash":"dot"}, "hovertemplate":"$%{y:,.2f}<extra>Estimated average quarter</extra>"}]
    self.income_plot.layout = chart_theme.layout({"margin":{"l":64,"r":18,"t":24,"b":70}, "legend":{"orientation":"h","y":-0.2}, "xaxis":{"type":"category"}, "yaxis":{"tickprefix":"$","rangemode":"tozero"}, "hovermode":"x unified"})
    self._chart_mode = chart_theme.MODE
    self._plot_size = None

  def redraw(self):
    if self._report is not None and self._view == "passive":
      if self._chart_mode != chart_theme.MODE:
        self._draw_chart()
      self._plot_size = chart_theme.redraw(self.income_plot, self._plot_size)

  def apply_appearance(self):
    self._draw_chart()
    self.redraw()
