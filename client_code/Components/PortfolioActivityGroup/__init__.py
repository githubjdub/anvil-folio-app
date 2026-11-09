from anvil import *
from ._anvil_designer import PortfolioActivityGroupTemplate


class PortfolioActivityGroup(PortfolioActivityGroupTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.chips_panel.items = self.item["chips"]
