from ._anvil_designer import SharedHoldingRowTemplate
from anvil import *


class SharedHoldingRow(SharedHoldingRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)

  def quantity_text(self):
    return "%s shares" % format(self.item["shares"], ",.4f").rstrip("0").rstrip(".")

  def price_text(self):
    price = self.item.get("price")
    return "—" if price is None else "$%s / share" % format(price, ",.2f")

  def value_text(self):
    return "$%s" % format(self.item["market_value"], ",.2f")

  def weight_text(self):
    return "%.1f%%" % (self.item["weight"] * 100)
