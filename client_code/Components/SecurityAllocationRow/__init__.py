from ._anvil_designer import SecurityAllocationRowTemplate
from anvil import *


class SecurityAllocationRow(SecurityAllocationRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.set_event_handler("x-set-editable", self.set_editable)
    default = self.item.get("default_sector", self.item["sector"])
    self.sector_dropdown.items = [("App default · " + default, None)] + [(sector, sector) for sector in self.item.get("sector_options", [])]
    self.sector_dropdown.selected_value = self.item.get("sector_override")
    self.swatch.style["background-color"] = self.item["color"]
    self.weight_fill.style.update({"background-color": self.item["color"], "width": "%s%%" % self.item.get("weight_percent", 0)})
    self.classes["is-excluded"] = not self.item["included"]
    profit = self.item.get("profit")
    self.performance_metrics.classes["is-positive"] = profit is not None and profit > 0
    self.performance_metrics.classes["is-negative"] = profit is not None and profit < 0

  def set_editable(self, editable, can_classify=False, **event_args):
    self.include_checkbox.enabled = editable
    self.sector_dropdown.enabled = editable and can_classify and not self.item.get("sector_locked", False)
    self.sector_dropdown.tooltip = ("Funds and ETFs are kept in their own group." if self.item.get("sector_locked", False)
                                   else "Choose a sector, or App default to clear your choice." if can_classify
                                   else "Sign in to save your own sector classification.")

  @handle("sector_dropdown", "change")
  def sector_dropdown_change(self, **event_args):
    self.parent.raise_event("x-sector-changed", symbol=self.item["symbol"], sector=self.sector_dropdown.selected_value)

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
