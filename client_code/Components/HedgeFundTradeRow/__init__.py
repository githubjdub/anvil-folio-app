from ._anvil_designer import HedgeFundTradeRowTemplate
from anvil import *


class HedgeFundTradeRow(HedgeFundTradeRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._editable = True
    self._expanded = False
    self.detail_region.visible = False

  @handle("", "x-set-editable")
  def set_editable(self, editable=True, **event_args):
    self._editable = bool(editable)
    self.detail_button.enabled = self._editable

  @handle("detail_button", "click")
  def detail_click(self, **event_args):
    if self._editable:
      self._expanded = not self._expanded
      self.detail_region.visible = self._expanded
      self.detail_button.text = "Hide details" if self._expanded else "Details"
