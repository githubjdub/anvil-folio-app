from ._anvil_designer import CreatorAccountRowTemplate
from anvil import *


class CreatorAccountRow(CreatorAccountRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._editable = True
    self.browse_button.visible = not self.item.get("selected", False)

  def display_date(self, value):
    return value.strftime("%b %d, %Y %H:%M UTC") if value is not None else "Not recorded"

  @handle("", "x-set-editable")
  def set_editable(self, editable=True, **event_args):
    self._editable = bool(editable)
    setattr(self.browse_button, "enabled", self._editable)

  @handle("browse_button", "click")
  def browse(self, **event_args):
    if self._editable and not self.item.get("selected", False):
      self.parent.raise_event("x-browse-account", account_id=self.item["account_id"])
