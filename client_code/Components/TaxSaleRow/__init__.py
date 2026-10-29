from anvil import *
from ._anvil_designer import TaxSaleRowTemplate
from ...tax_scenarios import money, value_role, format_shares


class TaxSaleRow(TaxSaleRowTemplate):
  money = staticmethod(money)
  value_role = staticmethod(value_role)

  def __init__(self, **properties):
    super().__init__(**properties)
    self.set_event_handler("x-set-editable", self.set_editable)

  def details(self):
    return "%s shares × %s · %s · %s shares remain" % (
      format_shares(self.item["shares"]), money(self.item["price"]), self.item["method"],
      format_shares(self.item["remaining_shares"]))

  def holding_periods(self):
    return "Short-term %s · long-term %s" % (money(self.item["short_term"], True), money(self.item["long_term"], True))

  def set_editable(self, editable=True, **event_args):
    self.edit_button.enabled = editable
    self.remove_button.enabled = editable

  @handle("edit_button", "click")
  def edit_click(self, **event_args):
    self.parent.raise_event("x-edit-sale", symbol=self.item["symbol"])

  @handle("remove_button", "click")
  def remove_click(self, **event_args):
    self.parent.raise_event("x-remove-sale", symbol=self.item["symbol"])
