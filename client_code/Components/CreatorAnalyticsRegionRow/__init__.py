from ._anvil_designer import CreatorAnalyticsRegionRowTemplate
from anvil import *


class CreatorAnalyticsRegionRow(CreatorAnalyticsRegionRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._editable = True

  @handle("", "x-set-editable")
  def set_editable(self, editable=True, **event_args):
    self._editable = bool(editable)
    setattr(self.filter_button, "enabled", self._editable)

  @handle("filter_button", "click")
  def filter_country(self, **event_args):
    if self._editable:
      self.parent.raise_event("x-filter-country", country_code=self.item["country_code"])
