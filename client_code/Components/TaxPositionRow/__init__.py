from anvil import *
from ._anvil_designer import TaxPositionRowTemplate
from ...tax_scenarios import format_shares


class TaxPositionRow(TaxPositionRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.set_event_handler("x-set-editable", self.set_editable)

  def set_editable(self, editable=True, **event_args):
    self.simulate_button.enabled = editable

  @handle("simulate_button", "click")
  def simulate_click(self, **event_args):
    self.parent.raise_event("x-simulate-position", symbol=self.item["symbol"])

  def money(self, value, signed=False):
    if value is None:
      return "Unavailable"
    prefix = "−" if value < 0 else "+" if signed and value > 0 else ""
    return "{}${:,.2f}".format(prefix, abs(value))

  def value_role(self, value):
    if value is None or value == 0:
      return ["security-value"]
    return ["security-value", "tax-value-positive" if value > 0 else "tax-value-negative"]

  def position_details(self):
    shares = self.item.get("shares")
    count = self.item.get("lot_count")
    share_text = "Shares unavailable" if shares is None else "%s shares" % format_shares(shares)
    lot_text = "Lot count unavailable" if count is None else "{} open lot{}".format(count, "" if count == 1 else "s")
    return "{} · {}".format(share_text, lot_text)

  def return_text(self):
    value = self.item.get("unrealized_return")
    return "" if value is None else "{:+,.1f}%".format(value * 100)

  def review_note(self):
    notes = []
    if self.item.get("price_date"):
      notes.append("Market price as of {}".format(self.item["price_date"]))
    if self.item.get("basis_estimated"):
      notes.append("Basis includes estimated purchase prices")
    if self.item.get("possible_wash_sale") and self.item.get("unrealized_gain", 0) < 0:
      notes.append("Possible wash-sale conflict: review recent purchases and reinvestments before selling")
    return " · ".join(notes)
