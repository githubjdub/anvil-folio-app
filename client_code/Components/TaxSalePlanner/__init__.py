from anvil import *
from ._anvil_designer import TaxSalePlannerTemplate
from ...tax_scenarios import METHODS, SaleInputError, simulate_sales, money, value_role, format_shares, netting_description


class TaxSalePlanner(TaxSalePlannerTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._tax = None
    self._sales = []
    self._editable = True
    self.method_dropdown.items = METHODS
    self.method_dropdown.selected_value = "FIFO"
    self.sales_panel.set_event_handler("x-edit-sale", self.edit_sale)
    self.sales_panel.set_event_handler("x-remove-sale", self.remove_sale)
    self.set_event_handler("x-set-editable", self.set_editable)
    self.set_report(None)

  def set_report(self, tax, as_of=None):
    self._tax = tax
    self._sales = []
    self.snapshot_label.text = "Hypothetical sale date: %s · prices in USD" % as_of if tax else "Load a portfolio review to begin."
    positions = sorted((p for p in tax["positions"].values() if p["shares"] > 0), key=lambda p: p["symbol"]) if tax else []
    self.symbol_dropdown.items = [(p["symbol"] + " · " + format_shares(p["shares"]) + " shares", p["symbol"]) for p in positions]
    self.symbol_dropdown.selected_value = positions[0]["symbol"] if positions else None
    self.no_positions_label.visible = not bool(positions)
    self.entry_region.visible = bool(positions)
    self.feedback_label.visible = False
    self._render_scenario(None)
    self.symbol_change()

  def set_editable(self, editable=True, **event_args):
    self._editable = editable
    self._update_controls()

  def _update_controls(self):
    enabled = self._editable and self._tax is not None and self.symbol_dropdown.selected_value is not None
    for control in (self.symbol_dropdown, self.shares_box, self.price_box, self.method_dropdown,
                    self.add_button, self.quarter_button, self.half_button, self.all_button):
      control.enabled = enabled
    self.clear_button.enabled = self._editable and bool(self._sales)
    self.sales_panel.raise_event_on_children("x-set-editable", editable=self._editable)

  @handle("symbol_dropdown", "change")
  def symbol_change(self, **event_args):
    symbol = self.symbol_dropdown.selected_value
    if self._tax is None or symbol is None:
      self.position_label.text = ""
      self._update_controls()
      return
    position = self._tax["positions"][symbol]
    existing = next((sale for sale in self._sales if sale["symbol"] == symbol), None)
    self.shares_box.text = str(existing["shares"] if existing else min(1, position["shares"]))
    price = existing["price"] if existing else position["current_price"]
    self.price_box.text = str(price) if price is not None else ""
    self.method_dropdown.selected_value = existing["method"] if existing else "FIFO"
    self.add_button.text = "Update sale" if existing else "Add to scenario"
    quote = "%s on %s" % (money(position["current_price"]), position["price_date"]) if position["current_price"] is not None else "No market quote · enter a sale price"
    self.position_label.text = "%s shares available · %s open lots · %s" % (format_shares(position["shares"]), position["lot_count"], quote)
    self.feedback_label.visible = False
    self._update_controls()

  def choose_position(self, symbol):
    if not self._editable or self._tax is None or symbol not in self._tax["positions"]:
      return
    self.symbol_dropdown.selected_value = symbol
    self.symbol_change()
    self.scroll_into_view(smooth=True, align="start")
    self.shares_box.focus()

  def _fill_shares(self, fraction):
    symbol = self.symbol_dropdown.selected_value
    if self._editable and self._tax is not None and symbol is not None:
      self.shares_box.text = str(self._tax["positions"][symbol]["shares"] * fraction)

  @handle("quarter_button", "click")
  def quarter_click(self, **event_args):
    self._fill_shares(0.25)

  @handle("half_button", "click")
  def half_click(self, **event_args):
    self._fill_shares(0.5)

  @handle("all_button", "click")
  def all_click(self, **event_args):
    self._fill_shares(1.0)

  @handle("shares_box", "pressed_enter")
  @handle("price_box", "pressed_enter")
  @handle("add_button", "click")
  def add_sale(self, **event_args):
    if not self._editable or self._tax is None or self.symbol_dropdown.selected_value is None:
      return
    symbol = self.symbol_dropdown.selected_value
    proposed = {"symbol": symbol, "shares": self.shares_box.text, "price": self.price_box.text,
                "method": self.method_dropdown.selected_value}
    sales = [proposed if sale["symbol"] == symbol else sale for sale in self._sales]
    if not any(sale["symbol"] == symbol for sale in self._sales):
      sales.append(proposed)
    try:
      result = simulate_sales(self._tax, sales)
    except SaleInputError as exc:
      self.feedback_label.text = str(exc)
      self.feedback_label.visible = True
      return
    self._sales = [{key: sale[key] for key in ("symbol", "shares", "price", "method")} for sale in result["sales"]]
    self._render_scenario(result)
    self.symbol_change()

  def edit_sale(self, symbol, **event_args):
    self.choose_position(symbol)

  def remove_sale(self, symbol, **event_args):
    if not self._editable:
      return
    self._sales = [sale for sale in self._sales if sale["symbol"] != symbol]
    self._render_scenario(simulate_sales(self._tax, self._sales) if self._sales else None)
    self.symbol_change()

  @handle("clear_button", "click")
  def clear_click(self, **event_args):
    if self._editable:
      self._sales = []
      self._render_scenario(None)
      self.symbol_change()

  def _render_scenario(self, result):
    self.results_region.visible = result is not None
    self.empty_label.visible = result is None
    self.sales_panel.items = result["sales"] if result else []
    self.comparisons_panel.items = result["comparisons"] if result else []
    self.lots_panel.items = result["lots"] if result else []
    self.netting_panel.items = result["holding_periods"] if result else []
    self.scenario_count_label.text = "%s sale%s in this scenario" % (len(self._sales), "" if len(self._sales) == 1 else "s")
    if result:
      totals = result["totals"]
      for label, key in ((self.gain_label, "gain"), (self.short_label, "short_term"),
                         (self.long_label, "long_term"), (self.ytd_label, "ytd_net")):
        label.text = money(totals[key], signed=True)
        label.role = value_role(totals[key])
      self.proceeds_label.text = money(totals["proceeds"])
      self.basis_label.text = money(totals["cost_basis"])
      self.ytd_terms_label.text = "Short-term %s · long-term %s" % (money(totals["ytd_short_term"], True), money(totals["ytd_long_term"], True))
      self.netting_label.text = netting_description(result["netting"])
      self.ytd_netting_label.text = netting_description(result["ytd_netting"])
      self.wash_label.visible = totals["flagged_loss"] > 0
      self.wash_label.text = "%s of modeled losses have recent-purchase flags. Review potential wash sales: the modeled offsets below may not be deductible as shown." % money(totals["flagged_loss"])
      self.estimated_label.visible = totals["basis_estimated"]
      self.estimated_label.text = "This scenario includes estimated purchase prices. Check the basis against your broker records."
    self._update_controls()
