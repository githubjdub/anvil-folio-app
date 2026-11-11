from ._anvil_designer import SectorAllocationRowTemplate
from anvil import *


class SectorAllocationRow(SectorAllocationRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._pointer_inside = False
    self._focused = False
    self.set_event_handler("x-highlight-allocation", self.highlight_allocation)
    self.style["--allocation-color"] = self.item["color"]
    self.swatch.style["background-color"] = self.item["color"]
    self.weight_fill.style.update({"background-color": self.item["color"], "width": "%s%%" % (self.item["weight"] * 100)})
    change = self.item.get("change_pp")
    self.classes["is-positive"] = change is not None and change >= 0.05
    self.classes["is-negative"] = change is not None and change <= -0.05

  def highlight_allocation(self, name=None, **event_args):
    self.classes["is-highlighted"] = name == self.item["sector"]
    self.classes["is-dimmed"] = name is not None and name != self.item["sector"]

  def _notify_hover(self):
    if self.parent is not None:
      self.parent.raise_event("x-allocation-hover", name=self.item["sector"],
                              active=self._pointer_inside or self._focused)

  def row_mouse_enter(self, event):
    self._pointer_inside = True
    self._notify_hover()

  def row_mouse_leave(self, event):
    self._pointer_inside = False
    self._notify_hover()

  def row_focus_in(self, event):
    self._focused = True
    self._notify_hover()

  def row_focus_out(self, event):
    self._focused = False
    self._notify_hover()
