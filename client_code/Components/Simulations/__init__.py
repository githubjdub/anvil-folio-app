"""Archived DRIP comparison Form. Preserved for reference; not mounted in navigation."""
from datetime import date
import anvil.server
from anvil import *
from ._anvil_designer import SimulationsTemplate
from ... import chart_theme
from ..DividendDateRow import DividendDateRow


class Simulations(SimulationsTemplate):
  DATE_PAGE_SIZE = 12
  EVENT_PAGE_SIZE = 30

  def __init__(self, **properties):
    super().__init__(**properties)
    self._busy = False
    self._parent_editable = True
    self._generation = 0
    self._fingerprint = None
    self._transactions = []
    self._as_of = None
    self._prepared = None
    self._dates = []
    self._result = None
    self._date_page = 0
    self._event_page = 0
    self._chart_mode = None
    self._chart_size = None
    self.set_event_handler("x-set-editable", self.set_editable)
    self.dividend_dates_panel.set_event_handler("x-date-changed", self.payment_date_change)
    self.reset_workspace()

  def reset_workspace(self):
    self._generation += 1
    self._fingerprint = None
    self._transactions = []
    self._as_of = None
    self.annual_rate_box.text = "4.0"
    self._clear_prepared()
    self.context_label.text = "Sync a portfolio first. This simulation uses the securities selected in your analysis."
    self._set_status("")
    self._update_controls()

  def set_portfolio_context(self, transactions, as_of=None):
    documents = [{key: row.get(key) for key in ("ticker", "trade_date", "action", "shares", "trade_price")} for row in (transactions or [])]
    fingerprint = (str(as_of), tuple(tuple(str(row[key]) for key in ("ticker", "trade_date", "action", "shares", "trade_price")) for row in documents))
    if fingerprint == self._fingerprint:
      return
    self._generation += 1
    self._fingerprint = fingerprint
    self._transactions = documents
    self._as_of = as_of
    self._clear_prepared()
    if documents and as_of:
      count = len(set(str(row["ticker"]).strip().upper().split(":", 1)[0].replace(".", "-") for row in documents))
      self.context_label.text = "%s selected securit%s · %s transactions · through %s" % (count, "y" if count == 1 else "ies", len(documents), as_of)
      self._set_status("Ready to compare. Reinvest DRIP finds payment dates and runs all three scenarios automatically.")
    else:
      self.context_label.text = "Sync a portfolio first. Select at least one security in your analysis to compare."
      self._set_status("")
    self._update_controls()

  def _clear_prepared(self):
    self._prepared = None
    self._dates = []
    self._date_page = 0
    self.review_region.visible = False
    self.dom_nodes["date_review"].removeAttribute("open")
    self.dividend_dates_panel.items = []
    self._invalidate_result()

  def _invalidate_result(self):
    self._result = None
    self.results_region.visible = False
    self.wealth_plot.data = []
    self.securities_panel.items = []
    self.dividend_events_panel.items = []
    self._chart_size = None
    self._event_page = 0

  def set_editable(self, editable=True, **event_args):
    self._parent_editable = editable
    self._update_controls()

  def _update_controls(self):
    enabled = self._parent_editable and not self._busy
    self.annual_rate_box.enabled = enabled
    setattr(self.prepare_button, "enabled", enabled and bool(self._transactions) and bool(self._as_of))
    self.prepare_button.text = "Looking up & comparing…" if self._busy and self._prepared is None else ("Refresh & compare" if self._prepared is not None else "Reinvest DRIP")
    setattr(self.compare_button, "enabled", enabled and self._prepared is not None)
    self.manual_compare_region.visible = self._prepared is not None and self._result is None
    self.compare_button.text = "Comparing…" if self._busy and self._prepared is not None else "Update comparison"
    setattr(self.date_previous_button, "enabled", enabled and self._date_page > 0)
    setattr(self.date_next_button, "enabled", enabled and (self._date_page + 1) * self.DATE_PAGE_SIZE < len(self._dates))
    setattr(self.event_previous_button, "enabled", enabled and self._event_page > 0)
    total_events = len(self._result["events"]) if self._result else 0
    setattr(self.event_next_button, "enabled", enabled and (self._event_page + 1) * self.EVENT_PAGE_SIZE < total_events)
    for row in self.dividend_dates_panel.get_components():
      if isinstance(row, DividendDateRow):
        row.set_editable(enabled)

  def _set_status(self, message, error=False):
    self.status_label.text = message
    self.status_region.classes["is-error"] = error

  @handle("annual_rate_box", "change")
  def annual_rate_change(self, **event_args):
    # Keep text untouched while editing so decimal entry preserves the cursor.
    self._generation += 1
    self._invalidate_result()
    self._set_status("Annual return changed. Run the comparison to update all three scenarios.")
    self._update_controls()

  def _read_annual_rate(self):
    try:
      value = float(str(self.annual_rate_box.text).strip())
    except (TypeError, ValueError):
      value = None
    if value is None or not 0 <= value <= 100:
      self._invalidate_result()
      self._set_status("Enter an annual return from 0 to 100%. Decimals such as 4.25 are supported.", error=True)
      self._update_controls()
      return None
    return value

  @handle("prepare_button", "click")
  def prepare_click(self, **event_args):
    if self._busy or not self._parent_editable or not self._transactions or not self._as_of:
      return
    annual_rate = self._read_annual_rate()
    if annual_rate is None:
      return
    self._generation += 1
    generation = self._generation
    self._clear_prepared()
    self._busy = True
    self._update_controls()
    self.raise_event("x-simulations-busy", busy=True)
    self._set_status("Looking up payment dates and comparing DRIP, cash, and the fixed-rate account…")
    try:
      result = anvil.server.call("prepare_drip_simulation", self._transactions, self._as_of, annual_rate)
      if generation != self._generation:
        return
      if not result["ok"]:
        self._set_status(result["message"], error=True)
        return
      self._prepared = result
      metadata = result["metadata"]
      self.prepared_period_label.text = "Simulation period: %s to %s · completed closing prices" % (metadata["start_date"], metadata["end_date"])
      self._load_dates(result["events"])
      self.review_region.visible = True
      self._render_date_page()
      self._show_comparison(result["comparison"])
    finally:
      self._busy = False
      self.raise_event("x-simulations-busy", busy=False)
      self._update_controls()

  def _load_dates(self, events, overrides=None):
    overrides = overrides or {}
    self._dates = []
    for event in events:
      row = dict(event)
      original = event.get("payment_date")
      value = overrides.get(event["id"]) or original
      row["_original_payment_date"] = date(*[int(part) for part in original.split("-")]) if original else None
      row["_payment_date"] = date(*[int(part) for part in value.split("-")]) if value else None
      self._dates.append(row)
    self._date_page = min(self._date_page, max(0, (len(self._dates) - 1) // self.DATE_PAGE_SIZE))

  def _commit_dates(self):
    changed = False
    for row in self.dividend_dates_panel.get_components():
      if isinstance(row, DividendDateRow):
        changed = row.commit_date() or changed
    if changed:
      self.payment_date_change()

  def payment_date_change(self, **event_args):
    self._generation += 1
    self._invalidate_result()
    self._update_date_summary()
    self._set_status("Payment date changed. Run the comparison to update the results.")
    self._update_controls()

  def _update_date_summary(self):
    total = len(self._dates)
    missing = sum(1 for row in self._dates if row.get("_payment_date") is None)
    if total == 0:
      message = "No dividend events were found in this period; all three scenarios use the same logged transactions."
    elif missing:
      message = "%s dividend records · %s payment dates unconfirmed. Only dividends earned by your holdings need a payment date." % (total, missing)
    else:
      message = "%s dividends · All payment dates are filled. You can review or correct them below." % total
    self.date_summary_label.text = message
    self.date_summary_region.classes["has-missing-dates"] = missing > 0

  def _render_date_page(self):
    start = self._date_page * self.DATE_PAGE_SIZE
    self.dividend_dates_panel.items = self._dates[start:start + self.DATE_PAGE_SIZE]
    total = len(self._dates)
    self.date_page_label.text = "%s–%s of %s dividends" % (start + 1, min(start + self.DATE_PAGE_SIZE, total), total) if total else "No dividend dates to review"
    self.date_navigation.visible = total > self.DATE_PAGE_SIZE
    self._update_date_summary()
    self._update_controls()

  @handle("date_previous_button", "click")
  def date_previous_click(self, **event_args):
    self._move_date_page(-1)

  @handle("date_next_button", "click")
  def date_next_click(self, **event_args):
    self._move_date_page(1)

  def _move_date_page(self, direction):
    if self._busy or not self._parent_editable:
      return
    self._commit_dates()
    maximum = max(0, (len(self._dates) - 1) // self.DATE_PAGE_SIZE)
    self._date_page = max(0, min(maximum, self._date_page + direction))
    self._render_date_page()

  @handle("compare_button", "click")
  def compare_click(self, **event_args):
    if self._busy or not self._parent_editable or self._prepared is None:
      return
    annual_rate = self._read_annual_rate()
    if annual_rate is None:
      return
    self._commit_dates()
    # A candidate dividend may never be earned after a position closes.
    # The server determines which missing dates actually affect the simulation.
    payment_dates = {row["id"]: row["_payment_date"].isoformat()
                     for row in self._dates if row.get("_payment_date") is not None
                     and row["_payment_date"] != row.get("_original_payment_date")}
    self._generation += 1
    generation = self._generation
    self._invalidate_result()
    self._busy = True
    self._update_controls()
    self.raise_event("x-simulations-busy", busy=True)
    self._set_status("Replaying your transactions with reinvestment, dividend cash, and the fixed-rate account…")
    try:
      result = anvil.server.call("run_drip_simulation", self._transactions, self._as_of, payment_dates, annual_rate)
      if generation != self._generation:
        return
      if "payment_events" in result:
        self._load_dates(result["payment_events"], payment_dates)
        self._render_date_page()
      self._show_comparison(result)
    finally:
      self._busy = False
      self.raise_event("x-simulations-busy", busy=False)
      self._update_controls()

  def _show_comparison(self, result):
    if not result["ok"]:
      event_id = result.get("payment_event_id")
      for index, row in enumerate(self._dates):
        if row["id"] == event_id:
          self._date_page = index // self.DATE_PAGE_SIZE
          self._render_date_page()
          self.dom_nodes["date_review"].setAttribute("open", "")
          break
      self._set_status(result["message"], error=True)
      return
    self._result = result
    self.results_region.visible = True
    self.dom_nodes["date_review"].removeAttribute("open")
    self._render_result()
    self._set_status("Comparison ready. Dividend dates were looked up automatically; any corrections you entered are included.")

  @staticmethod
  def _money(value):
    return "${:,.2f}".format(value)

  @staticmethod
  def _signed_money(value):
    return ("+" if value > 0 else "−" if value < 0 else "") + "${:,.2f}".format(abs(value))

  def _render_result(self):
    result = self._result
    if result is None:
      return
    totals = result["totals"]
    metadata = result["metadata"]
    self.result_period_label.text = "%s to %s · USD" % (metadata["start_date"], metadata["end_date"])
    self.drip_value_label.text = self._money(totals["drip_value"])
    self.cash_value_label.text = self._money(totals["cash_value"])
    self.rate_value_label.text = self._money(totals["rate_value"])
    self.rate_detail_label.text = "%s profit · %s interest earned" % (self._signed_money(totals["rate_profit"]), self._money(totals["rate_interest"]))
    self.rate_assumption_label.text = "Fixed-rate account: %g%% effective annual return · %s deposited · %s ending account balance. Interest compounds over actual calendar days / 365, starting on each payment date." % (metadata["annual_rate_percent"], self._money(totals["rate_principal"]), self._money(totals["rate_balance"]))
    self.difference_label.text = self._signed_money(totals["difference"])
    self.difference_card.classes["is-negative"] = totals["difference"] < 0
    self.rate_difference_label.text = self._signed_money(totals["drip_vs_rate"])
    self.rate_difference_card.classes["is-negative"] = totals["drip_vs_rate"] < 0
    rate_relative = totals["drip_vs_rate"] / totals["rate_value"] * 100 if totals["rate_value"] > 0 else None
    self.rate_difference_detail_label.text = ("%+.2f%% versus the fixed-rate scenario" % rate_relative) if rate_relative is not None else "Difference in ending wealth"
    self.drip_detail_label.text = "%s profit · %s dividends earned" % (self._signed_money(totals["drip_profit"]), self._money(totals["drip_dividends"]))
    self.cash_detail_label.text = "%s profit · %s dividends earned" % (self._signed_money(totals["cash_profit"]), self._money(totals["cash_dividends"]))
    relative = totals["difference"] / totals["cash_value"] * 100 if totals["cash_value"] > 0 else None
    self.difference_detail_label.text = ("%+.2f%% versus holding dividend cash" % relative) if relative is not None else "Difference in ending wealth"
    self.balance_detail_label.text = "Cash balances: DRIP %s · cash scenario %s. Unpaid dividends included: DRIP %s · cash scenario %s." % (self._money(totals["drip_cash"]), self._money(totals["cash_cash"]), self._money(totals["drip_receivable"]), self._money(totals["cash_receivable"]))
    rows = []
    for row in result["securities"]:
      rows.append({"symbol": row["symbol"], "drip_value": self._money(row["drip_value"]), "cash_value": self._money(row["cash_value"]), "difference": self._signed_money(row["difference"]), "negative": row["difference"] < 0,
        "rate_value": self._money(row["rate_value"]), "rate_detail": "%s deposited · %s interest" % (self._money(row["rate_principal"]), self._money(row["rate_interest"])),
        "rate_difference": self._signed_money(row["drip_vs_rate"]), "rate_negative": row["drip_vs_rate"] < 0,
        "drip_detail": "{:,.6f} shares · {} dividends".format(row["drip_shares"], self._money(row["drip_dividends"])),
        "cash_detail": "{:,.6f} shares · {} dividends".format(row["cash_shares"], self._money(row["cash_dividends"]))})
    self.securities_panel.items = rows
    self._render_event_page()
    self._render_chart()

  def _render_event_page(self):
    events = self._result["events"] if self._result else []
    start = self._event_page * self.EVENT_PAGE_SIZE
    rows = []
    for event in events[start:start + self.EVENT_PAGE_SIZE]:
      status = str(event.get("status") or "").replace("_", " ")
      reinvest_date = event.get("reinvest_date") or ("Not reinvested" if "held" in status.lower() else "Pending")
      price = "${:,.6f}".format(event["price"]) if event.get("price") is not None else "—"
      deposit = event["rate_deposit"]
      deposit_date = event.get("rate_deposit_date")
      rate_deposit = ("Fixed-rate deposit %s · %s" % (self._money(deposit), deposit_date)) if deposit_date else ("Fixed-rate deposit pending · %s" % self._money(deposit) if deposit > 0 else "No original-share dividend for the fixed-rate account")
      rows.append({"title": "%s · ex-date %s" % (event["symbol"], event["ex_date"]),
        "dates": "Payment %s · reinvestment %s" % (event.get("payment_date") or "Unknown", reinvest_date),
        "amounts": "Dividend/share %s · DRIP %s · cash %s" % ("${:,.6f}".format(event["amount"]), self._money(event["drip_dividend"]), self._money(event["cash_dividend"])),
        "shares": "+{:,.6f} shares".format(event.get("added_shares", 0)),
        "execution": "Price %s · %s" % (price, status),
        "rate_deposit": rate_deposit,
        "payment_status": "User-entered payment date" if event.get("source") == "Entered by you" else ("Payment date confirmed" if event.get("payment_date") else "Payment date not confirmed")})
    self.dividend_events_panel.items = rows
    self.event_page_label.text = "%s–%s of %s events" % (start + 1, min(start + self.EVENT_PAGE_SIZE, len(events)), len(events)) if events else "No dividend events in this period"
    self.event_navigation.visible = len(events) > self.EVENT_PAGE_SIZE
    self._update_controls()

  @handle("event_previous_button", "click")
  def event_previous_click(self, **event_args):
    self._move_event_page(-1)

  @handle("event_next_button", "click")
  def event_next_click(self, **event_args):
    self._move_event_page(1)

  def _move_event_page(self, direction):
    if self._busy or not self._parent_editable or self._result is None:
      return
    maximum = max(0, (len(self._result["events"]) - 1) // self.EVENT_PAGE_SIZE)
    self._event_page = max(0, min(maximum, self._event_page + direction))
    self._render_event_page()

  def _render_chart(self):
    if self._result is None:
      return
    series = self._result["series"]
    self.wealth_plot.data = [{"type": "scatter", "mode": "lines", "name": label,
      "x": [row["date"] for row in series], "y": [row[key] for row in series],
      "line": {"color": color, "width": 3},
      "hovertemplate": "$%{y:,.2f}<extra>%{fullData.name}</extra>"}
      for key, label, color in (("drip_value", "Reinvest dividends", chart_theme.POSITIVE), ("cash_value", "Keep dividends in cash", chart_theme.BENCHMARK), ("rate_value", "Fixed-rate · %g%% annually" % self._result["metadata"]["annual_rate_percent"], chart_theme.COMPARISON))]
    self.wealth_plot.layout = chart_theme.layout({
      "margin": {"l": 70, "r": 20, "t": 24, "b": 110},
      "xaxis": {"type": "date", "fixedrange": False},
      "yaxis": {"title": "Total wealth · USD", "tickprefix": "$", "tickformat": ",.2s", "rangemode": "tozero"},
      "legend": {"orientation": "h", "x": 0, "y": -0.22},
      "hovermode": "x unified", "showlegend": True})
    self._chart_mode = chart_theme.MODE
    self._chart_size = chart_theme.redraw(self.wealth_plot, force=True)

  def apply_appearance(self):
    if self._result is not None and self._chart_mode != chart_theme.MODE:
      self._render_chart()

  @handle("", "show")
  def form_show(self, **event_args):
    self.redraw()

  def redraw(self):
    self.apply_appearance()
    if self._result is not None:
      self._chart_size = chart_theme.redraw(self.wealth_plot, self._chart_size)
