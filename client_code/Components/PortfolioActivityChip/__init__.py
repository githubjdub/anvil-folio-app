from anvil import *
from ._anvil_designer import PortfolioActivityChipTemplate


class PortfolioActivityChip(PortfolioActivityChipTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.classes["is-sale"] = self.item["kind"] in ("trimmed", "closed")
    self.classes["is-mixed"] = self.item["kind"] == "mixed"
