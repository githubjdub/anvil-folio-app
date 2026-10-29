from ._anvil_designer import AllocationTargetRowTemplate
from anvil import *


class AllocationTargetRow(AllocationTargetRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.set_event_handler("x-set-editable", self.set_editable)
    self.set_event_handler("x-invalidate", self.invalidate)
    self.swatch.style["background-color"] = self.item["color"]
    self.classes["is-comparing"] = self.item["comparing"]
    self.current_region.visible = self.item["comparing"]
    self.difference_region.visible = self.item["comparing"]

  def set_editable(self, editable, **event_args):
    self.current_box.enabled = editable

  def invalidate(self, **event_args):
    self.item["target_text"] = "—"
    self.item["difference_text"] = "—"
    self.item["difference_hint"] = "Update to compare"
    self.refresh_data_bindings()

  @handle("current_box", "change")
  def current_change(self, **event_args):
    self.item["current_input"] = self.current_box.text
    self.parent.raise_event("x-balance-change", key=self.item["key"], value=self.current_box.text)

  @handle("current_box", "pressed_enter")
  def current_enter(self, **event_args):
    self.item["current_input"] = self.current_box.text
    self.parent.raise_event("x-calculate")
