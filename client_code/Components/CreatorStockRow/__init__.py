from ._anvil_designer import CreatorStockRowTemplate
from anvil import *


class CreatorStockRow(CreatorStockRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._editable = True

  @handle("", "x-set-editable")
  def set_editable(self, editable=True, **event_args):
    self._editable = bool(editable)
    setattr(self.view_button, "enabled", self._editable)

  @handle("view_button", "click")
  def view_portfolios(self, **event_args):
    if self._editable:
      self.parent.raise_event("x-filter-stock", ticker=self.item["ticker"], action=self.item.get("action", "Buy"))
