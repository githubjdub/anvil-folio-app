from ._anvil_designer import SectorAllocationRowTemplate
from anvil import *


class SectorAllocationRow(SectorAllocationRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.swatch.style["background-color"] = self.item["color"]
    self.weight_fill.style.update({"background-color": self.item["color"], "width": "%s%%" % (self.item["weight"] * 100)})
    change = self.item.get("change_pp")
    self.classes["is-positive"] = change is not None and change >= 0.05
    self.classes["is-negative"] = change is not None and change <= -0.05
