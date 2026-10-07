from ._anvil_designer import CreatorAnalyticsPageRowTemplate
from anvil import *


class CreatorAnalyticsPageRow(CreatorAnalyticsPageRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._editable = True

  @handle("", "x-set-editable")
  def set_editable(self, editable=True, **event_args):
    self._editable = bool(editable)
    setattr(self.filter_button, "enabled", self._editable)

  @handle("filter_button", "click")
  def filter_page(self, **event_args):
    if self._editable:
      self.parent.raise_event("x-filter-page", page_key=self.item["key"])
