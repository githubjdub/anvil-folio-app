from ._anvil_designer import SecurityAllocationRowTemplate
from anvil import *


class SecurityAllocationRow(SecurityAllocationRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.set_event_handler("x-set-editable", self.set_editable)
    self.swatch.style["background-color"] = self.item["color"]
    self.weight_fill.style.update({"background-color": self.item["color"], "width": "%s%%" % self.item.get("weight_percent", 0)})
    self.classes["is-excluded"] = not self.item["included"]
    profit = self.item.get("profit")
    self.performance_metrics.classes["is-positive"] = profit is not None and profit > 0
    self.performance_metrics.classes["is-negative"] = profit is not None and profit < 0

  def set_editable(self, editable, **event_args):
    self.include_checkbox.enabled = editable

  @handle("include_checkbox", "change")
  def include_checkbox_change(self, **event_args):
    included = bool(self.include_checkbox.checked)
    self.item["included"] = included
    self.classes["is-excluded"] = not included
    self.parent.raise_event(
      "x-security-selection-changed",
      symbol=self.item["symbol"],
      included=included,
    )
