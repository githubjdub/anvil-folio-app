from ._anvil_designer import HoldingRowTemplate
from anvil import *


class HoldingRow(HoldingRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)

  def _update_item(self, key, value):
    self.item[key] = value

  @handle("ticker_box", "change")
  def ticker_box_change(self, **event_args):
    self._update_item("ticker", self.ticker_box.text)

  @handle("trade_date_picker", "change")
  def trade_date_picker_change(self, **event_args):
    self._update_item("trade_date", self.trade_date_picker.date)

  @handle("action_dropdown", "change")
  def action_dropdown_change(self, **event_args):
    self._update_item("action", self.action_dropdown.selected_value)

  @handle("shares_box", "change")
  def shares_box_change(self, **event_args):
    self._update_item("shares", self.shares_box.text)

  @handle("remove_button", "click")
  def remove_button_click(self, **event_args):
    self.parent.raise_event("x-remove-holding", item=self.item)
