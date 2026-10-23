from anvil import *
from ._anvil_designer import IncomeLossRowTemplate


class IncomeLossRow(IncomeLossRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.set_event_handler("x-set-editable", self.set_editable)
    self.classes["needs-review"] = self.item["possible_wash_sale"]

  def set_editable(self, editable=True, **event_args):
    self.select_checkbox.enabled = editable

  @handle("select_checkbox", "change")
  def select_change(self, **event_args):
    self.item["selected"] = bool(self.select_checkbox.checked)
    self.parent.raise_event("x-loss-selection")

  def details(self):
    return "%s · acquired %s · {:,.6f} shares".format(self.item["shares"]) % (self.item["term"], self.item["acquired_date"])

  def values(self):
    return "Basis ${:,.2f} · market value ${:,.2f} · price ${:,.4f} on {}{}".format(self.item["cost_basis"], self.item["current_value"], self.item["current_price"], self.item["price_date"], " · estimated basis" if self.item["basis_estimated"] else "")
