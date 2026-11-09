from anvil import *
from ._anvil_designer import PortfolioHoldingTableRowTemplate


class PortfolioHoldingTableRow(PortfolioHoldingTableRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.weight_fill.style["width"] = "%s%%" % (self.item["weight"] * 100)
    value = self.item["return"]
    self.return_cell.classes["is-positive"] = value is not None and value > 0
    self.return_cell.classes["is-negative"] = value is not None and value < 0
    self.classes["is-closed"] = not self.item["is_open"]
