from ._anvil_designer import HedgeFundRankRowTemplate
from anvil import *


class HedgeFundRankRow(HedgeFundRankRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._editable = True

  @handle("", "x-set-editable")
  def set_editable(self, editable=True, **event_args):
    self._editable = bool(editable)
    self.compare_button.enabled = self._editable

  @handle("compare_button", "click")
  def compare_click(self, **event_args):
    if self._editable:
      self.parent.raise_event("x-compare-fund", fund_key=self.item["fund_key"])
