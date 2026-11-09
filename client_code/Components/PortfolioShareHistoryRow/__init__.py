from anvil import *
from ._anvil_designer import PortfolioShareHistoryRowTemplate


class PortfolioShareHistoryRow(PortfolioShareHistoryRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.classes["is-latest"] = self.item["latest"]
    value = self.item["change"]
    self.change_cell.classes["is-positive"] = value is not None and value > 0
    self.change_cell.classes["is-negative"] = value is not None and value < 0
